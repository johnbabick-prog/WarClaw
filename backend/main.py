"""
WarClaw — EdgeRunner AI Naval LAN Operating System
FastAPI backend entry point.
"""
import logging
import os
import time
import asyncio
from pathlib import Path

import psutil
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    APP_NAME, APP_SUBTITLE, APP_VERSION,
    FRONTEND_DIR, GENERATED_APPS_DIR, MODELS_DIR,
    HOST, PORT, API_KEY, DEFAULT_MODEL_PROVIDER
)
from .routers import chat, lan, apps, hardware, events, agents, traffic, reports, reminders

_START_TIME = time.time()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("warclaw")

# ── App init ────────────────────────────────────────────────────────────────
app = FastAPI(
    title=APP_NAME,
    description=APP_SUBTITLE,
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # LAN-only — all ship terminals allowed
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Enforce API key on /api/* routes when WARCLAW_API_KEY is set."""
    if API_KEY and request.url.path.startswith("/api/"):
        key = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )
        if key != API_KEY:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
    return await call_next(request)

# ── API routers ──────────────────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(lan.router)
app.include_router(apps.router)
app.include_router(hardware.router)
app.include_router(events.router)
app.include_router(agents.router)
app.include_router(traffic.router)
app.include_router(reports.router)
app.include_router(reminders.router)


# ── Static frontend ──────────────────────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main WarClaw dashboard."""
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return HTMLResponse("<h1>WarClaw starting...</h1>")


@app.get("/api/status")
async def status():
    """System health check with resource metrics and agent status."""
    from .services.llm import llm_service
    from .services.agent_engine import agent_engine, AgentStatus
    from .services.data_bus import data_bus
    from .services.anomaly import anomaly_detector

    vm = psutil.virtual_memory()
    agents_running = sum(1 for a in agent_engine.agents if a.status == AgentStatus.RUNNING)
    total_alerts = sum(a.alerts_fired for a in agent_engine.agents)

    return {
        "system": APP_NAME,
        "version": APP_VERSION,
        "tagline": APP_SUBTITLE,
        "model_ready": llm_service.ready,
        "model_path": llm_service.model_path,
        "model_provider": llm_service.provider,
        "generated_apps": len(list(GENERATED_APPS_DIR.glob("*/manifest.json"))),
        "uptime_s": round(time.time() - _START_TIME),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_gb": round(vm.used / (1024 ** 3), 2),
        "ram_total_gb": round(vm.total / (1024 ** 3), 2),
        "ram_percent": vm.percent,
        "agents_total": len(agent_engine.agents),
        "agents_running": agents_running,
        "agents_alerts": total_alerts,
        "data_bus_channels": data_bus.stats["active_channels"],
        "data_bus_frames": data_bus.stats["total_frames"],
        "anomalies_detected": anomaly_detector.total_anomalies,
    }


# ── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup():
    log.info("=" * 60)
    log.info(" %s v%s", APP_NAME, APP_VERSION)
    log.info(" %s", APP_SUBTITLE)
    log.info("=" * 60)

    # Ensure directories exist
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_APPS_DIR.mkdir(parents=True, exist_ok=True)

    # Auto-load model: env var first, then auto-detect from models/ directory
    from .services.llm import llm_service
    from .services.hardware import detect_hardware
    from .services.model_selection import best_available_gguf, resolve_startup_model
    from .config import DEFAULT_CONTEXT_LENGTH, DEFAULT_THREADS
    hw = detect_hardware()

    selection = resolve_startup_model(DEFAULT_MODEL_PROVIDER, os.getenv("WARCLAW_MODEL", ""))
    model_path = selection["model_path"]
    model_provider = selection["provider"]
    startup_gpu_layers = selection.get("n_gpu_layers", 0)
    if model_path:
        log.info("Startup model selection (%s): %s [%s]", selection.get("source", "unknown"), model_path, model_provider)

    if model_provider == "ollama" and model_path:
        log.info("Selecting Ollama model: %s", model_path)
        try:
            llm_service.load_ollama(model_path)
            log.info("AI model loaded and ready")
        except Exception as e:
            log.error("Model load failed: %s", e)
            fallback_model = best_available_gguf()
            if fallback_model:
                log.info("Falling back to local GGUF model: %s", fallback_model)
                try:
                    llm_service.load(
                        model_path=fallback_model,
                        n_ctx=DEFAULT_CONTEXT_LENGTH,
                        n_threads=DEFAULT_THREADS,
                        n_gpu_layers=hw.recommended_gpu_layers,
                    )
                    log.info("Fallback GGUF model loaded and ready")
                except Exception as fallback_exc:
                    log.error("Fallback GGUF load failed: %s", fallback_exc)
    elif model_path and Path(model_path).exists():
        log.info("Loading model: %s", model_path)
        try:
            llm_service.load(
                model_path=model_path,
                n_ctx=DEFAULT_CONTEXT_LENGTH,
                n_threads=DEFAULT_THREADS,
                n_gpu_layers=startup_gpu_layers or hw.recommended_gpu_layers,
            )
            log.info("AI model loaded and ready")
        except Exception as e:
            log.error("Model load failed: %s", e)
    else:
        log.info("No model found. Place .gguf files in: %s or run a local Ollama daemon.", MODELS_DIR)

    log.info("WarClaw ready at http://%s:%d", HOST, PORT)

    from .services.mission_log import emit
    from .services.reminders import reminder_loop
    asyncio.create_task(reminder_loop())
    emit("success", "system", f"{APP_NAME} v{APP_VERSION} started", {"host": HOST, "port": PORT})
