"""
Traffic Monitor endpoints — start/stop capture, view conversations, analyze with AI.
"""
import asyncio
import time
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..services.traffic_monitor import traffic_monitor
from ..services.llm import llm_service
from ..services.mission_log import emit as log_event

log = logging.getLogger("warclaw.traffic")
router = APIRouter(prefix="/api/traffic", tags=["traffic"])


class CaptureRequest(BaseModel):
    interface: str = ""
    duration: int = 0  # 0 = indefinite
    include_ports: list[int] = Field(default_factory=list)
    exclude_ports: list[int] = Field(default_factory=list)


@router.post("/start")
async def start_capture(req: CaptureRequest):
    """Start capturing LAN traffic."""
    if traffic_monitor.running:
        return {"status": "already_running", **traffic_monitor.stats}

    ok = traffic_monitor.start(
        interface=req.interface,
        duration=req.duration,
        include_ports=req.include_ports,
        exclude_ports=req.exclude_ports,
    )
    if ok:
        log_event("info", "traffic", "Traffic capture started")
        return {"status": "started", **traffic_monitor.stats}
    return {"status": "failed"}


@router.post("/stop")
async def stop_capture():
    """Stop traffic capture."""
    traffic_monitor.stop()
    log_event("info", "traffic", "Traffic capture stopped")
    return {"status": "stopped", **traffic_monitor.stats}


@router.get("/stats")
async def get_stats():
    """Get capture statistics."""
    return traffic_monitor.stats


@router.get("/conversations")
async def get_conversations():
    """Get all tracked conversations — who's talking to who."""
    return {
        "conversations": traffic_monitor.get_conversations(),
        **traffic_monitor.stats,
    }


@router.get("/topology")
async def get_topology():
    """Return a network/service topology graph derived from conversations."""
    return traffic_monitor.get_topology()


@router.get("/recommendations")
async def get_recommendations():
    """Return deterministic operational recommendations derived from traffic."""
    return {
        "recommendations": traffic_monitor.get_recommendations(),
        **traffic_monitor.stats,
    }


@router.get("/snapshot/export")
async def export_snapshot(limit: int = Query(100, ge=10, le=500)):
    """Export the current traffic operating picture as JSON."""
    payload = traffic_monitor.get_snapshot(recent_limit=limit)
    filename = f"warclaw-traffic-snapshot-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/snapshot/brief.md")
async def export_snapshot_brief(limit: int = Query(100, ge=10, le=500)):
    """Export the current traffic operating picture as a markdown watch brief."""
    payload = traffic_monitor.get_snapshot(recent_limit=limit)
    brief = traffic_monitor.render_snapshot_brief(payload)
    filename = f"warclaw-traffic-brief-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.md"
    return PlainTextResponse(
        content=brief,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/snapshots")
async def list_snapshots():
    return {"snapshots": traffic_monitor.list_snapshots()}


@router.post("/snapshots")
async def save_snapshot(limit: int = Query(100, ge=10, le=500)):
    payload = traffic_monitor.save_snapshot(recent_limit=limit)
    log_event("info", "traffic", f"Traffic snapshot saved: {payload['id']}", {"snapshot_id": payload["id"]})
    return payload


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    payload = traffic_monitor.get_saved_snapshot(snapshot_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return payload


@router.get("/snapshots/{snapshot_id}/brief.md")
async def get_snapshot_brief(snapshot_id: str):
    payload = traffic_monitor.get_saved_snapshot(snapshot_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    brief = traffic_monitor.render_snapshot_brief(payload)
    filename = f"{snapshot_id}.md"
    return PlainTextResponse(
        content=brief,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str):
    if not traffic_monitor.delete_snapshot(snapshot_id):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    log_event("info", "traffic", f"Traffic snapshot deleted: {snapshot_id}", {"snapshot_id": snapshot_id})
    return {"status": "deleted", "snapshot_id": snapshot_id}


@router.get("/recent")
async def get_recent(limit: int = Query(100, ge=1, le=500)):
    """Get recent captured frames."""
    return {
        "frames": traffic_monitor.get_recent_frames(limit),
        **traffic_monitor.stats,
    }


@router.post("/clear")
async def clear_data():
    """Clear all captured traffic data."""
    traffic_monitor.clear()
    return {"status": "cleared"}


@router.post("/analyze")
async def analyze_traffic():
    """Send current traffic data to the AI for analysis and recommendations."""
    if not llm_service.ready:
        raise HTTPException(status_code=503, detail="No AI model loaded. Load a GGUF model first.")

    context = traffic_monitor.get_analysis_context()
    if "No traffic" in context:
        return {
            "analysis": "No traffic data available. Start the traffic monitor and wait for some packets to be captured before requesting analysis.",
            "recommendations": [],
        }

    prompt = f"""Analyze this ship LAN traffic capture and provide:
1. A brief summary of what systems are communicating
2. What kind of data is flowing (navigation, engineering, control, web, etc.)
3. Any security observations (unusual patterns, unexpected connections)
4. Recommended WarClaw apps that could be built to make this data useful for the crew

Traffic Data:
{context}

Provide your analysis in clear, non-technical language suitable for a ship's crew."""

    response_parts = []
    async for token in llm_service.astream_chat([], prompt, max_tokens=2048, temperature=0.3):
        response_parts.append(token)
    analysis = "".join(response_parts).strip()

    # Extract recommendations
    convos = traffic_monitor.get_conversations()
    recommendations = []
    for c in convos[:10]:
        if c["protocol"] in ("NMEA", "MODBUS", "JSON/API"):
            recommendations.append({
                "type": "app",
                "title": f"{c['protocol']} Monitor for {c['src'].split(':')[0]}",
                "description": f"Monitor {c['protocol']} traffic from {c['src']} ({c['packet_count']} packets observed)",
                "context": f"Host: {c['src'].split(':')[0]}, Protocol: {c['protocol']}, Sample data available",
            })

    log_event("info", "traffic", f"Traffic analysis complete — {len(convos)} conversations analyzed")

    return {
        "analysis": analysis,
        "recommendations": recommendations,
        "conversation_count": len(convos),
        "packet_count": traffic_monitor.stats["packets_captured"],
    }


@router.websocket("/stream")
async def stream_traffic(ws: WebSocket):
    """WebSocket: stream live traffic frames as they're captured."""
    await ws.accept()
    log.info("Traffic stream WebSocket connected")

    q = traffic_monitor.subscribe()
    try:
        while True:
            try:
                frame = await asyncio.wait_for(q.get(), timeout=5.0)
                await ws.send_json(frame)
            except asyncio.TimeoutError:
                # Send heartbeat
                await ws.send_json({"heartbeat": True, "stats": traffic_monitor.stats})
    except WebSocketDisconnect:
        log.info("Traffic stream WebSocket disconnected")
    finally:
        traffic_monitor.unsubscribe(q)
