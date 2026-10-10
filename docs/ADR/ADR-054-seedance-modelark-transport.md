# ADR-054 — Seedance uses the BytePlus ModelArk task API

Status: Accepted

## Context

The active Seedance 2.5 adapter used the historical BytePlus LAS origin and credential names even
though the authoritative product contract is now ModelArk. Production already persists a provider
task reference and drives asynchronous polling from `GenerationJob`; replacing that durable state
machine would risk duplicate paid work. Existing approved jobs also bind the historical route and
must remain resumable without rewriting approvals, jobs, or reservations.

## Decision

- New `production_video` jobs bind provider `byteplus_modelark`, route
  `byteplus-modelark-seedance-2.5-2026-10-10`, and model
  `dreamina-seedance-2-5-260628`.
- The infrastructure adapter uses Bearer authentication and the regional ModelArk
  `/api/v3/contents/generations/tasks` create/retrieve contract. The root environment accepts
  `BYTEPLUS_ARK_API_KEY` or `ARK_API_KEY` and `BYTEPLUS_MODELARK_BASE_URL`; LAS configuration is
  removed from the active path.
- ModelArk task IDs remain in the existing immutable `provider_operation_ref` field. `PROCESSING`
  remains the durable polling state, and the successful `content.video_url` is immediately imported
  into private object storage.
- Activation, credit, request-contract, transient, and ambiguous-start failures map to bounded
  application codes. Provider bodies and messages are never persisted or logged.
- The exact historical LAS route/provider tuple remains an allowlisted execution-compatible route.
  It is resolved only when both the existing Job and its existing approval bind that exact tuple.
  Execution uses the ModelArk adapter—never a LAS endpoint or key. No record is rewritten and no
  new Job or reservation is created.
- A start with no authoritative HTTP/task response remains `OUTCOME_UNKNOWN` and is never retried
  automatically. Polling and result-download transport failures are read-only pre-effect failures;
  the persisted task ID remains resumable.
- A legacy or crash-stranded `STARTING` Job with no provider task ID converges to
  `OUTCOME_UNKNOWN` on its next governed execution. Absence of an ID is not treated as proof that
  the old request never crossed the provider boundary, so ModelArk is not called and no duplicate
  reservation or generation is created.

## Consequences

New jobs have truthful ModelArk provenance while approved historical jobs can continue under their
original immutable binding. Supporting both exact identities adds a narrow compatibility table but
does not weaken provider/model/pricing checks. ModelArk output URLs are temporary, so successful
results still require immediate validated import.
