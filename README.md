# WarClaw

WarClaw is the EdgeRunner AI local maritime operations console in this repository. The runnable application lives under `warclaw/`, and the repo root now exposes a matching launcher at `./start.sh`.

## Local Run

```bash
./start.sh
```

The launcher delegates to `warclaw/scripts/start.sh`, which starts the FastAPI app on port `7070` by default.

## App Docs

Project-specific setup, model loading, Docker usage, and API details are documented in `warclaw/README.md`.
