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
inside infrastructure processes; agents receive neither keys nor media tools.

## 5. Controlled smoke flow

```bash
make live-openai-smoke
```

The command advances one governed step at a time against `LIVE_E2E_PRODUCT_ID`: Researcher, then
Creative Strategist, then Producer, all through their current GPT-5.6 Sol routes. Re-run after
workers finish. It pauses for a human
to approve one Creative Concept in the UI. Successful runs report only route/model, token usage,
cost, and schema status—never prompts or provider payloads.

Review the exact Production Plan and cost in the UI. Human approval creates route-bound
GenerationJobs. These commands inspect and continue that governed path; they never call a provider
SDK directly:

```bash
make live-image-smoke
make live-seedance-smoke
```

The Seedance command derives a 4-second 720p no-input-video preview from versioned pricing before
approval. The approved plan's immutable reservation remains authoritative. Unknown actual spend is
reported as reserved/unknown, never as zero.

## 6. Full campaign guide

```bash
make live-e2e
```

This resumable command uses the same APIs and workers. It does not mutate the real Product, bypass
a Concept/Production/Assembly approval, or create a parallel implementation. Complete human
decisions in the UI and re-run. After media is ready, create/review Final Assembly in the UI, run
the command again: it requests the deterministic AssemblyPlan through the existing API when all
sources are ready. Keep the Assembly worker running, preview the final MP4, approve it for
publishing, and run `make obsidian-sync` (or keep `make obsidian-watch` active). The command returns
success only after the current Sol-linked FinalCreative is approved for publishing; browser
playback and Obsidian projection remain explicit operator checks.

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
