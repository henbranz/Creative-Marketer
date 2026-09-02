ifneq (,$(wildcard ./.env))
include .env
export
endif

.PHONY: bootstrap dev-up dev-down api-dev web-dev lint format-check typecheck test build check

bootstrap:
	./scripts/bootstrap.sh

dev-up:
	docker compose up --build

dev-down:
	docker compose down

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

build:
	npm run build

check: lint format-check typecheck test build
