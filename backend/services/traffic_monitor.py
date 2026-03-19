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
from pathlib import Path
from typing import AsyncIterator, Optional

from ..config import TRAFFIC_SNAPSHOTS_DIR

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
            "capture_mode": "idle",
            "last_error": "",
            "last_packet_time": 0.0,
        }
        self._port_filters = {
            "include_ports": [],
            "exclude_ports": [],
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
                "capture_mode": self._stats.get("capture_mode", "idle"),
                "last_error": self._stats.get("last_error", ""),
                "last_packet_time": self._stats.get("last_packet_time", 0.0),
                "include_ports": list(self._port_filters.get("include_ports", [])),
                "exclude_ports": list(self._port_filters.get("exclude_ports", [])),
            }

    def _scan_result(self) -> Optional[dict]:
        try:
            from ..routers import lan as lan_router
            return getattr(lan_router, "_last_scan_result", None)
        except Exception:
            return None

    def _scan_topology(self) -> dict:
        scan = self._scan_result() or {}
        hosts = scan.get("hosts") or []
        node_map: dict[str, dict] = {}
        edges: list[dict] = []

        for host in hosts:
            ip = str(host.get("ip") or "").strip()
            if not ip:
                continue
            hostname = str(host.get("hostname") or "").strip()
            node_map[ip] = {
                "id": ip,
                "host": ip,
                "port": "",
                "label": hostname or ip,
                "protocols": [],
                "packet_count": 0,
                "byte_count": 0,
                "kind": "host",
            }
            for service in host.get("services") or []:
                port = str(service.get("port") or "").strip()
                proto = str(service.get("protocol") or "service").upper()
                service_id = f"{ip}:{port}" if port else ip
                node_map[service_id] = {
                    "id": service_id,
                    "host": ip,
                    "port": port,
                    "label": f"{ip}:{port}" if port else ip,
                    "protocols": [proto] if proto else [],
                    "packet_count": 0,
                    "byte_count": 0,
                    "kind": "service",
                }
                edges.append({
                    "id": f"{ip}->{service_id}",
                    "source": ip,
                    "target": service_id,
                    "packet_count": 0,
                    "byte_count": 0,
                    "protocols": [proto] if proto else [],
                    "derived_from": "scan",
                })

        return {
            "nodes": list(node_map.values()),
            "edges": edges,
            "source": "scan" if hosts else "none",
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

    def get_topology(self) -> dict:
        """Build a service-to-service topology graph from tracked conversations."""
        conversations = self.get_conversations()
        node_map: dict[str, dict] = {}
        edge_map: dict[str, dict] = {}

        def ensure_node(endpoint: str, protocol: str) -> None:
            host, _, port = endpoint.partition(":")
            key = endpoint
            if key not in node_map:
                node_map[key] = {
                    "id": key,
                    "host": host,
                    "port": port,
                    "label": endpoint,
                    "protocols": set(),
                    "packet_count": 0,
                    "byte_count": 0,
                }
            node_map[key]["protocols"].add(protocol)

        for conv in conversations:
            src = conv["src"]
            dst = conv["dst"]
            protocol = conv["protocol"]
            ensure_node(src, protocol)
            ensure_node(dst, protocol)
            node_map[src]["packet_count"] += conv["packet_count"]
            node_map[src]["byte_count"] += conv["byte_count"]
            node_map[dst]["packet_count"] += conv["packet_count"]
            node_map[dst]["byte_count"] += conv["byte_count"]

            edge_key = f"{src}->{dst}"
            if edge_key not in edge_map:
                edge_map[edge_key] = {
                    "id": edge_key,
                    "source": src,
                    "target": dst,
                    "packet_count": 0,
                    "byte_count": 0,
                    "protocols": set(),
                }
            edge_map[edge_key]["packet_count"] += conv["packet_count"]
            edge_map[edge_key]["byte_count"] += conv["byte_count"]
            edge_map[edge_key]["protocols"].add(protocol)

        nodes = []
        for node in node_map.values():
            nodes.append({
                **node,
                "protocols": sorted(node["protocols"]),
            })
        edges = []
        for edge in edge_map.values():
            edges.append({
                **edge,
                "protocols": sorted(edge["protocols"]),
            })
        nodes.sort(key=lambda item: item["packet_count"], reverse=True)
        edges.sort(key=lambda item: item["packet_count"], reverse=True)
        if nodes or edges:
            scan_topology = self._scan_topology()
            for node in scan_topology.get("nodes", []):
                existing = node_map.get(node["id"])
                if existing:
                    existing["kind"] = existing.get("kind", "service")
                    protocols = set(existing.get("protocols", []))
                    protocols.update(node.get("protocols", []))
                    existing["protocols"] = sorted(protocols)
                else:
                    node_map[node["id"]] = dict(node)
            for edge in scan_topology.get("edges", []):
                edge_map.setdefault(edge["id"], dict(edge))
            nodes = []
            for node in node_map.values():
                nodes.append({
                    **node,
                    "protocols": sorted(node.get("protocols", [])),
                })
            edges = []
            for edge in edge_map.values():
                edges.append({
                    **edge,
                    "protocols": sorted(edge.get("protocols", [])),
                })
            nodes.sort(key=lambda item: (item.get("packet_count", 0), item.get("kind") == "host"), reverse=True)
            edges.sort(key=lambda item: item.get("packet_count", 0), reverse=True)
            return {"nodes": nodes, "edges": edges, "source": "combined"}
        scan_topology = self._scan_topology()
        if scan_topology["nodes"] or scan_topology["edges"]:
            return scan_topology
        return {"nodes": [], "edges": [], "source": "none"}

    def get_recommendations(self) -> list[dict]:
        """Generate deterministic operational recommendations from traffic."""
        conversations = self.get_conversations()
        if not conversations:
            scan = self._scan_result() or {}
            hosts = scan.get("hosts") or []
            if not hosts:
                return []
            recommendations = [{
                "title": "Network map available from LAN scan",
                "severity": "medium",
                "rationale": f"{len(hosts)} scanned host(s) are available for a scan-derived diagram even before live traffic is captured.",
                "action": "Use the network diagram to inspect host-to-service relationships, then start capture to confirm live conversations.",
            }]
            if any(any(str(service.get("protocol", "")).lower().startswith("nmea") for service in host.get("services", [])) for host in hosts):
                recommendations.append({
                    "title": "Navigation sources discovered",
                    "severity": "high",
                    "rationale": "The last LAN scan found one or more NMEA-capable services.",
                    "action": "Open a Navigation Dashboard or begin traffic capture to confirm live bridge data paths.",
                })
            if any(any(str(service.get("port")) == "502" or str(service.get("protocol", "")).lower().startswith("modbus") for service in host.get("services", [])) for host in hosts):
                recommendations.append({
                    "title": "Engineering services discovered",
                    "severity": "medium",
                    "rationale": "The last LAN scan found a MODBUS-like service footprint.",
                    "action": "Validate register ownership and create an engineering monitor around the discovered endpoint.",
                })
            return recommendations

        recommendations = []
        seen_titles = set()
        nmea_convs = [c for c in conversations if c["protocol"] == "NMEA"]
        modbus_convs = [c for c in conversations if c["protocol"] == "MODBUS"]
        http_convs = [c for c in conversations if c["protocol"] in {"HTTP", "JSON/API"}]

        def add(title: str, severity: str, rationale: str, action: str) -> None:
            if title in seen_titles:
                return
            seen_titles.add(title)
            recommendations.append({
                "title": title,
                "severity": severity,
                "rationale": rationale,
                "action": action,
            })

        if nmea_convs:
            busiest = nmea_convs[0]
            add(
                "Navigation feed available",
                "high",
                f"NMEA traffic is active between {busiest['src']} and {busiest['dst']} with {busiest['packet_count']} packets observed.",
                "Generate or open a Navigation Dashboard and verify heading, speed, and depth are decoding cleanly.",
            )
        if modbus_convs:
            busiest = modbus_convs[0]
            add(
                "Engineering telemetry detected",
                "high",
                f"MODBUS traffic is active between {busiest['src']} and {busiest['dst']}.",
                "Deploy the Engineering Plant Monitor or probe the target registers for critical machinery visibility.",
            )
        if http_convs:
            busiest = http_convs[0]
            add(
                "Web/API control plane visible",
                "medium",
                f"HTTP or JSON/API traffic is active on {busiest['src']} -> {busiest['dst']}.",
                "Inspect exposed web interfaces and determine whether a local integration or reverse-proxy app is warranted.",
            )

        for conv in conversations[:8]:
            if conv["packet_count"] >= 100:
                add(
                    "High-volume service path",
                    "medium",
                    f"{conv['src']} -> {conv['dst']} is carrying {conv['packet_count']} packets and {conv['byte_count']} bytes.",
                    "Prioritize this path in the network diagram and consider a dedicated monitoring workspace if it is mission-critical.",
                )
                break

        recent = self.get_recent_frames(60)
        checksum_failures = 0
        for frame in recent:
            decoded = frame.get("payload_decoded") or {}
            if frame.get("content_type") == "nmea" and decoded.get("checksum_ok") is False:
                checksum_failures += 1
        if checksum_failures:
            add(
                "NMEA checksum anomalies observed",
                "critical",
                f"{checksum_failures} recent NMEA frame(s) show checksum failures.",
                "Inspect cable integrity, serial gateway settings, or upstream multiplexers before relying on the feed operationally.",
            )

        return recommendations

    def get_snapshot(self, recent_limit: int = 100) -> dict:
        """Return an export-friendly traffic operating picture."""
        return {
            "exported_at": time.time(),
            "stats": self.stats,
            "topology": self.get_topology(),
            "recommendations": self.get_recommendations(),
            "conversations": self.get_conversations(),
            "recent_frames": self.get_recent_frames(recent_limit),
        }

    def render_snapshot_brief(self, snapshot: dict) -> str:
        """Render a markdown watch-handover brief from a traffic snapshot."""
        exported_at = snapshot.get("exported_at") or time.time()
        stats = snapshot.get("stats") or {}
        topology = snapshot.get("topology") or {}
        recommendations = snapshot.get("recommendations") or []
        conversations = snapshot.get("conversations") or []
        recent_frames = snapshot.get("recent_frames") or []

        top_paths = conversations[:5]
        top_nodes = (topology.get("nodes") or [])[:5]
        protocol_counts = stats.get("protocols") or {}
        protocol_summary = ", ".join(
            f"{name}: {count}" for name, count in sorted(protocol_counts.items(), key=lambda item: item[1], reverse=True)[:6]
        ) or "No protocol counts available"

        lines = [
            "# WarClaw Traffic Watch Brief",
            "",
            f"- Snapshot: {snapshot.get('title') or snapshot.get('id') or 'Current traffic picture'}",
            f"- Exported: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(exported_at))}",
            f"- Packets Captured: {stats.get('packets_captured', 0)}",
            f"- Bytes Captured: {stats.get('bytes_captured', 0)}",
            f"- Active Conversations: {stats.get('conversations', len(conversations))}",
            f"- Protocol Mix: {protocol_summary}",
            "",
            "## Top Service Paths",
            "",
        ]

        if top_paths:
            for conv in top_paths:
                lines.append(
                    f"- `{conv['src']} -> {conv['dst']}` [{conv['protocol']}] "
                    f"{conv['packet_count']} packets / {conv['byte_count']} bytes"
                )
        else:
            lines.append("- No service paths observed.")

        lines.extend(["", "## Critical Nodes", ""])
        if top_nodes:
            for node in top_nodes:
                protocols = ", ".join(node.get("protocols") or []) or "unknown"
                lines.append(
                    f"- `{node['label']}` carrying {node.get('packet_count', 0)} packets across {protocols}"
                )
        else:
            lines.append("- No nodes observed.")

        lines.extend(["", "## Recommendations", ""])
        if recommendations:
            for item in recommendations[:6]:
                lines.append(
                    f"- **{item['title']}** ({item['severity']}): {item['rationale']} Action: {item['action']}"
                )
        else:
            lines.append("- No recommendations generated.")

        lines.extend(["", "## Recent Payload Clues", ""])
        if recent_frames:
            for frame in recent_frames[-5:]:
                payload = frame.get("payload_preview") or ""
                preview = " ".join(payload.split())[:120]
                protocol = frame.get("protocol") or frame.get("content_type") or "unknown"
                lines.append(
                    f"- `{frame.get('src', '?')} -> {frame.get('dst', '?')}` [{protocol}] {preview or 'No payload preview'}"
                )
        else:
            lines.append("- No recent frames stored.")

        return "\n".join(lines) + "\n"

    def save_snapshot(self, recent_limit: int = 100) -> dict:
        TRAFFIC_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = self.get_snapshot(recent_limit=recent_limit)
        snapshot_id = time.strftime("snapshot-%Y%m%d-%H%M%S")
        snapshot["id"] = snapshot_id
        snapshot["title"] = f"Traffic Snapshot {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snapshot['exported_at']))}"
        path = TRAFFIC_SNAPSHOTS_DIR / f"{snapshot_id}.json"
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return snapshot

    def list_snapshots(self) -> list[dict]:
        if not TRAFFIC_SNAPSHOTS_DIR.exists():
            return []
        items = []
        for path in sorted(TRAFFIC_SNAPSHOTS_DIR.glob("snapshot-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items.append({
                "id": payload.get("id") or path.stem,
                "title": payload.get("title") or path.stem,
                "exported_at": payload.get("exported_at") or path.stat().st_mtime,
                "conversation_count": len(payload.get("conversations", [])),
                "recommendation_count": len(payload.get("recommendations", [])),
            })
        return items

    def get_saved_snapshot(self, snapshot_id: str) -> Optional[dict]:
        path = TRAFFIC_SNAPSHOTS_DIR / f"{Path(snapshot_id).name}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def delete_snapshot(self, snapshot_id: str) -> bool:
        path = TRAFFIC_SNAPSHOTS_DIR / f"{Path(snapshot_id).name}.json"
        if not path.exists():
            return False
        path.unlink()
        return True

    def start(self, interface: str = "", duration: int = 0, include_ports: Optional[list[int]] = None, exclude_ports: Optional[list[int]] = None) -> bool:
        """Start packet capture in a background thread."""
        if self._running:
            return False

        self._running = True
        self._stats["start_time"] = time.time()
        self._stats["capture_mode"] = "starting"
        self._stats["last_error"] = ""
        self._port_filters = {
            "include_ports": sorted({int(p) for p in (include_ports or []) if 1 <= int(p) <= 65535}),
            "exclude_ports": sorted({int(p) for p in (exclude_ports or []) if 1 <= int(p) <= 65535}),
        }
        self._thread = threading.Thread(
            target=self._capture_loop,
            args=(interface, duration),
            daemon=True,
        )
        self._thread.start()
        log.info(
            "Traffic monitor started (interface=%s, include_ports=%s, exclude_ports=%s)",
            interface or "auto",
            self._port_filters["include_ports"] or "all",
            self._port_filters["exclude_ports"] or "none",
        )
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
            self._stats["capture_mode"] = "idle"
            self._stats["last_error"] = ""
            self._stats["last_packet_time"] = 0.0
            self._port_filters = {"include_ports": [], "exclude_ports": []}

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

    def _packet_allowed(self, src_port: int, dst_port: int) -> bool:
        include_ports = set(self._port_filters.get("include_ports", []))
        exclude_ports = set(self._port_filters.get("exclude_ports", []))
        ports = {int(src_port or 0), int(dst_port or 0)}
        ports.discard(0)
        if include_ports and not (ports & include_ports):
            return False
        if exclude_ports and (ports & exclude_ports):
            return False
        return True

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

            if not self._packet_allowed(src_port, dst_port):
                return

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
                self._stats["last_packet_time"] = now

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
            self._stats["capture_mode"] = "scapy"

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
            self._stats["last_error"] = "Full packet capture requires elevated privileges; using limited socket fallback."
            self._socket_capture_fallback(duration)
        except ImportError:
            log.error("scapy not installed. Install with: pip install scapy")
            self._stats["capture_mode"] = "unavailable"
            self._stats["last_error"] = "scapy is not installed, so full capture is unavailable."
            self._running = False
        except Exception as e:
            log.error("Capture error: %s", e)
            self._stats["capture_mode"] = "error"
            self._stats["last_error"] = str(e)
            self._running = False

    def _socket_capture_fallback(self, duration: int):
        """Fallback capture using raw sockets when scapy needs root."""
        import socket as sock
        import struct

        log.info("Using socket-based traffic monitor (limited protocol detail)")
        self._stats["capture_mode"] = "socket-fallback"
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
                        self._stats["last_packet_time"] = now
                        self._recent_frames.append(frame_dict)
                        if len(self._recent_frames) > self._max_recent:
                            self._recent_frames = self._recent_frames[-self._max_recent:]

                    self._broadcast(frame_dict)
                except sock.timeout:
                    continue
            s.close()
        except Exception as e:
            log.error("Socket capture fallback error: %s", e)
            self._stats["last_error"] = str(e)
        finally:
            self._running = False


# Module-level singleton
traffic_monitor = TrafficMonitor()
