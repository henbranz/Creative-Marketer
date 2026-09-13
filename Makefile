.PHONY: bootstrap env-init env-check dev-up dev-down db-migrate api-dev web-dev lint format-check typecheck test test-postgres temporal-up temporal-down temporal-test researcher-bootstrap producer-bootstrap media-tools-bootstrap agent-worker live-provider-preflight live-openai-smoke live-image-smoke live-seedance-smoke live-e2e live-e2e-reset live-e2e-status production-worker assembly-worker demo-bootstrap agent-runs-stranded agent-run-abandon agent-run-rerun agent-run-reconcile-cost obsidian-setup obsidian-sync obsidian-rebuild obsidian-watch phase0-gate architecture-security build check

bootstrap:
	./scripts/bootstrap.sh

env-init:
	@python3 scripts/environment.py init

env-check:
	@python3 scripts/environment.py check

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
	cd apps/api && TEST_OBJECT_STORAGE_URL=http://localhost:9000 TEST_DATABASE_ADMIN_URL=postgresql+psycopg://creative_marketer_migrator:creative_marketer_migrator@localhost:5432/creative_marketer TEST_DATABASE_RUNTIME_URL=postgresql+psycopg://creative_marketer_runtime:creative_marketer_runtime@localhost:5432/creative_marketer TEST_DATABASE_PUBLISHER_URL=postgresql+psycopg://creative_marketer_event_publisher:creative_marketer_event_publisher@localhost:5432/creative_marketer uv run dotenv -f ../../.env run --no-override -- pytest -m 'postgres or object_storage' --no-cov

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
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- pytest -m temporal --no-cov

researcher-bootstrap:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python scripts/bootstrap_researcher.py

producer-bootstrap:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python scripts/bootstrap_producer.py

media-tools-bootstrap:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python scripts/bootstrap_media_tools.py

agent-worker:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.researcher_worker

production-worker:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.production_worker

assembly-worker:
	docker compose --profile temporal run --rm assembly-worker

demo-bootstrap:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m scripts.bootstrap_demo

live-provider-preflight:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m scripts.live_validation preflight

live-openai-smoke:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m scripts.live_validation openai-smoke

live-image-smoke:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m scripts.live_validation image-smoke

live-seedance-smoke:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m scripts.live_validation seedance-smoke

live-e2e:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m scripts.live_validation e2e

live-e2e-reset:
	cd apps/api && uv run python -m scripts.live_validation reset

live-e2e-status:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m scripts.live_validation status

researcher-live-smoke:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python scripts/researcher_live_smoke.py

agent-runs-stranded:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.agent_run_recovery stranded

agent-run-abandon:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.agent_run_recovery abandon $(RUN_ID)

agent-run-rerun:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.agent_run_recovery rerun $(RUN_ID)

agent-run-reconcile-cost:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.agent_run_recovery reconcile-cost $(RUN_ID) $(ACTUAL_COST) $(CURRENCY)

obsidian-sync:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.obsidian_bridge

obsidian-rebuild:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.obsidian_bridge --full

obsidian-watch:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.obsidian_bridge --watch

obsidian-setup:
	cd apps/api && uv run dotenv -f ../../.env run --no-override -- python -m creative_marketer_api.obsidian_bridge --setup

phase0-gate: lint format-check typecheck
	docker compose up -d postgres object-storage
	docker compose run --rm migrate
	docker compose run --rm object-storage-init
	cd apps/api && TEST_OBJECT_STORAGE_URL=http://localhost:9000 TEST_DATABASE_ADMIN_URL=postgresql+psycopg://creative_marketer_migrator:creative_marketer_migrator@localhost:5432/creative_marketer TEST_DATABASE_RUNTIME_URL=postgresql+psycopg://creative_marketer_runtime:creative_marketer_runtime@localhost:5432/creative_marketer TEST_DATABASE_PUBLISHER_URL=postgresql+psycopg://creative_marketer_event_publisher:creative_marketer_event_publisher@localhost:5432/creative_marketer uv run dotenv -f ../../.env run --no-override -- pytest --junitxml=phase0-gate.xml

architecture-security: phase0-gate

build:
	npm run build

check: lint format-check typecheck test build
