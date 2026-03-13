# ═══════════════════════════════════════════════════════════════
# WarClaw v2.1 — Dockerfile
# EdgeRunner AI · Naval LAN Operating System
#
# Build:  docker build -t warclaw .
# Run:    docker run -p 7070:7070 -v ./models:/app/models warclaw
# ═══════════════════════════════════════════════════════════════
FROM python:3.12-slim AS base

# Build dependencies for llama-cpp-python (compiled C++)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache layer)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy application code
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY scripts/ /app/scripts/
COPY models/ /app/models/ 2>/dev/null || true
COPY generated_apps/ /app/generated_apps/ 2>/dev/null || true

# Ensure runtime directories exist
RUN mkdir -p /app/models /app/generated_apps

# Non-root user for security
RUN useradd -r -s /bin/false warclaw && chown -R warclaw:warclaw /app
USER warclaw

# Default config
ENV WARCLAW_HOST=0.0.0.0
ENV WARCLAW_PORT=7070
ENV WARCLAW_THREADS=4
ENV WARCLAW_CTX=4096
ENV WARCLAW_GPU_LAYERS=0

EXPOSE 7070

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7070/api/status')" || exit 1

CMD ["python", "-m", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "7070", \
     "--ws", "websockets", "--log-level", "info"]
