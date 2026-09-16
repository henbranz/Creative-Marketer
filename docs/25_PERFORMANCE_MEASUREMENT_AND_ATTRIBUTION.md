# 25 — Performance Measurement and Attribution

## Purpose

Measurement closes the deterministic part of the creative loop:

```text
Publication → observed platform facts → immutable snapshot
Publication → opaque reference → conversion fact → exact attribution
```

It does not interpret results or recommend actions. Those responsibilities remain deferred to the Intelligence Agent.

## Boundaries

`measurement` owns normalized performance observations, collection runs and cursors, immutable snapshots, attribution references, conversion observations, and attribution results. Publishing remains the authority for Publications. Measurement reads that authority through a tenant-scoped repository and never edits, cancels, or creates a Publication.

The `SocialMetricsProvider` port is separate from `SocialPublishingProvider`. Phase 5 composes only `FakeSocialMetricsProvider`, which is deterministic and performs no network I/O. Real Meta and TikTok adapters require a later activation decision and credential-isolated connector boundary. Commerce connectors are also deferred.

## Metric truth

Normalized V1 observations support impressions, reach, video views, likes, comments, shares, saves, clicks, watch-time seconds, completions, and profile visits. Every observation stores its semantic kind (`CUMULATIVE`, `INTERVAL`, `DURATION`, or `RATIO`), unit, provider/version, observation time, and a deterministic source digest.

An unavailable metric is absent. It is never persisted as zero. Replaying an identical fact is ignored through `(tenant_id, source_digest)` uniqueness; a changed cumulative value is a new immutable observation.

## Collection and durability

Collection runs persist checkpoint, status, cursor, failure code, and completion time. The finite `PerformanceCollectionWorkflow` uses identifiers-only payloads and schedule version 1 checkpoints at +1 hour, +6 hours, +24 hours, +72 hours, and +7 days. Each Activity resolves current tenant/publication authority before collection. Same-checkpoint and Activity replay are safe because facts and snapshots are content-deduplicated.

Publication success is not coupled to metrics availability. `publishing.publication.published.v1` is the durable fact from which a separately deployed measurement scheduler can start the finite workflow; a metrics failure cannot roll back publishing.

## Derived metrics

The backend is authoritative for formula version `measurement-formulas-v1`:

- CTR = clicks / impressions
- engagement rate = (likes + comments + shares + saves) / impressions
- conversion rate = exact-reference attributed conversions / clicks

Missing inputs and zero denominators produce an unavailable value with a reason. ROAS, CPC, CPM, and CPA are intentionally unavailable because this phase has no trustworthy spend facts.

## Attribution

An `AttributionReference` binds one Publication to one fixed, validated HTTP(S) destination. Its public `cmr_…` code is high entropy and contains no internal UUID. Only a SHA-256 hash is stored. The tracked destination adds `cm_ref` to the approved destination; Creative Manager does not expose an arbitrary redirect endpoint.

`ConversionObservation` stores only source, external id, Decimal amount, ISO-style currency, time, and optional reference hash. It stores no customer name, email, phone, address, IP address, or raw provider payload. `(tenant_id, source, external_id)` is idempotent; a repeated key with contradictory facts fails.

Attribution V1 has one method: `DIRECT_REFERENCE`. A matching reference deterministically produces one immutable `AttributionResult`. Missing references remain unattributed; there is no probabilistic, time-window, fingerprint, or last-click inference.

## Performance snapshots

Every immutable `PerformanceSnapshot` stores the exact observation IDs used, latest normalized counters, versioned derived metrics, exact attributed conversion count, revenue grouped by currency, freshness, and a semantic digest. Mixed currencies are never summed.

Freshness is `NO_DATA`, `CURRENT`, or `STALE`. The UI presents Observed, Derived, and Attributed labels, unavailable values as `—`, collection state, light history, and exact-reference attribution explanations. It deliberately avoids winner, scale, kill, or optimization recommendations.

## Tenant and security controls

All canonical measurement tables use forced PostgreSQL RLS and same-tenant composite foreign keys. Immutable fact tables reject update/delete at the database layer. Owner/Admin may manually collect and create local fake conversions; Member remains read-only. The fake conversion endpoint exists only in development/test.

Audit records cover manual collection completion/failure and fake conversion ingestion without public codes, provider cursors, PII, or raw payloads. Knowledge/Obsidian projections expose only safe snapshot and attribution summaries. They exclude codes, cursor state, raw observations, customer data, and provider responses.

## Local walkthrough

1. Start the normal local stack and create a governed fake Publication.
2. Open the product **Performance** tab and choose **Refresh performance**.
3. Refresh again to advance the deterministic fake sequence; identical terminal facts deduplicate.
4. Set `CM_TENANT_ID` and `CM_API_TOKEN` in the root `.env`, then run:

   ```bash
   make measurement-demo-conversion PUBLICATION_ID=<published-publication-uuid>
   ```

5. Refresh the tab to see `DIRECT_REFERENCE` attribution and revenue by currency.
6. Run `make obsidian-watch` to project Performance notes incrementally.

No real social, commerce, model, or paid provider call is part of this walkthrough.
