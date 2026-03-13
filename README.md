# WarClaw v2.1

**EdgeRunner AI — Naval LAN Operating System**

WarClaw is an offline-first, AI-powered operating system for ship LANs. It runs 100% locally with no cloud dependency — designed for air-gapped naval and maritime environments.

## What It Does

- **LAN Discovery** — Scans the ship's network and fingerprints every device: NMEA navigation systems, MODBUS sensors, IEC 61162 bridge equipment, HTTP interfaces, serial-to-TCP gateways
- **Autonomous Agents** — Auto-recommends and deploys persistent agents that monitor live protocol streams, fire alerts on anomalies, and log data for post-voyage analysis
- **AI Assistant** — Local LLM (llama.cpp) answers questions about ship systems, writes integration code, and interprets sensor data
- **App Factory** — AI generates full-stack ship applications (FastAPI + HTML/JS) on demand, deployed as live routes
- **Anomaly Detection** — Statistical baseline monitoring with configurable sigma-threshold alerting on all numeric sensor data

## Quick Start

### Option 1: Docker (Recommended)

```bash
docker compose up --build
```

Open http://localhost:7070

### Option 2: Native Python

```bash
make setup          # Creates venv, installs deps
make download-model # Downloads a GGUF model (interactive)
make run            # Starts WarClaw on port 7070
```

### Option 3: Manual

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 7070 --ws websockets
```

## Loading an AI Model

WarClaw uses llama.cpp with GGUF model files. No model is included — download one:

```bash
make download-model
# Or manually download from https://huggingface.co/models?search=gguf
# Place .gguf files in the models/ directory
```

**Recommended models:**

| Server RAM | Model | Size |
|---|---|---|
| 8-16 GB | Phi-3-mini-4k-instruct Q4 | ~2.2 GB |
| 16-32 GB | Mistral-7B-Instruct Q5_K_M | ~5 GB |
| 32+ GB / GPU | Llama-3-8B-Instruct Q5_K_M | ~5.7 GB |

Load via the **Hardware** tab in the UI, or set `WARCLAW_MODEL=/path/to/model.gguf` before starting.

## Agent Types

Agents auto-deploy based on what WarClaw discovers on the LAN:

| Agent | Triggers On | Monitors |
|---|---|---|
| Navigation Watch | NMEA GGA/RMC/HDT/DBT | GPS fix, heading, depth, SOG — alerts on shoal water, fix loss |
| Engineering Plant Monitor | MODBUS TCP port 502 | Temperature, pressure, RPM via holding registers |
| Weather Station | NMEA MWV/MTW sentences | Wind speed/direction, water temp — gale alerts |
| LAN Security Monitor | 3+ hosts on LAN | New/disappeared hosts, baseline drift |
| Protocol Data Logger | Any NMEA/MODBUS stream | Timestamped JSONL recording for post-voyage |

## Configuration

Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

Key variables:

| Variable | Default | Description |
|---|---|---|
| `WARCLAW_PORT` | 7070 | HTTP port |
| `WARCLAW_MODEL` | _(empty)_ | Path to .gguf model file |
| `WARCLAW_API_KEY` | _(empty)_ | API key for `/api/*` routes (X-API-Key header) |
| `WARCLAW_CTX` | 4096 | LLM context window (tokens) |
| `WARCLAW_THREADS` | 4 | CPU threads for inference |
| `WARCLAW_GPU_LAYERS` | 0 | GPU layers to offload (0 = CPU only) |
| `WARCLAW_SCAN_TIMEOUT` | 3.0 | LAN scan TCP timeout (seconds) |

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | GET | System health, resource metrics, agent stats |
| `/api/chat/` | POST | Non-streaming AI chat |
| `/api/chat/ws` | WS | Streaming AI chat (WebSocket) |
| `/api/lan/scan` | GET | LAN discovery scan |
| `/api/lan/scan/export` | GET | Download scan as JSON file |
| `/api/lan/stream` | WS | Live protocol stream (WebSocket) |
| `/api/lan/modbus/probe` | POST | Probe a MODBUS device |
| `/api/agents/` | GET | List deployed agents |
| `/api/agents/recommend/auto` | GET | Scan LAN + auto-recommend agents |
| `/api/agents/deploy` | POST | Deploy an agent |
| `/api/agents/{id}/start` | POST | Start an agent |
| `/api/agents/{id}/stop` | POST | Stop an agent |
| `/api/agents/templates` | GET | Available agent templates |
| `/api/agents/bus/stats` | GET | DataBus channel statistics |
| `/api/apps/generate` | POST | Generate a full-stack app via AI |
| `/api/apps/` | GET | List generated apps |
| `/api/events/` | GET | Mission log entries |
| `/api/events/stream` | GET | Mission log SSE stream |
| `/api/hardware/profile` | GET | Server hardware detection |
| `/api/hardware/models` | GET | Available GGUF models |
| `/api/hardware/models/load` | POST | Load a model into memory |
| `/api/docs` | GET | Interactive API docs (Swagger) |

## Architecture

```
.
├── backend/
│   ├── main.py              # FastAPI app, middleware, startup
│   ├── config.py            # All env-var-driven configuration
│   ├── routers/             # API endpoint modules
│   │   ├── agents.py        # Agent CRUD + auto-recommend
│   │   ├── apps.py          # App Factory endpoints
│   │   ├── chat.py          # AI chat (REST + WebSocket)
│   │   ├── events.py        # Mission log + SSE stream
│   │   ├── hardware.py      # Hardware detection + model management
│   │   └── lan.py           # LAN scan + protocol stream
│   ├── services/            # Business logic
│   │   ├── agent_engine.py  # Agent lifecycle + templates
│   │   ├── anomaly.py       # Statistical anomaly detection
│   │   ├── app_factory.py   # AI app generation
│   │   ├── data_bus.py      # Pub/sub for protocol data
│   │   ├── hardware.py      # GPU/CPU detection
│   │   ├── lan_monitor.py   # Network scanning + protocol sniffing
│   │   ├── llm.py           # llama.cpp LLM wrapper
│   │   └── mission_log.py   # JSONL event persistence + SSE
│   ├── protocols/           # Wire protocol parsers
│   │   ├── nmea.py          # NMEA 0183 (11 sentence types)
│   │   ├── modbus.py        # MODBUS TCP
│   │   └── iec61162.py      # IEC 61162-450 UDP
│   └── requirements.txt
├── frontend/
│   ├── index.html           # Single-page app shell
│   ├── css/app.css          # Dark naval theme
│   └── js/                  # Vanilla JS modules (no framework)
│       ├── app.js           # Router, state, status polling
│       ├── agents.js        # Agent management UI
│       ├── apps.js          # App Factory UI
│       ├── chat.js          # WebSocket streaming chat
│       ├── events.js        # SSE mission log
│       ├── hardware.js      # Hardware & model UI
│       └── lan.js           # LAN discovery UI
├── scripts/
│   ├── setup.sh             # Full setup (venv, deps, GPU detection)
│   ├── start.sh             # Production start script
│   └── download_model.sh    # Interactive model downloader
├── models/                  # .gguf model files (not committed)
├── generated_apps/          # AI-created apps (runtime)
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── .env.example
```

## LAN Scanning on Docker

By default, Docker uses bridge networking. To scan the actual ship LAN:

```bash
# Option 1: Host networking (full LAN access)
docker run --network host -v ./models:/app/models warclaw

# Option 2: docker-compose with host networking
# Uncomment "network_mode: host" in docker-compose.yml
```

## Protocols Supported

- **NMEA 0183** — GGA, GLL, RMC, VTG, HDT, DBT, VHW, ZDA, MWV, MTW, ROT
- **IEC 61162-450** — UDP multicast datagrams wrapping NMEA sentences
- **MODBUS TCP** — Coil and holding register reads via pymodbus
- **HTTP** — Web interface detection and banner grabbing

## Security Notes

- WarClaw is designed for **isolated ship LANs** with no internet connectivity
- Set `WARCLAW_API_KEY` to restrict API access in shared environments
- CORS allows all origins (expected for LAN terminals)
- No telemetry, no cloud calls, no external dependencies at runtime

## License

MIT-0
