# ADR-036: Publishing Is an Approval-Bound External Side Effect

- Status: Accepted
- Date: 2026-09-14

## Context

Organic social publishing creates an externally visible, difficult-to-reverse effect. Duplicate submission, stale creative approval, a changed destination, or a lost provider response can all create customer harm. Agents must not be entrusted with credentials or final publication authority.

## Decision

Publishing is a deterministic bounded context governed by the existing Tool Registry, Permission Engine, Approval Engine, Tool Gateway, idempotency records, audit, and outbox infrastructure.

An immutable `PublicationDraft` binds the exact approved FinalCreative, output Asset digest, platform, SocialAccount, external destination, copy, settings, and schedule. Human approval binds that exact material. A change creates a new draft and requires a new approval.

Actual connector I/O is available only behind `social.publish.*` ToolVersions. Submit is R4. Tool input contains only the draft identifier; trusted server-side resolution supplies authority and any future connector credential. Execution intent must be durable before I/O. The stable operation identity is tenant plus draft plus platform plus account plus operation kind.

Temporal coordinates schedule, cancellation, and bounded reconciliation with identifiers-only payloads. PostgreSQL owns state. A missing provider response is `OUTCOME_UNKNOWN`, never a safe retry signal. A `Publication` is created only after confirmation and is immutable.

Phase 5 ships only a deterministic, no-network `FakeSocialProvider`; database and domain constraints make configuration of a real provider impossible.

## Consequences

- Agents may propose copy but cannot approve, publish, access credentials, or bypass policy.
- Replaying the same operation returns the authoritative result and cannot create a second post.
- Operators can distinguish failure from ambiguous external outcome and reconcile safely.
- A future real connector requires credential resolution at the connector boundary and a separate activation decision.
- The workflow and gateway add implementation complexity, but the cost is justified by the irreversibility and visibility of publication.

## Rejected alternatives

- Direct platform calls from the UI, API route, agent, or Temporal workflow: these bypass durable authorization and cannot prove exact approval.
- Treating timeouts as failures and retrying: this can publish duplicates.
- Storing credentials on SocialAccount or in agent context: this broadens exposure and makes redaction unverifiable.
- Mutable publication drafts: this makes prior approval ambiguous.
