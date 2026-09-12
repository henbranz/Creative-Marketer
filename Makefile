ifneq (,$(wildcard ./.env))
include .env
export
endif

.PHONY: bootstrap dev-up dev-down db-migrate api-dev web-dev lint format-check typecheck test test-postgres temporal-up temporal-down temporal-test researcher-bootstrap producer-bootstrap media-tools-bootstrap researcher-live-smoke producer-astra-live-smoke production-image-live-smoke production-seedance-live-smoke production-worker assembly-worker demo-bootstrap agent-runs-stranded agent-run-abandon agent-run-rerun agent-run-reconcile-cost obsidian-setup obsidian-sync obsidian-rebuild obsidian-watch phase0-gate architecture-security build check

bootstrap:
	./scripts/bootstrap.sh

dev-up:
	docker compose up --build

dev-down:
	docker compose down

db-migrate:
	docker compose run --rm migrate

test-postgres:
	docker compose up -d postgres object-storage
	docker compose run --rm migrate
	docker compose run --rm object-storage-init
	cd apps/api && TEST_OBJECT_STORAGE_URL=http://localhost:9000 TEST_DATABASE_ADMIN_URL=postgresql+psycopg://creative_marketer_migrator:creative_marketer_migrator@localhost:5432/creative_marketer TEST_DATABASE_RUNTIME_URL=postgresql+psycopg://creative_marketer_runtime:creative_marketer_runtime@localhost:5432/creative_marketer TEST_DATABASE_PUBLISHER_URL=postgresql+psycopg://creative_marketer_event_publisher:creative_marketer_event_publisher@localhost:5432/creative_marketer uv run pytest -m 'postgres or object_storage'

api-dev:
	cd apps/api && uv run uvicorn creative_marketer_api.main:app --reload --host 0.0.0.0 --port 8000

web-dev:
	npm run dev

lint:
	cd apps/api && uv run ruff check .
	npm run lint

format-check:
	cd apps/api && uv run ruff format --check .
	npm run format:check

typecheck:
	cd apps/api && uv run mypy src tests
	npm run typecheck

test:
	cd apps/api && uv run pytest
	npm run test

temporal-up:
	docker compose --profile temporal up -d temporal

temporal-down:
	docker compose --profile temporal down

temporal-test:
	cd apps/api && uv run pytest -m temporal

researcher-bootstrap:
	cd apps/api && uv run python scripts/bootstrap_researcher.py

producer-bootstrap:
	cd apps/api && uv run python scripts/bootstrap_producer.py

media-tools-bootstrap:
	cd apps/api && uv run python scripts/bootstrap_media_tools.py

production-worker:
	cd apps/api && uv run python -m creative_marketer_api.production_worker

assembly-worker:
	docker compose --profile temporal run --rm assembly-worker

demo-bootstrap:
	cd apps/api && DATABASE_URL="$(MIGRATION_DATABASE_URL)" uv run python -m scripts.bootstrap_demo

producer-astra-live-smoke:
	@test "$${RUN_LIVE_PRODUCTION_SMOKE:-}" = "I_UNDERSTAND_THIS_SPENDS_MONEY" || (echo "Set RUN_LIVE_PRODUCTION_SMOKE=I_UNDERSTAND_THIS_SPENDS_MONEY" && exit 2)
	@test -n "$${OPENAI_API_KEY:-}" || (echo "OPENAI_API_KEY is required" && exit 2)
	@echo "Live Producer smoke must be launched through an approved Concept API request."

production-image-live-smoke:
	@test "$${RUN_LIVE_PRODUCTION_SMOKE:-}" = "I_UNDERSTAND_THIS_SPENDS_MONEY" || (echo "Set RUN_LIVE_PRODUCTION_SMOKE=I_UNDERSTAND_THIS_SPENDS_MONEY" && exit 2)
	@test -n "$${OPENAI_API_KEY:-}" || (echo "OPENAI_API_KEY is required" && exit 2)
	@echo "Live image generation must be launched through an approved ProductionPlan."

production-seedance-live-smoke:
	@test "$${RUN_LIVE_PRODUCTION_SMOKE:-}" = "I_UNDERSTAND_THIS_SPENDS_MONEY" || (echo "Set RUN_LIVE_PRODUCTION_SMOKE=I_UNDERSTAND_THIS_SPENDS_MONEY" && exit 2)
	@test -n "$${BYTEPLUS_LAS_API_KEY:-}" || (echo "BYTEPLUS_LAS_API_KEY is required" && exit 2)
	@echo "Live Seedance generation must be launched through an approved ProductionPlan."

researcher-live-smoke:
	cd apps/api && uv run python scripts/researcher_live_smoke.py

agent-runs-stranded:
	cd apps/api && uv run python -m creative_marketer_api.agent_run_recovery stranded

agent-run-abandon:
	cd apps/api && uv run python -m creative_marketer_api.agent_run_recovery abandon $(RUN_ID)

agent-run-rerun:
	cd apps/api && uv run python -m creative_marketer_api.agent_run_recovery rerun $(RUN_ID)

agent-run-reconcile-cost:
	cd apps/api && uv run python -m creative_marketer_api.agent_run_recovery reconcile-cost $(RUN_ID) $(ACTUAL_COST) $(CURRENCY)

obsidian-sync:
	cd apps/api && uv run python -m creative_marketer_api.obsidian_bridge

obsidian-rebuild:
	cd apps/api && uv run python -m creative_marketer_api.obsidian_bridge --full

obsidian-watch:
	cd apps/api && uv run python -m creative_marketer_api.obsidian_bridge --watch

obsidian-setup:
	cd apps/api && uv run python -m creative_marketer_api.obsidian_bridge --setup

phase0-gate: lint format-check typecheck
	docker compose up -d postgres object-storage
	docker compose run --rm migrate
	docker compose run --rm object-storage-init
	cd apps/api && TEST_OBJECT_STORAGE_URL=http://localhost:9000 TEST_DATABASE_ADMIN_URL=postgresql+psycopg://creative_marketer_migrator:creative_marketer_migrator@localhost:5432/creative_marketer TEST_DATABASE_RUNTIME_URL=postgresql+psycopg://creative_marketer_runtime:creative_marketer_runtime@localhost:5432/creative_marketer TEST_DATABASE_PUBLISHER_URL=postgresql+psycopg://creative_marketer_event_publisher:creative_marketer_event_publisher@localhost:5432/creative_marketer uv run pytest --junitxml=phase0-gate.xml

architecture-security: phase0-gate

build:
	npm run build

check: lint format-check typecheck test build
