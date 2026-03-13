#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# WarClaw — Model Downloader
# Downloads a recommended GGUF model based on detected hardware.
# Run this script on a machine with internet BEFORE deploying to ship.
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="$(dirname "$SCRIPT_DIR")/models"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*" >&2; }

echo -e "${CYAN}WarClaw Model Downloader${NC}"
echo ""

# Models available from HuggingFace (GGUF quantized)
declare -A MODELS
MODELS[phi3-mini]="https://huggingface.co/microsoft/Phi-3-mini-4k-instruct-gguf/resolve/main/Phi-3-mini-4k-instruct-q4.gguf"
MODELS[mistral-7b]="https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF/resolve/main/mistral-7b-instruct-v0.2.Q5_K_M.gguf"
MODELS[llama3-8b]="https://huggingface.co/QuantFactory/Meta-Llama-3-8B-Instruct-GGUF/resolve/main/Meta-Llama-3-8B-Instruct.Q5_K_M.gguf"

echo "Available models:"
echo "  1) phi3-mini   ~2.2GB  — Fast CPU, good for 8-16GB RAM servers"
echo "  2) mistral-7b  ~5.1GB  — Best balance, requires 16GB+ RAM"
echo "  3) llama3-8b   ~5.7GB  — Strong reasoning, requires 16GB+ RAM"
echo ""

read -rp "Select model (1-3): " CHOICE

case "$CHOICE" in
  1) KEY="phi3-mini" ;;
  2) KEY="mistral-7b" ;;
  3) KEY="llama3-8b" ;;
  *) err "Invalid selection"; exit 1 ;;
esac

URL="${MODELS[$KEY]}"
FILENAME="$(basename "$URL")"
DEST="$MODELS_DIR/$FILENAME"

mkdir -p "$MODELS_DIR"

if [ -f "$DEST" ]; then
  warn "Model already exists: $DEST"
  read -rp "Re-download? (y/N): " REDOWNLOAD
  [[ "$REDOWNLOAD" =~ ^[Yy]$ ]] || exit 0
fi

info "Downloading $KEY..."
info "URL: $URL"
info "Destination: $DEST"
echo ""

if command -v wget &>/dev/null; then
  wget --progress=bar:force -O "$DEST" "$URL"
elif command -v curl &>/dev/null; then
  curl -L --progress-bar -o "$DEST" "$URL"
else
  err "Neither wget nor curl found. Install one and retry."
  exit 1
fi

info "Downloaded: $DEST"
echo ""
echo "To use this model, set:"
echo "  export WARCLAW_MODEL=$DEST"
echo "Or load it via the Hardware tab in the WarClaw UI."
