#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# WarClaw — EdgeRunner AI Naval LAN OS
# Setup Script — installs dependencies (fully offline-capable after
# initial package download)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WARCLAW_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$WARCLAW_DIR/.venv"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

banner() {
  echo -e "${CYAN}"
  echo "  ██╗    ██╗ █████╗ ██████╗  ██████╗██╗      █████╗ ██╗    ██╗"
  echo "  ██║    ██║██╔══██╗██╔══██╗██╔════╝██║     ██╔══██╗██║    ██║"
  echo "  ██║ █╗ ██║███████║██████╔╝██║     ██║     ███████║██║ █╗ ██║"
  echo "  ██║███╗██║██╔══██║██╔══██╗██║     ██║     ██╔══██║██║███╗██║"
  echo "  ╚███╔███╔╝██║  ██║██║  ██║╚██████╗███████╗██║  ██║╚███╔███╔╝"
  echo "   ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝"
  echo -e "${BLUE}  EdgeRunner AI — Naval LAN Operating System v2.1.0${NC}"
  echo ""
}

info()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
err()     { echo -e "${RED}[✗]${NC} $*" >&2; }
section() { echo -e "\n${BLUE}══ $* ══${NC}"; }

banner

# ── Check Python ────────────────────────────────────────────────
section "Python Environment"
if ! command -v python3 &>/dev/null; then
  err "python3 not found. Install Python 3.10+ first."
  exit 1
fi

PY_VERSION=$(python3 --version | awk '{print $2}')
info "Python $PY_VERSION found"

MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || [ "$MINOR" -lt 10 ]; then
  err "Python 3.10+ required (found $PY_VERSION)"
  exit 1
fi

# pydantic-core/llama-cpp-python use PyO3, which currently supports up to 3.13.
# On 3.14+ we set the ABI3 forward-compat flag so they compile without error.
if [ "$MINOR" -ge 14 ]; then
  warn "Python $PY_VERSION detected — PyO3-based packages need ABI3 compatibility mode"
  warn "Setting PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 for this install"
  export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
fi

# ── Virtual environment ──────────────────────────────────────────
section "Virtual Environment"
if [ ! -d "$VENV_DIR" ]; then
  info "Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
fi
info "Virtual environment: $VENV_DIR"

source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q

# ── Detect GPU for llama-cpp-python build ────────────────────────
section "GPU Detection"
CMAKE_ARGS=""
FORCE_CMAKE=0

if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
  GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
  info "NVIDIA GPU detected: $GPU"
  info "Building llama-cpp-python with CUDA support..."
  export CMAKE_ARGS="-DGGML_CUDA=on"
  export FORCE_CMAKE=1
elif [ "$(uname)" == "Darwin" ]; then
  info "macOS detected — building with Metal support"
  export CMAKE_ARGS="-DGGML_METAL=on"
  export FORCE_CMAKE=1
else
  warn "No GPU detected — building llama-cpp-python for CPU only"
  warn "CPU inference will be slower. A GPU is recommended for best performance."
fi

# ── Install Python dependencies ──────────────────────────────────
section "Installing Python Packages"
REQUIREMENTS="$WARCLAW_DIR/backend/requirements.txt"

# Install llama-cpp-python separately (may need cmake build)
info "Installing llama-cpp-python (may take 5-10 minutes on first install)..."
if [ "$FORCE_CMAKE" = "1" ]; then
  CMAKE_ARGS="$CMAKE_ARGS" pip install llama-cpp-python --force-reinstall --no-binary llama-cpp-python -q
else
  pip install llama-cpp-python -q
fi

# Install remaining dependencies
info "Installing remaining dependencies..."
pip install -r "$REQUIREMENTS" -q

info "All Python packages installed"

# ── System dependencies check ────────────────────────────────────
section "System Dependencies"
MISSING=()

check_cmd() {
  if command -v "$1" &>/dev/null; then
    info "$1 found"
  else
    warn "$1 not found — $2"
    MISSING+=("$1")
  fi
}

check_cmd "nmap" "optional, improves host OS detection (apt install nmap)"
check_cmd "tcpdump" "optional, needed for raw packet capture (apt install tcpdump)"

if [ ${#MISSING[@]} -gt 0 ]; then
  warn "Optional tools missing: ${MISSING[*]}"
  warn "WarClaw will run without them but with reduced capability."
  warn "Install with: sudo apt install ${MISSING[*]}"
fi

# ── Create directories ───────────────────────────────────────────
section "Directories"
mkdir -p "$WARCLAW_DIR/models"
mkdir -p "$WARCLAW_DIR/generated_apps"
info "models/       — place .gguf model files here"
info "generated_apps/ — AI-created apps stored here"

# ── Model guidance ───────────────────────────────────────────────
section "Model Setup"
MODELS_DIR="$WARCLAW_DIR/models"
GGUF_COUNT=$(find "$MODELS_DIR" -name "*.gguf" 2>/dev/null | wc -l)

if [ "$GGUF_COUNT" -eq 0 ]; then
  warn "No .gguf model files found in $MODELS_DIR"
  echo ""
  echo "  Download a model and place it in $MODELS_DIR"
  echo "  Recommended models by hardware tier:"
  echo ""
  echo "  CPU only (≥8GB RAM):"
  echo "    phi-3-mini-4k-instruct.Q4_K_M.gguf  (~2.2GB)"
  echo ""
  echo "  CPU (≥32GB RAM) or mid GPU:"
  echo "    mistral-7b-instruct-v0.3.Q5_K_M.gguf  (~5GB)"
  echo ""
  echo "  High-end GPU (≥20GB VRAM):"
  echo "    Meta-Llama-3-70B-Instruct.Q4_K_M.gguf  (~40GB)"
  echo ""
  echo "  Models can be downloaded from HuggingFace:"
  echo "    https://huggingface.co/models?search=gguf"
  echo ""
  echo "  After downloading, load via the Hardware tab in the WarClaw UI,"
  echo "  or set WARCLAW_MODEL=/path/to/model.gguf before starting."
else
  info "Found $GGUF_COUNT model file(s) in $MODELS_DIR"
fi

# ── Done ─────────────────────────────────────────────────────────
section "Setup Complete"
info "WarClaw is ready to start"
echo ""
echo "  Start with:   ./scripts/start.sh"
echo "  Or manually:  source .venv/bin/activate && python -m uvicorn backend.main:app --host 0.0.0.0 --port 7070"
echo ""
echo "  Access WarClaw at:  http://$(hostname -I | awk '{print $1}' 2>/dev/null || echo 'SERVER_IP'):7070"
echo ""
