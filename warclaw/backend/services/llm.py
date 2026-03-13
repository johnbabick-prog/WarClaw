"""
llama.cpp integration via llama-cpp-python.

The LLMService is a singleton loaded once at startup after hardware detection.
It exposes synchronous and async streaming chat completions.
"""
import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import AsyncIterator, Iterator, Optional

from ..config import MODELS_DIR, DEFAULT_CONTEXT_LENGTH, DEFAULT_THREADS, DEFAULT_GPU_LAYERS

log = logging.getLogger("warclaw.llm")

SYSTEM_PROMPT = """You are WarClaw v2, an AI operating system assistant built by EdgeRunner AI for naval ship LANs.
You run 100% locally — no cloud, no internet connection required.

## Core Capabilities
1. **Ship Systems Integration** — Enumerate and integrate devices on the ship's LAN
2. **Protocol Expertise** — NMEA 0183/2000, IEC 61162-1/450, MODBUS TCP/RTU, serial-to-TCP gateways
3. **Application Generation** — Write production-quality Python (FastAPI) + HTML/CSS/JS apps on demand
4. **Data Interpretation** — Decode and explain sensor streams: GPS fix, heading, depth, speed, wind, ROT, water temp
5. **Tactical Awareness** — Understand IMO/SOLAS requirements, ECDIS integration, bridge system architecture

## NMEA Sentence Knowledge
- GGA: GPS fix with position, altitude, satellites
- RMC: Recommended minimum navigation (pos, SOG, COG, date)
- GLL: Geographic lat/lon with status
- VTG: Track made good, ground speed (true + magnetic)
- HDT: True heading
- DBT: Depth below transducer
- VHW: Water speed and heading through water
- MWV: Wind speed and angle (true or apparent)
- MTW: Water temperature
- ROT: Rate of turn (deg/min)
- ZDA: UTC date and time

## System Architecture on Ship LANs
- ECDIS systems typically on port 20000 or HTTP
- NMEA multiplexers on TCP 10110, 4001, 2000, 3960
- MODBUS sensors on TCP 502 (unit IDs 1-247 for multi-drop)
- Serial-to-TCP gateways (Moxa, Lantronix) commonly on 10001
- IEC 61162-450 uses UDP multicast on 239.192.0.x

## Code Generation Rules
- Always output complete, runnable code — no stubs
- FastAPI routers use `from fastapi import APIRouter`; prefix is `/apps/{slug}`
- Frontend HTML is self-contained: embedded CSS + JS, no CDN
- Naval dark theme: bg #050d15, accent blue #00aaff, green #00ff88, amber #ffaa00
- Include error handling for network timeouts and sensor dropouts
- For NMEA streams: connect via asyncio TCP, parse line-by-line, handle checksum failures gracefully

Be concise and precise. Use naval/maritime terminology. Flag checksum failures and data anomalies.
"""


class LLMService:
    def __init__(self):
        self._llm = None
        self._model_path: Optional[str] = None
        self._ready = False
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def model_path(self) -> Optional[str]:
        return self._model_path

    def load(self, model_path: str, n_ctx: int = DEFAULT_CONTEXT_LENGTH,
             n_threads: int = DEFAULT_THREADS, n_gpu_layers: int = DEFAULT_GPU_LAYERS) -> None:
        """Load a GGUF model. Blocks until loaded."""
        from llama_cpp import Llama  # type: ignore

        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        log.info("Loading model %s (ctx=%d, threads=%d, gpu_layers=%d)",
                 path.name, n_ctx, n_threads, n_gpu_layers)

        self._llm = Llama(
            model_path=str(path),
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_gpu_layers=n_gpu_layers,
            chat_format="chatml",
            verbose=False,
        )
        self._model_path = str(path)
        self._ready = True
        log.info("Model loaded: %s", path.name)

    def list_available_models(self) -> list[dict]:
        """Return GGUF files found in the models directory."""
        models = []
        for f in sorted(MODELS_DIR.glob("*.gguf")):
            models.append({"name": f.name, "path": str(f), "size_mb": round(f.stat().st_size / 1e6, 1)})
        return models

    def _build_messages(self, history: list[dict], user_message: str) -> list[dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in history:
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": user_message})
        return messages

    def stream_chat(self, history: list[dict], user_message: str,
                    max_tokens: int = 2048, temperature: float = 0.7) -> Iterator[str]:
        """Synchronous streaming generator — yields token strings. Thread-safe via lock."""
        if not self._ready or self._llm is None:
            yield "[WarClaw] No model loaded. Please load a GGUF model first."
            return

        messages = self._build_messages(history, user_message)
        with self._lock:
            stream = self._llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            for chunk in stream:
                delta = chunk["choices"][0]["delta"]
                token = delta.get("content", "")
                if token:
                    yield token

    async def astream_chat(self, history: list[dict], user_message: str,
                           max_tokens: int = 2048, temperature: float = 0.7) -> AsyncIterator[str]:
        """Async wrapper — runs sync stream in thread pool to avoid blocking."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        def _run():
            try:
                for token in self.stream_chat(history, user_message, max_tokens, temperature):
                    loop.call_soon_threadsafe(queue.put_nowait, token)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _run)

        while True:
            token = await queue.get()
            if token is None:
                break
            yield token

    def chat_once(self, history: list[dict], user_message: str,
                  max_tokens: int = 2048, temperature: float = 0.7) -> str:
        """Non-streaming, returns full response string."""
        return "".join(self.stream_chat(history, user_message, max_tokens, temperature))


# Module-level singleton
llm_service = LLMService()
