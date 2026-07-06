.PHONY: quality test backend frontend ngrok format install infra-up infra-down

PYTHON := .venv/bin/python
BACKEND_PORT := 8020

# --- Qualitaet & Tests ---
quality:
	$(PYTHON) -m ruff format --check backend
	$(PYTHON) -m ruff check backend
	$(PYTHON) -m pytest

test:
	$(PYTHON) -m pytest

# Frontend: Typprüfung (vue-tsc) + Produktions-Build in einem Schritt.
test-frontend:
	cd frontend && npm run build

format:
	$(PYTHON) -m ruff format backend
	$(PYTHON) -m ruff check --fix backend

# --- Entwicklung ---
backend:
	set -a && . ./.env && set +a && \
	$(PYTHON) -m uvicorn backend.app.main:app --reload --reload-dir backend \
		--timeout-graceful-shutdown 3 --host 0.0.0.0 --port $(BACKEND_PORT)

frontend:
	cd frontend && npm run dev

# Beide Tunnel (App-Domain + OnlyOffice) aus ngrok.yml starten.
ngrok:
	set -a && . ./.env && set +a && \
	ngrok start --all --config "$$HOME/.config/ngrok/ngrok.yml" --config ./ngrok.yml

# --- Infrastruktur (Docker) ---
infra-up:
	docker compose up -d db
	docker compose --profile ocr up -d

infra-down:
	docker compose --profile ocr down

# --- Installation ---
install:
	uv venv .venv --python 3.14
	uv pip install --python .venv/bin/python -r backend/requirements.txt -r requirements-dev.txt
	cd frontend && npm install
