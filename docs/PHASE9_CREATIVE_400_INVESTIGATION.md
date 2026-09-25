# Phase 9 — Creative Strategist request rejection investigation

**Current update:** the count-only delta pass below confirms the Creative `const` representation
defect and a separate Producer `uniqueItems` defect through A/B/A evidence. The original investigation
is preserved as historical evidence; its UNKNOWN classifications describe knowledge at that time.
See [section 8](#8-count-only-schema-delta-resolution-2026-09-23) for the current conclusion.

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

## 8. Count-only schema delta resolution (2026-09-23)

Starting HEAD: `ac9bfd90d06895c58b5c8ec3943a56f77526f970`. Locked SDK: OpenAI Python 2.54.0,
unchanged. Exactly **16 POST `/v1/responses/input_tokens` requests** in this delta task, including
controls and all-six-agent verification. The earlier Diagnostic 0 call is separate; its safe request
ID was `req_26451b8247d342bfaa6102c0c2eb8176`. No POST `/v1/responses` request, generation,
AgentRun, worker start, Temporal operation, recovery, or reconciliation occurred.

All experimental schemas were deep copies held in memory. Nothing experimental was written into
canonical schema files. Every delta used identical Creative synthetic instructions/input, model,
reasoning, contract name, strict format, and empty tools; only the specified schema family changed.
The count endpoint omits create-only `store` and `max_output_tokens`. SDK retries and redirects were
disabled, network requests were restricted to the official count endpoint, and a durable local
safe-field ledger enforced a maximum of 20 attempted requests. All 16 received results. No further
count request is needed for this task.

### Delta table and safe diagnostics

Every rejected row below returned the same bounded diagnostic tuple:

- HTTP status: `400`
- error type: `invalid_request_error`
- error code: `invalid_json_schema`
- param: `text.format.schema`
- SDK exception class: `BadRequestError`

Only request IDs differ. No provider message, raw body, request body, schema contents, headers,
credentials, or live Product/Research context is retained here.

| Call | Variant | Exact change | Result | Safe request ID |
| --- | --- | --- | --- | --- |
| 1 | A_MINIMAL | Requested minimal closed object control | ACCEPTED, 90 tokens | — |
| 2 | B_CANONICAL | Unchanged full Creative schema | REJECTED, tuple above | `req_cff0a63d6e0a4b7ba2fb50edb4fa8b40` |
| 3 | C1_REMOVE_SCHEMA | Remove only root `$schema` | REJECTED, tuple above | `req_31a7def0557f4f56a86e3ffdd715e0d3` |
| 4 | C2_REMOVE_ID | Remove only root `$id` | REJECTED, tuple above | `req_25ecff1f51f544cbb3646f548769fba8` |
| 5 | C3_REMOVE_BOTH | Remove only root `$schema` and `$id` | REJECTED, tuple above | `req_802fafe001cc42c7bd16c789b01b1365` |
| 6 | CONST_TO_SINGLE_ENUM | Replace the three string `const` nodes with equivalent singleton `enum` nodes | ACCEPTED, 728 tokens | — |
| 7 | CONST_TO_SINGLE_ENUM_RESTORED | Restore only those three `const` nodes | REJECTED, tuple above | `req_1790773202b14a3782047f8cc6bfeba8` |
| 8 | AGENT_RESEARCHER | Researcher provider schema | ACCEPTED, 305 tokens | — |
| 9 | AGENT_CREATIVE_STRATEGIST | Creative provider schema with const normalization | ACCEPTED, 728 tokens | — |
| 10 | AGENT_PRODUCER | Producer provider schema with const normalization | REJECTED, tuple above | `req_5b83c818d6b946f19c0be717dac550fc` |
| 11 | PRODUCER_REMOVE_UNIQUE_ITEMS | Relative to call 10, remove only `uniqueItems` | ACCEPTED, 926 tokens | — |
| 12 | PRODUCER_UNIQUE_ITEMS_RESTORED | Restore only `uniqueItems` to call 11 | REJECTED, tuple above | `req_f0b542e972104382b8085db2e32776db` |
| 13 | AGENT_PRODUCER_FINAL | Producer with both proven provider normalizations | ACCEPTED, 926 tokens | — |
| 14 | AGENT_INTELLIGENCE | Intelligence with final provider normalization | ACCEPTED, 433 tokens | — |
| 15 | AGENT_COMMERCE_OPERATIONS | Commerce Operations with final provider normalization | ACCEPTED, 273 tokens | — |
| 16 | AGENT_SUPERVISOR | Supervisor with final provider normalization | ACCEPTED, 251 tokens | — |

### Exact conclusion and causality

Calls **2 → 6 → 7** isolate Creative's three **string `const`-only schema nodes**: rejected →
equivalent singleton enums accepted → const restored and rejected. Metadata, references, union
placement, UUID format, nullability, and other constraints stayed unchanged in that comparison.
The existing asset discriminator alternatives remain disjoint. This proves the current exact
Creative representation defect, not that all possible typed-const variants are unsupported, nor
recovery of historical provider messages. Later keyword-family experiments/ddmin were unnecessary
once this A/B/A was complete.

The six-agent sweep did not hide Producer's rejection. Calls **10 → 11 → 12** isolate its separate
`uniqueItems` incompatibility after const normalization. The final Producer recheck passed. The
other agent schemas sharing that keyword passed with the same final normalization.

### Provider documentation comparison

Official [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs)
requires an object root, no root anyOf, closed objects, and all declared properties required. It
supports nested anyOf and definitions/references; UUID format and nullable types are documented.
These constructs were preserved. The guide mentions const values in size accounting, which does
not establish support for these exact const-only nodes. `uniqueItems` is not promised in the listed
array constraints. Direct A/B/A provider evidence, not an inference from omission in the guide,
justifies both narrow transformations.

The [counting guide](https://developers.openai.com/api/docs/guides/token-counting) documents counting
before model generation. No explicit separate counting fee was established; these requests are
**not claimed to be free**. Count acceptance does not prove that Responses generation will succeed.

### Implemented boundary and unchanged semantics

`normalize_openai_strict_output_schema` makes a separate provider-facing copy, converts const to
singleton enum, removes provider-facing uniqueItems, and validates that normalized result. It is
idempotent and only visits schema positions, not literal payloads. Conflicting const-plus-enum is
rejected rather than silently overwriting a constraint. The compatibility validator rejects raw
const/uniqueItems. Both pre-provider-start validation and direct adapter execution normalize; the
offline request preflight reports the actual normalized schema shape.

No canonical JSON file or application/domain validator changed. Singleton enum preserves the
constant invariant exactly. Uniqueness remains in canonical post-response validation; duplicate
output fails before accepted capability output is persisted. Regression tests cover original
Creative const shapes, enum equivalence, unchanged unrelated keywords, literal-data traversal,
normalization idempotence, all agent schemas, emitted SDK parameters, and runtime duplicate rejection.
Provider diagnostics, immutable run/attempt semantics, historical costs, routing, and retries remain
unchanged. No dependency upgrade.

### Protected state and validation

Read-only before/after checks confirm cycle `49b53abc-35fd-4305-9b7c-05951b377cfa` remains
`ACTIVE / AWAITING_PRODUCTION_APPROVAL`; ProductionPlan `aa1fa31c-df26-4421-b324-9cf0e664f8b5`
exists with zero GenerationJobs; the Product still has four Creative runs. Session
`c1185337-6dbd-431f-92f6-4fc22d996e40` is untouched. No live retry is authorized by this report.

Local validation: 157 focused tests passed; full backend 979 passed / 1 skipped, coverage 95.10%;
46 isolated Temporal tests passed. Ruff, formatting, mypy, and the contract drift check passed.
Testing uses mock providers and isolated test infrastructure only. Commit/push is operator-approved;
the final SHA and CI outcome are reported in the task handoff. This does not authorize live inference.

EXACT SCHEMA ROOT CAUSE CONFIRMED — READY FOR CONTROLLED LIVE RETRY

## 9. Returned incomplete-response semantics (2026-09-25)

This incident is separate from the historical HTTP 400 schema rejection above. The schema A/B/A
finding remains resolved and the normalized Creative schema still passes the deterministic local
compatibility gate. Two later live Creative attempts entered
`MODEL_PROVIDER_INCOMPLETE_RESPONSE`; no new generation was used to investigate them.

The exact control-flow defect was confirmed offline. `OpenAIResponsesModelProvider` obtained an
authoritative Response, raised before constructing `ModelInvocationResult`, and discarded response
ID/status/usage/reason. `ModelIncompleteResponse` then used the outcome-unknown disposition.
`AgentRunService`, seeing `PROVIDER_STARTED`, `result=None`, and no known-no-response disposition,
persisted `UNKNOWN`. Thus a potentially known returned response was represented as transport
ambiguity. Mock Responses and runtime regressions reproduce the old boundary without network I/O.

ADR-046 introduces finite provider-neutral returned-response metadata and `FAILED_RESPONSE`.
Future returned `incomplete`, `failed`, `cancelled`, completed refusal, and completed invalid-output
responses are checkpointed before terminal failure. Available usage settles through frozen pricing;
missing usage is explicitly marked unavailable and keeps a conservative unknown-cost amount without
claiming provider-outcome ambiguity. True timeout/connection ambiguity remains `UNKNOWN`. No existing
row is rewritten.

The exact successor reconstruction for run `eb1a4920-3461-4e47-a86b-2b936959c090` used the normal
repository, capability, schema normalization, adapter, and SDK serialization paths inside a read-only
transaction and in-memory mock HTTP transport:

| Field | Bounded reconstruction |
| --- | --- |
| Provider / model | `openai` / `gpt-5.6-sol` |
| Route / pricing | `openai-gpt-5.6-sol-creative-2026-09-13` / `openai-gpt-5.6-sol-2026-09-13` |
| Reasoning / output / total cap | `high` / 8,000 / 32,000 |
| Output contract | `creative_creative_concept_set_v1` |
| Context | `creative_strategy.v1` |
| Normalized schema | digest `sha256:fdd66e5b82b66859a82ef2560b7a19f827e67750a408e4866c4820ea4f5414bf`; 5,430 bytes |
| Input bound | 21,324 (current configured input allowance: 24,000) |
| Product snapshot | `4117ec57-cfdc-4158-8104-cc80de810f43` / `sha256:f5985c2a52e8a617a14fd811072ea373671d1a36c2f499f634509a229b0df138` |
| Research snapshot | `b8db7514-7b8e-4709-8e86-567472ef337a` / `sha256:92a84590ab5289554438262989369b5a431724cd2c9d5ba7092812884b555d9f` |
| Request | 5 concepts / `ORGANIC_SHORT_FORM` |
| Gate | PASS locally; provider acceptance unverified; retry not authorized |

The historical reason is not recoverable from durable state. `max_output_tokens` is the leading
offline hypothesis because both attempts reached the returned-incomplete path, the route combines
high reasoning with a five-concept strict output, and OpenAI counts reasoning plus visible output
inside the 8,000-token cap. Safety filtering, provider-reported failure/cancellation, or another
allowlisted incomplete reason remain plausible. Repetition strengthens the headroom hypothesis but
does not prove the historical `incomplete_details.reason`.

Architecture-safe route options preserve the existing 24,000-token input admission allowance. At
the frozen USD 4/M input and USD 20/M output pricing, higher caps require both larger per-run and
daily budgets; none is applied here.

| Output cap | Total envelope | Worst USD/run | 20-run daily budget | Existing USD 5.12/day fits? |
| ---: | ---: | ---: | ---: | --- |
| 8,000 | 32,000 | 0.256 | 5.12 | Yes (current) |
| 12,000 | 36,000 | 0.336 | 6.72 | No |
| 16,000 | 40,000 | 0.416 | 8.32 | No |
| 25,000 | 49,000 | 0.596 | 11.92 | No |

The predecessor `71249591-d9ac-4f57-9235-cb234f5a20d1` / attempt
`c191b140-56d2-4184-8537-6e8037c7e436` remains `FAILED` / `UNKNOWN` with USD 0.256000 unknown cost.
The successor `eb1a4920-3461-4e47-a86b-2b936959c090` / attempt
`d50fa520-6b10-4cad-98f9-04dfc7eb05e7` remains `RUNNING` / `UNKNOWN`, operationally recovery-required,
with USD 0.256000 unknown cost. Their response IDs, usage, exact statuses, and reasons remain
unknowable from stored evidence. No reconciliation or successor was created.
