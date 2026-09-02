# Development

## Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 or newer and npm 11 or newer
- Docker with Compose (optional, for the full local stack)

## Bootstrap

```bash
cp .env.example .env
make bootstrap
make check
```

`make bootstrap` creates the Python environment from `uv.lock` and installs the npm workspaces from `package-lock.json`.

## Run locally

Start PostgreSQL, the API, and the web app together:

```bash
make dev-up
```

The web app is available at <http://localhost:3000>; the API health endpoint is at <http://localhost:8000/health>.

To run processes directly during development, start PostgreSQL with Docker and run these in separate terminals:

```bash
docker compose up postgres
make api-dev
make web-dev
```

The API reads the repository-root `.env` file. Configuration is validated at startup; invalid URLs, environments, origins, or ports fail closed with a clear validation error.

## Quality commands

```bash
make lint
make format-check
make typecheck
make test
make build
make check
```

## Repository layout

```text
apps/
  api/                 FastAPI delivery layer and future application composition root
  web/                 Next.js user interface
packages/
  contracts/           Provider-neutral TypeScript boundary contracts
infra/                 Deployment and infrastructure assets (added when justified)
scripts/               Reproducible developer scripts
docs/                  Architecture and decision records
```

Domain and application modules added later must follow the dependency direction in `AGENTS.md`: delivery layers depend on application services, which depend on domain code and ports. Infrastructure implements ports and points inward. Provider SDKs, database drivers, and web frameworks do not belong in domain modules.

## Environment and secrets

`.env.example` contains safe local defaults only. `.env` and all `.env.*` variants are ignored except for the example file. Never commit production credentials, OAuth tokens, or provider secrets.
