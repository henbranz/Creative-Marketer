/* eslint-disable @next/next/no-img-element -- private signed asset grants cannot use Next Image */
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type Asset,
  type AssemblyPlan,
  type AssemblyReadiness,
  type AgentRun,
  type Brand,
  type BriefWrite,
  type CreativeConcept,
  type CreativeConceptSet,
  type FinalCreative,
  catalogApi,
  listText,
  obsidianOpenUrl,
  type Product,
  type ProductionJob,
  type ProductionPlan,
  type ResearchEvidence,
  type ResearchFetch,
  type ResearchSource,
  type ResearchSourceCreate,
  type ResearchSnapshot,
  type Session,
  slugify,
  type Workspace,
  uploadToGrant,
} from "./catalog-api";

const navigation = [
  "Command Center",
  "Products",
  "Agents",
  "Approvals",
  "Activity",
  "Settings",
];
const tabs = [
  "Overview",
  "Brief",
  "Assets",
  "Research",
  "Creatives",
  "Production",
  "Published",
  "Performance",
  "Insights",
];
const briefSections = [
  "Product",
  "Audience",
  "Positioning",
  "Marketing",
  "Creative Direction",
  "Constraints",
];

const emptyBrief: BriefWrite = {
  product_why: "",
  emotional_benefits: [],
  primary_audience: null,
  secondary_audiences: [],
  positioning_statement: "",
  competitive_alternatives: [],
  why_choose_us: [],
  current_channels: [],
  priority_channels: [],
  conversion_goal: "",
  offers: [],
  cta_preferences: [],
  desired_creative_style: "",
  tones_to_explore: [],
  tones_to_avoid: [],
  creative_references: [],
  mandatory_messaging: [],
  prohibited_messaging: [],
  required_disclaimers: [],
  legal_safety_constraints: [],
  geographical_restrictions: [],
};

function AccessScreen({
  onConnect,
}: {
  onConnect: (session: Session) => void;
}) {
  const [tenantId, setTenantId] = useState("");
  const [credential, setCredential] = useState("");
  return (
    <main className="access-page">
      <section className="access-card">
        <div className="brand-mark">CM</div>
        <p className="eyebrow">Product workspace</p>
        <h1>Build the source of truth your creative system can trust.</h1>
        <p className="lede">
          Connect an authenticated workspace to organize brands, products,
          audiences, claims, and creative direction.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onConnect({ tenantId, credential });
          }}
        >
          <label>
            Tenant ID
            <input
              aria-label="Tenant ID"
              required
              value={tenantId}
              onChange={(event) => setTenantId(event.target.value)}
              placeholder="Your workspace UUID"
            />
          </label>
          <label>
            Credential
            <input
              aria-label="Credential"
              required
              type="password"
              value={credential}
              onChange={(event) => setCredential(event.target.value)}
              placeholder="Development issuer|subject"
            />
          </label>
          <button className="primary" type="submit">
            Open workspace
          </button>
        </form>
        <small>
          Production identity-provider selection remains intentionally deferred.
          These credentials are never stored by the application.
        </small>
      </section>
    </main>
  );
}

function EmptyPanel({ tab }: { tab: string }) {
  const copy =
    tab === "Assets"
      ? "Images and videos you upload will become the shared asset library used by future Creative and Producer Agents."
      : `${tab} will appear here when its product capability is introduced.`;
  return (
    <section className="empty-panel">
      <span className="empty-glyph" aria-hidden="true">
        {tab.slice(0, 1)}
      </span>
      <h2>No {tab.toLowerCase()} yet</h2>
      <p>{copy}</p>
      <span className="coming">Coming in a future product slice</span>
    </section>
  );
}

function AssetsPanel({ workspace }: { workspace: Workspace }) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [role, setRole] = useState("product_detail");
  const [rights, setRights] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState("");
  const session = useMemo(() => readSession(), []);
  const refresh = useCallback(async () => {
    setAssets(await catalogApi.listAssets(session, workspace.product.id));
  }, [session, workspace.product.id]);
  useEffect(() => {
    queueMicrotask(
      () => void refresh().catch(() => setError("Assets could not be loaded.")),
    );
  }, [refresh]);
  const upload = async () => {
    if (!file || !rights) return;
    setError("");
    setProgress(0);
    try {
      const kind = file.type.startsWith("image/")
        ? "image"
        : file.type.startsWith("video/")
          ? "video"
          : "document";
      const grant = await catalogApi.createAsset(session, {
        brand_id: workspace.brand.id,
        product_id: workspace.product.id,
        kind,
        role: role as "product_detail",
        original_filename: file.name,
        mime_type: file.type as "image/jpeg",
        rights_status: "confirmed",
        allowed_uses: ["internal_analysis", "generation_input"],
        parent_asset_id: null,
        source_url: null,
      });
      await uploadToGrant(grant, file, setProgress);
      await catalogApi.finalizeAsset(session, grant.asset.id);
      await refresh();
      setFile(null);
      setProgress(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Upload failed.");
      setProgress(null);
    }
  };
  const shown = assets.filter(
    (asset) => filter === "all" || asset.kind === filter,
  );
  return (
    <div className="assets-workspace">
      <section className="upload-card">
        <div>
          <p className="eyebrow">Secure media intake</p>
          <h2>Product assets</h2>
          <p>
            Private files are verified by type and digest before agents can
            reference them.
          </p>
        </div>
        {workspace.product.can_edit ? (
          <div className="upload-controls">
            <input
              aria-label="Choose asset"
              type="file"
              accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,application/pdf"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <select
              aria-label="Asset role"
              value={role}
              onChange={(event) => setRole(event.target.value)}
            >
              <option value="product_hero">Product hero</option>
              <option value="product_detail">Product detail</option>
              <option value="lifestyle">Lifestyle</option>
              <option value="packaging">Packaging</option>
              <option value="other">Other</option>
            </select>
            <label className="attestation">
              <input
                type="checkbox"
                checked={rights}
                onChange={(event) => setRights(event.target.checked)}
              />
              I confirm this workspace may use this file for analysis and
              creative generation.
            </label>
            <button
              className="primary"
              disabled={!file || !rights || progress !== null}
              onClick={() => void upload()}
            >
              {progress === null ? "Upload asset" : `Uploading ${progress}%`}
            </button>
          </div>
        ) : (
          <p className="readonly-note">
            You have read-only access to this asset library.
          </p>
        )}
      </section>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="asset-toolbar">
        <strong>{shown.length} assets</strong>
        <select
          aria-label="Filter assets"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="all">All types</option>
          <option value="image">Images</option>
          <option value="video">Videos</option>
          <option value="document">Documents</option>
        </select>
      </div>
      {shown.length ? (
        <div className="asset-grid">
          {shown.map((asset) => (
            <article className="asset-card" key={asset.id}>
              <div className={`asset-preview ${asset.kind}`} aria-hidden="true">
                {asset.kind.slice(0, 1).toUpperCase()}
              </div>
              <div>
                <strong title={asset.original_filename}>
                  {asset.original_filename}
                </strong>
                <p>
                  {asset.role.replaceAll("_", " ")} · {asset.status}
                </p>
                <small>
                  {asset.byte_size
                    ? `${(asset.byte_size / 1024 / 1024).toFixed(1)} MB`
                    : "Awaiting validation"}
                </small>
              </div>
              {asset.status === "ready" && (
                <button
                  className="secondary"
                  onClick={async () => {
                    const grant = await catalogApi.downloadAsset(
                      session,
                      asset.id,
                    );
                    window.open(grant.url, "_blank", "noopener,noreferrer");
                  }}
                >
                  Preview
                </button>
              )}
              {asset.can_edit &&
                ["ready", "rejected"].includes(asset.status) && (
                  <button
                    className="text-danger"
                    onClick={async () => {
                      await catalogApi.archiveAsset(session, asset.id);
                      await refresh();
                    }}
                  >
                    Archive
                  </button>
                )}
            </article>
          ))}
        </div>
      ) : (
        <section className="empty-panel">
          <span className="empty-glyph">A</span>
          <h2>No assets yet</h2>
          <p>
            Add approved product media to create a reusable, traceable shared
            asset library.
          </p>
        </section>
      )}
    </div>
  );
}

type SourceWithFetch = {
  source: ResearchSource;
  latest: ResearchFetch | null;
  history: ResearchFetch[];
};
type SourceCategory = ResearchSourceCreate["category"];

function safeSourceLabel(value: string) {
  const parsed = new URL(value);
  return `${parsed.origin}${parsed.pathname}${parsed.search ? "?…" : ""}`;
}

function ResearchPanel({ workspace }: { workspace: Workspace }) {
  const session = useMemo(() => readSession(), []);
  const obsidianVaultName = process.env.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME;
  const [sources, setSources] = useState<SourceWithFetch[]>([]);
  const [url, setUrl] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [category, setCategory] = useState<SourceCategory>("other");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [evidence, setEvidence] = useState<ResearchEvidence | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [snapshots, setSnapshots] = useState<ResearchSnapshot[]>([]);

  const load = useCallback(async () => {
    const [values, runValues, snapshotValues] = await Promise.all([
      catalogApi.listResearchSources(session, workspace.product.id),
      catalogApi.listResearcherRuns(session, workspace.product.id),
      catalogApi.listResearchSnapshots(session, workspace.product.id),
    ]);
    const enriched = await Promise.all(
      values.map(async (source) => {
        const history = await catalogApi.listResearchFetches(
          session,
          source.id,
        );
        return { source, latest: history[0] ?? null, history };
      }),
    );
    setSources(enriched);
    setRuns(runValues);
    setSnapshots(snapshotValues);
  }, [session, workspace.product.id]);

  useEffect(() => {
    queueMicrotask(
      () =>
        void load().catch(() =>
          setError("Research sources could not be loaded."),
        ),
    );
  }, [load]);

  useEffect(() => {
    if (!runs.some((run) => ["PENDING", "RUNNING"].includes(run.status)))
      return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [load, runs]);

  const validateUrl = () => {
    try {
      const parsed = new URL(url);
      return ["http:", "https:"].includes(parsed.protocol) && !parsed.username;
    } catch {
      return false;
    }
  };

  const add = async () => {
    if (!validateUrl()) {
      setError("Enter a valid HTTP or HTTPS URL without credentials.");
      return;
    }
    setBusy("new");
    setError("");
    setNotice("");
    try {
      await catalogApi.createResearchSource(session, workspace.product.id, {
        url,
        display_name: displayName,
        category,
        refresh: true,
      });
      setUrl("");
      setDisplayName("");
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Source ingestion failed.",
      );
    } finally {
      setBusy("");
    }
  };

  const refresh = async (item: SourceWithFetch) => {
    setBusy(item.source.id);
    setError("");
    setNotice("");
    try {
      const value = await catalogApi.refreshResearchSource(
        session,
        item.source.id,
      );
      if (value.status === "succeeded") {
        setNotice(
          value.evidence_snapshot_id === item.latest?.evidence_snapshot_id
            ? "Content unchanged; existing evidence retained."
            : "New evidence captured.",
        );
      }
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Refresh failed.");
    } finally {
      setBusy("");
    }
  };

  const viewEvidence = async (evidenceId: string) => {
    setError("");
    try {
      setEvidence(await catalogApi.getResearchEvidence(session, evidenceId));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Evidence could not be loaded.",
      );
    }
  };

  return (
    <div className="research-workspace">
      <section className="ai-research-card">
        <div>
          <p className="eyebrow">Evidence-grounded AI research</p>
          <h2>Researcher</h2>
          <p>
            Analyze the current Product snapshot and captured evidence. External
            pages remain untrusted data, and every factual finding must cite an
            exact evidence block.
          </p>
        </div>
        {workspace.product.can_edit ? (
          <button
            className="primary"
            disabled={
              !!busy ||
              runs.some((run) => ["PENDING", "RUNNING"].includes(run.status))
            }
            onClick={async () => {
              setBusy("researcher");
              setError("");
              try {
                await catalogApi.startResearcher(
                  session,
                  workspace.product.id,
                  crypto.randomUUID(),
                );
                await load();
              } catch (caught) {
                setError(
                  caught instanceof Error
                    ? caught.message
                    : "Researcher could not start.",
                );
              } finally {
                setBusy("");
              }
            }}
          >
            {busy === "researcher" ? "Queueing…" : "Run Researcher"}
          </button>
        ) : (
          <p className="readonly-note">
            Owners and admins can start billed research runs.
          </p>
        )}
        {runs[0] && (
          <div className="research-run-summary">
            <span className={`fetch-state ${runs[0].status.toLowerCase()}`}>
              {runs[0].is_stranded
                ? "Needs operational recovery"
                : runs[0].status === "PENDING"
                  ? "Queued"
                  : runs[0].status.toLowerCase()}
            </span>
            <span>Researcher v{runs[0].agent_version_number}</span>
            <span>{runs[0].model_profile_key.replaceAll("_", " ")}</span>
            {runs[0].total_tokens > 0 && (
              <span>{runs[0].total_tokens} tokens</span>
            )}
            {runs[0].failure_code && (
              <span>{runs[0].failure_code.replaceAll("_", " ")}</span>
            )}
            {runs[0].is_stranded && (
              <p>
                This run was interrupted and requires recovery before it can be
                rerun.
              </p>
            )}
          </div>
        )}
      </section>
      {snapshots[0] && (
        <section className="research-snapshot">
          <div className="research-snapshot-heading">
            <div>
              <p className="eyebrow">Latest AI research</p>
              <h2>{snapshots[0].findings.length} evidence-backed findings</h2>
            </div>
            <span className={`fetch-state ${snapshots[0].freshness}`}>
              {snapshots[0].freshness}
            </span>
            {obsidianVaultName && (
              <button
                className="secondary"
                onClick={() =>
                  void obsidianOpenUrl(
                    obsidianVaultName,
                    "research_snapshot",
                    snapshots[0]!.id,
                  ).then((url) => window.location.assign(url))
                }
              >
                Open in Obsidian
              </button>
            )}
          </div>
          <div className="finding-list">
            {snapshots[0].findings.map((finding) => (
              <article className="finding-card" key={finding.key}>
                <div className="finding-meta">
                  <span>{finding.category.replaceAll("_", " ")}</span>
                  <span title="Model-assessed confidence based on supplied evidence">
                    {finding.confidence.toLowerCase()} confidence
                  </span>
                </div>
                <p>{finding.statement}</p>
                {finding.implication && <small>{finding.implication}</small>}
                <div className="citation-list">
                  {finding.citations.map((citation) => (
                    <button
                      className="citation-chip"
                      key={`${citation.evidence_snapshot_id}-${citation.block_index}`}
                      onClick={() =>
                        void viewEvidence(citation.evidence_snapshot_id)
                      }
                    >
                      Evidence · block {citation.block_index}
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </div>
          {!!snapshots[0].research_gaps.length && (
            <div className="research-gaps">
              <h3>Research gaps</h3>
              <ul>
                {snapshots[0].research_gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </div>
          )}
          {!!snapshots[0].recommended_next_sources.length && (
            <div className="research-gaps">
              <h3>Suggested next research</h3>
              <ul>
                {snapshots[0].recommended_next_sources.map((item) => (
                  <li key={`${item.category}-${item.suggested_query}`}>
                    <strong>{item.category.replaceAll("_", " ")}</strong>:{" "}
                    {item.reason}
                  </li>
                ))}
              </ul>
              <small>
                Suggestions are proposals only and are never fetched
                automatically.
              </small>
            </div>
          )}
        </section>
      )}
      <section className="research-intake">
        <div>
          <p className="eyebrow">Governed web evidence</p>
          <h2>Research sources</h2>
          <p>
            Pages are fetched server-side under strict network policy and stored
            as immutable, untrusted evidence.
          </p>
        </div>
        {workspace.product.can_edit ? (
          <div className="research-form">
            <label>
              Source URL
              <input
                aria-label="Research source URL"
                type="url"
                value={url}
                placeholder="https://example.com/product"
                onChange={(event) => setUrl(event.target.value)}
              />
            </label>
            <label>
              Display name
              <input
                aria-label="Research source name"
                value={displayName}
                maxLength={200}
                onChange={(event) => setDisplayName(event.target.value)}
              />
            </label>
            <label>
              Category
              <select
                aria-label="Research category"
                value={category}
                onChange={(event) =>
                  setCategory(event.target.value as SourceCategory)
                }
              >
                {[
                  "competitor",
                  "product_page",
                  "landing_page",
                  "pricing",
                  "review",
                  "market_reference",
                  "creative_reference",
                  "other",
                ].map((value) => (
                  <option key={value} value={value}>
                    {value.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="primary"
              disabled={!url || !displayName || !!busy}
              onClick={() => void add()}
            >
              {busy === "new" ? "Fetching…" : "Add and fetch source"}
            </button>
          </div>
        ) : (
          <p className="readonly-note">
            You have read-only access to research evidence.
          </p>
        )}
      </section>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="success-banner" role="status">
          {notice}
        </p>
      )}
      {sources.length ? (
        <div className="research-grid">
          {sources.map((item) => (
            <article className="research-card" key={item.source.id}>
              <div className="research-card-heading">
                <div>
                  <span className="research-category">
                    {item.source.category.replaceAll("_", " ")}
                  </span>
                  <h3>{item.source.display_name}</h3>
                  <a
                    href={item.source.canonical_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={item.source.canonical_url}
                  >
                    {safeSourceLabel(item.source.canonical_url)}
                  </a>
                </div>
                <span
                  className={`fetch-state ${item.latest?.status ?? "pending"}`}
                >
                  {busy === item.source.id
                    ? "fetching"
                    : (item.latest?.status ?? "not fetched")}
                </span>
              </div>
              {item.latest?.failure_code && (
                <p className="research-failure">
                  Fetch rejected:{" "}
                  {item.latest.failure_code.replaceAll("_", " ")}
                </p>
              )}
              <small>
                {item.latest?.completed_at
                  ? `Last checked ${new Date(item.latest.completed_at).toLocaleString()}`
                  : "No completed fetch"}
              </small>
              <div className="research-actions">
                {item.latest?.evidence_snapshot_id && (
                  <button
                    className="secondary"
                    onClick={() =>
                      void viewEvidence(item.latest!.evidence_snapshot_id!)
                    }
                  >
                    View evidence
                  </button>
                )}
                {item.source.can_edit && item.source.status === "active" && (
                  <>
                    <button
                      className="secondary"
                      disabled={!!busy}
                      onClick={() => void refresh(item)}
                    >
                      Refresh
                    </button>
                    <button
                      className="text-danger"
                      disabled={!!busy}
                      onClick={async () => {
                        await catalogApi.archiveResearchSource(
                          session,
                          item.source.id,
                        );
                        await load();
                      }}
                    >
                      Archive
                    </button>
                  </>
                )}
              </div>
              {!!item.history.length && (
                <details className="fetch-history">
                  <summary>Fetch history ({item.history.length})</summary>
                  <ol>
                    {item.history.map((fetch, index) => (
                      <li key={fetch.id}>
                        <span>{fetch.status.replaceAll("_", " ")}</span>
                        <small>
                          {fetch.status !== "succeeded"
                            ? (fetch.failure_code?.replaceAll("_", " ") ??
                              "no evidence")
                            : index === item.history.length - 1
                              ? "first capture"
                              : fetch.evidence_snapshot_id &&
                                  fetch.evidence_snapshot_id ===
                                    item.history[index + 1]
                                      ?.evidence_snapshot_id
                                ? "content unchanged"
                                : "content changed"}
                        </small>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </article>
          ))}
        </div>
      ) : (
        <section className="empty-panel">
          <span className="empty-glyph">R</span>
          <h2>No research sources yet</h2>
          <p>Add a public page to establish traceable product evidence.</p>
        </section>
      )}
      {evidence && (
        <section className="evidence-viewer" aria-label="Evidence viewer">
          <div>
            <p className="eyebrow">Untrusted external evidence</p>
            <h3>{evidence.title || "Captured evidence"}</h3>
            <p>
              This content was retrieved from an external source and is not
              treated as verified Product truth.
            </p>
            <div className="evidence-metadata">
              <a
                href={evidence.final_url}
                target="_blank"
                rel="noopener noreferrer"
                title={evidence.final_url}
              >
                {safeSourceLabel(evidence.final_url)}
              </a>
              <small>
                Retrieved {new Date(evidence.captured_at).toLocaleString()}
              </small>
              <small title={evidence.semantic_digest}>
                Digest {evidence.semantic_digest.slice(0, 19)}…
              </small>
              {Object.entries(evidence.structured_metadata).map(
                ([key, value]) => (
                  <small key={key}>
                    {key.replaceAll("_", " ")}: {value}
                  </small>
                ),
              )}
            </div>
          </div>
          {evidence.instruction_like_content && (
            <p className="warning-banner">
              Instruction-like text detected. It remains data and is never
              treated as an instruction.
            </p>
          )}
          <div className="evidence-blocks">
            {evidence.blocks.map((block) => (
              <div
                className={`evidence-block ${block.kind}`}
                key={block.ordinal}
              >
                <small>{block.kind.replaceAll("_", " ")}</small>
                <p>{block.text}</p>
              </div>
            ))}
          </div>
          <button className="secondary" onClick={() => setEvidence(null)}>
            Close evidence
          </button>
        </section>
      )}
    </div>
  );
}

function creativeValue(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "";
}

function CreativesPanel({ workspace }: { workspace: Workspace }) {
  const session = useMemo(() => readSession(), []);
  const obsidianVaultName = process.env.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME;
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [sets, setSets] = useState<CreativeConceptSet[]>([]);
  const [research, setResearch] = useState<ResearchSnapshot[]>([]);
  const [selected, setSelected] = useState<CreativeConcept | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    const [nextRuns, nextSets, nextResearch] = await Promise.all([
      catalogApi.listCreativeRuns(session, workspace.product.id),
      catalogApi.listCreativeConceptSets(session, workspace.product.id),
      catalogApi.listResearchSnapshots(session, workspace.product.id),
    ]);
    setRuns(nextRuns);
    setSets(nextSets);
    setResearch(nextResearch);
  }, [session, workspace.product.id]);
  useEffect(() => {
    queueMicrotask(
      () =>
        void refresh().catch(() =>
          setError("Creative concepts could not be loaded."),
        ),
    );
  }, [refresh]);
  const latestRun = runs[0];
  const latest = sets[0];
  const canGenerate =
    workspace.product.can_edit &&
    workspace.completeness.score >= 80 &&
    Boolean(workspace.latest_snapshot) &&
    research[0]?.freshness === "current" &&
    !latestRun?.is_stranded;
  const decide = async (
    concept: CreativeConcept,
    state: "SHORTLISTED" | "APPROVED_FOR_PRODUCTION" | "REJECTED",
  ) => {
    await catalogApi.decideCreativeConcept(session, concept.id, state);
    await refresh();
    const updated = sets
      .flatMap((item) => item.concepts)
      .find((item) => item.id === concept.id);
    setSelected(updated ?? null);
  };
  return (
    <div className="creative-workspace">
      <section className="creative-hero">
        <div>
          <p className="eyebrow">Evidence-grounded strategy</p>
          <h2>Creative concepts</h2>
          <p>
            Turn current Product Brain and Research into production-ready
            short-form video directions. No media is generated yet.
          </p>
        </div>
        <div className="creative-readiness">
          <span>Brief {workspace.completeness.score}%</span>
          <span>
            Research{" "}
            {research[0]?.freshness === "current" ? "current" : "required"}
          </span>
          <button
            className="primary"
            disabled={!canGenerate || busy}
            onClick={() => {
              setBusy(true);
              setError("");
              void catalogApi
                .startCreativeStrategist(
                  session,
                  workspace.product.id,
                  crypto.randomUUID(),
                )
                .then(refresh)
                .catch((caught: unknown) =>
                  setError(
                    caught instanceof ApiError
                      ? caught.message.replaceAll("_", " ")
                      : "Creative Strategist could not start.",
                  ),
                )
                .finally(() => setBusy(false));
            }}
          >
            {busy ? "Queueing…" : "Generate 5 concepts"}
          </button>
        </div>
      </section>
      {workspace.completeness.score < 80 && (
        <p className="warning-banner">
          Complete at least 80% of the Product Brief before generating concepts.
        </p>
      )}
      {research[0]?.freshness !== "current" && (
        <p className="warning-banner">
          Current Research is required. Open Research and run Update Research
          first.
        </p>
      )}
      {latestRun && (
        <div className="creative-run-state">
          <strong>
            {latestRun.is_stranded
              ? "Needs operational recovery"
              : latestRun.status === "PENDING"
                ? "Queued"
                : latestRun.status === "RUNNING"
                  ? "Generating concepts"
                  : latestRun.status === "SUCCEEDED"
                    ? "Completed"
                    : "Failed"}
          </strong>
          <small>Creative Strategist v{latestRun.agent_version_number}</small>
          {obsidianVaultName && (
            <button
              className="secondary"
              onClick={() =>
                void obsidianOpenUrl(
                  obsidianVaultName,
                  "agent_run",
                  latestRun.id,
                ).then((url) => window.location.assign(url))
              }
            >
              Open run in Obsidian
            </button>
          )}
        </div>
      )}
      {error && <p className="error-banner">{error}</p>}
      {latest ? (
        <>
          {latest.freshness === "OUTDATED" && (
            <p className="warning-banner">
              These concepts were generated from outdated Product or Research
              context. Historical decisions remain visible; refresh Research and
              generate a current set before production.
            </p>
          )}
          <div className="concept-grid">
            {latest.concepts.map((concept) => {
              const payload = concept.payload as Record<string, unknown>;
              const hook = (payload.hook ?? {}) as Record<string, unknown>;
              const assets = Array.isArray(payload.required_assets)
                ? payload.required_assets
                : [];
              const missing = assets.filter(
                (item) =>
                  typeof item === "object" &&
                  item !== null &&
                  (item as Record<string, unknown>).kind === "MISSING_ASSET",
              ).length;
              return (
                <article className="concept-card" key={concept.id}>
                  <div className="concept-card-top">
                    <span>{creativeValue(payload, "channel_intent")}</span>
                    <span>
                      {creativeValue(payload, "estimated_duration_seconds")} sec
                    </span>
                  </div>
                  <h3>{creativeValue(payload, "title")}</h3>
                  <p className="concept-hook">
                    “
                    {creativeValue(hook, "on_screen_text") ||
                      creativeValue(hook, "visual_open")}
                    ”
                  </p>
                  <p>{creativeValue(payload, "creative_angle")}</p>
                  <div className="concept-meta">
                    <span>
                      {creativeValue(
                        payload,
                        "primary_success_metric",
                      ).replaceAll("_", " ")}
                    </span>
                    <span>
                      {assets.length - missing} ready · {missing} missing
                    </span>
                    <span>{concept.decision_state ?? "UNREVIEWED"}</span>
                  </div>
                  <button
                    className="secondary"
                    onClick={() => setSelected(concept)}
                  >
                    Inspect concept
                  </button>
                </article>
              );
            })}
          </div>
        </>
      ) : (
        <section className="empty-panel">
          <span className="empty-glyph">C</span>
          <h2>No concepts yet</h2>
          <p>
            Run Researcher, complete the Brief, then generate a strategy set.
          </p>
        </section>
      )}
      {selected && (
        <section
          className="concept-detail"
          aria-label="Creative concept detail"
        >
          <button className="secondary" onClick={() => setSelected(null)}>
            Close
          </button>
          <p className="eyebrow">Structured production direction</p>
          <h2>
            {creativeValue(
              selected.payload as Record<string, unknown>,
              "title",
            )}
          </h2>
          {obsidianVaultName && (
            <button
              className="secondary"
              onClick={() =>
                void obsidianOpenUrl(
                  obsidianVaultName,
                  "creative_concept",
                  selected.id,
                ).then((url) => window.location.assign(url))
              }
            >
              Open concept in Obsidian
            </button>
          )}
          <p>
            {creativeValue(
              selected.payload as Record<string, unknown>,
              "strategic_rationale",
            )}
          </p>
          <h3>Hook</h3>
          {Object.entries(
            ((selected.payload as Record<string, unknown>).hook ??
              {}) as Record<string, unknown>,
          ).map(([key, value]) =>
            value ? (
              <p key={key}>
                <strong>{key.replaceAll("_", " ")}: </strong>
                {String(value)}
              </p>
            ) : null,
          )}
          <h3>Scenes</h3>
          <ol>
            {(Array.isArray(selected.payload.scenes)
              ? selected.payload.scenes
              : []
            ).map((scene, index) => {
              const value = scene as Record<string, unknown>;
              return (
                <li key={index}>
                  <strong>{creativeValue(value, "purpose")}</strong>
                  <p>{creativeValue(value, "visual_direction")}</p>
                  <small>{creativeValue(value, "voiceover")}</small>
                </li>
              );
            })}
          </ol>
          <h3>Experiment hypothesis</h3>
          <p>
            {creativeValue(
              selected.payload as Record<string, unknown>,
              "hypothesis",
            )}
          </p>
          <h3>CTA</h3>
          <p>
            {creativeValue(
              ((selected.payload as Record<string, unknown>).cta ??
                {}) as Record<string, unknown>,
              "text",
            )}{" "}
            <small>
              {creativeValue(
                ((selected.payload as Record<string, unknown>).cta ??
                  {}) as Record<string, unknown>,
                "intent",
              ).replaceAll("_", " ")}
            </small>
          </p>
          <h3>Message points &amp; claims</h3>
          <ul>
            {(Array.isArray(selected.payload.message_points)
              ? selected.payload.message_points
              : []
            ).map((point, index) => {
              const value = point as Record<string, unknown>;
              return (
                <li key={index}>
                  {creativeValue(value, "text")}{" "}
                  {creativeValue(value, "kind") === "PRODUCT_FACT" && (
                    <small>Grounded in Product Brain</small>
                  )}
                </li>
              );
            })}
          </ul>
          <h3>Research support</h3>
          <ul>
            {(Array.isArray(selected.payload.supporting_research_refs)
              ? selected.payload.supporting_research_refs
              : []
            ).map((reference, index) => {
              const value = reference as Record<string, unknown>;
              const findingKey = creativeValue(value, "finding_key");
              const finding = research
                .flatMap((snapshot) => snapshot.findings)
                .find((item) => item.key === findingKey);
              return (
                <li key={index}>
                  <strong>{findingKey.replaceAll("_", " ")}</strong>
                  {finding && <p>{finding.statement}</p>}
                  <small>
                    Open Research to inspect its evidence citations.
                  </small>
                </li>
              );
            })}
          </ul>
          <h3>Production assets</h3>
          <ul>
            {(Array.isArray(selected.payload.required_assets)
              ? selected.payload.required_assets
              : []
            ).map((requirement, index) => {
              const value = requirement as Record<string, unknown>;
              return (
                <li key={index}>
                  <strong>
                    {creativeValue(value, "kind").replaceAll("_", " ")}
                  </strong>{" "}
                  {creativeValue(value, "description") ||
                    creativeValue(value, "intended_role")}
                  {creativeValue(value, "asset_id") && (
                    <small> · Asset {creativeValue(value, "asset_id")}</small>
                  )}
                </li>
              );
            })}
          </ul>
          {Array.isArray(selected.payload.required_disclaimers) &&
            selected.payload.required_disclaimers.length > 0 && (
              <>
                <h3>Required disclaimers</h3>
                <ul>
                  {selected.payload.required_disclaimers.map((value, index) => (
                    <li key={index}>{String(value)}</li>
                  ))}
                </ul>
              </>
            )}
          <h3>Production notes</h3>
          <p>
            {creativeValue(
              selected.payload as Record<string, unknown>,
              "production_notes",
            )}
          </p>
          {workspace.product.can_edit ? (
            <div className="concept-actions">
              <button onClick={() => void decide(selected, "SHORTLISTED")}>
                Shortlist
              </button>
              <button
                className="primary"
                onClick={() => void decide(selected, "APPROVED_FOR_PRODUCTION")}
              >
                Approve for production
              </button>
              <button
                className="danger"
                onClick={() => void decide(selected, "REJECTED")}
              >
                Reject
              </button>
            </div>
          ) : (
            <p className="readonly-note">
              Creative review is read-only for members.
            </p>
          )}
          <small>
            Approval accepts this concept as a future Production input. It does
            not authorize media spend or publishing.
          </small>
        </section>
      )}
    </div>
  );
}

function ProductionPanel({
  workspace,
  onOpenAssets,
}: {
  workspace: Workspace;
  onOpenAssets: () => void;
}) {
  const session = useMemo(() => readSession(), []);
  const obsidianVaultName = process.env.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME;
  const [plans, setPlans] = useState<ProductionPlan[]>([]);
  const [jobs, setJobs] = useState<ProductionJob[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [concept, setConcept] = useState<CreativeConcept | null>(null);
  const [assemblyPlans, setAssemblyPlans] = useState<AssemblyPlan[]>([]);
  const [assemblyReadiness, setAssemblyReadiness] =
    useState<AssemblyReadiness | null>(null);
  const [finalCreative, setFinalCreative] = useState<FinalCreative | null>(
    null,
  );
  const [sourceAssets, setSourceAssets] = useState<Asset[]>([]);
  const [finalPreview, setFinalPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    const [nextPlans, conceptSets, nextRuns] = await Promise.all([
      catalogApi.listProductionPlans(session, workspace.product.id),
      catalogApi.listCreativeConceptSets(session, workspace.product.id),
      catalogApi.listProducerRuns(session, workspace.product.id),
    ]);
    setPlans(nextPlans);
    setRuns(nextRuns);
    setConcept(
      conceptSets
        .flatMap((item) => item.concepts)
        .find((item) => item.decision_state === "APPROVED_FOR_PRODUCTION") ??
        null,
    );
    if (!nextPlans[0]) {
      setJobs([]);
      setAssemblyPlans([]);
      setAssemblyReadiness(null);
      setFinalCreative(null);
      return;
    }
    const [nextJobs, readiness, nextAssemblies, assets] = await Promise.all([
      catalogApi.listProductionJobs(session, nextPlans[0].id),
      catalogApi.getAssemblyReadiness(session, nextPlans[0].id),
      catalogApi.listAssemblyPlans(session, nextPlans[0].id),
      catalogApi.listAssets(session, workspace.product.id),
    ]);
    setJobs(nextJobs);
    setAssemblyReadiness(readiness);
    setAssemblyPlans(nextAssemblies);
    setSourceAssets(assets);
    const finalId = nextAssemblies[0]?.job.final_creative_id;
    setFinalCreative(
      finalId ? await catalogApi.getFinalCreative(session, finalId) : null,
    );
  }, [session, workspace.product.id]);
  useEffect(() => {
    queueMicrotask(
      () =>
        void refresh().catch(() => setError("Production could not be loaded.")),
    );
  }, [refresh]);
  useEffect(() => {
    const active =
      jobs.some((job) =>
        ["READY", "STARTING", "PROCESSING", "IMPORTING"].includes(job.status),
      ) ||
      ["PENDING", "READY", "RENDERING", "IMPORTING"].includes(
        assemblyPlans[0]?.job.status ?? "",
      );
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => window.clearInterval(timer);
  }, [assemblyPlans, jobs, refresh]);
  useEffect(() => {
    for (const job of jobs) {
      if (!job.output_asset_id || previews[job.id]) continue;
      void catalogApi
        .downloadAsset(session, job.output_asset_id)
        .then((grant) =>
          setPreviews((current) => ({ ...current, [job.id]: grant.url })),
        );
    }
  }, [jobs, previews, session]);
  useEffect(() => {
    if (!finalCreative || finalPreview) return;
    void catalogApi
      .downloadAsset(session, finalCreative.output_asset_id)
      .then((grant) => setFinalPreview(grant.url));
  }, [finalCreative, finalPreview, session]);
  const plan = plans[0];
  const producerRun = runs[0];
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
      await refresh();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Production action failed.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="creative-workspace">
      <section className="creative-hero">
        <div>
          <p className="eyebrow">Governed media production</p>
          <h2>Production</h2>
          <p>
            Astra plans. You approve exact cost and media work before providers
            run.
          </p>
        </div>
        <div className="creative-readiness">
          <span>Approved Concept: {concept ? "Ready" : "Required"}</span>
          <span>Creative context: {concept ? "Current" : "Unavailable"}</span>
          <span>
            Producer:{" "}
            {producerRun?.is_stranded
              ? "Needs operational recovery"
              : producerRun?.status === "RUNNING"
                ? "Planning"
                : producerRun?.status === "PENDING"
                  ? "Queued"
                  : producerRun?.status === "FAILED"
                    ? "Failed"
                    : plan
                      ? "Completed"
                      : concept
                        ? "Ready"
                        : "Waiting"}
          </span>
          {workspace.product.can_edit && concept && !plan && (
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                void act(() =>
                  catalogApi.startProducer(
                    session,
                    concept.id,
                    crypto.randomUUID(),
                  ),
                )
              }
            >
              Create Production Plan
            </button>
          )}
        </div>
      </section>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {plan ? (
        <section className="concept-detail">
          <p className="eyebrow">{plan.status.replaceAll("_", " ")}</p>
          <h3>Production plan</h3>
          <p>{plan.strategy}</p>
          {concept && (
            <p>
              Source concept:{" "}
              <strong>
                {String(concept.payload.title ?? concept.concept_key)}
              </strong>
            </p>
          )}
          {obsidianVaultName && (
            <button
              className="secondary"
              onClick={() =>
                void obsidianOpenUrl(
                  obsidianVaultName,
                  "production_plan",
                  plan.id,
                ).then((url) => window.location.assign(url))
              }
            >
              Open plan in Obsidian
            </button>
          )}
          {obsidianVaultName && (
            <button
              className="secondary"
              onClick={() =>
                void obsidianOpenUrl(
                  obsidianVaultName,
                  "agent_run",
                  plan.agent_run_id,
                ).then((url) => window.location.assign(url))
              }
            >
              Open Producer run in Obsidian
            </button>
          )}
          {plan.scenes.map((scene) => (
            <div key={scene.scene_key} className="concept-scene">
              <strong>
                Scene {scene.ordinal} · {scene.purpose} ·{" "}
                {scene.duration_seconds}s
              </strong>
              <p>{scene.message}</p>
              <ul>
                {scene.shots.map((shot) => (
                  <li key={shot.shot_key}>
                    {shot.shot_key.replaceAll("_", " ")} —{" "}
                    {shot.source_strategy.replaceAll("_", " ").toLowerCase()}
                    {obsidianVaultName && (
                      <button
                        className="secondary"
                        onClick={() =>
                          void obsidianOpenUrl(
                            obsidianVaultName,
                            "production_shot",
                            `${plan.id}:${shot.shot_key}`,
                          ).then((url) => window.location.assign(url))
                        }
                      >
                        Open in Obsidian
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <h3>Generation segments</h3>
          <div className="concept-grid">
            {plan.generation_segments.map((segment) => (
              <article key={segment.segment_key} className="concept-card">
                <p className="eyebrow">
                  {segment.media_kind === "IMAGE"
                    ? "Image generation"
                    : "Video generation"}
                </p>
                <strong>{segment.segment_key.replaceAll("_", " ")}</strong>
                <p>
                  {segment.shot_keys
                    .map((shot) => shot.replaceAll("_", " "))
                    .join(" → ")}
                  {segment.duration_seconds
                    ? ` · ${segment.duration_seconds}s`
                    : ""}
                </p>
                <details>
                  <summary>Technical specification</summary>
                  <dl>
                    {Object.entries(segment.generation_spec).map(
                      ([key, value]) => (
                        <div key={key}>
                          <dt>{key.replaceAll("_", " ")}</dt>
                          <dd>
                            {Array.isArray(value)
                              ? value.join(", ")
                              : String(value)}
                          </dd>
                        </div>
                      ),
                    )}
                  </dl>
                </details>
                {obsidianVaultName && (
                  <button
                    className="secondary"
                    onClick={() =>
                      void obsidianOpenUrl(
                        obsidianVaultName,
                        "generation_segment",
                        segment.id,
                      ).then((url) => window.location.assign(url))
                    }
                  >
                    Open in Obsidian
                  </button>
                )}
              </article>
            ))}
          </div>
          <div className="creative-readiness">
            <span>
              Astra planning: {plan.planning_cost} {plan.currency}
            </span>
            <span>
              Images: {plan.estimated_max_image_cost} {plan.currency}
            </span>
            <span>
              Video generation: {plan.estimated_max_video_cost} {plan.currency}
            </span>
            <strong>
              Total generation budget: {plan.estimated_total_cost}{" "}
              {plan.currency}
            </strong>
          </div>
          {workspace.product.can_edit && plan.status === "UNREVIEWED" && (
            <div className="concept-actions">
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  void act(() =>
                    catalogApi.approveProductionPlan(session, plan.id),
                  )
                }
              >
                Approve &amp; Generate
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void act(() =>
                    catalogApi.rejectProductionPlan(session, plan.id),
                  )
                }
              >
                Reject
              </button>
            </div>
          )}
          <h3>Generation progress</h3>
          {jobs.length ? (
            <div className="concept-grid">
              {jobs.map((job) => (
                <article key={job.id} className="concept-card">
                  <p className="eyebrow">
                    {job.kind === "IMAGE"
                      ? "Image generation"
                      : "Video generation"}
                  </p>
                  {job.local_demo_provider && (
                    <span className="status draft">Local demo provider</span>
                  )}
                  <h4>
                    {{
                      READY: "Waiting for production worker",
                      STARTING: "Starting",
                      PROCESSING: "Generating",
                      IMPORTING: "Importing",
                      SUCCEEDED: "Ready for use",
                      FAILED: "Failed",
                      OUTCOME_UNKNOWN: "Needs operational recovery",
                    }[job.status] ?? job.status.replaceAll("_", " ")}
                  </h4>
                  <p>
                    Reserved: {job.reserved_cost} {job.currency} · Actual:{" "}
                    {job.status === "SUCCEEDED"
                      ? `${job.actual_cost} ${job.currency}`
                      : job.status === "OUTCOME_UNKNOWN"
                        ? `unknown (${job.unknown_cost} ${job.currency} reserved)`
                        : "not settled"}
                  </p>
                  {job.failure_code && (
                    <p className="error">{job.failure_code}</p>
                  )}
                  {previews[job.id] && job.kind === "IMAGE" && (
                    <img
                      className="asset-preview"
                      src={previews[job.id]}
                      alt="Generated demo output"
                    />
                  )}
                  {previews[job.id] && job.kind === "VIDEO" && (
                    <video
                      className="asset-preview"
                      src={previews[job.id]}
                      controls
                    />
                  )}
                  {job.output_asset_id && (
                    <div className="concept-actions">
                      <code>{job.output_asset_id}</code>
                      <a
                        className="button secondary"
                        href={previews[job.id]}
                        target="_blank"
                      >
                        Preview
                      </a>
                      <button className="secondary" onClick={onOpenAssets}>
                        Open Asset
                      </button>
                      {obsidianVaultName && (
                        <button
                          className="secondary"
                          onClick={() =>
                            void obsidianOpenUrl(
                              obsidianVaultName,
                              "generation_job",
                              job.id,
                            ).then((url) => window.location.assign(url))
                          }
                        >
                          Open job in Obsidian
                        </button>
                      )}
                      {obsidianVaultName && (
                        <button
                          className="secondary"
                          onClick={() =>
                            void obsidianOpenUrl(
                              obsidianVaultName,
                              "asset",
                              job.output_asset_id!,
                            ).then((url) => window.location.assign(url))
                          }
                        >
                          Open Asset in Obsidian
                        </button>
                      )}
                    </div>
                  )}
                  <details>
                    <summary>Provider details</summary>
                    <p>
                      {job.model} · {job.provider} · {job.media_profile}
                    </p>
                  </details>
                </article>
              ))}
            </div>
          ) : (
            <p>No media jobs started.</p>
          )}
          <div className="final-assembly">
            <p className="eyebrow">Deterministic editing</p>
            <h3>Final Assembly</h3>
            <p>
              Source readiness:{" "}
              <strong>
                {assemblyReadiness?.status.replaceAll("_", " ") ?? "Checking"}
              </strong>
            </p>
            <div className="concept-grid">
              {plan.scenes.flatMap((scene) =>
                scene.shots.map((shot) => {
                  const source = assemblyReadiness?.sources.find(
                    (item) => item.shot_key === shot.shot_key,
                  );
                  return (
                    <article
                      key={`assembly-${shot.id}`}
                      className="concept-card"
                    >
                      <strong>{shot.shot_key.replaceAll("_", " ")}</strong>
                      <p>{source?.status.replaceAll("_", " ") ?? "Checking"}</p>
                      {shot.source_strategy === "MANUAL_CAPTURE" &&
                        source?.status === "MISSING_MANUAL_MEDIA" &&
                        workspace.product.can_edit && (
                          <label>
                            Manual footage required
                            <select
                              defaultValue=""
                              disabled={busy}
                              onChange={(event) => {
                                const assetId = event.currentTarget.value;
                                if (assetId)
                                  void act(() =>
                                    catalogApi.bindManualSource(
                                      session,
                                      shot.id,
                                      assetId,
                                    ),
                                  );
                              }}
                            >
                              <option value="">Select Asset</option>
                              {sourceAssets
                                .filter(
                                  (asset) =>
                                    asset.kind === "video" &&
                                    asset.status === "ready" &&
                                    asset.rights_status === "confirmed" &&
                                    asset.allowed_uses.includes(
                                      "generation_input",
                                    ),
                                )
                                .map((asset) => (
                                  <option key={asset.id} value={asset.id}>
                                    {asset.original_filename}
                                  </option>
                                ))}
                            </select>
                          </label>
                        )}
                    </article>
                  );
                }),
              )}
            </div>
            {assemblyPlans[0] && (
              <>
                <h4>Assembly timeline</h4>
                <ol className="timeline-list">
                  {assemblyPlans[0].items.map((item) => (
                    <li key={item.item_key}>
                      <strong>
                        {(item.timeline_start_ms / 1000).toFixed(1)}s
                      </strong>{" "}
                      {item.production_shot_keys.join(", ")} ·{" "}
                      {item.source_kind.replaceAll("_", " ").toLowerCase()} ·{" "}
                      {(item.timeline_duration_ms / 1000).toFixed(1)}s ·{" "}
                      {item.transition_out} · Audio{" "}
                      {item.audio_behavior.toLowerCase()}
                    </li>
                  ))}
                </ol>
                <p>
                  {assemblyPlans[0].overlays.length} overlays ·{" "}
                  {assemblyPlans[0].captions.length} scripted captions ·{" "}
                  {assemblyPlans[0].render_profile_key}
                </p>
                <p>
                  Render:{" "}
                  <strong>
                    {(
                      {
                        READY: "Preparing",
                        RENDERING: "Rendering",
                        IMPORTING: "Importing",
                        SUCCEEDED: "Ready",
                        FAILED: "Failed",
                      } as Record<string, string>
                    )[assemblyPlans[0].job.status] ??
                      assemblyPlans[0].job.status}
                  </strong>
                </p>
                {obsidianVaultName && (
                  <div className="concept-actions">
                    <button
                      className="secondary"
                      onClick={() =>
                        void obsidianOpenUrl(
                          obsidianVaultName,
                          "assembly_plan",
                          assemblyPlans[0]!.id,
                        ).then((url) => window.location.assign(url))
                      }
                    >
                      Open Assembly Plan in Obsidian
                    </button>
                    <button
                      className="secondary"
                      onClick={() =>
                        void obsidianOpenUrl(
                          obsidianVaultName,
                          "assembly_job",
                          assemblyPlans[0]!.job.id,
                        ).then((url) => window.location.assign(url))
                      }
                    >
                      Open Assembly Job in Obsidian
                    </button>
                  </div>
                )}
                {assemblyPlans[0].job.failure_code && (
                  <p className="error">{assemblyPlans[0].job.failure_code}</p>
                )}
              </>
            )}
            {workspace.product.can_edit &&
              assemblyReadiness?.ready &&
              !assemblyPlans.length && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    void act(() =>
                      catalogApi.createAssemblyPlan(session, plan.id),
                    )
                  }
                >
                  Assemble Final Creative
                </button>
              )}
            {finalCreative && (
              <article className="concept-card final-preview">
                <p className="eyebrow">Assembled final creative</p>
                <h4>Publish-ready candidate</h4>
                {finalPreview && (
                  <video
                    className="asset-preview"
                    src={finalPreview}
                    controls
                  />
                )}
                <p>
                  {(finalCreative.duration_ms / 1000).toFixed(1)}s · 9:16 ·{" "}
                  {finalCreative.width}×{finalCreative.height} ·{" "}
                  {finalCreative.fps} FPS · Audio:{" "}
                  {finalCreative.has_audio ? "source audio" : "none"}
                </p>
                <p>
                  {finalCreative.render_profile_key} v
                  {finalCreative.render_profile_version} ·{" "}
                  {finalCreative.source_count} sources ·{" "}
                  {finalCreative.decision_state?.replaceAll("_", " ") ??
                    "Awaiting review"}
                </p>
                <div className="concept-actions">
                  <button className="secondary" onClick={onOpenAssets}>
                    Open Final Asset
                  </button>
                  {workspace.product.can_edit && (
                    <>
                      <button
                        className="primary"
                        disabled={busy}
                        onClick={() =>
                          void act(() =>
                            catalogApi.approveFinalCreative(
                              session,
                              finalCreative.id,
                            ),
                          )
                        }
                      >
                        Approve for publishing
                      </button>
                      <button
                        className="danger"
                        disabled={busy}
                        onClick={() =>
                          void act(() =>
                            catalogApi.rejectFinalCreative(
                              session,
                              finalCreative.id,
                            ),
                          )
                        }
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {obsidianVaultName && (
                    <button
                      className="secondary"
                      onClick={() =>
                        void obsidianOpenUrl(
                          obsidianVaultName,
                          "final_creative",
                          finalCreative.id,
                        ).then((url) => window.location.assign(url))
                      }
                    >
                      Open in Obsidian
                    </button>
                  )}
                  {obsidianVaultName && (
                    <button
                      className="secondary"
                      onClick={() =>
                        void obsidianOpenUrl(
                          obsidianVaultName,
                          "asset",
                          finalCreative.output_asset_id,
                        ).then((url) => window.location.assign(url))
                      }
                    >
                      Open Final Asset in Obsidian
                    </button>
                  )}
                </div>
                <small>
                  Approval accepts this exact immutable render as a publishing
                  candidate. It does not publish anything.
                </small>
              </article>
            )}
          </div>
        </section>
      ) : (
        <section className="empty-panel">
          <h2>No production plan yet</h2>
          <p>Approve a current creative concept to unlock Astra Producer.</p>
        </section>
      )}
    </div>
  );
}

function PillList({
  values,
  empty = "Not provided",
}: {
  values: string[];
  empty?: string;
}) {
  if (!values.length) return <p className="muted">{empty}</p>;
  return (
    <div className="pill-list">
      {values.map((value) => (
        <span key={value}>{value}</span>
      ))}
    </div>
  );
}

function Overview({
  workspace,
  onSnapshot,
}: {
  workspace: Workspace;
  onSnapshot: () => Promise<void>;
}) {
  const { product, brand, completeness, latest_snapshot: snapshot } = workspace;
  return (
    <div className="overview-grid">
      <section className="hero-card">
        <div>
          <p className="eyebrow">
            {brand.name} · {product.category}
          </p>
          <h2>{product.name}</h2>
          <p>
            {product.short_description ||
              product.profile?.description ||
              "Add a concise product description to orient your team."}
          </p>
        </div>
        <div className="hero-meta">
          <span className={`status ${product.status}`}>{product.status}</span>
          {product.profile?.price && (
            <strong>
              {product.profile.currency} {product.profile.price}
            </strong>
          )}
        </div>
      </section>
      <section className="progress-card">
        <div
          className="progress-ring"
          style={
            {
              "--progress": `${completeness.score * 3.6}deg`,
            } as React.CSSProperties
          }
        >
          <span>{completeness.score}%</span>
        </div>
        <div>
          <p className="eyebrow">Brief health</p>
          <h3>
            {completeness.score === 100
              ? "Ready for creative work"
              : "Context still needed"}
          </h3>
          <p>
            {completeness.missing_sections.length
              ? `Complete ${completeness.missing_sections.slice(0, 2).join(" and ")} next.`
              : "All core briefing sections are complete."}
          </p>
        </div>
      </section>
      <section className="data-card">
        <p className="eyebrow">Core audiences</p>
        <PillList
          values={(product.profile?.target_audiences ?? []).map(
            (audience) => audience.name,
          )}
        />
      </section>
      <section className="data-card">
        <p className="eyebrow">Features</p>
        <PillList values={product.profile?.features ?? []} />
      </section>
      <section className="data-card">
        <p className="eyebrow">Benefits</p>
        <PillList values={product.profile?.benefits ?? []} />
      </section>
      <section className="data-card">
        <p className="eyebrow">Differentiators</p>
        <PillList values={product.profile?.differentiators ?? []} />
      </section>
      <section className="data-card wide">
        <p className="eyebrow">Claims & constraints</p>
        <div className="claim-columns">
          <div>
            <h4>Allowed</h4>
            <PillList values={product.profile?.allowed_claims ?? []} />
          </div>
          <div>
            <h4>Prohibited</h4>
            <PillList values={product.profile?.prohibited_claims ?? []} />
          </div>
        </div>
      </section>
      <section className="snapshot-card">
        <div>
          <p className="eyebrow">Knowledge snapshot</p>
          <h3>
            {snapshot
              ? `Revision ${snapshot.source_revision}`
              : "No saved snapshot"}
          </h3>
          <p className="digest">
            {snapshot?.digest ??
              "Create a reproducible context boundary when this brief is ready."}
          </p>
        </div>
        {product.can_edit && (
          <button className="secondary" onClick={() => void onSnapshot()}>
            Save brief version
          </button>
        )}
      </section>
    </div>
  );
}

type BriefField = keyof BriefWrite;

function TextArea({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {hint && <small>{hint}</small>}
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={4}
      />
    </label>
  );
}

function ListArea({
  label,
  values,
  onChange,
  hint,
}: {
  label: string;
  values: string[];
  onChange: (value: string[]) => void;
  hint?: string;
}) {
  return (
    <TextArea
      label={label}
      hint={hint ?? "One item per line"}
      value={values.join("\n")}
      onChange={(value) => onChange(listText(value))}
    />
  );
}

function BriefEditor({
  workspace,
  onSaved,
}: {
  workspace: Workspace;
  onSaved: (workspace: Workspace) => void;
}) {
  const [section, setSection] = useState(0);
  const [brief, setBrief] = useState<BriefWrite>(() => ({
    ...emptyBrief,
    ...workspace.brief,
  }));
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");
  const [error, setError] = useState("");
  const readOnly = !workspace.brief.can_edit;
  const set = <K extends BriefField>(key: K, value: BriefWrite[K]) => {
    setBrief((current) => ({ ...current, [key]: value }));
    setSaveState("idle");
  };
  const save = async () => {
    setSaveState("saving");
    setError("");
    try {
      const saved = await catalogApi.saveBrief(
        readSession(),
        workspace.product.id,
        brief,
      );
      const updated = await catalogApi.getWorkspace(
        readSession(),
        workspace.product.id,
      );
      onSaved({ ...updated, brief: saved });
      setSaveState("saved");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Save failed");
      setSaveState("error");
    }
  };
  return (
    <div className="brief-layout">
      <nav className="section-nav" aria-label="Brief sections">
        {briefSections.map((name, index) => (
          <button
            key={name}
            className={section === index ? "active" : ""}
            onClick={() => setSection(index)}
          >
            <span>{index + 1}</span>
            {name}
          </button>
        ))}
      </nav>
      <section className="brief-card">
        <header>
          <div>
            <p className="eyebrow">
              Section {section + 1} of {briefSections.length}
            </p>
            <h2>{briefSections[section]}</h2>
          </div>
          <div className={`save-state ${saveState}`}>
            {saveState === "idle"
              ? "Unsaved changes"
              : saveState === "saving"
                ? "Saving…"
                : saveState === "saved"
                  ? "Saved"
                  : "Error saving"}
          </div>
        </header>
        <fieldset disabled={readOnly}>
          {section === 0 && (
            <>
              <TextArea
                label="Why does this product exist?"
                value={brief.product_why ?? ""}
                onChange={(v) => set("product_why", v)}
              />
              <ListArea
                label="Emotional benefits"
                values={brief.emotional_benefits ?? []}
                onChange={(v) => set("emotional_benefits", v)}
              />
            </>
          )}
          {section === 1 && (
            <>
              <TextArea
                label="Primary audience"
                value={brief.primary_audience?.name ?? ""}
                onChange={(v) =>
                  set("primary_audience", {
                    name: v,
                    description: brief.primary_audience?.description ?? "",
                    pain_points: brief.primary_audience?.pain_points ?? [],
                    desires: brief.primary_audience?.desires ?? [],
                    motivations: brief.primary_audience?.motivations ?? [],
                    objections: brief.primary_audience?.objections ?? [],
                  })
                }
              />
              <ListArea
                label="Pain points"
                values={brief.primary_audience?.pain_points ?? []}
                onChange={(v) =>
                  set("primary_audience", {
                    name: brief.primary_audience?.name ?? "Primary audience",
                    description: brief.primary_audience?.description ?? "",
                    pain_points: v,
                    desires: brief.primary_audience?.desires ?? [],
                    motivations: brief.primary_audience?.motivations ?? [],
                    objections: brief.primary_audience?.objections ?? [],
                  })
                }
              />
              <ListArea
                label="Purchase objections"
                values={brief.primary_audience?.objections ?? []}
                onChange={(v) =>
                  set("primary_audience", {
                    name: brief.primary_audience?.name ?? "Primary audience",
                    description: brief.primary_audience?.description ?? "",
                    pain_points: brief.primary_audience?.pain_points ?? [],
                    desires: brief.primary_audience?.desires ?? [],
                    motivations: brief.primary_audience?.motivations ?? [],
                    objections: v,
                  })
                }
              />
            </>
          )}
          {section === 2 && (
            <>
              <TextArea
                label="Positioning statement"
                value={brief.positioning_statement ?? ""}
                onChange={(v) => set("positioning_statement", v)}
              />
              <ListArea
                label="Competitive alternatives"
                values={brief.competitive_alternatives ?? []}
                onChange={(v) => set("competitive_alternatives", v)}
              />
              <ListArea
                label="Why choose us?"
                values={brief.why_choose_us ?? []}
                onChange={(v) => set("why_choose_us", v)}
              />
            </>
          )}
          {section === 3 && (
            <>
              <TextArea
                label="Primary conversion goal"
                value={brief.conversion_goal ?? ""}
                onChange={(v) => set("conversion_goal", v)}
              />
              <ListArea
                label="Priority channels"
                values={brief.priority_channels ?? []}
                onChange={(v) => set("priority_channels", v)}
              />
              <ListArea
                label="Offers & promotions"
                values={brief.offers ?? []}
                onChange={(v) => set("offers", v)}
              />
            </>
          )}
          {section === 4 && (
            <>
              <TextArea
                label="Desired creative style"
                value={brief.desired_creative_style ?? ""}
                onChange={(v) => set("desired_creative_style", v)}
              />
              <ListArea
                label="Tones to explore"
                values={brief.tones_to_explore ?? []}
                onChange={(v) => set("tones_to_explore", v)}
              />
              <ListArea
                label="Tones to avoid"
                values={brief.tones_to_avoid ?? []}
                onChange={(v) => set("tones_to_avoid", v)}
              />
            </>
          )}
          {section === 5 && (
            <>
              <ListArea
                label="Mandatory messaging"
                values={brief.mandatory_messaging ?? []}
                onChange={(v) => set("mandatory_messaging", v)}
              />
              <ListArea
                label="Prohibited messaging"
                values={brief.prohibited_messaging ?? []}
                onChange={(v) => set("prohibited_messaging", v)}
              />
              <ListArea
                label="Required disclaimers"
                values={brief.required_disclaimers ?? []}
                onChange={(v) => set("required_disclaimers", v)}
              />
              <ListArea
                label="Legal & safety constraints"
                values={brief.legal_safety_constraints ?? []}
                onChange={(v) => set("legal_safety_constraints", v)}
              />
            </>
          )}
        </fieldset>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button
            className="secondary"
            disabled={section === 0}
            onClick={() => setSection((v) => v - 1)}
          >
            Back
          </button>
          {section < briefSections.length - 1 ? (
            <button
              className="primary"
              onClick={() => setSection((v) => v + 1)}
            >
              Continue
            </button>
          ) : (
            !readOnly && (
              <button
                className="primary"
                onClick={() => void save()}
                disabled={saveState === "saving"}
              >
                Save brief
              </button>
            )
          )}
        </footer>
        {readOnly && (
          <p className="readonly-note">
            You have read-only access. Ask a workspace owner or admin to update
            this brief.
          </p>
        )}
      </section>
      <aside className="completeness-panel">
        <p className="eyebrow">Completeness</p>
        <strong>{workspace.completeness.score}%</strong>
        <div className="progress-track">
          <span style={{ width: `${workspace.completeness.score}%` }} />
        </div>
        <h4>Still needed</h4>
        {workspace.completeness.missing_fields.length ? (
          <ul>
            {workspace.completeness.missing_fields.map((field) => (
              <li key={field}>{field.replaceAll("_", " ")}</li>
            ))}
          </ul>
        ) : (
          <p>Core context is complete.</p>
        )}
      </aside>
    </div>
  );
}

function readSession(): Session {
  const raw = sessionStorage.getItem("cm-session");
  if (!raw) throw new Error("Session is unavailable");
  return JSON.parse(raw) as Session;
}

export function ProductWorkspaceApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [products, setProducts] = useState<Record<string, Product[]>>({});
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [tab, setTab] = useState("Overview");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<"brand" | "product" | null>(null);
  const selectedBrand = useMemo(
    () => brands.find((brand) => brand.id === workspace?.brand.id) ?? brands[0],
    [brands, workspace],
  );
  const obsidianVaultName = process.env.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME;

  const connect = async (next: Session) => {
    sessionStorage.setItem("cm-session", JSON.stringify(next));
    setSession(next);
    setLoading(true);
    setError("");
    try {
      setBrands(await catalogApi.listBrands(next));
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 403
          ? "You do not have access to this workspace."
          : caught instanceof Error
            ? caught.message
            : "Unable to load workspace",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    const raw = sessionStorage.getItem("cm-session");
    if (raw) queueMicrotask(() => void connect(JSON.parse(raw) as Session));
  }, []);
  useEffect(() => {
    if (!session) return;
    for (const brand of brands) {
      void catalogApi
        .listProducts(session, brand.id)
        .then((items) =>
          setProducts((current) => ({ ...current, [brand.id]: items })),
        )
        .catch(() => setError("Some products could not be loaded."));
    }
  }, [brands, session]);
  const openProduct = async (id: string) => {
    if (!session) return;
    setLoading(true);
    setError("");
    try {
      setWorkspace(await catalogApi.getWorkspace(session, id));
      setTab("Overview");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to load product",
      );
    } finally {
      setLoading(false);
    }
  };
  if (!session)
    return <AccessScreen onConnect={(value) => void connect(value)} />;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="logo-row">
          <span className="brand-mark small">CM</span>
          <strong>Creative Marketer</strong>
        </div>
        <nav>
          {navigation.map((item) => (
            <button
              key={item}
              className={item === "Products" ? "active" : ""}
              disabled={item !== "Products"}
            >
              {item}
              {item !== "Products" && <span>Soon</span>}
            </button>
          ))}
        </nav>
        <div className="account">
          <span>Workspace</span>
          <code>{session.tenantId.slice(0, 8)}</code>
          <button
            onClick={() => {
              sessionStorage.removeItem("cm-session");
              setSession(null);
            }}
          >
            Disconnect
          </button>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <button className="mobile-menu" aria-label="Open navigation">
            CM
          </button>
          <div>
            <p className="eyebrow">Product brain</p>
            <h1>{workspace?.product.name ?? "Products"}</h1>
          </div>
          <div className="top-actions">
            {workspace && obsidianVaultName && (
              <button
                className="secondary"
                onClick={() => {
                  void obsidianOpenUrl(
                    obsidianVaultName,
                    "product",
                    workspace.product.id,
                  ).then((url) => window.location.assign(url));
                }}
              >
                Open in Obsidian
              </button>
            )}
            <button className="secondary" onClick={() => setDialog("brand")}>
              New brand
            </button>
            {selectedBrand?.can_edit && (
              <button className="primary" onClick={() => setDialog("product")}>
                New product
              </button>
            )}
          </div>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button onClick={() => setError("")}>Dismiss</button>
          </div>
        )}
        {loading && (
          <div className="loading" role="status">
            Loading workspace…
          </div>
        )}
        {!loading && !brands.length && (
          <section className="first-run">
            <p className="eyebrow">Start with your brand</p>
            <h2>Give every product a clear commercial home.</h2>
            <p>
              Brands keep voice, positioning, markets, and claims separate from
              each product’s own briefing.
            </p>
            <button className="primary" onClick={() => setDialog("brand")}>
              Create your first brand
            </button>
          </section>
        )}
        {!!brands.length && (
          <div className="product-layout">
            <aside className="product-tree">
              <div className="tree-title">
                <span>Brands & products</span>
                <button
                  onClick={() => setDialog("brand")}
                  aria-label="Create brand"
                >
                  +
                </button>
              </div>
              {brands.map((brand) => (
                <div className="tree-group" key={brand.id}>
                  <div className="brand-node">
                    <span>{brand.name.slice(0, 1)}</span>
                    <strong>{brand.name}</strong>
                  </div>
                  {(products[brand.id] ?? []).map((product) => (
                    <button
                      key={product.id}
                      className={
                        workspace?.product.id === product.id ? "selected" : ""
                      }
                      onClick={() => void openProduct(product.id)}
                    >
                      {product.name}
                      <small>{product.status}</small>
                    </button>
                  ))}
                  {brand.can_edit && (
                    <button
                      className="add-product"
                      onClick={() => {
                        if (!workspace || workspace.brand.id !== brand.id)
                          setWorkspace(null);
                        setDialog("product");
                      }}
                    >
                      + Add product
                    </button>
                  )}
                </div>
              ))}
            </aside>
            <section className="product-content">
              {workspace ? (
                <>
                  <nav className="tabs" aria-label="Product workspace">
                    {tabs.map((name) => (
                      <button
                        key={name}
                        className={tab === name ? "active" : ""}
                        onClick={() => setTab(name)}
                      >
                        {name}
                      </button>
                    ))}
                  </nav>
                  {tab === "Overview" ? (
                    <Overview
                      workspace={workspace}
                      onSnapshot={async () => {
                        const snapshot = await catalogApi.createSnapshot(
                          session,
                          workspace.product.id,
                        );
                        setWorkspace({
                          ...workspace,
                          latest_snapshot: snapshot,
                        });
                      }}
                    />
                  ) : tab === "Brief" ? (
                    <BriefEditor workspace={workspace} onSaved={setWorkspace} />
                  ) : tab === "Assets" ? (
                    <AssetsPanel workspace={workspace} />
                  ) : tab === "Research" ? (
                    <ResearchPanel workspace={workspace} />
                  ) : tab === "Creatives" ? (
                    <CreativesPanel workspace={workspace} />
                  ) : tab === "Production" ? (
                    <ProductionPanel
                      workspace={workspace}
                      onOpenAssets={() => setTab("Assets")}
                    />
                  ) : (
                    <EmptyPanel tab={tab} />
                  )}
                </>
              ) : (
                <section className="select-product">
                  <p className="eyebrow">Catalog ready</p>
                  <h2>Select a product to open its workspace.</h2>
                  <p>
                    Your Product Brain keeps strategy inputs structured,
                    reviewable, and ready for future agents.
                  </p>
                </section>
              )}
            </section>
          </div>
        )}
        {dialog && (
          <CreateDialog
            kind={dialog}
            brands={brands}
            preferredBrand={selectedBrand}
            session={session}
            onClose={() => setDialog(null)}
            onCreated={async (brand, nextWorkspace) => {
              setDialog(null);
              if (brand) setBrands((current) => [...current, brand]);
              if (nextWorkspace) {
                setWorkspace(nextWorkspace);
                setProducts((current) => ({
                  ...current,
                  [nextWorkspace.brand.id]: [
                    ...(current[nextWorkspace.brand.id] ?? []),
                    nextWorkspace.product,
                  ],
                }));
              }
            }}
          />
        )}
      </main>
    </div>
  );
}

function CreateDialog({
  kind,
  brands,
  preferredBrand,
  session,
  onClose,
  onCreated,
}: {
  kind: "brand" | "product";
  brands: Brand[];
  preferredBrand: Brand | undefined;
  session: Session;
  onClose: () => void;
  onCreated: (brand?: Brand, workspace?: Workspace) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [brandId, setBrandId] = useState(
    preferredBrand?.id ?? brands[0]?.id ?? "",
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const submit = async () => {
    setSaving(true);
    setError("");
    try {
      if (kind === "brand") {
        const brand = await catalogApi.createBrand(session, {
          name,
          slug: slugify(name),
          website_url: null,
          status: "active",
          profile: {
            industry: category,
            description: "",
            brand_positioning: "",
            brand_voice: "",
            tone_attributes: [],
            visual_style_keywords: [],
            target_markets: [],
            primary_language: "en",
            allowed_claims: [],
            prohibited_claims: [],
            competitors: [],
          },
        });
        await onCreated(brand);
      } else {
        const workspace = await catalogApi.createProduct(session, brandId, {
          name,
          slug: slugify(name),
          sku: null,
          category,
          short_description: "",
          status: "draft",
          profile: {
            description: "",
            features: [],
            benefits: [],
            materials: [],
            variants: [],
            price: null,
            currency: null,
            estimated_margin: null,
            target_audiences: [],
            problems_solved: [],
            use_cases: [],
            differentiators: [],
            purchase_objections: [],
            allowed_claims: [],
            prohibited_claims: [],
            shipping_summary: null,
            seasonality_notes: null,
            landing_page_url: null,
            competitor_product_refs: [],
          },
          brief: emptyBrief,
        });
        await onCreated(undefined, workspace);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to save");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
      >
        <p className="eyebrow">
          {kind === "brand" ? "Brand setup" : "Product basics"}
        </p>
        <h2 id="dialog-title">Create {kind}</h2>
        {kind === "product" && (
          <label>
            Brand
            <select
              value={brandId}
              onChange={(event) => setBrandId(event.target.value)}
            >
              {brands.map((brand) => (
                <option key={brand.id} value={brand.id}>
                  {brand.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          {kind === "brand" ? "Brand name" : "Product name"}
          <input
            autoFocus
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          {kind === "brand" ? "Industry" : "Category"}
          <input
            required
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          />
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="primary"
            disabled={!name || !category || saving}
            onClick={() => void submit()}
          >
            {saving ? "Creating…" : `Create ${kind}`}
          </button>
        </footer>
      </section>
    </div>
  );
}
