"""
LAN monitoring endpoints — scan, discover, and stream live protocol data.
"""
import ipaddress
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from ..services.lan_monitor import scan_network, stream_protocol_traffic
from ..services.mission_log import emit as log_event
from ..protocols.modbus import probe_modbus

log = logging.getLogger("warclaw.lan")
router = APIRouter(prefix="/api/lan", tags=["lan"])


def _parse_ports(ports: Optional[str]) -> list[int]:
    if not ports:
        return []
    values: list[int] = []
    for raw in ports.split(","):
        token = raw.strip()
        if not token:
            continue
        try:
            port = int(token)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid port value: {token}")
        if not 1 <= port <= 65535:
            raise HTTPException(status_code=400, detail=f"Port out of range: {port}")
        values.append(port)
    return values


@router.get("/scan")
async def lan_scan(
    network: Optional[str] = Query(None, description="CIDR network e.g. 192.168.1.0/24"),
    ports: Optional[str] = Query(None, description="Optional comma-separated extra ports, e.g. 1883,47808"),
):
    """
    Perform a full LAN discovery scan.
    Finds hosts, identifies services, and returns integration recommendations.
    """
    # Validate CIDR if supplied
    if network:
        try:
            ipaddress.IPv4Network(network, strict=False)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid CIDR notation: {network}")

    extra_ports = _parse_ports(ports)
    log.info("LAN scan requested, network=%s, extra_ports=%s", network or "auto-detect", extra_ports or "default")
    log_event("info", "lan", f"LAN scan started on {network or 'auto-detect'}")
    result = await scan_network(network=network, extra_ports=extra_ports)
    global _last_scan_result

    log_event(
        "success", "lan",
        f"LAN scan complete — {result.hosts_up} host(s) found on {result.network} in {result.scan_duration_s}s",
        {"network": result.network, "hosts_up": result.hosts_up, "duration_s": result.scan_duration_s},
    )

    payload = {
        "network": result.network,
        "interface": result.interface,
        "network_name": result.network_name,
        "probe_ports": result.probe_ports,
        "hosts_scanned": result.hosts_scanned,
        "hosts_up": result.hosts_up,
        "scan_duration_s": result.scan_duration_s,
        "recommendations": result.recommendations,
        "hosts": [
            {
                "ip": h.ip,
                "hostname": h.hostname,
                "open_ports": h.open_ports,
                "services": [
                    {
                        "port": s.port,
                        "protocol": s.protocol,
                        "banner": s.banner,
                        "latency_ms": s.latency_ms,
                    }
                    for s in h.services
                ],
                "integration_hints": h.integration_hints,
            }
            for h in result.discovered
        ],
    }
    _last_scan_result = payload
    return payload


_last_scan_result: Optional[dict] = None


@router.get("/scan/export")
async def export_scan(
    network: Optional[str] = Query(None),
    ports: Optional[str] = Query(None, description="Optional comma-separated extra ports"),
):
    """Run a LAN scan and return results as a downloadable JSON file."""
    if network:
        try:
            ipaddress.IPv4Network(network, strict=False)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid CIDR notation: {network}")

    result = await scan_network(network=network, extra_ports=_parse_ports(ports))
    global _last_scan_result
    payload = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "network": result.network,
        "interface": result.interface,
        "network_name": result.network_name,
        "probe_ports": result.probe_ports,
        "hosts_scanned": result.hosts_scanned,
        "hosts_up": result.hosts_up,
        "scan_duration_s": result.scan_duration_s,
        "recommendations": result.recommendations,
        "hosts": [
            {
                "ip": h.ip,
                "hostname": h.hostname,
                "open_ports": h.open_ports,
                "services": [
                    {"port": s.port, "protocol": s.protocol,
                     "banner": s.banner, "latency_ms": s.latency_ms}
                    for s in h.services
                ],
                "integration_hints": h.integration_hints,
            }
            for h in result.discovered
        ],
    }
    _last_scan_result = payload
    filename = f"warclaw-scan-{result.network.replace('/', '_')}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/last")
async def get_last_scan():
    return {"scan": _last_scan_result}


class ModbusScanRequest(BaseModel):
    host: str
    port: int = 502
    unit_id: int = 1


@router.post("/modbus/probe")
async def probe_modbus_device(req: ModbusScanRequest):
    """Probe a specific MODBUS TCP endpoint and read registers."""
    device = await probe_modbus(req.host, req.port, req.unit_id)
    return {
        "host": device.host,
        "port": device.port,
        "unit_id": device.unit_id,
        "reachable": device.reachable,
        "coils": device.coils,
        "holding_registers": device.holding_registers,
        "error": device.error,
    }


@router.websocket("/stream")
async def stream_lan_traffic(ws: WebSocket):
    """
    WebSocket: connect to a discovered NMEA/IEC service and stream decoded data.

    Client sends JSON: {"host": "192.168.1.10", "port": 10110, "max_messages": 100}
    Server streams JSON protocol frames until done or disconnect.
    """
    await ws.accept()
    log.info("LAN stream WebSocket connected")

    try:
        raw = await ws.receive_text()
        params = json.loads(raw)
        host = params.get("host", "")
        port = int(params.get("port", 10110))
        max_msgs = int(params.get("max_messages", 100))

        if not host:
            await ws.send_json({"error": "host is required"})
            await ws.close()
            return

        await ws.send_json({"status": "connecting", "host": host, "port": port})

        async for frame in stream_protocol_traffic(host, port, max_msgs):
            await ws.send_json(frame)

        await ws.send_json({"status": "complete"})
    except WebSocketDisconnect:
        log.info("LAN stream WebSocket disconnected")
    except Exception as e:
        log.exception("LAN stream error")
        try:
            await ws.send_json({"error": str(e)})
        except Exception:
            pass
