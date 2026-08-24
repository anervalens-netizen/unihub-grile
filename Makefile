# UniHub Grile — S1 local commands.
#
# Targets wrap the documented local workflow so reviewers can replay the
# exact steps used to validate the stage. The full play-by-play lives in
# ``docs/operations/local-commands.md`` and the active ExecPlan.

PYTHON ?= python3
VENV ?=.venv
BACKEND := backend
FRONTEND := frontend

# PostgreSQL container used for the local health probe and Alembic run.
PG_CONTAINER ?= ugrile-pg-s1
PG_PORT ?= 55432
PG_USER ?= grile
PG_PASSWORD ?= grile
PG_DB ?= grile
DATABASE_URL ?= postgresql+psycopg://$(PG_USER):$(PG_PASSWORD)@127.0.0.1:$(PG_PORT)/$(PG_DB)

API_PORT ?= 8080
API_HOST ?= 127.0.0.1

.PHONY: help
help: ## Show the available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ { printf "  %-22s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: install
install: ## Install backend (venv + deps) and frontend (pnpm) toolchains
	cd $(BACKEND) && $(PYTHON) -m venv $(VENV) && $(VENV)/bin/pip install --quiet --upgrade 'pip==26.2.1'
	cd $(BACKEND) && $(VENV)/bin/pip install --quiet -c requirements.lock -e ".[dev]"
	cd $(BACKEND) && $(VENV)/bin/pip check
	cd $(FRONTEND) && pnpm install --silent --frozen-lockfile

.PHONY: format
format: ## Run ruff on the backend (format only)
	cd $(BACKEND) && $(VENV)/bin/ruff format src tests

.PHONY: lint
lint: ## Run ruff on the backend (lint only)
	cd $(BACKEND) && $(VENV)/bin/ruff check src tests

.PHONY: typecheck
typecheck: ## mypy (backend) + tsc (frontend)
	cd $(BACKEND) && $(VENV)/bin/mypy src
	cd $(FRONTEND) && pnpm exec tsc --noEmit

.PHONY: test
test: ## Run backend pytest + frontend vitest
	cd $(BACKEND) && $(VENV)/bin/python -m pytest tests/ -q
	cd $(FRONTEND) && pnpm exec vitest run

.PHONY: build
build: ## Build the frontend bundle
	cd $(FRONTEND) && pnpm run build

.PHONY: pg-up
pg-up: ## Start a dedicated ephemeral Postgres 17 container
	@if [ "$$(docker ps -q -f name=^$(PG_CONTAINER)$$)" = "" ]; then \
		docker run -d --rm --name $(PG_CONTAINER) \
			-p $(PG_PORT):5432 \
			-e POSTGRES_DB=$(PG_DB) \
			-e POSTGRES_USER=$(PG_USER) \
			-e POSTGRES_PASSWORD=$(PG_PASSWORD) \
			postgres:17-alpine >/dev/null; \
		sleep 2; \
	fi
	@docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER)

.PHONY: pg-down
pg-down: ## Stop the ephemeral Postgres container
	@if [ "$$(docker ps -q -f name=^$(PG_CONTAINER)$$)" != "" ]; then \
		docker kill $(PG_CONTAINER) >/dev/null; \
	fi

.PHONY: migrate
migrate: pg-up ## Apply Alembic migrations to the local Postgres
	cd $(BACKEND) && DATABASE_URL=$(DATABASE_URL) $(VENV)/bin/alembic upgrade head

.PHONY: api
api: pg-up migrate ## Run the FastAPI server (foreground)
	cd $(BACKEND) && DATABASE_URL=$(DATABASE_URL) UGRILE_PORT=$(API_PORT) UGRILE_HOST=$(API_HOST) \
		$(VENV)/bin/uvicorn ugrile.main:app --host $(API_HOST) --port $(API_PORT)

.PHONY: web
web: ## Run the Vite dev server
	cd $(FRONTEND) && pnpm run dev

.PHONY: health
health: pg-up ## Probe /healthz and /readyz on the local API
	@curl -fsS http://$(API_HOST):$(API_PORT)/healthz | python3 -m json.tool
	@curl -fsS http://$(API_HOST):$(API_PORT)/readyz | python3 -m json.tool

.PHONY: ingest
ingest: pg-up migrate ## Apply the v1 fixture inline
	@curl -fsS -X POST http://$(API_HOST):$(API_PORT)/ingest/fixture \
		-H "Content-Type: application/json" \
		-H "X-Ugrile-Identity: user_admin" \
		-H "X-Ugrile-Tenant: tenant_acme" \
		-d '{"tenant_token":"acme"}' | python3 -m json.tool

.PHONY: coverage-test
coverage-test: ## Trigger an AC-02 conflict (409) through the API
	@curl -fsS -X POST http://$(API_HOST):$(API_PORT)/months/month_tenantacme_2026-08/assignments \
		-H "Content-Type: application/json" \
		-H "X-Ugrile-Identity: user_admin" \
		-H "X-Ugrile-Tenant: tenant_acme" \
		-d '{"month_id":"month_tenantacme_2026-08","store_id":"store_acme_bucuresticenter","person_id":"person_acme_alice","business_date":"2026-08-01","working_kind":"NORMAL"}' || true

.PHONY: smoke
smoke: pg-up migrate api ## Boot stack + verify health/ready (background API)
	@echo ">>> /healthz"
	@curl -fsS http://$(API_HOST):$(API_PORT)/healthz | python3 -m json.tool
	@echo ">>> /readyz"
	@curl -fsS http://$(API_HOST):$(API_PORT)/readyz | python3 -m json.tool
	@echo ">>> /version"
	@curl -fsS http://$(API_HOST):$(API_PORT)/version | python3 -m json.tool
	@echo ">>> stopping API"
	@pgrep -f 'uvicorn ugrile.main:app' | xargs -r kill

.PHONY: clean
clean: pg-down ## Remove local artefacts (cache, dist)
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(FRONTEND)/dist $(FRONTEND)/.vite $(FRONTEND)/node_modules/.vite

.DEFAULT_GOAL := help