#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# WarClaw — Start Script
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WARCLAW_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$WARCLAW_DIR/.venv"
if [ ! -f "$VENV_DIR/bin/python" ] && [ -f "$WARCLAW_DIR/venv/bin/python" ]; then
  VENV_DIR="$WARCLAW_DIR/venv"
fi

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'

# ── Check virtual env ────────────────────────────────────────────
if [ ! -f "$VENV_DIR/bin/activate" ]; then
  echo -e "${RED}[✗]${NC} Virtual environment not found. Run ./scripts/setup.sh first."
  exit 1
fi

PYTHON_BIN="$VENV_DIR/bin/python"

WS_BACKEND=$("$PYTHON_BIN" - <<'PY'
import importlib.util
if importlib.util.find_spec("websockets"):
    print("websockets")
elif importlib.util.find_spec("wsproto"):
    print("wsproto")
else:
    print("")
PY
)

if [ -z "$WS_BACKEND" ]; then
  echo -e "${RED}[✗]${NC} No WebSocket backend installed in $VENV_DIR."
  echo -e "    Run ${CYAN}./scripts/setup.sh${NC} or install ${CYAN}websockets${NC} into that environment."
  exit 1
fi

# ── Load .env if present (allows simple config without exporting vars) ──
if [ -f "$WARCLAW_DIR/.env" ]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$WARCLAW_DIR/.env"
  set +o allexport
fi

# ── Configuration from env or defaults ──────────────────────────
HOST="${WARCLAW_HOST:-0.0.0.0}"
PORT="${WARCLAW_PORT:-7070}"
WORKERS="${WARCLAW_WORKERS:-1}"
LOG_LEVEL="${WARCLAW_LOG:-info}"
MODEL="${WARCLAW_MODEL:-}"

# ── Detect LAN IP for display (works on macOS + Linux) ───────────
if command -v hostname &>/dev/null && hostname -I &>/dev/null; then
  LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
elif command -v ipconfig &>/dev/null; then
  # macOS
  LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "localhost")
else
  LAN_IP="localhost"
fi
[ -z "$LAN_IP" ] && LAN_IP="localhost"

# ── Banner ───────────────────────────────────────────────────────
echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║        WARCLAW v2 — EdgeRunner AI Naval LAN OS       ║"
echo "  ║              100% LOCAL · OFFLINE CAPABLE            ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${GREEN}Dashboard:${NC}   http://${LAN_IP}:${PORT}"
echo -e "  ${GREEN}API Docs:${NC}    http://${LAN_IP}:${PORT}/api/docs"
echo -e "  ${GREEN}Mission Log:${NC} http://${LAN_IP}:${PORT}/api/events/"
echo -e "  ${BLUE}Interface:${NC}   ${HOST}:${PORT}"
if [ -n "$MODEL" ]; then
  echo -e "  ${GREEN}Model:${NC}       ${MODEL}"
else
  echo -e "  ${GREEN}Model:${NC}       Auto-detecting from models/ directory"
fi
if [ -n "${WARCLAW_API_KEY:-}" ]; then
  echo -e "  ${GREEN}API Key:${NC}     set — pass as X-API-Key header"
else
  echo -e "  ${YELLOW}API Key:${NC}     not set — open LAN access (export WARCLAW_API_KEY to restrict)"
fi
echo ""

# ── Change to backend parent dir so package imports resolve ─────
cd "$WARCLAW_DIR"

# ── Start uvicorn ────────────────────────────────────────────────
exec "$PYTHON_BIN" -m uvicorn backend.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level "$LOG_LEVEL" \
  --ws "$WS_BACKEND"
