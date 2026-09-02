#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

cd "$repo_root/apps/api"
uv sync --frozen

cd "$repo_root"
uv run --project apps/api pre-commit install
npm ci
