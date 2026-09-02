# ADR-014 — API, Event, and Tool Contract Ownership

## Status
Accepted

## Decision
Each external or cross-language boundary has one canonical contract source.

- Backend API request/response contracts generate OpenAPI.
- The TypeScript API client and DTO types are generated from the versioned OpenAPI artifact.
- Domain event and tool input/output contracts use versioned language-neutral JSON Schema or an equivalent canonical schema format.
- Generated artifacts are reproducible and CI fails on uncommitted drift.

Python and TypeScript DTOs are not maintained independently as parallel authorities. Domain models remain internal and are mapped explicitly to boundary contracts.

Breaking contract changes require a new supported version and a documented compatibility/migration window. Additive changes are permitted only where consumers are designed to tolerate them.

## Alternatives

- manually duplicate Pydantic and TypeScript DTOs
- expose internal ORM/domain models directly
- use unversioned free-form event and tool payloads
- make generated clients the source rather than an output

## Rationale
One source per boundary reduces silent drift while preserving language-independent event/tool validation and generated frontend ergonomics.

## Tradeoffs
Code generation adds CI steps and artifact-management decisions. Boundary mapping creates deliberate duplication between internal models and public schemas, but prevents persistence design from becoming an API contract.

## Consequences

- contract fixtures and compatibility checks are part of Phase 0 tests
- event producers validate before persistence/publication and consumers validate before handling
- tool definitions reference immutable schema versions
- the current hand-written TypeScript health interface is transitional and must not become a second authority
