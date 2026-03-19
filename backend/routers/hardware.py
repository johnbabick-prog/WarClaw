"""Hardware detection and model management endpoints."""
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from ..services.hardware import detect_hardware
from ..services.llm import llm_service
from ..services.model_selection import load_selected_model, save_selected_model
from ..config import MODELS_DIR, DEFAULT_CONTEXT_LENGTH, DEFAULT_THREADS

router = APIRouter(prefix="/api/hardware", tags=["hardware"])


@router.get("/profile")
def get_hardware_profile():
    """Return detected hardware profile and model recommendation."""
    profile = detect_hardware()
    return {
        "cpu_cores": profile.cpu_cores,
        "cpu_model": profile.cpu_model,
        "ram_gb": profile.ram_gb,
        "gpu_name": profile.gpu_name,
        "gpu_vram_gb": profile.gpu_vram_gb,
        "has_cuda": profile.has_cuda,
        "recommended_tier": profile.recommended_tier,
        "recommended_gpu_layers": profile.recommended_gpu_layers,
        "recommended_model": profile.recommended_model_hint,
        "notes": profile.notes,
    }


@router.get("/models")
def list_models():
    """List local GGUF files and Ollama models available to WarClaw."""
    gguf_models = llm_service.list_available_models()
    ollama_models = llm_service.list_ollama_models()
    selected = load_selected_model()
    return {
        "models": gguf_models,
        "ollama_models": ollama_models,
        "models_dir": str(MODELS_DIR),
        "current_model": llm_service.model_path,
        "current_provider": llm_service.provider,
        "model_ready": llm_service.ready,
        "saved_model": selected.get("model_path"),
        "saved_provider": selected.get("provider"),
    }


class LoadModelRequest(BaseModel):
    model_path: str = ""
    provider: str = "gguf"
    n_ctx: int = DEFAULT_CONTEXT_LENGTH
    n_threads: int = DEFAULT_THREADS
    n_gpu_layers: int = 0

    model_config = {
        "protected_namespaces": (),
    }


@router.post("/models/load")
def load_model(req: LoadModelRequest):
    """Load a GGUF model into memory or select an Ollama model."""
    try:
        provider = (req.provider or "gguf").lower()
        if provider == "ollama":
            llm_service.load_ollama(req.model_path)
        else:
            llm_service.load(
                model_path=req.model_path,
                n_ctx=req.n_ctx,
                n_threads=req.n_threads,
                n_gpu_layers=req.n_gpu_layers,
            )
        save_selected_model(req.model_path, provider=provider, n_gpu_layers=req.n_gpu_layers)
        return {"status": "loaded", "model": req.model_path, "provider": provider}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/models/upload")
async def upload_model(file: UploadFile = File(...)):
    """Upload a GGUF file into the local models directory."""
    name = Path(file.filename or "").name
    if not name.lower().endswith(".gguf"):
        raise HTTPException(status_code=400, detail="Only .gguf files are supported")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODELS_DIR / name
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")
    finally:
        await file.close()

    return {
        "status": "uploaded",
        "name": name,
        "path": str(dest),
    }
