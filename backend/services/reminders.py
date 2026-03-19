"""Persistent reminder storage and due reminder processing."""
import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from ..config import REMINDERS_PATH
from .mission_log import emit

_LOCK = threading.Lock()


def _load() -> dict:
    path = Path(REMINDERS_PATH)
    if not path.exists():
        return {"reminders": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"reminders": []}


def _save(payload: dict) -> None:
    Path(REMINDERS_PATH).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _next_daily_due(hour: int, minute: int, now: datetime | None = None) -> float:
    now = now or datetime.now().astimezone()
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return due.timestamp()


def _next_weekly_due(weekday: int, hour: int, minute: int, now: datetime | None = None) -> float:
    now = now or datetime.now().astimezone()
    days_ahead = (weekday - now.weekday()) % 7
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
    if due <= now:
        due += timedelta(days=7)
    return due.timestamp()


def _reschedule(reminder: dict) -> float | None:
    cadence = reminder.get("cadence")
    schedule = reminder.get("schedule") or {}
    if cadence == "daily":
        return _next_daily_due(int(schedule.get("hour", 8)), int(schedule.get("minute", 0)))
    if cadence == "weekly":
        return _next_weekly_due(int(schedule.get("weekday", 0)), int(schedule.get("hour", 8)), int(schedule.get("minute", 0)))
    if cadence == "hourly":
        return time.time() + max(3600, int(schedule.get("interval_s", 3600)))
    return None


def list_reminders() -> list[dict]:
    with _LOCK:
        payload = _load()
    reminders = payload.get("reminders", [])
    reminders.sort(key=lambda item: item.get("next_due_ts", 0))
    return reminders


def create_reminder(title: str, message: str, cadence: str, schedule: dict, source: str = "assistant") -> dict:
    reminder = {
        "id": uuid.uuid4().hex,
        "title": title.strip() or "Reminder",
        "message": message.strip() or title.strip() or "Reminder",
        "cadence": cadence,
        "schedule": schedule,
        "source": source,
        "enabled": True,
        "created_at": time.time(),
        "updated_at": time.time(),
        "last_triggered_at": None,
        "next_due_ts": 0.0,
    }
    reminder["next_due_ts"] = _reschedule(reminder) or time.time()
    with _LOCK:
        payload = _load()
        payload.setdefault("reminders", []).append(reminder)
        _save(payload)
    return reminder


def delete_reminder(reminder_id: str) -> bool:
    with _LOCK:
        payload = _load()
        reminders = payload.get("reminders", [])
        kept = [item for item in reminders if item.get("id") != reminder_id]
        if len(kept) == len(reminders):
            return False
        payload["reminders"] = kept
        _save(payload)
    return True


def process_due_reminders(now_ts: float | None = None) -> list[dict]:
    now_ts = now_ts or time.time()
    triggered = []
    with _LOCK:
        payload = _load()
        changed = False
        for reminder in payload.get("reminders", []):
            if not reminder.get("enabled", True):
                continue
            if (reminder.get("next_due_ts") or 0) > now_ts:
                continue
            emit("info", "system", f"Reminder due: {reminder['title']}", {"reminder_id": reminder["id"], "message": reminder["message"]})
            reminder["last_triggered_at"] = now_ts
            reminder["next_due_ts"] = _reschedule(reminder) or now_ts
            reminder["updated_at"] = now_ts
            triggered.append(reminder.copy())
            changed = True
        if changed:
            _save(payload)
    return triggered


async def reminder_loop(poll_interval_s: float = 30.0):
    while True:
        process_due_reminders()
        await asyncio.sleep(poll_interval_s)
