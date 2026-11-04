# Creative Manager: coastal playbook adaptation

Source: the user-supplied **NEW GENIE Design Playbook**, 2026-11-04. This is a
design-method adaptation, not a Softr/Airtable migration. This decision supersedes
the blue-only visual direction in UX-2026-REVAMP, not its product/safety requirements.

## Direction

Operate mode: a founder reviews evidence, concepts and existing media without
losing their place. Slate-white surfaces, light blue `#bae6fd`, light green
`#bbf7d0`, turquoise `#5eead4`, dark slate `#0f172a`, and deep teal `#0f766e`.
Pastels are surfaces, not low-contrast text. Teal is the action color; emerald,
amber and rose retain success/advisory/error meaning. Status always includes words.

One self-hosted Open Sans family, two primary rem sizes (16/24 equivalent), weight
before size, tabular numbers. Preserve 44px targets and visible focus. These are
intentional accessibility/product-contract adaptations of Genie's 10–14px labels
and 32–36px targets. No imported success thresholds, scores or financial rounding.
Cost approvals still display their exact existing amounts.

```text
Light scope rail | Product + actions
                 | Stage tabs / one filter bar
                 | Cards / rows             | detail drawer on demand
Mobile: collapsible navigation, one column, full-width accessible drawer.
```

## Method mapping

| Playbook method | Creative Manager implementation / boundary |
| --- | --- |
| Palette / semantics | Central coastal tokens, channel/series tokens, no purple brand actions |
| Soft surfaces | 16px outer cards, 12px inner tiles/inputs, 8px buttons; faint resting shadow |
| Hierarchy / density | Shared spacing, two-size typography, strong labels, tabular numeric values |
| One shell / scope | Existing product/brand navigation; authenticated API remains tenant authority |
| Beside, not instead | Shared focus-managed drawer for Asset and Concept inspection |
| Overlays | Shared modal/drawer with Escape, focus containment/restoration, labelled close, mobile sizing |
| Filters / search | Asset type pills, filename/role search, explicit Reset, count and scoped empty state |
| Loading / empty / errors | Shared skeleton and retryable read error; persistent actionable messages, no silent Asset failures |
| Saving | Pending buttons and server-confirmed feedback; never optimistic approval, publish or spend success |
| Media | Existing immutable Assets shown through authenticated short-lived grants on explicit preview; never raster-to-SVG wrappers |
| Icons / artwork | Pinned Lucide SVG icons, transparent C/play/spark SVG redraw; original rasters retained for provenance |
| Motion | 200ms feedback, 250ms drawer reveal; reduced motion disables it; lift only actual clickable surfaces |
| Charts | Current=teal, comparison=sky, benchmark=slate; no fabricated series or new chart dependency without a real data surface |
| Page archetypes | Dashboard: Overview/Performance; review: Creatives; detail: Production; gallery: Assets; queue: Command Center; form: Brief |
| Number / date / copy rules | Consistent labels and machine IDs; no mutation of domain data or spend precision for cosmetic formatting |
| Channel layouts | Existing shared panels retained; no copied Softr channel blocks or new platform integration claims |

## Not transferable literally

- MR numbers, Softr datasource UUIDs, global window scoping, 500ms scope polling,
  localStorage aggregates and sandbox Airtable writes are app-specific. Keep
  canonical IDs, generated API contracts, RLS and governed runtime boundaries.
- A pan/zoom campaign tree, speculative charts and gold Soon ribbons are not new
  product capabilities authorized by adopting design methods. Use their patterns
  only when real corresponding data/features exist.
- Do not make production decisions optimistic. A failed or unknown response must
  not look like approval or publication. Keep server validation and audit intact.
- No photograph or generated video is overwritten or “converted” by embedding a
  raster into an SVG. Interface art is vector; real media stays its original format.
- The SVG logo is an authored vector redraw of the supplied C/play/spark silhouette,
  recolored at the user's request; not an upstream original vector master.
- SVG is the web favicon. Apple touch-icon conventions require a separate raster
  export; do not mislabel SVG as a supported Apple raster icon.

## Verification

Test token contrast, SVG structure/no embedded raster or script, accessible overlays,
focus/close behavior, Asset loading/retry/filter states and source preservation.
Use mock grants for interactive screenshots; the live browser audit blocks all
POSTs and external hosts. No paid provider request, live save, generation, rerender,
historical Asset rewrite, or pipeline mutation is part of this design change.

Lucide React is pinned to 1.68.0 (ISC). Registry metadata has no install lifecycle
hook or runtime dependency; install uses `--ignore-scripts`. React is its peer.
No Softr, Tailwind, chart, toast service, native design engine or hook was installed.

## Delivery evidence (2026-11-04)

- Base: `e80157152d03bbd88b521f77f4f5171a42d17632`, the existing foundation PR.
  This is a follow-up branch, not a deployment or main-branch merge.
- 104 frontend tests pass, including 14 added cases covering vector structure,
  palette contrast, dialogs, preview failure/retry/expiry/unsafe URLs, unchanged
  source metadata, filters and unsuccessful server-confirmed Creative decisions.
- TypeScript, ESLint, Prettier, generated OpenAPI contract parity and diff checks pass.
- Browser audit: 44 product-tab captures at 1440/1280/768/390px; no document
  overflow, no clipped Asset card buttons, only 16px/24px authored text at default
  browser size. Sidebar wordmark contrast checked. These are not claims of full
  screen-reader, native Hebrew or 200% browser-zoom certification.
- Native browser drawer QA passes: focus containment/restoration, Escape,
  Create dialog initial focus, failed preview retry, Concept inspection and
  mobile viewport positioning. All preview POSTs were intercepted synthetic
  fixtures (including development StrictMode replay); none reached the backend.
- General browser audit blocks every POST and external host, including existing
  Production auto-preview grant requests. No session, approval, original Asset,
  generation, Assembly revision or live configuration was changed.
- Private product screenshots remain outside Git. Only the blank access screen
  is shareable: [coastal access](ux-2026/access-coastal.png). Previous screenshots
  remain historical evidence, not active runtime image assets.
- Dependency audit: 19 advisories in existing dependencies (10 moderate, 8 high,
  1 critical). No advisory was reported for Lucide; the lockfile adds only Lucide
  and adjusts peer metadata, with no pre-existing package version changes. These
  advisories require a separate security remediation review; green CI is not a
  claim of zero dependency vulnerabilities.

Changed boundaries: web presentation, tests, static UI artwork and audit tooling.
No backend, database migration, generated API contract, provider route, worker,
approval policy or event schema changed. Original PNG brand sources are retained
for provenance/rollback, unused by current runtime branding. Photographs and
generated media remain their original immutable assets.

`frontend-design` and Impeccable operate/polish guidance informed hierarchy,
state handling and visual QA. Missing native tooling was not silently installed.
The user-supplied playbook, project font/accessibility contract and security
constitution take precedence over external defaults.

Next: review this visual direction and the separate foundation PR before merging;
continue the master brief's seven-answer/server-draft and real-thumbnail work as
separate tested changes. This slice does not claim those future capabilities or
convert live product media into vectors.
