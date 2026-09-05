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

The web app is available at <http://localhost:3000>; the API health endpoint is at <http://localhost:8000/health>. Compose applies Alembic migrations with the migration role before starting the API with the restricted runtime role.

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
make test-postgres
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

Reusable backend code lives in `apps/api/src/creative_marketer`; `creative_marketer_api` is only the delivery/composition adapter. Domain and application modules follow the dependency direction in `AGENTS.md`: delivery depends on application services, and infrastructure implements inward-owned ports. Provider SDKs, database drivers, and web frameworks do not belong in domain modules.

## Database security model

`creative_marketer_migrator` owns and migrates the schema. `creative_marketer_runtime` is a non-owner login used by the application and is subject to forced RLS. The committed password values are local/CI-only; production provisioning must create rotated credentials outside the repository.

Tenant-aware use cases pass an immutable `TenantContext` into a unit of work. On transaction entry the adapter calls PostgreSQL `set_config(..., true)`, equivalent to transaction-local `SET LOCAL`. Policies compare protected rows to `app.current_tenant_id`; missing or invalid context never exposes rows. Do not set this value outside the trusted application boundary.

With `DEV_IDENTITY_ENABLED=true`, protected proof routes accept a synthetic bearer credential formatted as `issuer|opaque-subject`. The credential still must match an `identity.external_identities` record and an active User/Membership/Tenant chain. This adapter cannot be enabled in staging or production; those environments return an authentication-unavailable error until a production adapter is configured.

Migration `20260905_0002` preserves legacy issuer/subject pairs from TASK-002 while extracting them to `identity.external_identities`. Its downgrade restores the legacy columns only when every User has at most one identity; it fails before changing the schema if that downgrade would be lossy.

The reusable relationship convention for future tenant-owned tables is a unique `(tenant_id, id)` target and a composite `(tenant_id, resource_id)` foreign key. Membership currently relates a tenant-owned row to a platform-scoped User, so its composite primary key `(tenant_id, user_id)`, tenant/user foreign keys, and RLS jointly prevent duplicates, orphan relationships, and cross-context attachment.

Run migrations directly only with the migration URL:

```bash
cd apps/api
uv run alembic upgrade head
```

`make test-postgres` starts the repository PostgreSQL service, applies migrations, and runs the real RLS/security suite. If an existing volume predates the role bootstrap, create a fresh development volume intentionally with `docker compose down -v` before retrying; that command deletes local database data.

## Environment and secrets

`.env.example` contains safe local defaults only. `.env` and all `.env.*` variants are ignored except for the example file. Never commit production credentials, OAuth tokens, or provider secrets.
