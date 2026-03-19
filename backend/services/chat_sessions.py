"""Persistent chat sessions for the AI assistant."""
import json
import threading
import time
import uuid
from pathlib import Path

from ..config import CHAT_SESSIONS_PATH

_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _load() -> dict:
    path = Path(CHAT_SESSIONS_PATH)
    if not path.exists():
        return {"sessions": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": []}


def _save(payload: dict) -> None:
    path = Path(CHAT_SESSIONS_PATH)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _title_from_message(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return "New Session"
    if len(cleaned) <= 48:
        return cleaned
    return cleaned[:45].rstrip() + "..."


def list_sessions() -> list[dict]:
    with _LOCK:
        payload = _load()
    sessions = payload.get("sessions", [])
    sessions.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
    return [
        {
            "id": item["id"],
            "title": item.get("title", "New Session"),
            "created_at": item.get("created_at", 0),
            "updated_at": item.get("updated_at", 0),
            "message_count": len(item.get("messages", [])),
        }
        for item in sessions
    ]


def get_session(session_id: str) -> dict | None:
    with _LOCK:
        payload = _load()
    for item in payload.get("sessions", []):
        if item["id"] == session_id:
            return item
    return None


def create_session(title: str = "") -> dict:
    session = {
        "id": uuid.uuid4().hex,
        "title": title.strip() or "New Session",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    }
    with _LOCK:
        payload = _load()
        payload.setdefault("sessions", []).append(session)
        _save(payload)
    return session


def delete_session(session_id: str) -> bool:
    with _LOCK:
        payload = _load()
        sessions = payload.get("sessions", [])
        kept = [item for item in sessions if item["id"] != session_id]
        if len(kept) == len(sessions):
            return False
        payload["sessions"] = kept
        _save(payload)
        return True


def append_message(session_id: str, role: str, content: str) -> dict:
    with _LOCK:
        payload = _load()
        for item in payload.get("sessions", []):
            if item["id"] != session_id:
                continue
            item.setdefault("messages", []).append({
                "role": role,
                "content": content,
                "ts": _now(),
            })
            if role == "user" and (item.get("title") in ("", "New Session")):
                item["title"] = _title_from_message(content)
            item["updated_at"] = _now()
            _save(payload)
            return item
    raise KeyError(session_id)

