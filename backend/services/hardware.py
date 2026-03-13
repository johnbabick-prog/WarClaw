"""Detect ship server hardware and recommend appropriate llama.cpp model tier."""
import subprocess
import shutil
import psutil
import platform
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HardwareProfile:
    cpu_cores: int
    cpu_model: str
    ram_gb: float
    gpu_name: Optional[str]
    gpu_vram_gb: Optional[float]
    has_cuda: bool
    has_metal: bool
    recommended_tier: str          # "small" | "medium" | "large"
    recommended_gpu_layers: int
    recommended_model_hint: str
    notes: list[str] = field(default_factory=list)


def _detect_gpu_nvidia() -> tuple[Optional[str], Optional[float]]:
    if not shutil.which("nvidia-smi"):
        return None, None
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            timeout=5, stderr=subprocess.DEVNULL
        ).decode().strip()
        if not out:
            return None, None
        parts = out.split(",")
        name = parts[0].strip()
        vram_mb = float(parts[1].strip())
        return name, round(vram_mb / 1024, 1)
    except Exception:
        return None, None


def detect_hardware() -> HardwareProfile:
    ram_bytes = psutil.virtual_memory().total
    ram_gb = round(ram_bytes / (1024 ** 3), 1)
    cpu_count = psutil.cpu_count(logical=False) or psutil.cpu_count() or 1

    # CPU model
    cpu_model = platform.processor() or "Unknown CPU"
    try:
        if platform.system() == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        cpu_model = line.split(":")[1].strip()
                        break
    except Exception:
        pass

    gpu_name, gpu_vram = _detect_gpu_nvidia()
    has_cuda = gpu_name is not None
    has_metal = platform.system() == "Darwin"

    notes: list[str] = []
    recommended_gpu_layers = 0

    # ── Tier selection ──────────────────────────────────────────────────────
    if has_cuda and gpu_vram and gpu_vram >= 20:
        tier = "large"
        recommended_gpu_layers = 80
        hint = "llama-3-70b-instruct.Q4_K_M.gguf"
        notes.append(f"GPU {gpu_name} detected with {gpu_vram}GB VRAM — large model recommended")
    elif has_cuda and gpu_vram and gpu_vram >= 8:
        tier = "medium"
        recommended_gpu_layers = 40
        hint = "mistral-7b-instruct-v0.3.Q5_K_M.gguf"
        notes.append(f"GPU {gpu_name} detected with {gpu_vram}GB VRAM — medium model recommended")
    elif ram_gb >= 48:
        tier = "medium"
        hint = "mistral-7b-instruct-v0.3.Q5_K_M.gguf"
        notes.append(f"{ram_gb}GB RAM available — medium model on CPU")
    elif ram_gb >= 16:
        tier = "small"
        hint = "phi-3-mini-4k-instruct.Q4_K_M.gguf"
        notes.append(f"{ram_gb}GB RAM — small/fast model recommended")
    else:
        tier = "small"
        hint = "phi-3-mini-4k-instruct.Q4_K_M.gguf"
        notes.append(f"Only {ram_gb}GB RAM detected — small model only")

    if not has_cuda and not has_metal:
        notes.append("No GPU detected — CPU-only inference (slower response times)")

    return HardwareProfile(
        cpu_cores=cpu_count,
        cpu_model=cpu_model,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram,
        has_cuda=has_cuda,
        has_metal=has_metal,
        recommended_tier=tier,
        recommended_gpu_layers=recommended_gpu_layers,
        recommended_model_hint=hint,
        notes=notes,
    )
