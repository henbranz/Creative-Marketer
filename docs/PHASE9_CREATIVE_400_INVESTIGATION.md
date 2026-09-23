# Phase 9 — Creative Strategist request rejection investigation

Investigated 2026-09-23 against application HEAD `74a7ad12a28dc8227567ad9f3fc3add020bfe4ac`.
This is an investigation, not live acceptance or retry authorization.

## 1. First finding: failure classification B

Run `49e11f80-018a-487d-97b2-afd578add295` is `FAILED`, with
`MODEL_PROVIDER_BAD_REQUEST`, after the provider-start boundary. It is **not**
`MODEL_PROVIDER_SCHEMA_UNSUPPORTED` before that boundary.

| Field | Canonical value |
| --- | --- |
| ModelAttempt | `d5f46d55-be74-4d2c-9a68-806bab08a256` |
| Attempt status | `FAILED_NO_RESPONSE` |
| Workload | `local-live-agent-worker` |
| Provider / model | `openai` / `gpt-5.6-sol` |
| Route | `openai-gpt-5.6-sol-creative-2026-09-13` |
| Pricing | `openai-gpt-5.6-sol-2026-09-13` |
| Reasoning | `high` |
| Maximum output / total tokens | 8,000 / 32,000 |
| Model call count | 1 |
| Provider started (UTC) | `2026-09-23 17:23:30.173086+00` |
| Response recorded | NULL |
| Attempt finished (UTC) | `2026-09-23 17:23:31.720847+00` |
| Input / output / total usage | 0 / 0 / 0 |
| Estimated/actual recorded cost | USD 0.000000 |
| Attempt unknown cost | USD 0.000000 |
| Provider response ID | NULL |
| Predecessor | `497f1507-af38-4247-be15-1f28b0fbb560` |
| Successors of latest run | 0 |

The operator reported HTTP 400. Canonical storage cannot independently distinguish 400 from 422:
both map to `MODEL_PROVIDER_BAD_REQUEST`. The exact HTTP status, OpenAI error type/code/parameter,
request ID, and SDK exception class were not retained. They are **unavailable**, not null values
known to have been returned by OpenAI. No new attempt was made to recapture them.

## 2. Credential path and free access probe

`make agent-worker` runs python-dotenv against `../../.env` after entering `apps/api`, with
`--no-override`. `Settings()` also loads the repository-root `.env` using Pydantic Settings.
An existing process environment assignment takes precedence. Programmatic Settings overrides have
their separate existing hermetic behavior; the worker calls `Settings()` without such overrides.

Checks printed booleans only, never keys or fingerprints:

- Root key exists and is non-placeholder.
- No inherited `OPENAI_API_KEY` was present in the investigation shell.
- Settings credential equals the root dotenv credential.
- The adapter's SDK credential equals the Settings credential.
- Current backend is `openai`, workload `local-live-agent-worker`.
- No root or inherited `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, or `OPENAI_BASE_URL` override exists.
- SDK endpoint is the default official HTTPS endpoint.
- The worker's OpenAI branch constructs `OpenAIResponsesModelProvider` using that Settings secret.
  The Docker fake service's environment does not propagate into this local process. Explicit shell
  overrides can still change configuration; fake workload/backend identity checks remain in place.

`make live-provider-preflight` exited 0. It performed model-metadata GETs, not generation:
GPT-5.6 Sol and the configured Sunburst image model were accessible. BytePlus hostname/route checks
passed; BytePlus authentication remains deferred, and no video job was submitted.

This proves current credential authentication and model visibility, not inference permission for
every parameter combination. It does not identify the intended owning project or establish the
credential in the earlier worker process. Neither run stores credential fingerprints/project
identity, so credential equality between the successful Researcher and failed Creative execution
cannot be proven. Researcher used the historical `local-agent-worker` workload name; Creative used
`local-live-agent-worker`.

The installed OpenAI SDK is 2.54.0. Its status dispatcher makes 401 `AuthenticationError`, 400
`BadRequestError`, 403 `PermissionDeniedError`, and 404 `NotFoundError`. Real SDK mock-transport
tests verify 400 versus 401 domain mapping. Invalid credentials are therefore unlikely to explain
this failure, but cannot be historically ruled out solely from a current successful GET.

## 3. Frozen invocation reconstruction, with no provider network

Both exact run IDs were read with the runtime repository in a transaction explicitly set READ ONLY
and scoped to tenant `5fbff06c-0d69-5710-aa0c-9cd58688aef6`. Existing resolvers checked frozen
AgentVersion/Product/Research digests; historical route and pricing matched current installed
routes. The ordinary capability handler constructed each ModelInvocation. The ordinary adapter and
real installed SDK serialized it into `httpx.MockTransport`; no HTTP network transport existed.
Only structural summaries were printed. Prompts, Product context, and raw payloads were not saved.

These are reproducible reconstructions at the inspected HEAD, not independently recorded copies
of historical wire requests. Raw historical payloads, SDK version, process environment and schema
byte digests were not stored with ModelAttempt. Source display labels used by the Researcher
resolver are not a historical wire archive. Do not overstate byte-for-byte historical equivalence.

| Shape | Successful Researcher reconstruction | Failed Creative reconstruction |
| --- | --- | --- |
| Run | `23ada293-6cab-49b5-9c3e-f7774133a85e` | `49e11f80-018a-487d-97b2-afd578add295` |
| Provider/model | openai / gpt-5.6-sol | Same |
| Route suffix | research-2026-09-13 | creative-2026-09-13 |
| Pricing | openai-gpt-5.6-sol-2026-09-13 | Same |
| Reasoning | medium | high |
| Maximum output / total | 6,000 / 16,000 | 8,000 / 32,000 |
| Context kind | researcher.v1 | creative_strategy.v1 |
| Context shape | Product plus selected untrusted evidence blocks | Product claims, derived Research findings, Asset metadata and strategy request |
| Schema format name | research_research_snapshot_v2 | creative_creative_concept_set_v1 |
| Schema bytes, compact UTF-8 | 1,848 | 4,809 |
| SDK-serialized request bytes | 10,685 | 15,396 |
| Existing application input-token bound | 9,924 | 14,647 |
| Objects / declared properties | 4 / 17 | 9 / 47 |
| Reference occurrences | 0 | 7 |
| anyOf branches | 0 | 2 |
| Maximum container nesting | 5 | 7 |
| Nullable type arrays | 1 | 5 |
| Enum/const nodes without explicit type | 3 | 8 |
| Input messages / representation | 1 user message / string | Same |
| Images | None | None |
| Tools / store | [] / false | Same |
| Strict structured output | true | true |

Nesting counts object and array containers along the deepest expanded local-reference path;
`$defs` storage and `anyOf` syntax do not add artificial instance levels. The input-token estimates
are the application's conservative context estimates, not provider tokenizer measurements or the
entire serialized HTTP byte count.

Common keywords: `additionalProperties`, `enum`, `items`, `maxItems`, `maxLength`, `maximum`,
`minItems`, `minLength`, `minimum`, `pattern`, `properties`, `required`, `type`.
Researcher-only: `description`, `title`.
Creative-only: `$defs`, `$id`, `$ref`, `$schema`, `anyOf`, `const`, `format`.

Researcher's actual successful usage remains 2,086 input, 703 output, 2,789 total, USD 0.022404.

## 4. Request and schema compatibility

The Responses interface supports the emitted `model`, `instructions`, string-content user input,
`text.format` JSON-schema configuration, reasoning, maximum output, tools and storage fields.
No Chat Completions-style `response_format` is sent. Both names satisfy the documented character
and length constraint. `max_output_tokens` includes reasoning as well as visible output.
[Responses reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).

Sol documents Responses, Structured Outputs, high reasoning, a 1,050,000-token context and
128,000 maximum output tokens; 8,000 output is within that limit. Neither request supplies
temperature, tool-choice, service-tier, or mutually exclusive conversation options.
[Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

The Structured Outputs guide documents closed objects with every property required, nested anyOf,
local definitions/references, UUID format, pattern, numeric bounds, array size constraints, and
nullable type arrays. Creative meets the audited object and structural-size rules. Its alternative
branches have disjoint constant discriminators. Const values are mentioned in the guide's size
rules; this is not a guarantee for every const-only schema shape. `$id` and `$schema` acceptance is
not explicitly established by the guide. String-length constraints are explicitly excluded for
fine-tuned models; the base-model listing is less explicit, while Researcher's successful contract
already uses them. Treat these uncertainties as uncertainties, not reasons to delete constraints.
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

| OpenAI-routed contract | Bytes | Container depth | Objects | Refs | anyOf branches | Additional concerns |
| --- | --- | --- | --- | --- | --- | --- |
| Researcher v2 | 1,848 | 5 | 4 | 0 | 0 | Untyped enums, already present in successful request |
| Creative v1 | 4,809 | 7 | 9 | 7 | 2 | Metadata, const-only discriminators; server acceptance unverified |
| Producer v1 | 5,063 | 7 | 7 | 43 | 2 | Metadata, const-only nodes, uniqueItems |
| Intelligence v1 | 3,094 | 6 | 7 | 0 | 0 | Metadata, uniqueItems |
| Commerce v1 | 1,772 | 3 | 5 | 0 | 2 | Metadata, const-only discriminators |
| Supervisor v1 | 1,117 | 2 | 1 | 0 | 0 | Metadata, uniqueItems |

Legacy Researcher v1 also passes the existing local schema test. `uniqueItems` appears in three
other routes, not Creative, and is not explicitly guaranteed by the consulted subset guide.
It therefore cannot explain this Creative failure. No canonical schema was changed in this task.

The current validator checks a blacklist of composition keywords, root type, closed/fully-required
objects, basic anyOf shape and allowlisted formats. It is **not** a complete provider validator:
unknown keywords, metadata, type inference for enum/const, full reference semantics, aggregate
size/enum limits and every server-specific restriction are not verified. The new narrow gate adds
reference expansion/depth and property-count checks, request serialization, route/configuration and
the application token envelope. A PASS explicitly retains `provider_acceptance=UNVERIFIED` and
`retry_authorized=false`.

Local SDK request definitions are TypedDicts, not a validating model that reproduces server schema
validation. Successful mock serialization rules out a reproducible local serialization failure for
these reconstructed requests. It does not prove server acceptance. The stored status mapping is
consistent with server-side rejection after transmission, rather than local Python construction.

## 5. Root-cause decision table

| Hypothesis | Evidence for | Evidence against / limitation | Status |
| --- | --- | --- | --- |
| Invalid API key | Historical process credential not recorded | Current authenticated model GET succeeds; SDK maps invalid authentication to 401, not the stored bad-request category | UNLIKELY |
| Wrong OpenAI project | Project identity and old process not recorded | No current overrides; current model visibility passes; project identity is not established | UNKNOWN |
| Model inaccessible | Visibility alone is not complete inference authorization | Current model GET succeeds; same model previously generated Research | UNLIKELY |
| Invalid route/model name | No direct evidence | Frozen and installed model/route match, model lookup succeeds | UNLIKELY |
| Unsupported reasoning effort | Creative changes medium to high | High is documented for Sol | UNLIKELY |
| Unsupported output-token parameter | Creative increases 6,000 to 8,000 | Parameter is valid, within model limits; same parameter used by Researcher | UNLIKELY |
| Unsupported JSON Schema construct | Creative adds references, nested alternatives, const-only nodes and formats; old local check is incomplete | Most are documented; no exact provider diagnostic; no rejection proven for a specific construct | UNKNOWN |
| Unsupported schema metadata | Creative adds $id and $schema absent from Researcher | No direct rejection evidence or explicit guide statement banning them | UNKNOWN |
| Invalid text.format shape | HTTP request validation could reject format | Exact emitted shape matches interface and working Researcher structure | UNLIKELY |
| Request too large | Creative is larger | 15.4 KB, shallow schema and generous model context; application bound fits | UNLIKELY |
| Invalid input content shape | Different context content | Same one-string user-message representation; SDK serializes both | UNLIKELY |
| SDK/API version mismatch | Historical SDK/version provenance missing | Current SDK serializes all parameters; no identified incompatible field | UNLIKELY |
| Other provider validation / project policy | Status category is bad request; rejection details absent | No specific error code/param available | UNKNOWN |

Project policy cannot be dismissed solely because the status is 400: OpenAI documents service-tier
policy failures as 400, including when a default tier is resolved. No service tier was explicitly
sent here; this is a documented possibility, not evidence that it happened.
[OpenAI error guide](https://developers.openai.com/api/docs/guides/error-codes).

No hypothesis has enough direct evidence to be CONFIRMED. The highest-priority next evidence is the
existing failed request's safe error type/code/param/request ID from retained operator/provider
records, if available—not another generation. Do not supply full bodies, Product context, or keys.

## 6. Changes and validation

Implemented only diagnostics and the requested free gate:

- `openai_diagnostics.py`: bounded allowlisted status/type/code/param/class and request ID extraction;
  supports flattened and nested SDK error mappings. Unknown scalar values are redacted.
- Adapter emits sanitized operational diagnostics, compatible with both ordinary and safe structured
  logging; domain codes, retries, cost accounting and provider request construction are unchanged.
- `scripts.agent_request_preflight`: explicit tenant/run read-only inspection using mock transport.
- Regression tests cover real SDK 400/401 mapping, secret non-exposure, structured logging, exact
  Creative schema serialization, invalid request settings and configuration, and no claim/commit.
- ADR-043 and Creative documentation no longer assert that the earlier oneOf defect proved the
  historical HTTP-400 root cause. The live runbook describes the gate and its limitations.

Local validation: 170 focused provider/schema/Creative/runtime/recovery/live-validation tests
passed; 28 diagnostic/observability tests passed; 46 Temporal tests passed; full backend 941 passed,
1 skipped (local FFmpeg unavailable), coverage 95.10%. Ruff, format, mypy (340 source files),
contract drift check, frontend lint/typecheck and all 70 frontend tests passed. CI results and the
final source revision are reported in the task handoff. No smallest root-cause
fix was applied because the cause remains unproven. No dependency/model/provider route change.

## 7. Preserved live state

Session `c1185337-6dbd-431f-92f6-4fc22d996e40` and all four Creative lineage runs remain untouched.
The first attempt retains USD 0.256000 unresolved unknown cost. The fake-collision attempt retains
its immutable original USD 0.256000 unknown evidence and its existing reconciliation, leaving zero
remaining unknown. Both later known-no-response failures retain zero actual/unknown cost. No fifth
run, reconciliation, session reset, retry, or worker start occurred.

Protected cycle `49b53abc-35fd-4305-9b7c-05951b377cfa` remains
`ACTIVE / AWAITING_PRODUCTION_APPROVAL`; plan `aa1fa31c-df26-4421-b324-9cf0e664f8b5` still exists,
with zero GenerationJobs. Inspection used SELECT-only read-only transactions and rollback.

No paid inference, real Responses generation, image generation, Seedance call, or full live E2E ran.

ROOT CAUSE NOT YET PROVEN — DO NOT RETRY
