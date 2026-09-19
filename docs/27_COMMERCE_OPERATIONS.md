# Commerce Operations

Commerce is a bounded context in the modular monolith. It separates external observation from internal intent and external mutation.

## Truth and authority

`CommerceReadProvider` collects catalog, inventory, and order facts without mutation authority. Normalized observations are immutable, privacy-minimal, tenant-owned, and deduplicated by semantic source digest. A changed provider fact creates a new observation. Current state is a projection over those facts.

`CommerceMutationProvider` is a separate port. The Commerce Operations Agent never receives this port or credentials. Inventory and refund execution is available only to an internal workload through immutable Tool Registry versions, the Permission Engine, exact Approval binding, idempotency, Tool Gateway, audit, and reconciliation.

V1 ships only `FakeCommerceProvider`. It makes no network calls, identifies every store and fact as fake, supports deterministic pages/cursors, failure, unknown outcome, idempotent mutation, and status reconciliation. No Shopify connector or commerce credential configuration exists.

## Product mapping and privacy

`ProductCommerceMapping` is explicit and user-confirmed. Product names are never used to infer a mapping. External commerce catalog observations do not replace Product Brain truth.

Orders retain only safe identifiers, Decimal totals/currency, state, timestamps, privacy-minimal lines, and an optional Creative Marketer attribution code. Customer names, email, phone, addresses, IP data, cards, payment tokens, and tracking numbers are prohibited by architecture tests and absent from schemas.

## Deterministic rules

Inventory rules are `commerce-inventory-rules-v1`: `available <= 0` is `OUT_OF_STOCK`; `1..5` is `LOW_STOCK`; `None` is `UNAVAILABLE`, never zero. Order rules are `commerce-order-rules-v1`: paid/unfulfilled, failed payment, and partial refund are the only V1 exceptions.

Inventory mutation uses one canonical semantic: `SET_AVAILABLE_TO`. It binds connection, product, variant, observed evidence, and exact non-negative quantity. It is R5.

Refund binds connection, order, exact positive Decimal amount, and the order currency. Deterministic validation rejects unpaid orders, currency mismatch, and amounts above the currently refundable observed amount. It is always R6 and never autonomous.

## Agent and context

`commerce_operations` uses the common AgentRuntime and the immutable `commerce_operations` route (`gpt-5.6-sol`, high reasoning). It has one structured model call, zero tool calls, no web, connectors, or memory. `CommerceOperationsContextManifest` binds the ProductKnowledgeSnapshot, explicit mapping, exact observation IDs/digests, deterministic exceptions, and one semantic digest. Raw provider payloads, PII, and credentials are excluded.

Provider output is validated by JSON Schema and then by deterministic code. Unknown order/store/SKU IDs, unsupported amounts/currencies, and ungrounded inventory quantities are rejected. Canonical proposals remain internal intent until exact approval and external reconciliation.

## Measurement boundary and refunds

A newly observed `PAID` order with a valid tenant attribution reference is handed to `MeasurementService.ingest_commerce_conversion`. Commerce repositories never write measurement tables. Duplicate order observations reuse the existing conversion identity and cannot duplicate revenue. Created, authorized, failed, or unattributed orders do not become attributed purchase conversions.

V1 selects the conservative deferred reversal policy (option B). The original purchase observation is never overwritten. Refunded states do not create new purchases; automatic historical revenue reversal remains deferred until measurement gains a first-class immutable reversal fact. This limitation is displayed in agent context and documentation.

## Unknown outcomes

Mutation intent is persisted before provider I/O by Tool Gateway. `OUTCOME_UNKNOWN` is terminal for blind retry: an operator/workflow must call `commerce.operation.status` and reconcile the provider operation first. The provider idempotency key prevents duplicate fake refunds and inventory operations.

## Production Worker Composition

Commerce has a dedicated Temporal task queue and worker. The worker registers only
`CommerceSyncWorkflow`, `CommerceActionWorkflow`, and their Commerce activities; it has no model,
media, assembly, or publishing authority. Development/test derives a stable local workload actor.
Staging and production require deployment-issued `COMMERCE_WORKLOAD_ACTOR_ID` and
`COMMERCE_WORKLOAD_ID`, and reject missing or `local-*` workload IDs. These values come only from
the repository-root `.env`/deployment environment and are never sent to the browser.

The executing workload and initiating user are deliberately different principals. A third,
non-model-routed `commerce_execution_workload` Agent Registry principal supplies the narrow Tool
Gateway policy; the AI Commerce Operations Agent remains proposal-only with zero allowed tools. The
deployment workload identity is recorded on the action job, while the immutable ToolCall and
ApprovalRequest preserve the initiating user, tenant, requested execution AgentDefinition, exact ToolDefinition/ToolVersion,
operation, resource, normalized identifier-only input, proposal digest, and correlation ID. The
opaque `tool-request://<uuid>` reference contains none of those values.

Temporal carries only tenant, proposal/sync request, correlation, and opaque request identifiers.
Before any action, the worker resolves the ToolCall from PostgreSQL, reloads the user, membership,
tenant and active Commerce Agent, reconstructs only
`{"commerce_action_proposal_id":"<uuid>"}`, and invokes Tool Gateway. Tool Gateway then re-resolves
the exact active tool version, permission, approval binding, authorization snapshot, resource scope,
and idempotency record. A revoked user or membership, inactive tenant/agent, tenant mismatch,
changed proposal digest, changed policy/version, or malformed reference fails before provider I/O.

The request API uses Tool Gateway's request-only mode: it may create the exact R5/R6 approval and
durable ToolCall but cannot run an executor in the API process. Approval is a wake-up signal, not
authority; the worker re-reads PostgreSQL and executes through Tool Gateway. Rejection produces no
provider call. `APPROVED`, `SUBMITTING`, `OUTCOME_UNKNOWN`, `SUCCEEDED`, and `FAILED` are separate
states derived from canonical approval, ToolCall, action job, and result records.

An ambiguous mutation is submitted once. Temporal switches to bounded, read-only
`commerce.operation.status` reconciliation and never invokes the mutation tool again. Fake-provider
operation IDs are derived from immutable mutation material, so a restarted worker can validate and
reconstruct the minimum unknown operation state before status reconciliation. Confirmed fake effects
are projected into immutable Commerce observations and can be rehydrated for later fake syncs.

Manual sync creates a durable, tenant-owned sync request with the initiating user and a caller
idempotency key, then starts `CommerceSyncWorkflow` on the dedicated queue. The API returns `QUEUED`
without paging the provider. The worker reloads current identity/tenant authority and the canonical
connection target before finite cursor-based sync. The workspace exposes only the safe sync state.

## Local walkthrough

Run migrations, then:

```bash
make commerce-tools-bootstrap
make commerce-agent-bootstrap
make commerce-demo-bootstrap
make temporal-up
make commerce-worker
```

The commands use only the repository-root `.env`. The agent/demo helpers require the existing
`BOOTSTRAP_TENANT_ID` and `BOOTSTRAP_USER_ID`; Tool Registry bootstrap additionally requires
`BOOTSTRAP_PLATFORM_ACTOR_ID` and the privileged `MIGRATION_DATABASE_URL` because runtime database
authority cannot mutate platform Tool Registry state. No app- or worker-specific dotenv file is
created.

The demo creates `Fake Store`, catalog, low/out-of-stock inventory, paid/unfulfilled and failed-payment orders. It deliberately does not map any existing Product. Map a fixture explicitly in the Commerce workspace/API, sync finite pages, and use **Analyze commerce** with the configured fake model provider. No real commerce or paid provider call is required.

## Future Shopify integration

A future connector must implement the existing read and mutation ports, keep credentials in connector infrastructure, and be designed against then-current official Admin API, OAuth, webhook, rate-limit, and idempotency documentation. It must not weaken R5/R6 controls or expose raw provider payloads.
