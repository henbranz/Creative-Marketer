# ADR-025: Untrusted Research Ingestion and Evidence Boundary

## Status

Accepted

## Context

Research must acquire public pages without turning the application into an SSRF proxy or granting
Internet text authority over tenants, tools, credentials, or Product truth. Raw pages are
tenant-confidential and potentially adversarial. Future agents need stable provenance and bounded
references rather than arbitrary live browsing.

## Decision

Research is an independent bounded context. A policy-enforcing fetcher permits only HTTP/HTTPS on
standard ports; validates every resolved address; pins the connection to a validated public IP with
the original Host/SNI; revalidates robots and every redirect; strips caller credentials/headers;
and enforces timeout, redirect, MIME, binary-sniff, and decompressed-size budgets. No generic proxy,
fetch, or raw-content endpoint exists.

Server-only acquisition writes raw bytes under a private tenant/Source/Fetch object prefix. A
deterministic non-executing parser produces bounded structured plain-text evidence. Raw acquisition,
SourceFetch history, and immutable `EvidenceSnapshot` have separate identities. Unchanged semantic
content reuses the previous evidence identity while preserving the attempt. On-demand
`ResearchContextManifest` contains only latest active evidence references.

Internet content remains untrusted even after sanitization. It cannot invoke tools, mutate
permissions, become a system instruction, or overwrite `ProductKnowledgeSnapshot`. Future agents
must consume explicitly selected evidence blocks through an untrusted-data channel. Audit, events,
metrics, logs, and traces exclude raw/extracted content, URLs/query strings, headers, PII, secrets,
and storage keys.

## Consequences

The boundary materially reduces SSRF, DNS rebinding, prompt-injection authority, and privacy risk,
and gives future Researcher runs reproducible evidence identities. Conservative robots and static
HTML extraction reject or miss some useful sites. Synchronous fetching consumes API capacity until
durable workflow adoption. Object/database atomicity is not claimed, so private orphan cleanup and
retention need a later janitor. Sanitization is defense in depth, never authorization.
