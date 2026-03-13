"""
Traffic Monitor — Real-time LAN packet capture and analysis.

Captures network traffic using scapy, decodes payloads into human-readable
format, tracks conversation flows (who's talking to who), and provides
data for LLM analysis.
"""
import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

log = logging.getLogger("warclaw.traffic")


@dataclass
class Conversation:
    """A tracked communication flow between two endpoints."""
    src: str
    dst: str
    protocol: str
    packet_count: int = 0
    byte_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    sample_payloads: list[dict] = field(default_factory=list)
    max_samples: int = 10


@dataclass
class TrafficFrame:
    """A single decoded packet for the live feed."""
    timestamp: float
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    protocol: str
    length: int
    payload_preview: str
    payload_decoded: Optional[dict] = None
    content_type: str = "unknown"


class TrafficMonitor:
    """Captures and analyzes LAN traffic using scapy."""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._conversations: dict[str, Conversation] = {}
        self._recent_frames: list[dict] = []
        self._max_recent = 500
        self._stats = {
            "packets_captured": 0,
            "bytes_captured": 0,
            "start_time": 0.0,
            "protocols": defaultdict(int),
        }

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        with self._lock:
            elapsed = time.time() - self._stats["start_time"] if self._stats["start_time"] else 0
            return {
                "running": self._running,
                "packets_captured": self._stats["packets_captured"],
                "bytes_captured": self._stats["bytes_captured"],
                "elapsed_s": round(elapsed, 1),
                "conversations": len(self._conversations),
                "protocols": dict(self._stats["protocols"]),
            }

    def get_conversations(self) -> list[dict]:
        """Return all tracked conversations as dicts."""
        with self._lock:
            convos = []
            for key, c in sorted(
                self._conversations.items(),
                key=lambda x: x[1].last_seen,
                reverse=True,
            ):
                convos.append({
                    "key": key,
                    "src": c.src,
                    "dst": c.dst,
                    "protocol": c.protocol,
                    "packet_count": c.packet_count,
                    "byte_count": c.byte_count,
                    "first_seen": c.first_seen,
                    "last_seen": c.last_seen,
                    "duration_s": round(c.last_seen - c.first_seen, 1),
                    "sample_payloads": c.sample_payloads[-5:],
                })
            return convos

    def get_recent_frames(self, limit: int = 100) -> list[dict]:
        """Return recent captured frames."""
        with self._lock:
            return list(self._recent_frames[-limit:])

    def get_analysis_context(self) -> str:
        """Build a text summary of current traffic for LLM analysis."""
        convos = self.get_conversations()
        if not convos:
            return "No traffic has been captured yet. Start the traffic monitor first."

        lines = [f"LAN Traffic Analysis — {len(convos)} active conversations, {self._stats['packets_captured']} packets captured\n"]
        for c in convos[:20]:
            lines.append(f"- {c['src']} -> {c['dst']} [{c['protocol']}]: {c['packet_count']} packets, {c['byte_count']} bytes")
            for sample in c["sample_payloads"][-2:]:
                preview = sample.get("preview", "")[:200]
                if preview:
                    lines.append(f"  Sample data: {preview}")
                if sample.get("decoded"):
                    lines.append(f"  Decoded: {json.dumps(sample['decoded'], default=str)[:300]}")
        return "\n".join(lines)

    def start(self, interface: str = "", duration: int = 0) -> bool:
        """Start packet capture in a background thread."""
        if self._running:
            return False

        self._running = True
        self._stats["start_time"] = time.time()
        self._thread = threading.Thread(
            target=self._capture_loop,
            args=(interface, duration),
            daemon=True,
        )
        self._thread.start()
        log.info("Traffic monitor started (interface=%s)", interface or "auto")
        return True

    def stop(self):
        """Stop packet capture."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("Traffic monitor stopped")

    def clear(self):
        """Clear all captured data."""
        with self._lock:
            self._conversations.clear()
            self._recent_frames.clear()
            self._stats["packets_captured"] = 0
            self._stats["bytes_captured"] = 0
            self._stats["protocols"] = defaultdict(int)

    def subscribe(self) -> asyncio.Queue:
        """Subscribe to live traffic frames."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Remove a subscriber."""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self, frame_dict: dict):
        """Send frame to all subscribers (non-blocking)."""
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(frame_dict)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    def _decode_payload(self, raw: bytes, src_port: int, dst_port: int) -> tuple[str, Optional[dict], str]:
        """Decode raw payload bytes into human-readable form."""
        if not raw:
            return "", None, "empty"

        text = ""
        decoded = None
        content_type = "binary"

        # Try UTF-8 / ASCII decode
        try:
            text = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            text = raw.hex()[:200]
            return text, None, "binary"

        # Try JSON
        if text.startswith("{") or text.startswith("["):
            try:
                decoded = json.loads(text)
                content_type = "json"
                return text[:500], decoded, content_type
            except json.JSONDecodeError:
                pass

        # Check for NMEA
        if text.startswith("$") or text.startswith("!"):
            content_type = "nmea"
            from ..protocols.nmea import parse_sentence
            sentence = parse_sentence(text.split("\n")[0].strip())
            if sentence:
                decoded = {
                    "talker": sentence.talker,
                    "type": sentence.sentence_type,
                    "data": sentence.decoded,
                    "checksum_ok": sentence.checksum_ok,
                }
            return text[:500], decoded, content_type

        # Check for HTTP
        if text.startswith(("GET ", "POST ", "PUT ", "DELETE ", "HTTP/")):
            content_type = "http"
            lines = text.split("\r\n")
            decoded = {
                "method_or_status": lines[0] if lines else "",
                "headers": {
                    k.strip(): v.strip()
                    for line in lines[1:]
                    if ": " in line
                    for k, v in [line.split(": ", 1)]
                },
            }
            return text[:500], decoded, content_type

        # Check for MODBUS (port 502)
        if dst_port == 502 or src_port == 502:
            content_type = "modbus"
            if len(raw) >= 7:
                decoded = {
                    "transaction_id": int.from_bytes(raw[0:2], "big"),
                    "unit_id": raw[6],
                    "function_code": raw[7] if len(raw) > 7 else None,
                }
            return text[:500], decoded, content_type

        # Plain text
        if text and all(32 <= ord(c) < 127 or c in "\r\n\t" for c in text[:100]):
            content_type = "text"

        return text[:500], decoded, content_type

    def _process_packet(self, pkt):
        """Process a single scapy packet."""
        try:
            from scapy.layers.inet import IP, TCP, UDP

            if not pkt.haslayer(IP):
                return

            ip = pkt[IP]
            src_ip = ip.src
            dst_ip = ip.dst
            length = len(pkt)
            src_port = 0
            dst_port = 0
            protocol = "other"
            payload_bytes = b""

            if pkt.haslayer(TCP):
                tcp = pkt[TCP]
                src_port = tcp.sport
                dst_port = tcp.dport
                protocol = "TCP"
                if tcp.payload:
                    payload_bytes = bytes(tcp.payload)[:1024]
            elif pkt.haslayer(UDP):
                udp = pkt[UDP]
                src_port = udp.sport
                dst_port = udp.dport
                protocol = "UDP"
                if udp.payload:
                    payload_bytes = bytes(udp.payload)[:1024]
            else:
                protocol = ip.proto if isinstance(ip.proto, str) else f"IP/{ip.proto}"

            now = time.time()
            preview, decoded, content_type = self._decode_payload(payload_bytes, src_port, dst_port)

            # Determine protocol label from content
            proto_label = protocol
            if content_type == "nmea":
                proto_label = "NMEA"
            elif content_type == "http":
                proto_label = "HTTP"
            elif content_type == "json":
                proto_label = "JSON/API"
            elif content_type == "modbus":
                proto_label = "MODBUS"

            frame_dict = {
                "timestamp": now,
                "src": f"{src_ip}:{src_port}" if src_port else src_ip,
                "dst": f"{dst_ip}:{dst_port}" if dst_port else dst_ip,
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "src_port": src_port,
                "dst_port": dst_port,
                "protocol": proto_label,
                "length": length,
                "payload_preview": preview[:300] if preview else "",
                "payload_decoded": decoded,
                "content_type": content_type,
            }

            with self._lock:
                self._stats["packets_captured"] += 1
                self._stats["bytes_captured"] += length
                self._stats["protocols"][proto_label] += 1

                # Track conversation
                conv_key = f"{src_ip}:{src_port}->{dst_ip}:{dst_port}"
                if conv_key not in self._conversations:
                    self._conversations[conv_key] = Conversation(
                        src=f"{src_ip}:{src_port}",
                        dst=f"{dst_ip}:{dst_port}",
                        protocol=proto_label,
                        first_seen=now,
                    )
                conv = self._conversations[conv_key]
                conv.packet_count += 1
                conv.byte_count += length
                conv.last_seen = now
                if preview and len(conv.sample_payloads) < conv.max_samples:
                    conv.sample_payloads.append({
                        "preview": preview[:200],
                        "decoded": decoded,
                        "content_type": content_type,
                        "timestamp": now,
                    })

                # Store recent frame
                self._recent_frames.append(frame_dict)
                if len(self._recent_frames) > self._max_recent:
                    self._recent_frames = self._recent_frames[-self._max_recent:]

            self._broadcast(frame_dict)

        except Exception as e:
            log.debug("Packet processing error: %s", e)

    def _capture_loop(self, interface: str, duration: int):
        """Run scapy sniff in a thread."""
        try:
            from scapy.all import sniff, conf
            conf.verb = 0  # suppress scapy output

            kwargs = {
                "prn": self._process_packet,
                "store": False,
                "filter": "ip",
            }
            if interface:
                kwargs["iface"] = interface
            if duration:
                kwargs["timeout"] = duration

            def stop_filter(_pkt):
                return not self._running

            kwargs["stop_filter"] = stop_filter

            log.info("Starting scapy capture...")
            sniff(**kwargs)

        except PermissionError:
            log.error("Traffic capture requires elevated privileges (sudo). Falling back to socket-based capture.")
            self._socket_capture_fallback(duration)
        except ImportError:
            log.error("scapy not installed. Install with: pip install scapy")
            self._running = False
        except Exception as e:
            log.error("Capture error: %s", e)
            self._running = False

    def _socket_capture_fallback(self, duration: int):
        """Fallback capture using raw sockets when scapy needs root."""
        import socket as sock
        import struct

        log.info("Using socket-based traffic monitor (limited protocol detail)")
        try:
            # Create a raw socket — works without root on macOS for UDP
            s = sock.socket(sock.AF_INET, sock.SOCK_DGRAM, sock.IPPROTO_UDP)
            s.settimeout(1.0)
            s.bind(("", 0))

            start = time.time()
            while self._running:
                if duration and (time.time() - start) > duration:
                    break
                try:
                    data, addr = s.recvfrom(4096)
                    now = time.time()
                    src_ip, src_port = addr
                    preview, decoded, content_type = self._decode_payload(data, src_port, 0)

                    frame_dict = {
                        "timestamp": now,
                        "src": f"{src_ip}:{src_port}",
                        "dst": "this_host",
                        "src_ip": src_ip,
                        "dst_ip": "this_host",
                        "src_port": src_port,
                        "dst_port": 0,
                        "protocol": "UDP",
                        "length": len(data),
                        "payload_preview": preview[:300],
                        "payload_decoded": decoded,
                        "content_type": content_type,
                    }

                    with self._lock:
                        self._stats["packets_captured"] += 1
                        self._stats["bytes_captured"] += len(data)
                        self._recent_frames.append(frame_dict)
                        if len(self._recent_frames) > self._max_recent:
                            self._recent_frames = self._recent_frames[-self._max_recent:]

                    self._broadcast(frame_dict)
                except sock.timeout:
                    continue
            s.close()
        except Exception as e:
            log.error("Socket capture fallback error: %s", e)
        finally:
            self._running = False


# Module-level singleton
traffic_monitor = TrafficMonitor()
