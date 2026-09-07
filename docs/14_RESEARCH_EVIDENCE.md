# 14 — Research Sources and Evidence

## Trust boundary

```text
Internet
  ↓
ResearchFetcher (public-network policy, robots, budgets)
  ↓
Raw private acquisition (object storage)
  ↓
Deterministic extractor (untrusted plain structured data)
  ↓
Immutable EvidenceSnapshot
  ↓
On-demand ResearchContextManifest
  ↓
future Researcher (selected references/blocks only)
```

Research is its own bounded context. It may verify a Product reference through a read-only inward
port, but it does not edit Catalog or `ProductKnowledgeSnapshot`. User-provided Product truth and
external evidence therefore cannot silently overwrite one another. No LLM, AgentRuntime, search
provider, browser automation, embedding, or vector index participates in ingestion.

## Source and fetch lifecycle

`ResearchSource` is tenant- and Product-owned, has type `web_page`, a canonical URL, display name,
category, creator, and `active → archived` lifecycle. OWNER/ADMIN may create, refresh, and archive;
MEMBER is read-only. One active attempt per Source is enforced by a partial unique index.

Every refresh appends a `SourceFetch`: `pending → fetching → succeeded | rejected | failed`.
Rejected means deterministic policy refusal; failed means an operational/acquisition failure. The
finite failure-code set is safe to expose. Terminal attempts cannot be edited. A successful fetch
stores status, media class, byte count, raw digest, and evidence reference. It never exposes the raw
object key through an API.

## URL and network policy

Canonicalization lowercases scheme/host, removes a default port and fragment, supplies `/`, and
preserves query semantics. Only absolute HTTP/HTTPS URLs on ports 80/443 are accepted. URL
credentials, overlong URLs, and secret-like query keys are rejected.

Before every robots or page request, all A/AAAA results must be globally routable. One private,
loopback, link-local, multicast, unspecified, reserved, documentation, or metadata address rejects
the request. The transport connects to the approved IP—not the hostname—while retaining original
Host and TLS SNI, closing the DNS resolution/connection rebinding gap. Each redirect is resolved and
validated again; at most five are followed. Requests are GET-only with server-owned User-Agent,
Accept, language, encoding, and connection headers. Caller authorization, cookies, and headers are
never forwarded.

Robots is conservative: explicit denial rejects, 404 permits, and unavailable/malformed/non-success
robots responses reject as `robots_unavailable`. Connect and overall budgets are 5 and 15 seconds.
Only HTML, XHTML, and plain text are accepted. The streaming transport caps compressed acquisition
and 5 MiB decompressed output, rejects unsupported encodings/MIME and NUL-sniffed binary content,
and allows no generic fetch/proxy/raw route.

## Raw and extracted evidence

Raw bytes are server-written to the private prefix
`tenants/<tenant>/research/<source>/<fetch>/raw/...`. There is no browser grant. Object persistence
necessarily precedes the evidence database transaction; a crash may leave a private unreferenced
object. A future bounded retention janitor may remove such orphans. It can never create a false
successful database state.

The HTML parser does not execute scripts, load subresources, or parse XML entities. It drops active,
form, SVG, and obvious hidden DOM; emits at most 500 bounded heading, paragraph, list, table-text, and
quote blocks; and records at most 200 normalized outbound links without following them. Extraction
is capped at 500 KiB. Declared Python-supported charsets are honored; unknown charsets fall back to
UTF-8, and invalid bytes are replaced deterministically.

`EvidenceSnapshot` is append-only and contains provenance, structured blocks/metadata, raw and
semantic SHA-256 digests, extractor/schema versions, and an `instruction_like_content` signal.
Equivalent semantic content reuses the latest immutable EvidenceSnapshot while still preserving a
new SourceFetch. Sanitization and the heuristic flag do **not** make evidence trusted and are not an
authorization boundary. Future AgentRuntime must label it untrusted, select only task-relevant
blocks, keep it out of system/developer instructions, and give it no tool or permission authority.

## Context, privacy, and facts

`ResearchContextManifest` is built on demand from the latest successful EvidenceSnapshot for each
active Product Source. It contains at most 50 deterministically ordered references and digests—not
raw HTML or concatenated evidence. Future retrieval may add indexing only after AgentRuntime needs
are defined; pgvector/full-text search is intentionally absent.

Evidence completion, compact Audit, and `research.evidence.captured.v1` commit atomically. Source
creation/archive emit `research.source.created.v1` and `research.source.archived.v1`. Events and
Audit contain IDs, category, lifecycle and digests only; never full URLs, query strings, page text,
PII, headers, or object keys. Telemetry dimensions are restricted to result/state, source category,
and content-type class; tenant, hostname, URL, Product/Source/Fetch/Evidence IDs and content are
forbidden metric labels.

## Known limitations

- The stdlib HTML extractor is deliberately narrow; complex JavaScript-rendered pages are not
  supported.
- Robots 404 is the sole unavailable-file allow case; this can reduce coverage but fails safely.
- Fetching is synchronous in the API process until a durable Research workflow is introduced.
- A process crash can leave a `fetching` attempt requiring an operator recovery path; automatic
  stale-attempt reconciliation is deferred with the durable workflow.
- Raw-object orphan cleanup, malware/content classification, retention policy, and production
  egress firewall enforcement remain follow-up work.
- Prompt-injection ingestion isolation is tested; Agent-side prompt/context isolation remains a
  mandatory AgentRuntime responsibility.
