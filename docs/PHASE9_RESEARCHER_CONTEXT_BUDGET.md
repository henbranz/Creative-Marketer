# Phase 9: Researcher context-budget investigation

## Read-only findings (2026-09-25)

Preparation was reconstructed with the active Researcher configuration from PostgreSQL in a
repeatable-read, read-only transaction. No request endpoint, worker, provider or token-count API
was invoked. The fitter was exercised only in memory.

Exact failure: **NO_EVIDENCE_BLOCK_FITS**, not fixed-context overflow. The input allowance remains
10,000 (16,000 total minus 6,000 output). Ten candidate blocks are selected by canonical evidence
priority. The first is also the smallest: its complete serialized contribution is 309 bytes.

| Measured conservative bytes | Old revision 8 | Revision 11 | Delta |
|---|---:|---:|---:|
| Full snapshot content JSON | 7,894 | 8,134 | +240 |
| Full snapshot digest envelope JSON | 7,945 | 8,186 | +241 |
| Legacy compact provider Product context | 7,111 | 7,371 | +260 |
| Fixed bound | 9,615 | 9,875 | +260 |
| Fixed + first/smallest complete block | 9,924 | 10,184 | +260 |
| Complete blocks admitted by legacy fitter | 1 | 0 | -1 |

The extra envelope byte is revision 8 → 11. The compact representation previously omitted empty
`allowed_claims`; adding four claims introduces 242 bytes of list data plus its key/comma overhead,
260 bytes total. **Those four claims alone crossed the evidence boundary.** All other provider
sections are identical; no other Product Brain field grew. The Brief was already the main
baseline contributor, but did not cause this delta.

Section values below use the same compact UTF-8 JSON serialization as admission. Nested claim rows
are informational, already included in Profile; zero means absent from the compact representation.

| Section | Revision 8 | Revision 11 | Projected V2 (both) |
|---|---:|---:|---:|
| brand | 91 | 91 | 15 |
| brand_profile | 90 | 90 | 61 |
| product | 180 | 180 | 46 |
| profile | 30 | 290 | 0 |
| profile.allowed_claims (nested) | 0 | 242 | 0 |
| profile.prohibited_claims (nested) | 0 | 0 | 0 |
| brief | 6,337 | 6,337 | 3,574 |
| assets | 315 | 315 | 0 |
| Other top-level sections | 0 | 0 | 0 |
| Top-level keys/braces/commas | 68 | 68 | 47 |

System instructions contribute 555 serialized bytes, output schema 1,848, and the remaining
envelope/empty-evidence framing 101. These are conservative byte bounds, **not measured provider
token usage**. No provider was called to estimate tokens.

## Provenance discrepancy

The requested latest snapshot ID exists at revision 11:
`4117ec57-cfdc-4158-8104-cc80de810f43`.
Its stored, domain-verified canonical digest is:
`sha256:f5985c2a52e8a617a14fd811072ea373671d1a36c2f499f634509a229b0df138`.
The task quoted `sha256:cd5a6b2818e0ba444c266f82340c6aafa7832be50071b534bdc98338c49f2551`;
that is **not** this database snapshot's digest. No digest or data was rewritten to match it.

The old snapshot `08509eac-89b8-4c76-8a2e-d8b545e05116` has canonical digest
`sha256:94983a0ab0ead25eb92983968e8e1dc236aaa8902b751bd44bf0363a82cf3449`.
Read-only reconstruction of historical Researcher run `23ada293-6cab-49b5-9c3e-f7774133a85e`
successfully reproduced its original `researcher.v1` context digest after the code change.

## Repair and budget decision

ADR-045 documents the exact allowlist and exclusions. For **both** snapshots, provider Product
context is now 3,743 bytes; fixed context is **6,247**; with the first block **6,556**; with all
**10 complete blocks, 9,647**. No evidence block or included Product field is truncated.
The full original snapshot ID and canonical digest remain bound to every admitted run.

No AgentVersion, route, run budget or period budget changes. Using the frozen application price
snapshot (USD 4/M input and USD 20/M output), worst case remains:
10,000 × 4/M + 6,000 × 20/M = **USD 0.16/run**; 20 runs/day = **USD 3.20/day**.
Projection alone fixes the incident. Further research-specific growth fails with numeric section
diagnostics and requires explicit curation/design, not an automatic spend increase.

## Live-state safety

Session `9659a4af-402a-474c-bf16-b6ff5d9ebea9` retains `researcher_run_id=null`. Its idempotency
namespace has zero durable runs and zero ModelAttempts. All Researcher period reservation counts
reconcile exactly to durable runs (4, 1, 1 for the existing three period rows); the failed request
created no reservation. Admission precedes run insertion/reservation, and both are transactional.
There is no provider-started checkpoint, provider response, usage or cost for this session.
It can reuse `live-research-9659a4af-402a-474c-bf16-b6ff5d9ebea9` once corrected code is deployed;
this task does not submit it or authorize inference.

Before/after read-only row fingerprints match for AgentRuns, ModelAttempts, budget accounting,
Product snapshots/profiles/Briefs, Brand profiles, cycles, plans and GenerationJobs. The session
file hash is unchanged. Protected cycle `49b53abc-35fd-4305-9b7c-05951b377cfa` remains
`ACTIVE / AWAITING_PRODUCTION_APPROVAL`; protected plan
`aa1fa31c-df26-4421-b324-9cf0e664f8b5` still has **zero jobs**.

## Regression strategy

Tests reproduce the exact incident geometry using ASCII filler with measured serialized field
sizes, synthetic IDs and no Product/Evidence text. They cover V1 and V2, deterministic empty-field
compaction, large claim/asset growth, full snapshot identity/digest preservation, unchanged exact
Creative authority, all ten atomic blocks, first-block priority, both diagnostic branches,
content-free persisted denial audit, zero run/reservation/provider calls and reuse of the rejected
idempotency key. PostgreSQL tests also check durable V1/V2 context reconstruction and tamper refusal.

Local validation: full backend **1,027 passed, 1 skipped**, **95.12% coverage**; focused
Researcher/AgentRuntime/claim/live-validation selection **119 passed**; Temporal **46 passed**;
Ruff, formatting, mypy and generated contract drift checks passed. Database/S3 integration tests
used explicitly isolated test resources, not the live Product database. CI is recorded on the
resulting commit in GitHub Actions.
