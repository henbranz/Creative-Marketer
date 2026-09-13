/* eslint-disable @next/next/no-img-element -- private signed asset grants cannot use Next Image */
"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  type Asset,
  type AssetCreate,
  type AssemblyPlan,
  type AssemblyReadiness,
  type AgentRun,
  type Brand,
  type BriefWrite,
  type CreativeConcept,
  type CreativeConceptSet,
  type FinalCreative,
  catalogApi,
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
  type ResearchTarget,
  type SocialCapability,
  type SocialEvidence,
  slugify,
  type Workspace,
  uploadToGrant,
} from "./catalog-api";
import {
  AUDIENCE_DESCRIPTION_MAX,
  AUDIENCE_NAME_MAX,
  BriefDraftValidationError,
  briefDraftKey,
  draftDigest,
  readStoredBriefDraft,
  serializeBriefDraft,
  toBriefDraft,
  toBriefWrite,
  type AudienceDraftV1,
  type BriefDraftV1,
  type StoredBriefDraftV1,
} from "./brief-draft";
import {
  BrandLockup,
  BrandMark,
  Button,
  EmptyState,
  Metric,
  StatusBadge,
} from "./ui/components";

const navigation = [
  "Command Center",
  "Products",
  "Agents",
  "Approvals",
  "Activity",
  "Settings",
];
const navigationIcons: Record<string, string> = {
  "Command Center": "⌂",
  Products: "◇",
  Agents: "✦",
  Approvals: "✓",
  Activity: "↗",
  Settings: "⚙",
};
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
        <BrandLockup />
        <p className="eyebrow">Ideas · Create · Publish · Grow</p>
        <h1>Turn product knowledge into creative impact.</h1>
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
          <Button variant="primary" type="submit">
            Open workspace
          </Button>
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
  const copy: Record<string, string> = {
    Published:
      "Approved work will appear here when governed publishing is introduced.",
    Performance:
      "Campaign results will appear here when performance integrations are available.",
    Insights:
      "Cross-campaign learning will appear here when the Intelligence workspace is introduced.",
  };
  return (
    <EmptyState icon={tab.slice(0, 1)} title={`No ${tab.toLowerCase()} yet`}>
      {copy[tab] ??
        `${tab} will appear here when its product capability is introduced.`}
      <span className="coming">Coming in a future product slice</span>
    </EmptyState>
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
  const [selectedSocialEvidence, setSelectedSocialEvidence] =
    useState<SocialEvidence | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [snapshots, setSnapshots] = useState<ResearchSnapshot[]>([]);
  const [targets, setTargets] = useState<ResearchTarget[]>([]);
  const [socialEvidence, setSocialEvidence] = useState<SocialEvidence[]>([]);
  const [capabilities, setCapabilities] = useState<SocialCapability[]>([]);
  const [researchView, setResearchView] = useState<
    "overview" | "web" | "social" | "results"
  >("overview");
  const [targetName, setTargetName] = useState("");
  const [targetPlatform, setTargetPlatform] = useState<
    "facebook" | "instagram" | "tiktok" | "other"
  >("facebook");
  const [targetProfileUrl, setTargetProfileUrl] = useState("");
  const [evidenceTargetId, setEvidenceTargetId] = useState("");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [evidenceHeadline, setEvidenceHeadline] = useState("");
  const [evidenceBody, setEvidenceBody] = useState("");
  const [evidenceType, setEvidenceType] = useState<
    | "ad"
    | "post"
    | "reel"
    | "video"
    | "screenshot"
    | "exported_image"
    | "exported_video"
    | "other"
  >("ad");
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);

  const load = useCallback(async () => {
    const [
      values,
      runValues,
      snapshotValues,
      targetValues,
      socialValues,
      capabilityValues,
    ] = await Promise.all([
      catalogApi.listResearchSources(session, workspace.product.id),
      catalogApi.listResearcherRuns(session, workspace.product.id),
      catalogApi.listResearchSnapshots(session, workspace.product.id),
      catalogApi.listResearchTargets(session, workspace.product.id),
      catalogApi.listSocialEvidence(session, workspace.product.id),
      catalogApi.listSocialCapabilities(session),
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
    setTargets(targetValues);
    setSocialEvidence(socialValues);
    setCapabilities(capabilityValues);
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
      setSelectedSocialEvidence(null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        try {
          setSelectedSocialEvidence(
            await catalogApi.getSocialEvidence(session, evidenceId),
          );
          setEvidence(null);
          return;
        } catch (socialError) {
          setError(
            socialError instanceof Error
              ? socialError.message
              : "Evidence could not be loaded.",
          );
          return;
        }
      }
      setError(
        caught instanceof Error
          ? caught.message
          : "Evidence could not be loaded.",
      );
    }
  };

  const addTarget = async () => {
    setBusy("target");
    setError("");
    try {
      const created = await catalogApi.createResearchTarget(
        session,
        workspace.product.id,
        {
          kind: "competitor_brand",
          display_name: targetName,
          website_url: null,
          platform: targetPlatform,
          platform_handle: null,
          platform_profile_url: targetProfileUrl || null,
          platform_identifier: null,
        },
      );
      setEvidenceTargetId(created.id);
      setTargetName("");
      setTargetProfileUrl("");
      await load();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Competitor could not be added.",
      );
    } finally {
      setBusy("");
    }
  };

  const addManualEvidence = async () => {
    if (!evidenceTargetId) return;
    setBusy("manual-evidence");
    setError("");
    try {
      let mediaAssetId: string | null = null;
      if (evidenceFile) {
        const allowedMimeTypes: AssetCreate["mime_type"][] = [
          "image/jpeg",
          "image/png",
          "image/webp",
          "video/mp4",
          "video/webm",
        ];
        if (
          !allowedMimeTypes.includes(
            evidenceFile.type as AssetCreate["mime_type"],
          )
        )
          throw new Error("Use a JPEG, PNG, WebP, MP4, or WebM evidence file.");
        const kind = evidenceFile.type.startsWith("video/") ? "video" : "image";
        const grant = await catalogApi.createAsset(session, {
          brand_id: workspace.brand.id,
          product_id: workspace.product.id,
          kind,
          role: "other",
          original_filename: evidenceFile.name,
          mime_type: evidenceFile.type as AssetCreate["mime_type"],
          rights_status: "restricted",
          allowed_uses: ["internal_analysis"],
          parent_asset_id: null,
          source_url: evidenceUrl || null,
        });
        await uploadToGrant(grant, evidenceFile, () => undefined);
        const finalized = await catalogApi.finalizeAsset(
          session,
          grant.asset.id,
        );
        mediaAssetId = finalized.id;
      }
      const target = targets.find((item) => item.id === evidenceTargetId);
      await catalogApi.addManualSocialEvidence(session, evidenceTargetId, {
        platform: target?.platform ?? targetPlatform,
        evidence_type: evidenceType,
        source_url: evidenceUrl || null,
        destination_url: null,
        advertiser_name: target?.display_name ?? null,
        platform_content_id: null,
        headline: evidenceHeadline || null,
        body_text: evidenceBody || null,
        cta: null,
        media_type: evidenceFile?.type ?? null,
        placements: [],
        first_seen_at: null,
        last_seen_at: null,
        activity_status: null,
        region: null,
        media_asset_id: mediaAssetId,
      });
      setEvidenceUrl("");
      setEvidenceHeadline("");
      setEvidenceBody("");
      setEvidenceFile(null);
      await load();
      setNotice(
        "Manual social evidence captured as restricted, analysis-only evidence.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Evidence could not be added.",
      );
    } finally {
      setBusy("");
    }
  };

  const activeSources = sources.filter(
    (item) => item.source.status === "active",
  );
  const archivedSources = sources.filter(
    (item) => item.source.status === "archived",
  );
  const activeTargets = targets.filter((item) => item.status === "active");
  const archivedTargets = targets.filter((item) => item.status === "archived");

  return (
    <div className="research-workspace">
      <nav className="research-subnav" aria-label="Research sections">
        {(["overview", "web", "social", "results"] as const).map((value) => (
          <button
            key={value}
            className={researchView === value ? "active" : ""}
            onClick={() => setResearchView(value)}
          >
            {value === "web"
              ? "Web Sources"
              : value === "social"
                ? "Competitors & Ads"
                : value === "results"
                  ? "Research Results"
                  : "Overview"}
          </button>
        ))}
      </nav>
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
      {researchView === "overview" && (
        <section className="research-overview">
          <div>
            <p className="eyebrow">Intelligence workspace</p>
            <h2>Build strategy on traceable evidence.</h2>
            <p>
              Research stays product-scoped, source-linked, and clearly
              separated from Product truth.
            </p>
          </div>
          <div className="research-overview-metrics">
            <Metric
              label="Web sources"
              value={activeSources.length}
              note={`${archivedSources.length} archived`}
            />
            <Metric
              label="Competitors"
              value={activeTargets.length}
              note={`${socialEvidence.length} social evidence items`}
            />
            <Metric
              label="Latest run"
              value={runs[0]?.status.replaceAll("_", " ") ?? "Not run"}
            />
            <Metric
              label="Snapshot"
              value={snapshots[0]?.freshness ?? "None"}
              note={
                snapshots[0]
                  ? `${snapshots[0].findings.length} findings`
                  : "Run the Researcher when ready"
              }
            />
          </div>
        </section>
      )}
      {researchView === "overview" && (
        <section className="ai-research-card">
          <div>
            <p className="eyebrow">Evidence-grounded AI research</p>
            <h2>Researcher</h2>
            <p>
              Analyze the current Product snapshot and captured evidence.
              External pages remain untrusted data, and every factual finding
              must cite an exact evidence block.
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
                  This run was interrupted and requires recovery before it can
                  be rerun.
                </p>
              )}
            </div>
          )}
          <AgentRunProvenance runs={runs} />
        </section>
      )}
      {researchView === "results" && snapshots[0] && (
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
                  <span>{finding.basis.toLowerCase()}</span>
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
      {researchView === "results" && !snapshots[0] && (
        <EmptyState icon="R" title="No research results yet">
          Add trusted evidence, then run the Researcher to create
          evidence-grounded findings.
        </EmptyState>
      )}
      {researchView === "social" && (
        <section className="social-research">
          <div>
            <p className="eyebrow">Competitor evidence</p>
            <h2>Competitors &amp; Ads</h2>
            <p>
              Manual evidence works without platform credentials. Every upload
              is restricted to internal analysis and cannot become a production
              input.
            </p>
          </div>
          {workspace.product.can_edit && (
            <div className="research-form">
              <label>
                Competitor name
                <input
                  aria-label="Competitor name"
                  value={targetName}
                  maxLength={200}
                  onChange={(event) => setTargetName(event.target.value)}
                />
              </label>
              <label>
                Platform
                <select
                  aria-label="Competitor platform"
                  value={targetPlatform}
                  onChange={(event) =>
                    setTargetPlatform(
                      event.target.value as typeof targetPlatform,
                    )
                  }
                >
                  <option value="facebook">Facebook</option>
                  <option value="instagram">Instagram</option>
                  <option value="tiktok">TikTok</option>
                  <option value="other">Other</option>
                </select>
              </label>
              <label>
                Public profile URL
                <input
                  aria-label="Competitor profile URL"
                  type="url"
                  value={targetProfileUrl}
                  onChange={(event) => setTargetProfileUrl(event.target.value)}
                />
              </label>
              <button
                className="primary"
                disabled={!targetName || busy === "target"}
                onClick={() => void addTarget()}
              >
                Add competitor
              </button>
            </div>
          )}
          {!!activeTargets.length && (
            <div className="research-grid">
              {activeTargets.map((target) => {
                const platformCapability = capabilities.find(
                  (item) => item.platform === target.platform,
                );
                return (
                  <article className="research-card" key={target.id}>
                    <div className="research-card-heading">
                      <div>
                        <span className="research-category">
                          {target.platform ?? "other"}
                        </span>
                        <h3>{target.display_name}</h3>
                        <small>{target.kind.replaceAll("_", " ")}</small>
                      </div>
                      <span className="fetch-state active">Active</span>
                    </div>
                    {target.platform_profile_url && (
                      <a
                        href={target.platform_profile_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Open original profile
                      </a>
                    )}
                    <div className="research-actions">
                      <button
                        className="secondary"
                        disabled={!platformCapability?.enabled || !!busy}
                        title={
                          platformCapability?.enabled
                            ? "Query the configured official provider"
                            : "Official provider capability is not configured"
                        }
                        onClick={() =>
                          void catalogApi
                            .querySocialProvider(
                              session,
                              target.id,
                              "search_ads",
                            )
                            .then(load)
                            .catch((caught: unknown) =>
                              setError(
                                caught instanceof Error
                                  ? caught.message
                                  : "Provider query failed.",
                              ),
                            )
                        }
                      >
                        Query official provider
                      </button>
                      {target.can_edit && (
                        <button
                          className="text-danger"
                          onClick={async () => {
                            if (
                              !window.confirm(
                                "Archive this competitor? Historical social evidence and previous research results will remain available.",
                              )
                            )
                              return;
                            try {
                              await catalogApi.archiveResearchTarget(
                                session,
                                target.id,
                              );
                              await load();
                            } catch (caught) {
                              setError(
                                caught instanceof Error
                                  ? caught.message
                                  : "Competitor could not be archived.",
                              );
                            }
                          }}
                        >
                          Archive competitor
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
          {workspace.product.can_edit && !!activeTargets.length && (
            <div className="research-form">
              <label>
                Competitor
                <select
                  aria-label="Evidence competitor"
                  value={evidenceTargetId}
                  onChange={(event) => setEvidenceTargetId(event.target.value)}
                >
                  <option value="">Select competitor</option>
                  {activeTargets.map((target) => (
                    <option key={target.id} value={target.id}>
                      {target.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Evidence type
                <select
                  aria-label="Social evidence type"
                  value={evidenceType}
                  onChange={(event) =>
                    setEvidenceType(event.target.value as typeof evidenceType)
                  }
                >
                  {(
                    [
                      "ad",
                      "post",
                      "reel",
                      "video",
                      "screenshot",
                      "exported_image",
                      "exported_video",
                      "other",
                    ] as const
                  ).map((value) => (
                    <option key={value} value={value}>
                      {value.replaceAll("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Public source URL
                <input
                  aria-label="Social evidence URL"
                  type="url"
                  value={evidenceUrl}
                  onChange={(event) => setEvidenceUrl(event.target.value)}
                />
              </label>
              <label>
                Headline
                <input
                  aria-label="Social evidence headline"
                  value={evidenceHeadline}
                  maxLength={1000}
                  onChange={(event) => setEvidenceHeadline(event.target.value)}
                />
              </label>
              <label>
                Caption / notes
                <textarea
                  aria-label="Social evidence body"
                  value={evidenceBody}
                  maxLength={8000}
                  onChange={(event) => setEvidenceBody(event.target.value)}
                />
              </label>
              <label>
                Screenshot or exported media
                <input
                  aria-label="Social evidence media"
                  type="file"
                  accept="image/jpeg,image/png,image/webp,video/mp4,video/webm"
                  onChange={(event) =>
                    setEvidenceFile(event.target.files?.[0] ?? null)
                  }
                />
              </label>
              <button
                className="primary"
                disabled={
                  !evidenceTargetId ||
                  (!evidenceUrl &&
                    !evidenceHeadline &&
                    !evidenceBody &&
                    !evidenceFile) ||
                  !!busy
                }
                onClick={() => void addManualEvidence()}
              >
                {busy === "manual-evidence"
                  ? "Capturing…"
                  : "Add manual evidence"}
              </button>
            </div>
          )}
          {!!socialEvidence.length && (
            <div className="research-grid">
              {socialEvidence.map((item) => (
                <article className="research-card" key={item.id}>
                  <div className="research-card-heading">
                    <div>
                      <span className="research-category">{item.platform}</span>
                      <h3>
                        {item.headline ||
                          item.advertiser_name ||
                          `${item.evidence_type} evidence`}
                      </h3>
                    </div>
                    <span className="fetch-state succeeded">
                      {item.provenance === "provider_fetched"
                        ? "Provider verified"
                        : "Manual"}
                    </span>
                  </div>
                  {item.body_text && <p>{item.body_text}</p>}
                  <small>
                    Captured {new Date(item.captured_at).toLocaleString()}
                  </small>
                  <small>Restricted · internal analysis only</small>
                  {item.source_url && (
                    <a
                      href={item.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open original source
                    </a>
                  )}
                </article>
              ))}
            </div>
          )}
          {!!archivedTargets.length && (
            <details className="archived-research">
              <summary>Archived competitors ({archivedTargets.length})</summary>
              <div className="research-grid">
                {archivedTargets.map((target) => (
                  <article className="research-card" key={target.id}>
                    <span className="research-category">
                      Archived · {target.platform ?? "other"}
                    </span>
                    <h3>{target.display_name}</h3>
                    <small>
                      Historical social evidence and Research results remain
                      available.
                    </small>
                  </article>
                ))}
              </div>
            </details>
          )}
        </section>
      )}
      {researchView === "web" && (
        <>
          <section className="research-intake">
            <div>
              <p className="eyebrow">Governed web evidence</p>
              <h2>Research sources</h2>
              <p>
                Pages are fetched server-side under strict network policy and
                stored as immutable, untrusted evidence.
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
          {activeSources.length ? (
            <div className="research-grid">
              {activeSources.map((item) => (
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
                    {item.source.can_edit &&
                      item.source.status === "active" && (
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
                              if (
                                !window.confirm(
                                  "Remove this source from future research?\nHistorical evidence and previous research results will remain available.",
                                )
                              )
                                return;
                              try {
                                await catalogApi.archiveResearchSource(
                                  session,
                                  item.source.id,
                                );
                                await load();
                              } catch (caught) {
                                setError(
                                  caught instanceof Error
                                    ? caught.message
                                    : "Source could not be removed.",
                                );
                              }
                            }}
                          >
                            Remove source
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
          {!!archivedSources.length && (
            <details className="archived-research">
              <summary>Archived sources ({archivedSources.length})</summary>
              <div className="research-grid">
                {archivedSources.map((item) => (
                  <article className="research-card" key={item.source.id}>
                    <span className="research-category">Archived · Web</span>
                    <h3>{item.source.display_name}</h3>
                    <small>
                      Historical evidence remains available to previous Research
                      results.
                    </small>
                  </article>
                ))}
              </div>
            </details>
          )}
        </>
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
      {selectedSocialEvidence && (
        <section
          className="evidence-viewer"
          aria-label="Social evidence viewer"
        >
          <div>
            <p className="eyebrow">Untrusted social evidence · analysis only</p>
            <h3>
              {selectedSocialEvidence.headline ||
                selectedSocialEvidence.advertiser_name ||
                "Social evidence"}
            </h3>
            <p>
              {selectedSocialEvidence.body_text ||
                "No caption or body text was supplied."}
            </p>
            <div className="evidence-metadata">
              <small>
                {selectedSocialEvidence.platform} ·{" "}
                {selectedSocialEvidence.provenance === "provider_fetched"
                  ? "Provider verified"
                  : "Manual"}
              </small>
              <small>
                {selectedSocialEvidence.rights_status} ·{" "}
                {selectedSocialEvidence.allowed_uses.join(", ")}
              </small>
              {selectedSocialEvidence.source_url && (
                <a
                  href={selectedSocialEvidence.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open original source
                </a>
              )}
            </div>
          </div>
          <button
            className="secondary"
            onClick={() => setSelectedSocialEvidence(null)}
          >
            Close evidence
          </button>
        </section>
      )}
    </div>
  );
}

function AgentRunProvenance({ runs }: { runs: AgentRun[] }) {
  if (!runs.length) return null;
  return (
    <details>
      <summary>Run provenance</summary>
      <ul>
        {runs.map((run, index) => (
          <li key={run.id}>
            <strong>{index === 0 ? "Current" : "Historical"}</strong> ·{" "}
            {run.resolved_model ?? "Unresolved model"} ·{" "}
            {run.model_route_version ?? "pending route"}
          </li>
        ))}
      </ul>
    </details>
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
      <AgentRunProvenance runs={runs} />
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
                    <StatusBadge
                      status={concept.decision_state ?? "UNREVIEWED"}
                    />
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
        <EmptyState icon="C" title="No concepts yet">
          Research the market, complete the Brief, then turn evidence into
          creative directions.
        </EmptyState>
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
  const money = (value: number) =>
    value.toFixed(6).replace(/\.?0+$/, "") || "0";
  const planningSpend = Number(plan?.planning_cost ?? 0);
  const imageSpend = jobs
    .filter((job) => job.kind === "IMAGE" && job.status === "SUCCEEDED")
    .reduce((total, job) => total + Number(job.actual_cost), 0);
  const videoSpend = jobs
    .filter((job) => job.kind === "VIDEO" && job.status === "SUCCEEDED")
    .reduce((total, job) => total + Number(job.actual_cost), 0);
  const reservedOrUnknown = jobs
    .filter((job) => !["SUCCEEDED", "FAILED"].includes(job.status))
    .reduce(
      (total, job) =>
        total +
        Number(
          job.status === "OUTCOME_UNKNOWN"
            ? job.unknown_cost
            : job.reserved_cost,
        ),
      0,
    );
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
    <div className="production-workspace creative-workspace">
      <section className="creative-hero">
        <div>
          <p className="eyebrow">Governed media production</p>
          <h2>Production</h2>
          <p>
            The Producer plans. You approve exact cost and media work before
            providers run.
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
          {producerRun?.resolved_model && (
            <span>
              Producer route: {producerRun.resolved_model} ·{" "}
              {producerRun.model_route_version}
            </span>
          )}
          <AgentRunProvenance runs={runs} />
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
        <section className="production-plan">
          <StatusBadge status={plan.status} />
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
          <div className="creative-readiness" aria-label="Cost dashboard">
            <span>
              AI planning: {money(planningSpend)} {plan.currency}
            </span>
            <span>
              Image generation: {money(imageSpend)} {plan.currency}
            </span>
            <span>
              Video generation: {money(videoSpend)} {plan.currency}
            </span>
            <strong>
              Total known spend:{" "}
              {money(planningSpend + imageSpend + videoSpend)} {plan.currency}
            </strong>
            <span>
              Reserved/unknown amount: {money(reservedOrUnknown)}{" "}
              {plan.currency}
            </span>
            <small>
              Approved generation ceiling: {plan.estimated_total_cost}{" "}
              {plan.currency}
            </small>
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
                  <StatusBadge
                    status={job.local_demo_provider ? "draft" : "active"}
                    label={
                      job.local_demo_provider
                        ? "Local demo provider"
                        : "Live provider"
                    }
                  />
                  <StatusBadge status={job.status} />
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
        <EmptyState icon="P" title="No production plan yet">
          Approve a current creative concept to begin production planning.
        </EmptyState>
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
          <StatusBadge status={product.status} />
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
      <Metric
        label="Product brain"
        value={
          workspace.latest_snapshot
            ? `Revision ${workspace.latest_snapshot.source_revision}`
            : "Not versioned"
        }
        note="Immutable knowledge snapshot"
      />
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

function TextArea({
  label,
  value,
  onChange,
  hint,
  maxLength,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  maxLength?: number;
  error?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {hint && <small>{hint}</small>}
      <textarea
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={4}
        aria-invalid={Boolean(error)}
      />
      {maxLength && (
        <small>
          {value.length}/{maxLength}
        </small>
      )}
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

function ListArea({
  label,
  value,
  onChange,
  hint,
  maxItems = 30,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  maxItems?: number;
  error?: string;
}) {
  const itemCount = value.split("\n").filter((item) => item.trim()).length;
  return (
    <label className="field">
      <span>{label}</span>
      <small>{hint ?? "One item per line"}</small>
      <textarea
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={4}
        aria-invalid={Boolean(error)}
      />
      <small>
        {itemCount}/{maxItems} items
      </small>
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

function QuestionCard({
  title,
  helper,
  children,
}: {
  title?: string;
  helper?: string;
  children: ReactNode;
}) {
  return (
    <section className="brief-question" data-primary-question>
      {title && <h3>{title}</h3>}
      {helper && <p>{helper}</p>}
      <div className="brief-question-fields">{children}</div>
    </section>
  );
}

function hasLines(value: string | undefined): boolean {
  return Boolean(value?.split("\n").some((item) => item.trim()));
}

function briefSectionState(section: number, brief: BriefDraftV1) {
  const audience = brief.primary_audience;
  const checks = [
    [hasLines(brief.product_why), hasLines(brief.emotional_benefits)],
    [
      Boolean(audience?.name.trim() && audience.description.trim()),
      hasLines(audience?.pain_points),
      hasLines(audience?.desires) || hasLines(audience?.motivations),
      hasLines(audience?.objections),
    ],
    [
      hasLines(brief.positioning_statement),
      hasLines(brief.competitive_alternatives),
      hasLines(brief.why_choose_us),
    ],
    [
      hasLines(brief.priority_channels),
      hasLines(brief.conversion_goal),
      hasLines(brief.cta_preferences),
    ],
    [hasLines(brief.desired_creative_style), hasLines(brief.tones_to_explore)],
    [hasLines(brief.mandatory_messaging), hasLines(brief.prohibited_messaging)],
  ][section]!;
  const completed = checks.filter(Boolean).length;
  if (completed === 0) return "Not started";
  if (completed === checks.length) return "Complete";
  return "In progress";
}

function advancedAnswerCount(section: number, brief: BriefDraftV1): number {
  if (section === 1) return brief.secondary_audiences.length;
  if (section === 3) return hasLines(brief.current_channels) ? 1 : 0;
  if (section === 4) return hasLines(brief.tones_to_avoid) ? 1 : 0;
  return 0;
}

function advancedFieldSection(field: string): number | null {
  if (field.startsWith("secondary_audiences")) return 1;
  if (field === "current channels") return 3;
  if (field === "tones to avoid") return 4;
  return null;
}

function missingBriefLabel(field: string): string {
  return (
    {
      "brief.product_why": "Why the product exists",
      "brief.emotional_benefits": "Main customer benefits",
      "brief.primary_audience.identity":
        "Primary audience name and description",
      "brief.primary_audience.pain_points": "Audience problems or frustrations",
      "brief.primary_audience.goals":
        "Audience desired outcomes or motivations",
      "brief.primary_audience.objections": "Purchase objections",
      "brief.positioning_statement":
        "How customers should understand the product",
      "brief.competitive_alternatives": "What customers choose instead",
      "brief.why_choose_us": "Why customers should choose this product",
      "brief.priority_channels": "Where to reach customers",
      "brief.conversion_goal": "The action customers should take",
      "brief.cta_preferences": "Preferred calls to action",
      "brief.desired_creative_style": "How the content should feel",
      "brief.tones_to_explore": "Tone to use",
      "brief.mandatory_messaging": "What content must mention",
      "brief.prohibited_messaging": "What content must never say",
    }[field] ?? field.replaceAll("_", " ").replace(/^brief\./, "")
  );
}

function BriefEditor({
  workspace,
  onSaved,
  onDirtyChange,
}: {
  workspace: Workspace;
  onSaved: (workspace: Workspace) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [section, setSection] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState<Set<number>>(
    () => new Set(),
  );
  const canonicalDraft = useMemo(
    () => toBriefDraft(toBriefWrite(workspace.brief)),
    [workspace.brief],
  );
  const [brief, setBrief] = useState<BriefDraftV1>(canonicalDraft);
  const [baseRevision, setBaseRevision] = useState(workspace.brief.revision);
  const [recovery, setRecovery] = useState<StoredBriefDraftV1 | null>(() => {
    const session = readSession();
    return readStoredBriefDraft(
      window.localStorage,
      session.tenantId,
      workspace.product.id,
    );
  });
  const [migrationNotice, setMigrationNotice] = useState(
    canonicalDraft.legacyAudienceMigrated,
  );
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState<{
    field: string;
    message: string;
  } | null>(null);
  const readOnly = !workspace.brief.can_edit;
  const dirty = draftDigest(brief) !== draftDigest(canonicalDraft);
  const set = <K extends keyof BriefDraftV1>(
    key: K,
    value: BriefDraftV1[K],
  ) => {
    setBrief((current) => ({ ...current, [key]: value }));
    setSaveState("idle");
    setFieldError(null);
  };
  const setAudience = (value: Partial<AudienceDraftV1>) =>
    set("primary_audience", {
      name: "",
      description: "",
      pain_points: "",
      desires: "",
      motivations: "",
      objections: "",
      ...brief.primary_audience,
      ...value,
    });
  const setSecondaryAudience = (
    index: number,
    value: Partial<BriefDraftV1["secondary_audiences"][number]>,
  ) =>
    set(
      "secondary_audiences",
      brief.secondary_audiences.map((audience, audienceIndex) =>
        audienceIndex === index ? { ...audience, ...value } : audience,
      ),
    );
  const openAdvanced = (index: number) =>
    setAdvancedOpen((current) => new Set(current).add(index));
  const toggleAdvanced = (index: number) =>
    setAdvancedOpen((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  useEffect(() => {
    if (readOnly || !dirty) return;
    const session = readSession();
    const timer = window.setTimeout(() => {
      const stored: StoredBriefDraftV1 = {
        version: 1,
        tenantId: session.tenantId,
        productId: workspace.product.id,
        baseRevision,
        savedAt: new Date().toISOString(),
        dirty: true,
        digest: draftDigest(brief),
        draft: brief,
      };
      window.localStorage.setItem(
        briefDraftKey(session.tenantId, workspace.product.id),
        JSON.stringify(stored),
      );
    }, 300);
    return () => window.clearTimeout(timer);
  }, [baseRevision, brief, dirty, readOnly, workspace.product.id]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const save = async () => {
    setSaveState("saving");
    setError("");
    try {
      const saved = await catalogApi.saveBrief(
        readSession(),
        workspace.product.id,
        serializeBriefDraft(brief),
      );
      const session = readSession();
      let refreshedCompleteness = workspace.completeness;
      try {
        refreshedCompleteness = (
          await catalogApi.getWorkspace(session, workspace.product.id)
        ).completeness;
      } catch {
        // The Brief save is already acknowledged. Keep the prior guidance until refresh.
      }
      onSaved({
        ...workspace,
        brief: saved,
        completeness: refreshedCompleteness,
      });
      window.localStorage.removeItem(
        briefDraftKey(session.tenantId, workspace.product.id),
      );
      setBrief(toBriefDraft(toBriefWrite(saved)));
      setBaseRevision(saved.revision);
      setRecovery(null);
      setSaveState("saved");
    } catch (caught) {
      if (caught instanceof BriefDraftValidationError) {
        setFieldError({ field: caught.field, message: caught.message });
        const advancedSection = advancedFieldSection(caught.field);
        if (advancedSection !== null) {
          setSection(advancedSection);
          openAdvanced(advancedSection);
        }
      }
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
            <span className="section-nav-copy">
              <b>{name}</b>
              <small>{briefSectionState(index, brief)}</small>
            </span>
          </button>
        ))}
      </nav>
      <section className="brief-card">
        {recovery && (
          <div className="warning-banner" role="status">
            <strong>Unsaved local draft found</strong>
            <p>
              {recovery.baseRevision !== workspace.brief.revision
                ? `The server is now revision ${workspace.brief.revision}; this draft began from revision ${recovery.baseRevision}. Restore it for review or discard it. No automatic merge will occur.`
                : "Restore your browser draft or discard it and continue from the saved Brief."}
            </p>
            <button
              className="secondary"
              onClick={() => {
                setBrief(recovery.draft);
                setBaseRevision(recovery.baseRevision);
                setMigrationNotice(recovery.draft.legacyAudienceMigrated);
                setAdvancedOpen(
                  new Set(
                    [1, 3, 4].filter(
                      (index) => advancedAnswerCount(index, recovery.draft) > 0,
                    ),
                  ),
                );
                setRecovery(null);
              }}
            >
              Restore draft
            </button>
            <button
              className="text-danger"
              onClick={() => {
                const session = readSession();
                window.localStorage.removeItem(
                  briefDraftKey(session.tenantId, workspace.product.id),
                );
                setRecovery(null);
              }}
            >
              Discard local draft
            </button>
          </div>
        )}
        {migrationNotice && (
          <p className="warning-banner" role="status">
            We moved your previous audience text into Audience description so it
            would not be lost. Please add a short Audience name before saving.
          </p>
        )}
        <header>
          <div>
            <p className="eyebrow">
              Brief · {section + 1} of {briefSections.length}
            </p>
            <h2>{briefSections[section]}</h2>
            <p>
              Build the minimum context Creative Manager needs to create strong
              work.
            </p>
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
              <QuestionCard>
                <TextArea
                  label="Why does this product exist?"
                  hint="Describe the problem it solves and its core value."
                  value={brief.product_why ?? ""}
                  maxLength={3000}
                  onChange={(v) => set("product_why", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What are the main benefits?"
                  hint="One per line. Focus on the value customers experience."
                  value={brief.emotional_benefits}
                  onChange={(v) => set("emotional_benefits", v)}
                />
              </QuestionCard>
            </>
          )}
          {section === 1 && (
            <>
              <QuestionCard
                title="Who is the primary audience?"
                helper="Give this audience a short name and a useful description."
              >
                <label className="field">
                  <span>Audience name</span>
                  <input
                    aria-label="Audience name"
                    value={brief.primary_audience?.name ?? ""}
                    aria-invalid={fieldError?.field === "primary_audience.name"}
                    onChange={(event) =>
                      setAudience({ name: event.target.value })
                    }
                  />
                  <small>
                    {brief.primary_audience?.name.length ?? 0}/
                    {AUDIENCE_NAME_MAX}
                  </small>
                  {fieldError?.field === "primary_audience.name" && (
                    <small className="field-error">{fieldError.message}</small>
                  )}
                </label>
                <TextArea
                  label="Audience description"
                  value={brief.primary_audience?.description ?? ""}
                  maxLength={AUDIENCE_DESCRIPTION_MAX}
                  {...(fieldError?.field === "primary_audience.description"
                    ? { error: fieldError.message }
                    : {})}
                  onChange={(v) => setAudience({ description: v })}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What problems or frustrations do they have?"
                  hint="One per line. Focus on problems the product can realistically solve."
                  value={brief.primary_audience?.pain_points ?? ""}
                  maxItems={20}
                  onChange={(v) => setAudience({ pain_points: v })}
                />
              </QuestionCard>
              <QuestionCard
                title="What do they want to achieve or feel?"
                helper="Capture desired outcomes, motivations, or both."
              >
                <ListArea
                  label="Desired outcomes"
                  value={brief.primary_audience?.desires ?? ""}
                  maxItems={20}
                  onChange={(v) => setAudience({ desires: v })}
                />
                <ListArea
                  label="Motivations"
                  value={brief.primary_audience?.motivations ?? ""}
                  maxItems={20}
                  onChange={(v) => setAudience({ motivations: v })}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What could stop them from buying?"
                  value={brief.primary_audience?.objections ?? ""}
                  maxItems={20}
                  onChange={(v) => setAudience({ objections: v })}
                />
              </QuestionCard>
              <section className="advanced-details">
                <button
                  type="button"
                  className="advanced-toggle"
                  aria-expanded={advancedOpen.has(1)}
                  onClick={() => toggleAdvanced(1)}
                >
                  <span>Advanced audience details</span>
                  <small>
                    {advancedAnswerCount(1, brief)
                      ? `${advancedAnswerCount(1, brief)} advanced audience${advancedAnswerCount(1, brief) === 1 ? "" : "s"} saved`
                      : "Optional"}
                  </small>
                </button>
                <div className="advanced-content" hidden={!advancedOpen.has(1)}>
                  {brief.secondary_audiences.map((audience, index) => (
                    <article className="secondary-audience" key={index}>
                      <header>
                        <h3>Secondary audience {index + 1}</h3>
                        <button
                          type="button"
                          className="text-danger"
                          onClick={() =>
                            set(
                              "secondary_audiences",
                              brief.secondary_audiences.filter(
                                (_, audienceIndex) => audienceIndex !== index,
                              ),
                            )
                          }
                        >
                          Remove
                        </button>
                      </header>
                      <label className="field">
                        <span>Name</span>
                        <input
                          aria-label={`Secondary audience ${index + 1} name`}
                          value={audience.name}
                          onChange={(event) =>
                            setSecondaryAudience(index, {
                              name: event.target.value,
                            })
                          }
                        />
                        {fieldError?.field ===
                          `secondary_audiences.${index}.name` && (
                          <small className="field-error">
                            {fieldError.message}
                          </small>
                        )}
                      </label>
                      <TextArea
                        label={`Secondary audience ${index + 1} description`}
                        value={audience.description}
                        maxLength={AUDIENCE_DESCRIPTION_MAX}
                        onChange={(value) =>
                          setSecondaryAudience(index, { description: value })
                        }
                      />
                      {(
                        [
                          ["Pain points", "pain_points"],
                          ["Desired outcomes", "desires"],
                          ["Motivations", "motivations"],
                          ["Objections", "objections"],
                        ] as const
                      ).map(([label, field]) => (
                        <ListArea
                          key={field}
                          label={`Secondary audience ${index + 1} ${label.toLowerCase()}`}
                          value={(audience[field] ?? []).join("\n")}
                          maxItems={20}
                          onChange={(value) =>
                            setSecondaryAudience(index, {
                              [field]: value.split("\n"),
                            })
                          }
                        />
                      ))}
                    </article>
                  ))}
                  <button
                    type="button"
                    className="secondary"
                    onClick={() =>
                      set("secondary_audiences", [
                        ...brief.secondary_audiences,
                        {
                          name: "",
                          description: "",
                          pain_points: [],
                          desires: [],
                          motivations: [],
                          objections: [],
                        },
                      ])
                    }
                  >
                    Add secondary audience
                  </button>
                </div>
              </section>
            </>
          )}
          {section === 2 && (
            <>
              <QuestionCard>
                <TextArea
                  label="How should customers understand this product?"
                  value={brief.positioning_statement ?? ""}
                  maxLength={3000}
                  onChange={(v) => set("positioning_statement", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What are customers choosing instead?"
                  value={brief.competitive_alternatives}
                  onChange={(v) => set("competitive_alternatives", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="Why should they choose us?"
                  value={brief.why_choose_us}
                  onChange={(v) => set("why_choose_us", v)}
                />
              </QuestionCard>
            </>
          )}
          {section === 3 && (
            <>
              <QuestionCard>
                <ListArea
                  label="Where do you want to reach customers?"
                  value={brief.priority_channels}
                  onChange={(v) => set("priority_channels", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <TextArea
                  label="What action do you want customers to take?"
                  value={brief.conversion_goal ?? ""}
                  maxLength={500}
                  onChange={(v) => set("conversion_goal", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What offer are you promoting?"
                  hint="Optional. Add one offer per line."
                  value={brief.offers}
                  onChange={(v) => set("offers", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What CTA should we use?"
                  value={brief.cta_preferences}
                  onChange={(v) => set("cta_preferences", v)}
                />
              </QuestionCard>
              <section className="advanced-details">
                <button
                  type="button"
                  className="advanced-toggle"
                  aria-expanded={advancedOpen.has(3)}
                  onClick={() => toggleAdvanced(3)}
                >
                  <span>Advanced details</span>
                  <small>
                    {advancedAnswerCount(3, brief)
                      ? "1 advanced answer saved"
                      : "Optional"}
                  </small>
                </button>
                <div className="advanced-content" hidden={!advancedOpen.has(3)}>
                  <ListArea
                    label="Where are you currently reaching customers?"
                    value={brief.current_channels}
                    {...(fieldError?.field === "current channels"
                      ? { error: fieldError.message }
                      : {})}
                    onChange={(v) => set("current_channels", v)}
                  />
                </div>
              </section>
            </>
          )}
          {section === 4 && (
            <>
              <QuestionCard>
                <TextArea
                  label="What should the content feel like?"
                  value={brief.desired_creative_style ?? ""}
                  maxLength={2000}
                  onChange={(v) => set("desired_creative_style", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What tone should we use?"
                  value={brief.tones_to_explore}
                  onChange={(v) => set("tones_to_explore", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="Do you have creative references?"
                  hint="Optional. Add examples, links, or descriptions one per line."
                  value={brief.creative_references}
                  onChange={(v) => set("creative_references", v)}
                />
              </QuestionCard>
              <section className="advanced-details">
                <button
                  type="button"
                  className="advanced-toggle"
                  aria-expanded={advancedOpen.has(4)}
                  onClick={() => toggleAdvanced(4)}
                >
                  <span>Advanced details</span>
                  <small>
                    {advancedAnswerCount(4, brief)
                      ? "1 advanced answer saved"
                      : "Optional"}
                  </small>
                </button>
                <div className="advanced-content" hidden={!advancedOpen.has(4)}>
                  <ListArea
                    label="What tones should we avoid?"
                    value={brief.tones_to_avoid}
                    {...(fieldError?.field === "tones to avoid"
                      ? { error: fieldError.message }
                      : {})}
                    onChange={(v) => set("tones_to_avoid", v)}
                  />
                </div>
              </section>
            </>
          )}
          {section === 5 && (
            <>
              <QuestionCard>
                <ListArea
                  label="What must the content always mention?"
                  value={brief.mandatory_messaging}
                  onChange={(v) => set("mandatory_messaging", v)}
                />
              </QuestionCard>
              <QuestionCard>
                <ListArea
                  label="What must the content never say?"
                  value={brief.prohibited_messaging}
                  onChange={(v) => set("prohibited_messaging", v)}
                />
              </QuestionCard>
              <QuestionCard
                title="Are there legal, safety or geographic restrictions?"
                helper="These are optional, but keep each type separate for reliable downstream use."
              >
                <ListArea
                  label="Required disclaimers"
                  value={brief.required_disclaimers}
                  onChange={(v) => set("required_disclaimers", v)}
                />
                <ListArea
                  label="Legal and safety restrictions"
                  value={brief.legal_safety_constraints}
                  onChange={(v) => set("legal_safety_constraints", v)}
                />
                <ListArea
                  label="Geographic restrictions"
                  value={brief.geographical_restrictions}
                  onChange={(v) => set("geographical_restrictions", v)}
                />
              </QuestionCard>
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
              <li key={field}>{missingBriefLabel(field)}</li>
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

function ProductCatalog({
  brands,
  products,
  onOpen,
  onCreateBrand,
  onCreateProduct,
}: {
  brands: Brand[];
  products: Record<string, Product[]>;
  onOpen: (id: string) => void;
  onCreateBrand: () => void;
  onCreateProduct: (brand: Brand) => void;
}) {
  const productCount = Object.values(products).reduce(
    (total, values) => total + values.length,
    0,
  );
  return (
    <div className="catalog-page">
      <section className="catalog-hero">
        <div>
          <p className="eyebrow">Products</p>
          <h2>Every idea starts with a clear product truth.</h2>
          <p>
            Organize the context your Researcher, Creative Strategist, and
            Producer use to build evidence-grounded work.
          </p>
        </div>
        <div className="catalog-actions">
          <Button onClick={onCreateBrand}>Create brand</Button>
          {brands.some((brand) => brand.can_edit) && (
            <Button
              variant="primary"
              onClick={() =>
                onCreateProduct(brands.find((brand) => brand.can_edit)!)
              }
            >
              Create product
            </Button>
          )}
        </div>
        <div className="catalog-stats">
          <Metric label="Brands" value={brands.length} />
          <Metric label="Products" value={productCount} />
          <Metric
            label="Workflow"
            value="Research → Create"
            note="Publish remains governed"
          />
        </div>
      </section>
      {brands.map((brand) => (
        <section className="brand-catalog" key={brand.id}>
          <header>
            <div className="brand-node large">
              <span>{brand.name.slice(0, 1)}</span>
              <div>
                <h3>{brand.name}</h3>
                <small>{brand.profile?.industry || "Brand workspace"}</small>
              </div>
            </div>
            {brand.can_edit && (
              <Button onClick={() => onCreateProduct(brand)}>
                Add product
              </Button>
            )}
          </header>
          {(products[brand.id] ?? []).length ? (
            <div className="product-card-grid">
              {(products[brand.id] ?? []).map((product) => (
                <button
                  className="product-card"
                  key={product.id}
                  onClick={() => onOpen(product.id)}
                >
                  <span className="product-card-art" aria-hidden="true">
                    {product.name.slice(0, 1)}
                  </span>
                  <span className="product-card-body">
                    <span>
                      <strong>{product.name}</strong>
                      <StatusBadge status={product.status} />
                    </span>
                    <small>{product.category || "Uncategorized product"}</small>
                    <span className="product-card-link">
                      Open Product Brain <b>→</b>
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="brand-empty">No products in this brand yet.</p>
          )}
        </section>
      ))}
    </div>
  );
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
  const [dialogBrandId, setDialogBrandId] = useState<string | null>(null);
  const [briefDirty, setBriefDirty] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const selectedBrand = useMemo(
    () =>
      brands.find((brand) => brand.id === dialogBrandId) ??
      brands.find((brand) => brand.id === workspace?.brand.id) ??
      brands[0],
    [brands, dialogBrandId, workspace],
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
    if (
      briefDirty &&
      !window.confirm(
        "You have unsaved Brief changes. Leave this Product? Your local draft will remain available.",
      )
    )
      return;
    setBriefDirty(false);
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
    <div className={`app-shell ${navigationOpen ? "navigation-open" : ""}`}>
      <aside className="sidebar">
        <div className="logo-row">
          <BrandLockup compact />
          <button
            className="sidebar-close"
            aria-label="Close navigation"
            onClick={() => setNavigationOpen(false)}
          >
            ×
          </button>
        </div>
        <nav>
          {navigation.map((item) => (
            <button
              key={item}
              className={item === "Products" ? "active" : ""}
              disabled={item !== "Products"}
            >
              <i aria-hidden="true">{navigationIcons[item]}</i>
              <b>{item}</b>
              {item !== "Products" && <span>Soon</span>}
            </button>
          ))}
        </nav>
        <div className="account">
          <span className="workspace-avatar" aria-hidden="true">
            W
          </span>
          <div>
            <span>Workspace</span>
            <code>{session.tenantId.slice(0, 8)}</code>
          </div>
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
      {navigationOpen && (
        <button
          className="navigation-scrim"
          aria-label="Close navigation"
          onClick={() => setNavigationOpen(false)}
        />
      )}
      <main className="workspace">
        <header className="topbar">
          <button
            className="mobile-menu"
            aria-label="Open navigation"
            aria-expanded={navigationOpen}
            onClick={() => setNavigationOpen(true)}
          >
            <BrandMark compact />
          </button>
          <div>
            <p className="eyebrow">Creative Manager · Product Brain</p>
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
              Create brand
            </button>
            {selectedBrand?.can_edit && (
              <button
                className="primary"
                aria-label="New product"
                onClick={() => {
                  setDialogBrandId(selectedBrand.id);
                  setDialog("product");
                }}
              >
                Create product
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
          <div
            className={`product-layout ${workspace ? "" : "catalog-layout"}`}
          >
            {workspace && (
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
                          setDialogBrandId(brand.id);
                          setDialog("product");
                        }}
                      >
                        + Add product
                      </button>
                    )}
                  </div>
                ))}
              </aside>
            )}
            <section className="product-content">
              {workspace ? (
                <>
                  <nav className="tabs" aria-label="Product workspace">
                    {tabs.map((name) => (
                      <button
                        key={name}
                        className={tab === name ? "active" : ""}
                        onClick={() => {
                          if (
                            tab === "Brief" &&
                            briefDirty &&
                            name !== "Brief" &&
                            !window.confirm(
                              "You have unsaved Brief changes. Leave the Brief? Your local draft will remain available.",
                            )
                          )
                            return;
                          if (name !== "Brief") setBriefDirty(false);
                          setTab(name);
                        }}
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
                    <BriefEditor
                      workspace={workspace}
                      onSaved={setWorkspace}
                      onDirtyChange={setBriefDirty}
                    />
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
                <ProductCatalog
                  brands={brands}
                  products={products}
                  onOpen={(id) => void openProduct(id)}
                  onCreateBrand={() => setDialog("brand")}
                  onCreateProduct={(brand) => {
                    setDialogBrandId(brand.id);
                    setDialog("product");
                  }}
                />
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
            onClose={() => {
              setDialog(null);
              setDialogBrandId(null);
            }}
            onCreated={async (brand, nextWorkspace) => {
              setDialog(null);
              setDialogBrandId(null);
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
