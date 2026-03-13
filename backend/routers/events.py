"""
Mission Events endpoints — retrieve log history and subscribe to live SSE stream.
"""
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ..services.mission_log import recent, subscribe

log = logging.getLogger("warclaw.events")
router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/")
def get_events(limit: int = Query(default=200, ge=1, le=1000)):
    """Return recent mission log entries."""
    return {"events": recent(limit)}


@router.get("/stream")
async def stream_events():
    """
    Server-Sent Events stream of real-time mission log entries.

    Connect with: EventSource('/api/events/stream')
    Each event is a JSON-encoded mission log entry.
    """
    async def generator():
        # Send buffered recent events first so client catches up
        for event in recent(50):
            yield f"data: {json.dumps(event)}\n\n"
        # Then stream live events
        async for event in subscribe():
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
