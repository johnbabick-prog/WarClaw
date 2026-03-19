"""
Chat endpoints — REST + WebSocket streaming AI conversations.
"""
import datetime as dt
import json
import logging
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from ..services.llm import llm_service
from ..services.assistant_actions import try_handle_action
from ..services.chat_sessions import (
    append_message,
    create_session,
    delete_session,
    get_session,
    list_sessions,
)
from ..services.mission_log import emit as log_event
from ..config import APP_NAME, APP_VERSION

log = logging.getLogger("warclaw.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


def _clean_history(history: list[dict], limit: int = 8) -> list[dict]:
    cleaned = []
    for item in history[-limit:]:
      content = (item.get("content") or "").strip()
      role = item.get("role", "")
      if not content or role not in {"user", "assistant"}:
          continue
      if "<|im_start|>" in content or "<|im_end|>" in content:
          continue
      cleaned.append({"role": role, "content": content})
    return cleaned


def _history_for_model(history: list[dict], limit: int = 8) -> list[dict]:
    """Keep only complete alternating turns so a stale orphan user prompt is not resent."""
    cleaned = _clean_history(history, limit=limit * 2)
    if not cleaned:
        return []

    normalized: list[dict] = []
    expected_role = "user"
    for item in cleaned:
        role = item["role"]
        if role != expected_role:
            continue
        normalized.append(item)
        expected_role = "assistant" if expected_role == "user" else "user"

    if normalized and normalized[-1]["role"] == "user":
        normalized.pop()

    return normalized[-limit:]


def _local_chat_answer(message: str) -> str | None:
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    now = dt.datetime.now().astimezone()

    if any(phrase in text for phrase in ("what date is it", "what is the date", "what's the date", "todays date", "today's date")):
        return now.strftime("Today is %A, %B %d, %Y.")

    if any(phrase in text for phrase in ("what time is it", "current time", "what's the time")):
        return now.strftime("The current local time is %I:%M:%S %p %Z on %A, %B %d, %Y.")

    if any(phrase in text for phrase in ("what model are you", "which model are you using", "what model is loaded")):
        model_name = llm_service.model_path or "no model loaded"
        if llm_service.provider == "gguf":
            model_name = model_name.split("/")[-1]
        return f"I am {APP_NAME} v{APP_VERSION} running locally. The current {llm_service.provider} model is {model_name}."

    if any(phrase in text for phrase in ("who are you", "what are you")):
        model_name = llm_service.model_path or "no model loaded"
        if llm_service.provider == "gguf":
            model_name = model_name.split("/")[-1]
        return f"I am {APP_NAME} v{APP_VERSION}, the local ship operations assistant. Current {llm_service.provider} model: {model_name}."

    if "first instruction" in text or "system prompt" in text:
        return "I am configured as WarClaw, a local ship-operations assistant for LAN discovery, monitoring, reporting, and operational app generation."

    if any(phrase in text for phrase in ("mobile phone", "my phone", "text this", "send this to my phone", "forward this")):
        return (
            "WarClaw cannot send items to personal devices from this console. "
            "I can generate a watch brief, checklist, or markdown summary you can copy or export locally."
        )

    if text in ("hello", "hi", "hey", "good morning", "good afternoon", "good evening"):
        model_status = "AI model online" if llm_service.ready else "no model loaded yet"
        return f"WarClaw standing by ({model_status}). How can I help? Ask me to scan the LAN, generate an app, write a report, or ask a question about ship systems."

    return None


def _capabilities_response(message: str) -> str | None:
    """Handle common 'what can you do' / 'help' requests with a useful capabilities summary."""
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    if any(phrase in text for phrase in (
        "what can you do", "what do you do", "help me", "how do i use",
        "what are your capabilities", "what are you capable of", "show me what you can do",
    )):
        return (
            "Here is what I can do:\n\n"
            "**Chat & Analysis** — Ask me anything about ship systems, protocols, or operations and I will explain or advise.\n\n"
            "**LAN Scanning** — Say *\"scan the LAN\"* and I will discover hosts, services, and protocols on the network.\n\n"
            "**App Generation** — Say *\"create a navigation dashboard\"* or *\"build a sensor monitor\"* and I will generate a working app.\n\n"
            "**Reports** — Say *\"generate a daily OPORD summary\"* or *\"write a watch brief\"* and I will compile one from system data.\n\n"
            "**Task Lists** — Say *\"create a todo list: check GPS, brief engineering, review logs\"* and I will build a tracked checklist.\n\n"
            "**Reminders** — Say *\"remind me every day at 0600 to review GPS integrity\"* and I will set a recurring reminder.\n\n"
            "**NMEA/MODBUS** — I can decode protocol sentences, explain sensor data, and troubleshoot integration issues.\n\n"
            "Try asking a question or giving me a command to get started."
        )
    return None


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = []
    message: str
    max_tokens: int = 2048
    temperature: float = 0.7


class ChatSessionRequest(BaseModel):
    title: str = ""


@router.post("/")
async def chat(req: ChatRequest):
    """Non-streaming chat — returns full response. Use WebSocket for streaming."""
    local = _local_chat_answer(req.message)
    if local is not None:
        return {"response": local, "model": llm_service.model_path, "local": True}

    caps = _capabilities_response(req.message)
    if caps is not None:
        return {"response": caps, "model": llm_service.model_path, "local": True}

    action = await try_handle_action(req.message)
    if action is not None:
        return {"response": action, "model": llm_service.model_path, "action": True}

    if not llm_service.ready:
        raise HTTPException(status_code=503, detail="No model loaded. Load a GGUF model or select an Ollama model first via /api/hardware/models/load")

    history = _history_for_model([{"role": m.role, "content": m.content} for m in req.messages])
    response = llm_service.chat_once(history, req.message, req.max_tokens, req.temperature)
    return {"response": response, "model": llm_service.model_path}


@router.get("/sessions")
def get_sessions():
    return {"sessions": list_sessions()}


@router.post("/sessions")
def new_session(req: ChatSessionRequest):
    session = create_session(req.title)
    return session


@router.get("/sessions/{session_id}")
def read_session(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str):
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "id": session_id}


@router.websocket("/ws")
async def chat_ws(ws: WebSocket):
    """
    WebSocket streaming chat.

    Client sends JSON: {"message": "...", "history": [...], "max_tokens": 2048}
    Server streams JSON: {"token": "..."} repeatedly, then {"done": true}
    """
    await ws.accept()
    log.info("Chat WebSocket connected")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "Invalid JSON"})
                continue

            message = data.get("message", "")
            history = data.get("history", [])
            max_tokens = int(data.get("max_tokens", 2048))
            temperature = float(data.get("temperature", 0.7))
            session_id = data.get("session_id", "")

            if not message:
                await ws.send_json({"error": "Empty message"})
                continue

            # Check for local/canned answers first
            quick = _local_chat_answer(message) or _capabilities_response(message)
            if quick is not None:
                if session_id:
                    session = get_session(session_id)
                    if session is None:
                        await ws.send_json({"error": "Unknown session"})
                        continue
                    append_message(session_id, "user", message)
                    append_message(session_id, "assistant", quick)
                await ws.send_json({"token": quick})
                await ws.send_json({"done": True})
                continue

            action = await try_handle_action(message)
            if action is not None:
                if session_id:
                    session = get_session(session_id)
                    if session is None:
                        await ws.send_json({"error": "Unknown session"})
                        continue
                    append_message(session_id, "user", message)
                    append_message(session_id, "assistant", action)
                await ws.send_json({"token": action})
                await ws.send_json({"done": True})
                continue

            if not llm_service.ready:
                await ws.send_json({"error": "No model loaded. Load a GGUF model or select an Ollama model first."})
                continue

            if session_id:
                session = get_session(session_id)
                if session is None:
                    await ws.send_json({"error": "Unknown session"})
                    continue
                append_message(session_id, "user", message)
                history = _history_for_model([
                    {"role": item["role"], "content": item["content"]}
                    for item in session.get("messages", [])[:-1]
                ])
            else:
                history = _history_for_model(history)

            try:
                response_parts = []
                async for token in llm_service.astream_chat(history, message, max_tokens, temperature):
                    response_parts.append(token)
                    await ws.send_json({"token": token})
                if session_id:
                    append_message(session_id, "assistant", llm_service.sanitize_response("".join(response_parts).strip()))
                log_event("info", "ai", "AI chat response completed", {"session_id": session_id or None})
                await ws.send_json({"done": True})
            except Exception as e:
                log.exception("LLM streaming error")
                await ws.send_json({"error": str(e), "done": True})

    except WebSocketDisconnect:
        log.info("Chat WebSocket disconnected")
