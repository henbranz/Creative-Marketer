# Live provider and end-to-end validation

> **THIS PATH CAN SPEND REAL MONEY.** The free fake-provider walkthrough remains the default.

This runbook activates OpenAI and BytePlus only for a controlled existing Product. It never
publishes to a social or commerce platform. Generated media remains in the private Asset Library;
Git and Obsidian receive no media binaries or credentials.

## 1. Initialize the central environment

```bash
make env-init
make env-check
```

`env-init` creates or merges only `<repo>/.env`, preserves existing assignments, adds missing
contract fields, prints no values, and attempts mode `0600` on Unix. The file is Git-ignored. Edit
it locally rather than placing credentials on a command line, where shell history may retain them.

Fill `OPENAI_API_KEY`, `BYTEPLUS_LAS_API_KEY`, `CM_TENANT_ID`, `CM_API_TOKEN`, and
`LIVE_E2E_PRODUCT_ID`. `CM_API_TOKEN` is the local development credential (the demo identity uses
`local-demo|owner`). Keep the account-specific `BYTEPLUS_LAS_BASE_URL`; the example is the
documented AP Southeast endpoint and is not assumed for every account. Never share `.env`, terminal
recordings, screenshots, or support bundles containing its contents.

## 2. Start local infrastructure

In terminal 1, keep the foreground Compose stack running:

```bash
make dev-up
```

In terminal 2:

```bash
make temporal-up
make demo-bootstrap
```

`make temporal-up` starts Temporal only. It does not start the fake Agent worker. For an intentional
free fake/demo Agent execution, start it explicitly in a separate terminal:

```bash
docker compose --profile temporal --profile fake-agent up researcher-worker
```

That service is pinned to `MODEL_PROVIDER_BACKEND=fake`, workload identity
`local-fake-agent-worker`, and rejects every `live-*` AgentRun before claim.

Use the Tenant ID printed by bootstrap in `CM_TENANT_ID`. The API is at
<http://localhost:8000>, the product UI at <http://localhost:3000>, and Temporal at
<http://localhost:8233>. A `NEXT_PUBLIC_*` change requires rebuilding/restarting the web app.

## 3. Free preflight—no paid inference or generation

Leave provider selectors disabled while checking account access:

```bash
make env-check
make live-provider-preflight
```

Preflight retrieves OpenAI model metadata once for the shared reasoning model `gpt-5.6-sol` and for
`gpt-image-2.5-sunburst-2026-09-08`, checks application route identity, and resolves the configured
BytePlus LAS host. BytePlus documents no credential-only endpoint for this API, so authentication
is explicitly deferred to the minimal Seedance smoke; no video task is created. An inaccessible
official OpenAI model fails as `PROVIDER_MODEL_NOT_AVAILABLE_TO_ACCOUNT`; there is no fallback.

## 4. Intentionally activate live execution

Before any proposed structured Agent retry, inspect the exact frozen run without generation:

```bash
cd apps/api
uv run dotenv -f ../../.env run --no-override -- python -m scripts.agent_request_preflight TENANT_UUID RUN_UUID
```

This local gate supports Researcher, Creative Strategist, Producer, Intelligence, Commerce
Operations, and Supervisor. It verifies the frozen AgentVersion, route, pricing, context provenance,
token envelope, and exact compiled provider contract in a read-only tenant transaction. Image
references are reported only as bounded identity/count metadata; bytes are not loaded. It neither
claims a run nor starts a worker and prints no Product or Research prose. A local PASS explicitly
leaves provider acceptance UNVERIFIED and never authorizes a retry.

Audit every installed contract version offline:

```bash
make openai-contract-audit
make openai-contract-gate
```

The second command is also offline and proves that all registry-installed contract versions across the
six model-backed agent types serialize exclusively for the official input-token count endpoint. With
separate operator approval, validate those synthetic contracts against OpenAI without generation:

```bash
make openai-contract-gate-approved \
  APPROVAL=I_APPROVE_OPENAI_INPUT_TOKEN_CONTRACT_GATE \
  DIAGNOSTIC_FILE=/private/path/openai-contract-gate.jsonl
```

This calls only `POST /v1/responses/input_tokens`. It sends no Product, Research, tenant, prompt, or
asset content, makes no `/v1/responses` request, and records only contract identity, model, compiler
revision, provider-schema digest, input-token count, and bounded rejection metadata.

Provider HTTP failures now emit an `openai_status_diagnostic` log containing only status, allowlisted
type/code/parameter, bounded request ID, and SDK class. Unknown values are redacted rather than
copied; no body, message, request content, credentials, or arbitrary headers are logged. Domain
failure codes remain unchanged. Never enable SDK DEBUG/body logging to investigate a failure.

Edit `.env`:

```dotenv
MODEL_PROVIDER_BACKEND=openai
MEDIA_IMAGE_PROVIDER=openai
MEDIA_VIDEO_PROVIDER=byteplus
ALLOW_BILLABLE_MEDIA=true
RUN_LIVE_E2E=I_UNDERSTAND_THIS_SPENDS_MONEY
LIVE_E2E_MAX_USD=10
```

Restart Compose. Keep these processes active in separate terminals:

```bash
make agent-worker
make production-worker
make assembly-worker
```

The generic Agent worker runs Researcher, Creative Strategist, and Producer. Provider keys remain
inside infrastructure processes; agents receive neither keys nor media tools. Its local workload
identity is `local-live-agent-worker`; do not reuse the fake worker identity.

## 5. Controlled smoke flow

Start a fresh acceptance session before the first paid run:

```bash
make live-e2e-reset
make live-e2e-status
```

The reset removes only the ignored local `.creative-marketer/live-validation.json` checkpoint; it
never deletes database records. The checkpoint contains safe UUID identifiers only and is bound to
the exact `CM_TENANT_ID` and `LIVE_E2E_PRODUCT_ID`. If either changes, reset explicitly. Historical
demo or live records are never adopted into a new session.

```bash
make live-openai-smoke
```

The command advances one newly requested, session-bound governed step at a time against
`LIVE_E2E_PRODUCT_ID`: Researcher, then
Creative Strategist, then Producer, all through their current GPT-5.6 Sol routes. Re-run after
workers finish. It pauses for a human
to approve one Creative Concept in the UI. Successful runs report only route/model, token usage,
cost, and schema status—never prompts or provider payloads. Re-running resumes the exact persisted
run IDs and does not create duplicates. `MODEL_PROVIDER_BACKEND=openai` is required before a run can
be created or accepted. The approved Concept must belong to the exact session Creative run, and the
Production Plan must belong to the exact session Producer run.

If the session-bound Creative run has an authoritative incomplete response whose bounded reason is
`MAX_OUTPUT_TOKENS`, first deploy and run `make creative-strategist-bootstrap` to create/activate the
newer compatible immutable AgentVersion, then use the explicit operator transition:

```bash
make live-creative-replace
```

This command does not invoke a provider or rerun Researcher. The API verifies the failed run and its
single immutable attempt, confirms a different compatible active route with a larger output limit,
and admits a fresh normal Creative run through the standard budget and context boundary. The local
session checkpoint records the prior Creative run only after tenant, Product, stage, and new-run
provenance pass. Its deterministic transition identity makes repeated commands idempotent. Use
`make live-e2e-status` to see both the current run and historical Creative/recovery cost lineage.

If the session-bound Producer run has one authoritative completed response but failed deterministic
plan validation on an older output contract, first deploy the migration/API and run
`make producer-bootstrap` to activate the immutable v3 Producer. With every Agent worker stopped,
first confirm that the approved Concept's Research authority is still current. If it expired,
request one governed Research refresh:

```bash
make live-research-refresh
```

This creates a new pending Researcher AgentRun and retains the prior successful Researcher ID in
session history. It does not execute the run itself and never resets the session. Start the live
Agent worker only as a separately controlled paid action, then inspect until the refresh succeeds.
No Creative or Producer run starts automatically.

With the fresh ResearchSnapshot current, stop the worker and run the no-provider deterministic
transition:

```bash
make live-creative-revalidate
make live-e2e-status
```

`REVALIDATED_FOR_PRODUCTION` means every exact referenced finding assertion is unchanged and Product
authority is identical. Citation identities may have refreshed. `REQUIRES_RESTRATEGY` stops the
continuation and requires a fresh Creative Strategist run; it never weakens Product claim or
Research validation. Status shows the current Researcher, historical Research lineage, original
Research authority, current Research authority, and revalidation result. See ADR-052.

When revalidation reports `REQUIRES_RESTRATEGY`, keep the Agent worker stopped and admit the exact
state-machine transition explicitly:

```bash
make live-creative-restrategy
make live-e2e
```

The first command validates the session's current Research authority and immutable historical
Concept/revalidation lineage, then creates one normal `PENDING` Creative Strategist run with
`restrategy_of_concept_id`. It preserves historical Creative and Producer database rows, removes the
historical Producer only from the active local checkpoint, and does not execute a provider. Repeated
invocation reuses the session-bound pending run. The second command should report
`WAIT_FOR_CREATIVE`; provider execution remains a separately controlled worker action.

Only after successful revalidation, use the existing governed continuation:

```bash
make live-producer-replace
```

The command verifies known usage/cost, zero unknown cost, no ProductionPlan, valid current frozen
provenance, a strictly newer active supported contract/prompt, and no successor. It creates one v3
run in `PENDING` with `recovery_of_run_id`; it does not execute a provider. Inspect status before
starting one controlled Agent worker. Historical v1/v2 responses, diagnostics, and spend remain
unchanged. Successor detection is directional: a non-null `recovery_of_run_id` on the failed run
identifies its parent and does not block another strictly newer contract upgrade. The harness checks
for children whose `recovery_of_run_id` equals the exact failed run ID, adopts one valid existing
child idempotently, and fails closed if multiple children are exposed. See ADR-051.

Review the exact Production Plan and cost in the UI. Human approval creates route-bound
GenerationJobs. These commands inspect and continue that governed path; they never call a provider
SDK directly:

```bash
make live-image-smoke
make live-seedance-smoke
```

The smoke commands persist and inspect only the exact live-provider GenerationJob IDs created for
the session-bound plan; fake and historical jobs cannot satisfy acceptance. The Seedance command
derives a 4-second 720p no-input-video preview from versioned pricing before
approval. The approved plan's immutable reservation remains authoritative. Unknown actual spend is
reported as reserved/unknown, never as zero.

## 6. Full campaign guide

```bash
make live-e2e
```

`make live-e2e` is now a read-only inspection of the authoritative Research → Creative → Producer
state machine. It reports exactly one next action, bounded blocking reason, provider-cost flag,
human-approval flag, provider-execution permission, and the three typed states. It never starts a
worker, creates an AgentRun, calls a provider, approves an artifact, creates media/assembly work, or
mutates the live session/database. Optional session UUIDs only bind the projection to the selected
history; PostgreSQL remains authoritative.

When it reports `REQUIRES_RESTRATEGY`, run `make live-creative-restrategy` with the Agent worker
stopped. That operator transition uses the normal Creative API with `restrategy_of_concept_id` set to
the exact historical Concept and a bounded authority-derived idempotency key. Admission verifies the
current `REQUIRES_RESTRATEGY` authority and current Research, and freezes both in the new run. When it
reports `READY_FOR_GENERATION`, the pre-generation acceptance path is complete. Image, video, and
assembly remain separate explicit commands and are not part of this state inspection. See ADR-053.

At any point, `make live-e2e-status` reports safe stage status and cost for only the exact persisted
AgentRuns and GenerationJobs. It prints no credentials. Assembly and final acceptance are likewise
bound to the persisted ProductionPlan, generated assets, AssemblyPlan, and FinalCreative IDs; an
older successful final cannot satisfy the current session.

`LIVE_E2E_MAX_USD` covers reserved/actual Agent and media cost for the selected Product. The media
authority locks and recomputes cumulative committed spend immediately before provider I/O. It
blocks a different Product and execution above the cap. Increase the value explicitly in `.env` to
authorize more. Provider billing remains authoritative; reservations are conservative.

## Acceptance checklist

```text
[ ] root .env created
[ ] OpenAI key configured
[ ] BytePlus key configured
[ ] env-check passes
[ ] provider preflight passes
[ ] Researcher live run succeeds
[ ] Creative Strategist live run succeeds
[ ] Producer GPT-5.6 Sol live run succeeds
[ ] Sunburst image generation succeeds
[ ] Seedance 2.5 generation succeeds
[ ] generated assets preview
[ ] Final Assembly succeeds
[ ] final MP4 plays in browser
[ ] FinalCreative provenance visible
[ ] Obsidian graph updates
[ ] total live cost is visible
[ ] no secrets appear in logs/UI/Obsidian
```

CI acceptance and live-provider acceptance are separate. CI never receives provider keys and never
runs these commands. Record live acceptance as `NOT RUN`, `PASS`, or `FAIL` in operator notes.

When a session-bound AgentRun is recovered with the operator-only `agent-run-rerun` command, the
harness may adopt only the unique successor whose `recovery_of_run_id` points to that exact run and
whose tenant-scoped Product, Agent type/version/configuration, context provenance, and applicable
route match. Zero successors leaves the checkpoint unchanged; multiple or mismatched successors fail
closed. Status reports operational recovery separately from lifecycle state and reports actual,
reserved, immutable original unknown, reconciled actual, and remaining unknown-potential costs as
distinct values.

A terminal `FAILED_NO_RESPONSE` run with authoritative zero response, tokens, actual cost, and
unknown cost may use the same operator-only rerun command after its deterministic request defect is
fixed. The predecessor is not reset or reconciled. The harness applies the same exact unique-
successor provenance checks before adopting the new run; creating the successor does not invoke the
provider.
