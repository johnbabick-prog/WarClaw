"""
Mission Log Service

Persists ship events to a JSONL file and broadcasts them to SSE subscribers.
Events include: LAN discoveries, protocol detections, AI interactions, system alerts.
"""
import asyncio
import json
import logging
import time
from typing import AsyncIterator

from ..config import MISSION_LOG_PATH

log = logging.getLogger("warclaw.mission_log")

# In-memory subscriber queues for SSE
_subscribers: list[asyncio.Queue] = []


def _utc_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(level: str, category: str, message: str, data: dict | None = None) -> dict:
    """
    Emit a mission event. Persists to disk and notifies SSE subscribers.

    level: "info" | "warn" | "alert" | "success"
    category: "lan" | "ai" | "system" | "protocol" | "app"
    """
    event = {
        "ts": round(time.time(), 3),
        "utc": _utc_iso(),
        "level": level,
        "category": category,
        "message": message,
        "data": data or {},
    }

    # Persist to JSONL
    try:
        with open(MISSION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        log.warning("Mission log write failed: %s", e)

    # Broadcast to all SSE subscribers
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.remove(q)

    return event


def recent(limit: int = 200) -> list[dict]:
    """Return the most recent N mission events from the JSONL log."""
    if not MISSION_LOG_PATH.exists():
        return []
    events = []
    try:
        with open(MISSION_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        log.warning("Mission log read failed: %s", e)
    return events[-limit:]


async def subscribe() -> AsyncIterator[dict]:
    """Async generator — yields mission events as they are emitted (SSE)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    _subscribers.append(q)
    try:
        while True:
            event = await q.get()
            yield event
    finally:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass
