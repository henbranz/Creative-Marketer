# ADR-035 — Social research is evidence, not production media

## Status

Accepted

## Context

Competitor ads and social posts are strategically useful but may be copyrighted, platform-limited,
unverified, adversarial, and unavailable through official APIs. Treating captured media as ordinary
Assets could accidentally authorize copying or publishing it. Forcing social pages through the Web
Research fetcher would weaken its SSRF/robots/static-content boundary and encourage unofficial
scraping.

## Decision

Research owns archived `ResearchTarget` identities and immutable `SocialEvidenceSnapshot` records.
Manual and official-provider provenance are distinct. Competitor media is always `restricted` with
the sole allowed use `internal_analysis`; persistence and service checks reject any linked Asset
without the same policy. AgentRuntime consumes only bounded normalized textual evidence and exact
digests, not competitor media bytes or raw provider payloads.

Official connectors implement the provider-neutral `SocialResearchProvider` capability contract,
advertise only verified operations, remain disabled by default, and fail unsupported operations as
`CAPABILITY_NOT_SUPPORTED`. There is no scraping fallback. A future credentialed connector must be
composed through the governed external-operation boundary. ADR-025 remains authoritative for the
separate, read-only Web Research acquisition path.

## Consequences

The platform can analyze user-supplied public creative without claiming provider verification or
granting reuse rights. Historical evidence and downstream citations remain reproducible after a
target is archived. V1 cannot automatically search Meta, Instagram, or TikTok until official
access, exact configuration, governance, and provider tests are implemented. Strategy can reuse
abstracted patterns and descriptors, but production cannot reuse captured competitor media.
