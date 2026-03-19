"""Persist and resolve the user's preferred model selection."""
import json
from pathlib import Path

from ..config import MODEL_SELECTION_PATH, MODELS_DIR


def load_selected_model() -> dict:
    if not MODEL_SELECTION_PATH.exists():
        return {}
    try:
        payload = json.loads(MODEL_SELECTION_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "model_path": str(payload.get("model_path") or "").strip(),
        "provider": str(payload.get("provider") or "gguf").strip().lower() or "gguf",
        "n_gpu_layers": int(payload.get("n_gpu_layers") or 0),
    }


def save_selected_model(model_path: str, provider: str = "gguf", n_gpu_layers: int = 0) -> dict:
    payload = {
        "model_path": str(model_path or "").strip(),
        "provider": str(provider or "gguf").strip().lower() or "gguf",
        "n_gpu_layers": int(n_gpu_layers or 0),
    }
    MODEL_SELECTION_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def clear_selected_model() -> None:
    if MODEL_SELECTION_PATH.exists():
        MODEL_SELECTION_PATH.unlink()


def best_available_gguf() -> str:
    gguf_files = sorted(
        MODELS_DIR.glob("**/*.gguf"),
        key=lambda f: f.stat().st_size,
        reverse=True,
    )
    return str(gguf_files[0]) if gguf_files else ""


def resolve_startup_model(default_provider: str, env_model_path: str) -> dict:
    env_provider = (default_provider or "gguf").strip().lower() or "gguf"
    env_path = str(env_model_path or "").strip()
    if env_provider == "ollama" and env_path:
        return {"provider": "ollama", "model_path": env_path, "n_gpu_layers": 0, "source": "env"}
    if env_provider != "ollama" and env_path and Path(env_path).exists():
        return {"provider": "gguf", "model_path": env_path, "n_gpu_layers": 0, "source": "env"}

    saved = load_selected_model()
    saved_provider = saved.get("provider") or "gguf"
    saved_path = saved.get("model_path") or ""
    if saved_provider == "ollama" and saved_path:
        return {"provider": "ollama", "model_path": saved_path, "n_gpu_layers": int(saved.get("n_gpu_layers") or 0), "source": "saved"}
    if saved_provider == "gguf" and saved_path and Path(saved_path).exists():
        return {"provider": "gguf", "model_path": saved_path, "n_gpu_layers": int(saved.get("n_gpu_layers") or 0), "source": "saved"}

    fallback_gguf = best_available_gguf()
    if fallback_gguf:
        return {"provider": "gguf", "model_path": fallback_gguf, "n_gpu_layers": 0, "source": "auto"}

    return {"provider": env_provider, "model_path": "", "n_gpu_layers": 0, "source": "none"}
