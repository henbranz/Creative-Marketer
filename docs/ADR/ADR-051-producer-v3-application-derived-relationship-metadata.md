# ADR-051 — Producer v3 and application-derived relationship metadata

Status: Accepted

## Context

A live `production.production_plan.v2` response completed successfully at OpenAI and retained exact
usage and cost, but deterministic domain validation rejected it with `SHOT_ORDINAL_INVALID`. The
second scene contained one shot, so its required local ordinal was `1`; because raw provider output
was not retained, the diagnostic proves only that the model emitted a different positive integer.
The model had been asked to repeat metadata that trusted code already knew from frozen Creative
authority and array position:
scene identity/order, shot order, and the shot's parent scene. Prompt instructions cannot make those
redundant values authoritative.

The v2 schema and validator correctly failed closed. Weakening ordinal or parent validation would
allow ambiguous plans, so the ownership error must be removed at the provider contract boundary.

## Decision

`production.production_plan.v3` is the current immutable provider output contract. Its ordered
`scenes` and nested ordered `shots` remain model output, but it excludes:

- `scene.scene_key`
- `scene.ordinal`
- `shot.scene_key`
- `shot.ordinal`

After strict schema validation, application code requires the scene count to equal the frozen
`concept_scene_keys` count and requires every scene to contain at least one shot. It materializes:

- scene ordinal as scene array index plus one;
- scene key as `concept_scene_keys[index]`;
- shot ordinal as shot array index plus one;
- shot parent scene key as the materialized parent key.

Array order is preserved. A count mismatch, empty shot list, parse error, or unresolved frozen
binding fails closed. The model continues to own creative decisions such as scene content, shot
content, source strategy, shot keys, segment membership, and generation specifications. Existing
Asset authorization, source-strategy shape, cross-reference, duration/count, pricing,
provider-neutrality, and digest checks remain unchanged.

Provider contract version and persistence schema version are intentionally distinct. AgentRun
provenance records output contract v3. Trusted materialization produces the unchanged internal
`ProductionPlan` schema v2, including keys and ordinals, so API, database, assembly, generation,
knowledge projection, and UI contracts do not change. The existing database constraint allowing
internal schema versions 1 and 2 remains correct.

New Producer AgentVersions use prompt revision `producer_v6_contract_v3`. Historical provider
contracts v1/v2, AgentVersions, runs, responses, diagnostics, usage, and cost remain immutable and
readable. `SCENE_ORDINAL_INVALID`, `SHOT_ORDINAL_INVALID`, and `SHOT_SCENE_MISMATCH` remain valid
historical diagnostics but cannot be caused by v3 provider fields because those fields no longer
exist.

The explicit Producer replacement operation is a contract-upgrade continuation, not a retry. It
admits at most one pending successor when the predecessor is terminal failed, has exactly one
authoritative completed response with known usage/cost and zero unknown cost, has no materialized
plan, has an eligible contract-validation failure, retains valid current provenance, and the active
Producer has a strictly newer supported contract and current prompt revision. V1
`MODEL_INVALID_OUTPUT` and v2-or-later `PRODUCTION_PLAN_INVALID` predecessors are recognized. The
database trigger, unique lineage constraint, idempotency, active-run admission, and application
checks all remain fail-closed. Creation never starts provider execution.

## Repository-wide deterministic metadata audit

Classification definitions:

- `MODEL_DECISION`: substantive selection or naming owned by the model.
- `PROVIDER_STRUCTURAL_OUTPUT`: structure needed to express model decisions.
- `APPLICATION_DERIVED`: exactly computable by trusted code and therefore not model-owned.
- `FROZEN_AUTHORITY`: an immutable input identity the model may select but must match exactly.

| Contract | Field family | Classification | Result / risk |
|---|---|---|---|
| Research v1/v2 | finding key | `MODEL_DECISION` | Stable semantic label used by later exact references; retain. |
| Research v1/v2 | citation snapshot ID, block index, block digest | `FROZEN_AUTHORITY` | Exact manifest membership is validated; retain. |
| Creative v1 | concept/message-point keys | `MODEL_DECISION` | Intra-output semantic labels; uniqueness/reference checks retain them. |
| Creative v1 | research, Product-claim, and existing-Asset refs | `FROZEN_AUTHORITY` | Exact allowlist validation is required; retain. |
| Creative v1 | scene ordinal | `APPLICATION_DERIVED` | Redundant and a future contract-hardening candidate; no unrelated redesign in this ADR. |
| Producer v1/v2 | scene key/ordinal and shot parent/ordinal | `APPLICATION_DERIVED` | Removed from current v3 and deterministically materialized. |
| Producer v3 | shot/segment keys and segment shot membership | `MODEL_DECISION` + `PROVIDER_STRUCTURAL_OUTPUT` | Keys express model-chosen grouping and are validated globally; retain. |
| Producer v3 | existing/reference Asset IDs | `FROZEN_AUTHORITY` | Exact selected-Asset membership is validated; retain. |
| Intelligence v1 | source/comparison IDs | `FROZEN_AUTHORITY` | Exact context-manifest membership is validated; retain. |
| Intelligence v1 | candidate indexes and scope dimensions | `MODEL_DECISION` + `PROVIDER_STRUCTURAL_OUTPUT` | They express which insight/experiment to advance; retain with bounds. |
| Commerce v1 | observation and external order/variant IDs | `FROZEN_AUTHORITY` | Exact observed authority is revalidated before any action; retain. |
| Commerce v1 | quantities, amounts, and action choice | `MODEL_DECISION` | Human approval and deterministic execution revalidation remain mandatory. |
| Supervisor v1 | suggested next actions | `MODEL_DECISION` | Finite enum additionally constrained by deterministic readiness; retain. |

The actionable unrelated risk is Creative scene ordinal, which repeats array position and can fail
for the same class of reason. It should receive its own versioned contract change before another
live acceptance that depends on model-authored ordinals. Research/Creative semantic keys and
Producer shot/segment keys are not blindly derivable without first changing their reference model;
they must not be silently rewritten.

## Consequences

The provider is no longer asked to reproduce deterministic Producer relationship metadata. The
canonical v3 schema is already a complete strict object graph and therefore needs only the systemic
OpenAI compiler's normal keyword normalization; the v2 identity-specific alternative expansion is
retained only for historical v2. All installed-contract audit and admission gates include v3.

The Provider may still return a semantically invalid plan, and those failures remain visible and
bounded. This change removes only impossible-to-justify model ownership; it does not accept
fabricated references or weaken governance.
