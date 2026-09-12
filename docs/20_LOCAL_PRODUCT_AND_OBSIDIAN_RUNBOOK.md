# Local Product and Obsidian Runbook

This walkthrough produces a visible end-to-end Creative Marketer workspace with no provider bill.
Values marked `LOCAL DEMO` are deterministic fixtures; they never bootstrap outside development.

## 1. Start the product

From a fresh checkout:

```bash
git pull
cp .env.example .env
make bootstrap
make dev-up
```

Open the UI at <http://localhost:3000>. API liveness is
<http://localhost:8000/health/live>, API readiness is
<http://localhost:8000/health/ready>, and MinIO is <http://localhost:9001>.

## 2. Start durable workflows

In a second terminal:

```bash
make temporal-up
```

Temporal listens on `localhost:7233`; its development UI is <http://localhost:8233>.

## 3. Create the deterministic demo

After migrations and MinIO are ready:

```bash
make demo-bootstrap
```

The command creates/reuses the Tenant, User/external identity/OWNER membership, complete Product
Brain V2 snapshot, private READY images, safe Research evidence and snapshot, successful Researcher,
Creative Strategist and Producer provenance, an approved CreativeConcept, Producer configuration,
media Tool versions and tenant permissions, and an unreviewed ProductionPlan. It is idempotent and
prints the exact Tenant ID and development credential. No database shell or provider credential is
needed. The narrower `make producer-bootstrap` and `make media-tools-bootstrap` commands remain
available for explicit development control-plane work.

## 4. Run free governed production

In another terminal:

```bash
MEDIA_IMAGE_PROVIDER=fake \
MEDIA_VIDEO_PROVIDER=fake \
make production-worker
```

This worker uses the real Temporal workflow, Tool Gateway, permission/idempotency/audit controls,
GenerationAuthority, binary validation, private Asset import, and lineage path. The only substituted
components are deterministic image/video providers; the UI labels their outputs `Local demo
provider`. Keep this worker running.

## 5. Open the Product Workspace

1. Open <http://localhost:3000>.
2. Paste the Tenant ID printed by `make demo-bootstrap`.
3. Paste its `local-demo|owner` credential.
4. Open `LOCAL DEMO Commuter Bottle`.
5. Inspect Research and its evidence.
6. Inspect Creatives and the approved concept.
7. Open Production and inspect the Producer run and plan.
8. Click **Approve & Generate**.
9. Watch each Job update independently through Ready, Starting, Generating, Importing, and Ready
   for use. If the worker is absent, Ready honestly says it is waiting for the production worker.
10. Preview the generated image and playable video through ordinary short-lived Asset grants.

One failed output does not hide a successful sibling. `Needs operational recovery` means external
effect or cost is uncertain and is never displayed as zero or automatically retried.

## 6. Open the graph in Obsidian

Choose or create a local Vault directory and use the exact values printed above:

```bash
export OBSIDIAN_VAULT_PATH="$HOME/Documents/Creative-Marketer-Vault"
export CM_API_BASE_URL="http://localhost:8000"
export CM_TENANT_ID="<printed Tenant ID>"
export CM_API_TOKEN="local-demo|owner"

make obsidian-setup
make obsidian-rebuild
make obsidian-watch
```

Open that Vault in Obsidian, then open `Creative Marketer.md` and `Production.md`. In Graph View,
search for the demo Product and verify Product → Research → Creative → ProductionPlan →
GenerationJob → Asset provenance. Job changes and generated Assets appear incrementally. Add text
under `## My Notes`, allow another sync, and verify it remains. Only one watch process may own a
Vault.

To enable browser deep links, set `NEXT_PUBLIC_OBSIDIAN_VAULT_NAME` to the Vault's display name before
building/starting the web app. **Open in Obsidian** then opens the exact stable Product,
ResearchSnapshot, CreativeConcept, AgentRun, ProductionPlan, Shot, Segment, Job, or Asset note.

## Manual acceptance checklist

- [ ] API liveness/readiness, UI, MinIO, Temporal, and worker are available.
- [ ] A second `make demo-bootstrap` reports/reuses the same Product/User/Agents.
- [ ] Production renders scenes, shots, segments, provider-neutral labels, and nonzero estimates.
- [ ] Approval creates separate image and video Jobs.
- [ ] Both fake outputs become private READY Assets and preview in the browser.
- [ ] Stopping/restarting the worker resumes a known video operation without a second start.
- [ ] Obsidian updates incrementally, keeps `My Notes`, and contains no key, prompt, object key, or
  signed URL.
- [ ] Browser deep links open exact notes.

This checklist is intentionally manual; automated validation does not claim that a GUI check ran.

## Optional billed providers

Only after the free path works, inject the provider secret into the production-worker process and
affirm spending:

```bash
ALLOW_BILLABLE_MEDIA=true \
MEDIA_IMAGE_PROVIDER=openai \
MEDIA_VIDEO_PROVIDER=byteplus \
OPENAI_API_KEY="<injected secret>" \
BYTEPLUS_LAS_API_KEY="<injected secret>" \
make production-worker
```

Do not place secrets in `.env` on shared machines, logs, Obsidian, browser storage, or command
history. Production/staging additionally require `MEDIA_WORKLOAD_ACTOR_ID` and
`MEDIA_WORKLOAD_ID`; credentials alone never enable spending.
