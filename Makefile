ifneq (,$(wildcard ./.env))
include .env
export
endif

.PHONY: bootstrap dev-up dev-down db-migrate api-dev web-dev lint format-check typecheck test test-postgres temporal-up temporal-down temporal-test phase0-gate architecture-security build check

bootstrap:
	./scripts/bootstrap.sh

dev-up:
	docker compose up --build

dev-down:
	docker compose down

db-migrate:
	docker compose run --rm migrate

test-postgres:
	docker compose up -d postgres
	docker compose run --rm migrate
	cd apps/api && TEST_DATABASE_ADMIN_URL=postgresql+psycopg://creative_marketer_migrator:creative_marketer_migrator@localhost:5432/creative_marketer TEST_DATABASE_RUNTIME_URL=postgresql+psycopg://creative_marketer_runtime:creative_marketer_runtime@localhost:5432/creative_marketer TEST_DATABASE_PUBLISHER_URL=postgresql+psycopg://creative_marketer_event_publisher:creative_marketer_event_publisher@localhost:5432/creative_marketer uv run pytest -m postgres

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

phase0-gate: lint format-check typecheck
	docker compose up -d postgres
	docker compose run --rm migrate
	cd apps/api && TEST_DATABASE_ADMIN_URL=postgresql+psycopg://creative_marketer_migrator:creative_marketer_migrator@localhost:5432/creative_marketer TEST_DATABASE_RUNTIME_URL=postgresql+psycopg://creative_marketer_runtime:creative_marketer_runtime@localhost:5432/creative_marketer TEST_DATABASE_PUBLISHER_URL=postgresql+psycopg://creative_marketer_event_publisher:creative_marketer_event_publisher@localhost:5432/creative_marketer uv run pytest --junitxml=phase0-gate.xml

architecture-security: phase0-gate

build:
	npm run build

check: lint format-check typecheck test build
