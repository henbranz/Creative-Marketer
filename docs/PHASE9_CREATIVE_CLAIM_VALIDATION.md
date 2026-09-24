# Phase 9 — Creative post-provider claim validation

## Verified evidence and limits

Run `1703ed43-1c86-4472-b396-fce6b7981186` failed with
`INVALID_CREATIVE_CLAIM_REFERENCE` after attempt `77ae09b0-20fc-461e-90eb-b9c80b986445`
received response `resp_0bfac1c984375070016ab43288d86087d288ec5f6fc306a010`.
The attempt is `SUCCEEDED`: this means provider response/usage received, not domain acceptance.
Input/output/total usage is 2866 / 7936 / 10802; known cost is USD 0.170184.

Read-only PostgreSQL inspection identified the exact bound Product snapshot:
`08509eac-89b8-4c76-8a2e-d8b545e05116`, digest
`sha256:94983a0ab0ead25eb92983968e8e1dc236aaa8902b751bd44bf0363a82cf3449`.
The Catalog canonical envelope (schema version, source revision, content) reproduces that digest.
Both `brand_profile.allowed_claims` and `profile.allowed_claims` are canonical empty arrays.
Recomputing ProductClaimRefs therefore produces the exact allowed identity set **`[]`**.
The snapshot also has zero required disclaimers. Research binds snapshot
`5de7f285-e3a8-4211-8815-30dd477ba117`; Research is not Product claim authority.

The old error code covered three branches: invalid/missing Product-fact references, references on
non-fact points, and missing disclaimers. The frozen context rules out the disclaimer branch. The
returned output therefore either used a `PRODUCT_FACT` without any authorized claim identity or
attached a non-null reference to a non-fact. No valid claim identity existed to normalize.

**The exact returned reference(s) and which of those two branches fired cannot be recovered from
the retained evidence.** Rejected structured output was not persisted; AgentRun/ModelAttempt
checkpoints retained response metadata/usage only, and Audit retained the generic failure code.
The adapter used `store:false`. One read-only GET for this existing response returned HTTP 404.
No generation or input-token count request was made. Official OpenAI documentation describes
[`store:false` as disabling response storage](https://developers.openai.com/api/docs/guides/migrate-to-responses).
An operator-supplied original response export could establish the exact offending value without a
retry. It is not honest to label the missing value a hallucinated hash or formatting error.

## Confirmed contract gap and safe fix

The old task instructed the model to use supported claims, but did not explicitly define the
hash-key relationship, non-fact null rule, or the empty-authority case. The generic output schema
allowed every message kind and any nullable string reference regardless of the bound claim set.
This is a confirmed prompt/generation-contract versus validator gap. The snapshot representation
is canonical; the validator correctly denied unsupported authority. We do not promote benefits,
descriptions, or Research findings to allowed claims, nor alter existing Product data to make a run
pass.

Changes:

- Explicit runtime claim-binding task guidance: copy exact allowed keys; no text/field paths or
  invented references; no Product-fact points with an empty list; no factual assertion relabeling.
- An invocation-local schema narrows message-point kind/reference pairs to frozen authority.
  Non-facts require null. Facts require a key enumerated from that exact snapshot. With no keys,
  only non-facts are allowed. The same schema feeds the conservative input-budget estimator.
- The canonical JSON file, key derivation, exact matching, and whole-set validation remain intact.
  Schema adherence is not trusted; a deliberately invalid mocked provider response still fails.
- Bounded, redacted `creative.claim_validation.failed` Audit evidence commits with failure/cost
  settlement. No ordinary logs, full response retention, schema migration, or historical backfill.
- Tests cover exact IDs, altered-case/abbreviated/text references, fabricated IDs, null references,
  empty authority, non-fact misuse, disclaimers, wrong-snapshot IDs, whole-set failure, diagnostic
  size/redaction, and real-SDK/mock-HTTP runtime execution with live-style usage and costs.

No normalization is intended: deterministic formatting comparisons classify a mismatch but do not
accept it. Semantic truth of arbitrary creative prose is not proven by reference validation;
human content review remains required.

## Validation and retry boundary

Local validation: 997 backend tests passed, one skipped, 95.11% coverage; all 46 isolated Temporal
tests passed. Ruff, formatting, mypy, and generated API contract drift checks passed. The 18 added
regression cases include four live-style runtime cases using mocked HTTP, not live inference.
Commit/push is operator-authorized; the final SHA and CI results are reported in the task handoff.
That authorization does not permit a live retry or creation/approval of Product claims.

All provider tests use fake output or a mandatory in-memory HTTP transport. Backend integration
tests use a brand-new empty database `creative_marketer_claim_fix_test_20260923`, not the user's
application database or any previous test database. No application-data reset, deletion, Product
edit, new AgentRun, reconciliation, worker restart, protected-cycle operation, or protected-plan
operation was performed. Existing failed-run usage/cost/history remain unchanged.

A controlled live Creative-only run is optional after review and explicit approval, not necessary
to run the deterministic regression suite. The new bound schema has offline SDK validation, not a
fresh server-acceptance claim. With the current snapshot, output must avoid Product facts. If factual
selling points are required, the user must first explicitly approve claims through the normal
Product editing/snapshot workflow (and refresh bound Research as required); never rewrite the old
snapshot. Any paid retry needs separate approval, the existing spend ceiling, and no media,
publishing, protected-cycle, or protected-plan actions. This investigation authorizes none.
