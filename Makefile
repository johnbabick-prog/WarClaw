# ═══════════════════════════════════════════════════════════════
# WarClaw v2.1 — Makefile
# EdgeRunner AI · Naval LAN Operating System
# ═══════════════════════════════════════════════════════════════

.PHONY: setup run docker docker-run download-model clean help

help: ## Show available commands
	@echo ""
	@echo "  WarClaw v2.1 — EdgeRunner AI Naval LAN OS"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""

setup: ## Install dependencies (Python venv)
	./scripts/setup.sh

run: ## Start WarClaw (requires setup first)
	./scripts/start.sh

download-model: ## Download a GGUF model (interactive)
	./scripts/download_model.sh

docker: ## Build Docker image
	docker build -t warclaw .

docker-run: ## Run via Docker (mounts ./models)
	docker compose up --build

docker-stop: ## Stop Docker container
	docker compose down

docker-lan: ## Run with host networking for LAN scanning
	docker compose up --build -e network_mode=host

clean: ## Remove venv, caches, generated apps
	rm -rf .venv __pycache__ backend/__pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned. Models and generated_apps preserved."
