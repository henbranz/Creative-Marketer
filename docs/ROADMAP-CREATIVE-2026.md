# Creative Manager 2026: governed delivery roadmap

Every row is a small feature-branch PR, not authorization to run providers or update
live state. Main changes only after review and green CI. Complexity is relative (S/M/L).
Preserve `.env`, approved media, source digests, old runs, RLS, Temporal, budgets,
approvals, outbox and audit. No destructive migration or fixture insertion into live DB.

| PR / phase | Files / ownership | Contracts / ADR | Acceptance and dependency | Size |
| --- | --- | --- | --- | --- |
| 0 + 1A (started) | `docs/UX-2026-REVAMP.md`, roadmap, skill manifest; `apps/web/src/ui/tokens.css`, `app/styles.css`, shared shell, UI tests | Presentation only; no migration/API changes | Baselines, two font tokens, no gradient actions, responsive Brief, 44px controls, before/after; full existing CI | M |
| 1B: durable intake draft | `catalog/domain.py`, `application.py`, infrastructure repository, `catalog_routes.py`, migration 0041 (allocate after main recheck), generated contracts | New draft aggregate + ADR: draft ≠ Product authority | Tenant+product+user RLS/FK; CAS revision conflicts; cross-device read; no snapshot/event or provider mutation on autosave; audit redaction | L |
| 1C: seven-question intake | Extract `src/brief/intake.tsx`, schema/mapping, `brief-draft.ts`, workspace adapter | Versioned seven-answer draft; questionnaire/readiness separate | 7 prompts including subordinate questions; immediate validated count/percent; debounce/blur/save-exit, offline recovery, cross-tab conflict, resume URL; lossless legacy roundtrip | L |
| 1D: visual assets | `src/assets/asset-preview.tsx`, upload/preview hooks, workspace Assets adapter | Existing signed grants/rights, no extra provider capability | Thumbnails, deliberate video playback, local URL revocation, grant expiry retry, tenant switch cleanup, keyboard lightbox, no secret URL logging | M |
| 1E: navigation/workspace extraction | Feature components and route-state adapter; common shell | No application service rewrite | Back/forward, deep links, dirty-state guards, scroll restoration, 390/768/1280/1440 and 200% zoom | M |
| 2A: ranked creative assessment | `creative` application/domain/repository, routes, `src/creative/` | ADR + immutable ConceptAssessment schema/migration/event | Stable ranks/ties, bounded heuristic scores, evidence/provenance, override history, exclusion gates; no fake probabilities or new hidden model call | L |
| 2B: direction / QA | Strategist/Producer context builders, canonical schemas, versioned registry definitions | Versioned creative-mode/QA contract | Distinct 3–5 hypotheses, product reveal/proof mapping, evidence-authorized testimonials/comparisons; offline fixtures first | M |
| 2C: script/timeline drafts | `production` and `assembly` public services, storyboard UI | ADR + ProductionEditDraft/PlanRevision/TimelineRevision | CAS, undo/redo, duration sums, trim bounds, overlay-free vs generation-paid delta, approval invalidation, immutable rerender idempotency | L |
| 3A: optional modern captions | Assembly contracts/renderer, `src/assembly/` controls | Versioned caption policy; NONE default for new revisions | Three presets, SRT/VTT, bidi shaping, mixed Hebrew/English/numerals, no forced opaque background; old revisions unchanged; moving FFmpeg fixture | M |
| 3B: narration | Narration service/provider port, private Audio Assets, voice selection | ADR: locale/capability/consent/retention lineage | Explicit preview cost, he-IL/en-US, pace/overrun/silence/ducking; paid benchmark + native review are approval gates | L |
| 3C: own voice | Consent aggregate, secure upload/transcript review, narration adapter | Consent revocation + deletion policy; no impersonation | Exact approved words, authorized voice, encrypted storage, Hebrew STS verification or explicit TTS fallback | L |
| 4A: owned metrics | `measurement/provider.py` adapters + OAuth vault/config and performance UI | Provider-specific scope/metric capability ADR | Fixture normalization, timezone/currency, pagination/rate limits/revocation, RLS, observed missingness; live connector read requires consent | L |
| 4B: lawful competitor observations | `research` source contracts/ingestion, evidence inspector | Distinct public/authorized evidence + rights | Ad Library/Top Ads limits verified at implementation; source links/uploads if no permitted API; never scrape around access controls | M |
| 5A: bounded extractors | Optional intelligence infrastructure worker and feature store | Model/version/digest/rights registry ADR | CPU offline fixtures, Hebrew OCR/text retrieval, pinned weights/license audit, no runtime model auto-download; feature-flag off by default | L |
| 5B: calibrated trees | Intelligence training/evaluation package | Versioned training manifest, time/group split and calibration | Decision-tree baseline then CatBoost comparison only with sufficient authorized labels; NO_CALIBRATED_MODEL otherwise | L |
| 6: release gate | Visual/browser CI, docs/runbooks, feature flags | Rollout/rollback ADR if deployment changes | Full backend >=95%, frontend/build/contracts, migrations/RLS, security, Temporal, motion FFmpeg, containers, mobile/RTL/keyboard; no paid regression replay | L |

## Fifteen mandatory requirement checkpoints

1. Seven total visible intake questions: 1C; one answer component each, not hidden nested prompts.
2. Per-answer validated progress: 1C; navigation alone never completes an answer.
3. Durable drafts/resume/conflicts: 1B–1C; autosave never updates approved Product authority.
4. Visual Asset previews: 1D; authenticated grants and explicit video interaction.
5. Evidence-grounded ranking: 2A; separate immutable assessment, human override history.
6. Two primary font sizes: 1A; 16/24 equivalent rem tokens, weights for hierarchy.
7. Dense minimalist interface: 1A–1E/6; measure dead canvas, preserve 44px targets.
8. Non-generic concepts and product salience: 2B; explicit creative modes and QA.
9. Editable Producer scripts/timings: 2C; governed revisions, never mutable old plan.
10. Verified Meta/TikTok measurement: 4A; owned paid/organic differentiated.
11. Optional modern subtitles: 3A; NONE default, modern templates, source motion preserved.
12. Native-quality Hebrew narration: 3B; capability docs plus approved benchmark/native QA.
13. Consent-based own voice: 3C; exact words and truthful conversion limitations.
14. Multimodal competitor intelligence: 4B/5A; lawful sources and optional PyTorch extraction.
15. Appropriate tree models and truthful outcomes: 5B; no training claims from sparse/public popularity labels.

## Phase 1B schema sketch (proposal, not a migration in this PR)

`catalog.intake_drafts`: tenant_id, product_id, user_id (authenticated), schema_version,
answers JSONB, preserved_brief JSONB, base_source_revision, revision, active_step,
updated_at. Unique (tenant_id, product_id, user_id); tenant-aware product FK and RLS;
membership checked in application. PUT requires expected_revision; stale state is 409.
Local draft carries user scope and base revision; no bearer credential in localStorage.
Apply calls the existing authoritative Product update with expected source revision,
after a diff/review. Do not map free-text proof into approved claims automatically.

## Rollout / rollback

Phase 1A is frontend-only and can be reverted independently; it does not start workers
or migrate data. Keep original domain behaviors and readable labels. Later features
ship disabled when dependencies/credentials are missing, with explicit unavailable
states. Each PR includes screenshots, test evidence, SHA/CI URL, changed contracts,
compatibility and operator instructions. Do not merge a dependent PR before its base.
Never use rollback to delete an immutable historical revision or Asset.

Next: approve/review the foundation PR, then implement the isolated draft API and RLS
tests before binding the seven-answer form to autosave. Platform connector approval,
paid audio quality tests and licensed model weights are later, separate requests.
