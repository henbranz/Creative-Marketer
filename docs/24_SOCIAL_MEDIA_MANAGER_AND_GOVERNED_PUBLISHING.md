# Social Media Manager and Governed Publishing

## Scope

Phase 5 adds an organic social publishing bounded context. It prepares an exact publication proposal, binds a human decision to that exact side effect, schedules durable work, submits only through the governed tool boundary, and records the confirmed external result as an immutable fact. Real Meta, Instagram, and TikTok integrations remain deliberately unavailable.

The implemented provider is `FakeSocialProvider`. Its links use the reserved `.invalid` domain, it performs no network I/O, requires no credential, and cannot incur provider cost.

## Canonical lifecycle

```text
approved FinalCreative
  -> immutable PublicationDraft
  -> exact human decision
  -> PublicationJob
  -> PublicationWorkflow (IDs only)
  -> Tool Gateway / social.publish.*
  -> FakeSocialProvider
  -> immutable Publication
```

PostgreSQL remains the source of truth. Temporal is an orchestration coordinator. Knowledge Graph and Obsidian records are derived, safe projections.

## Records and invariants

- `SocialAccount` contains safe destination metadata and lifecycle state. Credentials never enter this record. Phase 5 constrains its provider to `fake` in both domain and database checks.
- `PublicationDraft` is immutable and binds tenant, Product, current approved FinalCreative and digest, output Asset and digest, platform, account, external destination, caption, hashtags, settings, mode, and UTC schedule.
- `PublicationDecision` is immutable. Its action digest changes when any material publication value changes.
- `PublicationJob` is the controlled mutable execution state. `OUTCOME_UNKNOWN` blocks a blind resubmit and permits reconciliation only.
- `Publication` is an immutable confirmed-publish fact. A unique tenant/draft relationship prevents duplicate facts.

All publishing tables use tenant-scoped composite relationships, PostgreSQL row-level security, and `FORCE ROW LEVEL SECURITY`.

## Responsibilities

Deterministic application services own validation, authorization, approval, scheduling, capability checks, execution state, idempotency, reconciliation, audit, events, and persistence. No model is needed to publish. A future Social Media Manager Agent may propose bounded copy, but it must never receive credentials or invoke a provider directly. If added, its reasoning route remains GPT-5.6 Sol behind the existing model abstraction.

## Tool and approval boundary

The registered contracts are:

- `social.publish.submit`: R4 external mutation, required idempotency.
- `social.publish.status`: R1 reconciliation of a known operation.
- `social.publish.cancel`: R4 external mutation, required idempotency.

Only `publication_draft_id` crosses the tool input boundary. Trusted adapters resolve current tenant ownership, account state, FinalCreative approval, exact digests, capability, permission, and approval before provider I/O. Durable execution intent is committed first. Any real connector added later must resolve credentials inside the connector boundary; raw tokens are forbidden in drafts, agent context, events, audit metadata, UI, and Obsidian.

The local HTTP execute route is available only in development/test and can address only `FakeSocialProvider`. It is a fake walkthrough convenience, not a production external-effect path.

## Scheduling and failure handling

`PublicationWorkflow` receives tenant and draft identifiers plus bounded timing values. It waits durably for scheduled work, supports cancellation before submit, submits once, and reconciles delayed or ambiguous results within a bounded deadline. Workflow payloads contain no caption, media, credential, or provider response.

Provider failure becomes a stable safe code. An ambiguous response becomes `OUTCOME_UNKNOWN`; it is never automatically treated as failure and never blindly submitted again. Fake reconciliation is deterministic even after process restart.

## UI and human knowledge

The Product workspace exposes a Published tab. `Prepare publication` appears only on a current FinalCreative approved for publishing. The composer shows the exact platform, destination account, creative, caption, hashtags, and local-time schedule before approval. It also states that the provider is fake, live posting is disabled, and cost is not applicable.

Knowledge projection adds SocialAccount, PublicationDraft, PublicationDecision, and Publication nodes with relationships back to FinalCreative, Asset, and Product. Obsidian receives generated notes under `Publishing/` and a `Publishing.md` MOC. Credentials and raw provider responses are never projected.

## Deferred work

OAuth, real access tokens, real platform APIs, paid campaigns, performance ingestion, attribution, and automated optimization are outside Phase 5. Activating any real connector requires a new ADR, production connector implementation, credential-vault integration, platform-specific conformance tests, and an explicit acceptance gate.
