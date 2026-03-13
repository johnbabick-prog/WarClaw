"""
Chat endpoints — REST + WebSocket streaming AI conversations.
"""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from ..services.llm import llm_service

log = logging.getLogger("warclaw.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = []
    message: str
    max_tokens: int = 2048
    temperature: float = 0.7


@router.post("/")
async def chat(req: ChatRequest):
    """Non-streaming chat — returns full response. Use WebSocket for streaming."""
    if not llm_service.ready:
        raise HTTPException(status_code=503, detail="No model loaded. Load a GGUF model first via /api/hardware/models/load")

    history = [{"role": m.role, "content": m.content} for m in req.messages]
    response = llm_service.chat_once(history, req.message, req.max_tokens, req.temperature)
    return {"response": response, "model": llm_service.model_path}


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

            if not llm_service.ready:
                await ws.send_json({"error": "No model loaded. Load a GGUF model first."})
                continue

            message = data.get("message", "")
            history = data.get("history", [])
            max_tokens = int(data.get("max_tokens", 2048))
            temperature = float(data.get("temperature", 0.7))

            if not message:
                await ws.send_json({"error": "Empty message"})
                continue

            try:
                async for token in llm_service.astream_chat(history, message, max_tokens, temperature):
                    await ws.send_json({"token": token})
                await ws.send_json({"done": True})
            except Exception as e:
                log.exception("LLM streaming error")
                await ws.send_json({"error": str(e), "done": True})

    except WebSocketDisconnect:
        log.info("Chat WebSocket disconnected")
