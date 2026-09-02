# ADR-007 — Modular Monolith First

## Status
Accepted

## Decision
Begin with bounded-context modules in a modular monolith plus thin API and worker entrypoints. Do not create a microservice per agent.

Dependency direction is:

`delivery adapters → application/use cases → domain`

Infrastructure implements outbound ports owned by the application/domain boundary and depends inward. Domain modules do not depend on web, database, provider, model, or workflow SDKs. Workers import reusable application packages rather than FastAPI routes.

Contexts may share a PostgreSQL cluster initially but own their tables and write paths. Cross-context access uses public application interfaces or versioned events/contracts.

Extraction is justified by a materially different trust zone, scaling profile, failure domain, release cadence, or operational SLO—not by the existence of another agent type.

## Rationale
Early service fragmentation increases operational complexity and makes schema/workflow evolution slower. Explicit module and data ownership preserves a credible extraction path without paying distributed-systems costs before they are necessary.

## Consequences
Architecture tests must enforce imports and provider isolation. Direct cross-context table writes are prohibited. Research ingestion and credentialed connector execution are likely early extractions because their trust zones differ, but remain in-process until the security or scaling need is concrete.
