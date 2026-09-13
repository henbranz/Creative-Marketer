# 23 — Product Data Safety and Social Research

## Lossless Brief editing

The API continues to expose a strict `BriefResponse` and accept only `BriefContract`. The browser
crosses that boundary through an explicit `toBriefWrite` allowlist; response-only Product ID,
revision, timestamps, permissions, and provenance cannot enter a PUT body.

`BriefDraftV1` is a browser editing model, not an API contract. Multiline and list fields remain raw
strings while the user types, preserving spaces, newlines, and blank lines. Save is the only point
that trims list items, removes empty final items, checks case-insensitive duplicates, and validates
item/count/prose bounds. Content is never silently truncated. Primary audience has a 120-character
single-line name and a distinct 8,000-character multiline description. A legacy local value longer
than 120 characters moves byte-for-byte from an empty audience name/description pair into the
description and displays a review notice; persisted historical Brief revisions are not rewritten.

Dirty drafts are debounced into browser storage under
`creative-marketer:brief-draft:v1:<tenant>:<product>`. The value contains only editable Brief data,
schema/product/tenant identity, base revision, timestamp, dirty state, and a non-security digest. It
never includes the session credential, headers, secrets, URLs created by signing, or server
configuration. Recovery is opt-in. A revision mismatch is displayed and never auto-merged. Failed
saves retain the draft; successful PUT acknowledgement clears only that Product's matching draft.
Navigation and browser unload warn while edits are dirty.

Frontend API failures use one defensive formatter for plain detail, FastAPI validation arrays,
nested locations, structured detail, malformed JSON, and empty bodies. It ignores validation
`input` and credential-shaped keys and does not stringify unknown objects.

## Research targets and social evidence

Research owns tenant/Product-scoped `ResearchTarget` records. Targets identify a competitor brand,
advertiser, social profile, or Product and may carry public website/platform identifiers. They
contain no credentials and follow `active → archived`; archival excludes them from future context
without deleting their evidence.

`SocialEvidenceSnapshot` is a distinct immutable artifact for ads, posts, reels, videos,
screenshots, and exports. It records normalized public metadata, truthful `user_provided` or
`provider_fetched` provenance, capture time, semantic digest, optional raw-metadata digest, and the
officially supplied reach range only when present. It must not contain invented CTR, CPC, CPM,
conversion, ROAS, or spend. Unchanged target/content/digest observations reuse an immutable
snapshot; changed content creates a new one.

Manual evidence is the enabled V1 path for Facebook, Instagram, TikTok, and other public sources.
An optional uploaded Asset must already be READY, `restricted`, and allowed only for
`internal_analysis`. Social evidence itself enforces those rights and cannot become generation,
organic-publishing, or paid-advertising input. The Researcher receives safe textual analysis blocks,
never competitor media bytes.

## Provider capability policy

`SocialResearchProvider` is a provider-neutral boundary with explicit advertised capabilities.
Every platform is disabled by default, and unsupported calls fail with
`CAPABILITY_NOT_SUPPORTED`; there is no HTML fetcher, browser impersonation, cookie, private API,
CAPTCHA, or scraping fallback. Credentialed provider execution must use the existing governed
external-operation boundary when an official adapter is implemented. The existing public Web
Research fetch remains Research-owned under ADR-025 because it is a specialized read-only,
policy-enforcing acquisition path rather than a general execution framework.

Current official review found that Meta Ad Library API access is authenticated and its documented
scope is not a general competitor-organic Instagram API. TikTok's Commercial Content API requires
approved access and client credentials and exposes bounded ad/commercial-content queries. No
credentials or sufficiently verified production eligibility are part of this phase, so Meta,
Instagram, and TikTok official connectors implement zero enabled capabilities. See the
[Meta Ad Library API](https://www.facebook.com/ads/library/api/) and
[TikTok Commercial Content API](https://developers.tiktok.com/doc/commercial-content-api-getting-started/)
documentation.

No social environment variables were added: manual evidence requires none, all providers default
disabled, and a fresh clone performs zero social API calls. Future adapters may add exact official
variables only when implemented; all such secrets remain infrastructure-only.

## Bounded model context and projection

`ResearchContextManifest` version 2 additively includes web and social references, with at most 50
total and 20 social references. Active targets/sources only contribute future context. AgentRuntime
deterministically selects no more than 20 evidence snapshots overall and preserves exact snapshot
IDs, block indices, block digests, manifest digest, and Product snapshot identity on each run.
External text remains untrusted and cannot supply instructions or tools.

Research findings expose `OBSERVED` versus `INFERRED`. Both require exact evidence citations. The
Researcher is explicitly forbidden to label creative high-performing without a supplied performance
signal.

Knowledge Graph and Obsidian add `research_target` and `social_evidence_snapshot` nodes with safe
summaries and public source links. Provider payloads, raw metadata, request headers, tokens, signed
media URLs, and storage keys are excluded. Obsidian remains a one-way projection.
