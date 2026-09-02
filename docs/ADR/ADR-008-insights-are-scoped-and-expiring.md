# ADR-008 — Insights Are Scoped, Evidenced, and Expiring

## Status
Accepted

## Decision
AI-generated learnings are stored as structured Insight entities with evidence, sample size, confidence, scope, provenance, lifecycle status, and expiry/revalidation.

## Rationale
Anecdotal or stale correlations must not become permanent global instructions.

## Consequences
The Intelligence layer must manage validation, contradiction, expiry, and supersession.
