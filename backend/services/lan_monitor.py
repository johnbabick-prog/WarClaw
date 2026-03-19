"""
LAN Monitor Service

Performs two functions:
  1. Discovery scan — finds hosts and fingerprints open services
  2. Live capture — sniffs packets and detects protocol types in real-time

Runs entirely locally, no external calls.
"""
import asyncio
import ipaddress
import logging
import platform
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import psutil

from ..config import NMEA_TCP_PORTS, MODBUS_TCP_PORT, IEC61162_PORTS, SCAN_TIMEOUT
from ..protocols.nmea import is_nmea_data
from ..protocols.modbus import is_modbus_response, probe_modbus
from ..protocols.iec61162 import is_iec_61162_data

log = logging.getLogger("warclaw.lan")


@dataclass
class ServiceInfo:
    host: str
    port: int
    protocol: str        # "nmea", "modbus", "iec61162", "http", "unknown"
    banner: Optional[str] = None
    latency_ms: Optional[float] = None


@dataclass
class DiscoveredHost:
    ip: str
    hostname: Optional[str] = None
    open_ports: list[int] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
    integration_hints: list[str] = field(default_factory=list)


@dataclass
class LanScanResult:
    network: str
    interface: Optional[str]
    network_name: Optional[str]
    probe_ports: list[int]
    hosts_scanned: int
    hosts_up: int
    discovered: list[DiscoveredHost]
    scan_duration_s: float
    recommendations: list[str] = field(default_factory=list)


# Ports we probe on every host
DEFAULT_PROBE_PORTS = sorted(set(NMEA_TCP_PORTS + [MODBUS_TCP_PORT] + IEC61162_PORTS + [
    21, 22, 23, 25, 53, 80, 110, 123, 139, 143, 161, 443, 445, 502,
    515, 548, 554, 993, 995, 1025, 1080, 1883, 2000, 3306, 3389, 4001,
    4840, 5432, 5672, 5900, 6379, 8000, 8080, 8443, 8888, 9100, 10001,
    10110, 18830, 20000, 2222, 3000,
]))


def _normalize_probe_ports(extra_ports: Optional[list[int]] = None) -> list[int]:
    ports = set(DEFAULT_PROBE_PORTS)
    for port in extra_ports or []:
        if 1 <= int(port) <= 65535:
            ports.add(int(port))
    return sorted(ports)


def _get_local_network_info() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return detected CIDR, interface name, and friendly network name when available."""
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                try:
                    net = ipaddress.IPv4Network(f"{addr.address}/{addr.netmask}", strict=False)
                    # Skip huge networks — only scan /24 or smaller
                    if net.prefixlen >= 16:
                        return str(net), iface, _get_network_name(iface)
                except Exception:
                    continue
    return None, None, None


def _get_network_name(interface: str) -> Optional[str]:
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.check_output(
                ["networksetup", "-getairportnetwork", interface],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
            if ":" in out:
                name = out.split(":", 1)[1].strip()
                if name and "not associated" not in name.lower():
                    return name
        elif system == "Linux":
            out = subprocess.check_output(
                ["iwgetid", interface, "--raw"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
            if out:
                return out
    except Exception:
        return None
    return None


async def _probe_port(host: str, port: int, timeout: float = SCAN_TIMEOUT) -> Optional[ServiceInfo]:
    """Try to connect and grab a banner."""
    t0 = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # Try to grab a small banner
        banner_bytes = b""
        try:
            banner_bytes = await asyncio.wait_for(reader.read(256), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        # Classify protocol
        protocol = "unknown"
        banner_str = None
        if banner_bytes:
            if is_nmea_data(banner_bytes) or is_iec_61162_data(banner_bytes):
                protocol = "nmea/iec61162"
            elif is_modbus_response(banner_bytes):
                protocol = "modbus"
            elif banner_bytes[:4] in (b"HTTP", b"http"):
                protocol = "http"
            try:
                banner_str = banner_bytes.decode("ascii", errors="replace")[:120].strip()
            except Exception:
                pass
        elif port == MODBUS_TCP_PORT:
            protocol = "modbus_candidate"
        elif port in NMEA_TCP_PORTS:
            protocol = "nmea_candidate"

        return ServiceInfo(host=host, port=port, protocol=protocol,
                           banner=banner_str, latency_ms=latency_ms)
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
        return None


def _build_integration_hints(host: str, services: list[ServiceInfo]) -> list[str]:
    hints = []
    protocols = {s.protocol for s in services}
    ports = {s.port for s in services}

    if "nmea/iec61162" in protocols or "nmea_candidate" in protocols:
        hints.append(f"Navigation system detected at {host} — NMEA 0183/IEC 61162 stream available for integration")
    if "modbus" in protocols or "modbus_candidate" in protocols:
        hints.append(f"MODBUS device at {host}:{MODBUS_TCP_PORT} — likely engineering plant or sensor array")
    if "http" in protocols:
        hints.append(f"Web interface at {host} — may expose REST API for system integration")
    if 20000 in ports:
        hints.append(f"Port 20000 open at {host} — possible ECDIS or bridge navigation system")
    if 10001 in ports:
        hints.append(f"Port 10001 open at {host} — possible serial-to-TCP gateway (RS-232/422 equipment)")
    if not hints:
        hints.append(f"Unknown services at {host} — manual inspection recommended")

    return hints


def _build_recommendations(discovered: list[DiscoveredHost]) -> list[str]:
    recs = []
    nmea_hosts = [h for h in discovered if any("nmea" in s.protocol for s in h.services)]
    modbus_hosts = [h for h in discovered if any("modbus" in s.protocol for s in h.services)]
    http_hosts = [h for h in discovered if any(s.protocol == "http" for s in h.services)]

    if nmea_hosts:
        ips = ", ".join(h.ip for h in nmea_hosts[:3])
        recs.append(f"Create a Navigation Dashboard app that streams live position, heading, and depth from {ips}")
    if modbus_hosts:
        ips = ", ".join(h.ip for h in modbus_hosts[:3])
        recs.append(f"Create an Engineering Plant Monitor app to display MODBUS registers from {ips}")
    if http_hosts:
        ips = ", ".join(h.ip for h in http_hosts[:3])
        recs.append(f"Integrate with existing web interfaces at {ips} — reverse-proxy or API bridge possible")
    if len(discovered) >= 5:
        recs.append("High host density detected — consider creating a Ship Systems Overview dashboard")

    return recs


async def scan_network(network: Optional[str] = None, max_hosts: int = 254, extra_ports: Optional[list[int]] = None) -> LanScanResult:
    """Full async LAN discovery scan."""
    t0 = time.monotonic()
    detected_network = None
    detected_interface = None
    detected_network_name = None
    if not network:
        detected_network, detected_interface, detected_network_name = _get_local_network_info()
        network = detected_network or "192.168.1.0/24"

    log.info("Scanning network %s", network)
    net = ipaddress.IPv4Network(network, strict=False)
    hosts = list(net.hosts())[:max_hosts]
    probe_ports = _normalize_probe_ports(extra_ports)

    # Phase 1: port probe all hosts concurrently
    discovered: list[DiscoveredHost] = []

    async def probe_host(ip: str) -> Optional[DiscoveredHost]:
        tasks = [_probe_port(ip, port) for port in probe_ports]
        results = await asyncio.gather(*tasks)
        open_services = [r for r in results if r is not None]
        if not open_services:
            return None
        hostname = None
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass
        hints = _build_integration_hints(ip, open_services)
        return DiscoveredHost(
            ip=ip,
            hostname=hostname,
            open_ports=[s.port for s in open_services],
            services=open_services,
            integration_hints=hints,
        )

    # Scan in batches of 32 to avoid overwhelming the network stack
    batch_size = 32
    for i in range(0, len(hosts), batch_size):
        batch = [str(h) for h in hosts[i:i + batch_size]]
        batch_results = await asyncio.gather(*[probe_host(ip) for ip in batch])
        discovered.extend(h for h in batch_results if h is not None)

    recommendations = _build_recommendations(discovered)
    duration = round(time.monotonic() - t0, 2)

    return LanScanResult(
        network=network,
        interface=detected_interface,
        network_name=detected_network_name,
        probe_ports=probe_ports,
        hosts_scanned=len(hosts),
        hosts_up=len(discovered),
        discovered=discovered,
        scan_duration_s=duration,
        recommendations=recommendations,
    )


async def stream_protocol_traffic(host: str, port: int, max_messages: int = 50) -> AsyncIterator[dict]:
    """
    Connect to a detected service and stream decoded protocol data.
    Used for live monitoring of NMEA/IEC61162 feeds.
    """
    from ..protocols.nmea import parse_sentence

    count = 0
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        log.info("Streaming from %s:%d", host, port)

        while count < max_messages:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not line:
                    break
                text = line.decode("ascii", errors="ignore").strip()
                if text.startswith("$"):
                    sentence = parse_sentence(text)
                    if sentence:
                        yield {
                            "source": f"{host}:{port}",
                            "raw": text,
                            "talker": sentence.talker,
                            "type": sentence.sentence_type,
                            "decoded": sentence.decoded,
                            "checksum_ok": sentence.checksum_ok,
                            "timestamp": time.time(),
                        }
                        count += 1
            except asyncio.TimeoutError:
                yield {"source": f"{host}:{port}", "heartbeat": True, "timestamp": time.time()}

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception as e:
        yield {"source": f"{host}:{port}", "error": str(e), "timestamp": time.time()}
