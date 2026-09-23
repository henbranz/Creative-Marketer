# Phase 9 — existing HTTP 400 evidence and unexecuted synthetic probes

Date: 2026-09-23. Starting commit: `6d200414312cd0acd5153e25836e32c187c9d7a3`.

## Conclusion

No historical OpenAI rejection metadata was recovered from the accessible retained sources.
Root cause remains **unproven**. Neither live inference nor either synthetic probe was executed.
The Creative canonical schema is unchanged. No new AgentRun, live worker, recovery, cost
reconciliation, session reset, or media generation was initiated.

## Exact historical executions

All times below are UTC. Database reads used explicit read-only transactions.

| AgentRun | ModelAttempt | Provider start | Finish |
| --- | --- | --- | --- |
| `497f1507-af38-4247-be15-1f28b0fbb560` | `6e596371-7058-4d74-80c8-f8a2d27dbb51` | 16:10:23.711852 | 16:10:25.820153 |
| `49e11f80-018a-487d-97b2-afd578add295` | `d5f46d55-be74-4d2c-9a68-806bab08a256` | 17:23:30.173086 | 17:23:31.720847 |

Both runs are `FAILED / MODEL_PROVIDER_BAD_REQUEST`; both attempts are
`FAILED_NO_RESPONSE`, with no provider response ID. This normalized code is retained evidence,
not recovery of the raw HTTP status or OpenAI error details: the adapter maps both 400 and 422
to that code. The previously reported HTTP 400 cannot be independently reconstructed from it.
Error type, code, param, request ID, and SDK exception class are unavailable for both executions.

## Sources searched and results

1. **AgentRun / ModelAttempt rows:** exact IDs, timestamps, normalized failure code, response-ID
   availability, and correlation IDs only; no Product context or prompt reads.
2. **Actual Temporal histories:** exact workflow IDs
   `tenant/5fbff06c-0d69-5710-aa0c-9cd58688aef6/agent-run/<AgentRun ID>`.
   Temporal execution IDs are `01a0cf08-5001-773a-8296-58ea689d8628` and
   `01a0cf4b-3e98-7a1d-b5f8-e2a6a30776de`. Both workflows are `COMPLETED`, each with 11 events,
   one completed Activity, and no failure events or exception-cause chain. Activity and workflow
   results retain only the bounded `FAILED / MODEL_PROVIDER_BAD_REQUEST` outcome and identifiers.
   The application handled the rejection and returned a normal failure result. Histories were
   fetched, never replayed; no Activity was retried.
3. **Append-only Audit:** five directly run-bound records, six including correlation/reference
   matches. Started, failed, and recovery-lineage actions exist. Failed records contain model,
   provider, Product ID, failure code, token counters, currency, profile, and estimated cost keys,
   but no HTTP/error/request-ID diagnostics. Only safe fields and metadata key names were selected.
4. **Outbox / Inbox:** two published request events and two pending completion events. No last
   delivery error or traceparent; payload key inspection revealed no provider diagnostics.
   Inbox has two `researcher-temporal-starter` v1 request receipts, no completion receipts or
   diagnostic fields. No delivery was triggered.
5. **Docker application/worker/Temporal logs:** bounded window 16:05–17:30 UTC. API: 15 lines,
   two run-related candidates, no allowlisted OpenAI diagnostics. Orchestration worker, production
   worker, Temporal, and the stopped researcher worker yielded no retained lines in that window.
   The researcher container was checked directly after Compose profile lookup failed. The current
   API container was created after the first failed request. Raw entries were never emitted.
6. **Host worker / structured logging:** no active host researcher-worker process found. Worker
   code uses `NullTelemetry` and has no persistent file logger. Repository and bounded temporary
   directory filename searches found no retained application/worker diagnostic log. No process
   environment, shell history, or unrestricted terminal scrollback was dumped.
7. **OpenTelemetry/exporter configuration:** `otel_mode=disabled`, no configured exporter.
   No reachable configured telemetry sink existed to search. Configuration values/credentials
   were not emitted.
8. **Operator artifacts:** existing Phase 9 investigation report, local live-validation checkpoint
   (key names only), and retained operator diagnostic script presence were checked. The checkpoint
   stores identifiers/stage, not provider rejection details; scripts are not captured output logs.
   No safe historical request ID was recovered.
9. **Persistence schema discovery:** no separate retained provider-diagnostic store found. Unrelated
   research HTTP status and Tool Gateway error-code columns are not evidence for these model calls.

This exhausts accessible retained local evidence, not hypothetical deleted logs or a provider-side
support dashboard. No provider dashboard or remote support access was available or used.

## Observability gap and correction

Before this change, the recent SDK extractor logged safe fields only to the process logger;
the runtime persisted only normalized failure codes. Terminal/stdout capture was ephemeral, not
a durable diagnostic source. The historical executions cannot be backfilled from missing evidence.

- Unknown type/code/param values now survive only with `[A-Za-z0-9_.:-]{1,128}` grammar.
  Whitespace/prose, quotes, braces, brackets, URLs, key-like values, bearer markers, and JWT-like
  values are redacted. Request IDs retain their separate `req_...` bound. `message` is never read.
- The immutable SDK-free diagnostic value is validated at construction before entering application
  code. Raw SDK exceptions/bodies do not become Audit metadata.
- Runtime appends `agent.model.provider_rejected` using existing insert-only, tenant-scoped Audit.
  Metadata contains provider, the six bounded diagnostic fields, AgentRun ID, and ModelAttempt ID.
  Authoritative workload, tenant, run, attempt, and correlation bindings come from runtime state,
  never from provider content.
- Terminal rejection diagnostics share the failure/unknown-outcome transaction. Retryable HTTP
  diagnostics commit before the next already-permitted transport attempt. Audit failure blocks
  that next request. HTTP 408/5xx diagnostics do not reclassify unknown cost as zero.
- No database migration, event/API contract change, historical mutation, or retry-policy change.

## Prepared probes — NOT EXECUTED

Script: `apps/api/scripts/synthetic_openai_probe.py`. Offline preview uses the real SDK and existing
Responses adapter with `httpx.MockTransport`, never the network and never Settings/credentials.

| Setting | Creative | Researcher control |
| --- | --- | --- |
| Model | `gpt-5.6-sol` | `gpt-5.6-sol` |
| Schema | unchanged `creative.creative_concept_set` v1 | unchanged `research.research_snapshot` v2 |
| Reasoning | `high` | `medium` (known-good route) |
| Synthetic context | empty capability context | empty Product context and evidence |
| Offline serialized request bytes | 5,364 | 2,452 |
| Maximum output tokens | 32 | 32 |
| Tools / storage | `[]` / `false` | `[]` / `false` |

Both use the same normal root-dotenv `Settings.openai_api_key` path in execution mode, existing
Responses adapter/envelope, strict canonical schema, and zero SDK retries. No AgentRuntime service,
AgentRun, Temporal client, PostgreSQL operation, Product/Research lookup, live-session file, or user
asset is used. No schema keyword was removed and canonical validation remains intact.

The guard allows one POST only to the official Responses endpoint, rejects oversized/envelope-drift
requests before sending, disables redirects, and has a 20-second timeout. Success closes the HTTP
response immediately at headers before reading output; a rejection captures only bounded fields.
Timeout/connection failure means **unknown outcome, do not retry**, not safe zero spend. The tiny
cap itself may produce a rejection or incomplete output; neither proves the historical cause.

Execution requires the literal `--execute-approved I_APPROVE_ONE_SYNTHETIC_REQUEST` and an explicit
`--diagnostic-file`. Neither flag was used. One invocation selects exactly one probe, never both.
Execution must be separately operator-approved. A new exclusive mode-0600 JSONL artifact is opened
and fsynced before network I/O; a started/unknown marker and safe result are retained. Existing files
cannot be overwritten. No model output is read/persisted. Standalone probes cannot use tenant Audit
without inventing run authority; this private local artifact is their diagnostic record instead.

Safe offline commands from `apps/api`:

```bash
uv run python -m scripts.synthetic_openai_probe creative
uv run python -m scripts.synthetic_openai_probe researcher
```

### Conservative cost envelope

Per probe: at most 8,192 serialized request bytes, including the schema and envelope, plus a
1,024-token framing allowance gives a conservative **9,216 input-token allowance**; output cap
is **32 tokens**, including reasoning. At repository prices $4/M input and $20/M output:

`2 × ((9,216 × $4 + 32 × $20) / 1,000,000) = $0.075008` (under eight US cents).

This is a conservative planned upper estimate under byte-bound token accounting and the stated
framing allowance, **not a provider-enforced USD cap**. Hidden provider accounting cannot be proven
offline. Closing the response is not a guarantee of server-side cancellation or refund; budget for
all 32 output tokens even on acceptance/timeout. No automatic retries, output continuation, or media
calls are permitted. Execution still needs explicit operator approval after reviewing this estimate.

The OpenAI [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning) explains that
`max_output_tokens` bounds reasoning and visible tokens and that incomplete output can still incur
charges. The [Sol model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol) supports
the route/reasoning and pricing reference used here.

### Interpretation, not a verdict

- Creative rejected / Researcher accepted: strong differential evidence for the Creative request
  or schema, not automatic proof of any particular schema construct.
- Both rejected with the same code: investigate shared configuration/envelope or probe cap.
- Both accepted: historical failure not reproduced with synthetic context; do not retry live work
  or claim full Creative generation/output validity.

## Hypotheses preserved

Invalid key, model accessibility, route, high reasoning, historical max-output setting, and request
size remain **UNLIKELY**. Unsupported schema constructs, schema metadata, and other provider/project
validation remain **UNKNOWN**. Nothing became CONFIRMED.

## Protected state and validation

Protected live session `c1185337-6dbd-431f-92f6-4fc22d996e40` was not mutated. Tests use mocked
provider transports, isolated `creative_marketer_hotfix33` PostgreSQL fixtures, and ephemeral
Temporal test servers, not live acceptance runs. No paid call or image/video generation ran.

Validation results and final commit/CI link are reported in the accompanying task handoff.

FORENSICS EXHAUSTED — SYNTHETIC PROBE READY, OPERATOR APPROVAL REQUIRED
