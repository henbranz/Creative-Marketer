import type { components } from "@creative-marketer/contracts";

import { getPublicConfig } from "./config";

export type Brand = components["schemas"]["BrandResponse"];
export type BrandWrite = components["schemas"]["BrandWrite"];
export type Brief = components["schemas"]["BriefResponse"];
export type BriefWrite = components["schemas"]["BriefContract"];
export type Product = components["schemas"]["ProductResponse"];
export type ProductCreate = components["schemas"]["ProductCreate"];
export type Workspace = components["schemas"]["WorkspaceResponse"];
export type Snapshot = components["schemas"]["SnapshotResponse"];
export type Asset = components["schemas"]["AssetResponse"];
export type AssetCreate = components["schemas"]["AssetCreate"];
export type UploadGrant = components["schemas"]["UploadGrantResponse"];
export type ResearchSource = components["schemas"]["SourceResponse"];
export type ResearchSourceCreate = components["schemas"]["SourceCreate"];
export type ResearchFetch = components["schemas"]["FetchResponse"];
export type ResearchEvidence = components["schemas"]["EvidenceResponse"];
export type ResearchManifest = components["schemas"]["ManifestResponse"];
export type AgentRun = components["schemas"]["AgentRunResponse"];
export type ResearchSnapshot =
  components["schemas"]["ResearchSnapshotResponse"];
export type CreativeConceptSet =
  components["schemas"]["CreativeConceptSetResponse"];
export type CreativeConcept = components["schemas"]["CreativeConceptResponse"];
export type CreativeDecision =
  components["schemas"]["CreativeDecisionResponse"];
export interface ProductionShot {
  shot_key: string;
  ordinal: number;
  source_strategy:
    | "USE_EXISTING_ASSET"
    | "GENERATE_IMAGE"
    | "GENERATE_VIDEO"
    | "MANUAL_CAPTURE";
  specification: Record<string, unknown>;
}
export interface ProductionScene {
  scene_key: string;
  ordinal: number;
  purpose: string;
  duration_seconds: number;
  message: string;
  voiceover: string | null;
  on_screen_text: string | null;
  shots: ProductionShot[];
}
export interface ProductionSegment {
  id: string;
  segment_key: string;
  shot_keys: string[];
  media_kind: "IMAGE" | "VIDEO";
  duration_seconds: number | null;
  continuity: string[];
  reference_asset_ids: string[];
  generation_spec: Record<string, unknown>;
}
export interface ProductionPlan {
  id: string;
  agent_run_id: string;
  concept_id: string;
  strategy: string;
  status: string;
  scenes: ProductionScene[];
  generation_segments: ProductionSegment[];
  generated_image_count: number;
  video_segment_count: number;
  existing_asset_count: number;
  manual_shot_count: number;
  planning_cost: string;
  estimated_max_image_cost: string;
  estimated_max_video_cost: string;
  estimated_total_cost: string;
  currency: string;
}
export interface ProductionJob {
  id: string;
  kind: "IMAGE" | "VIDEO";
  status: string;
  media_profile: string;
  provider: string;
  model: string;
  reserved_cost: string;
  actual_cost: string;
  unknown_cost: string;
  currency: string;
  output_asset_id: string | null;
  failure_code: string | null;
  local_demo_provider: boolean;
  updated_at: string;
}

export interface Session {
  readonly tenantId: string;
  readonly credential: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  session: Session,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${getPublicConfig().apiBaseUrl}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${session.credential}`,
      "Content-Type": "application/json",
      "X-Tenant-ID": session.tenantId,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new ApiError(
      response.status,
      error?.detail ?? "The request could not be completed.",
    );
  }
  return (await response.json()) as T;
}

export const catalogApi = {
  listBrands: (session: Session) => request<Brand[]>(session, "/v1/brands"),
  createBrand: (session: Session, value: BrandWrite) =>
    request<Brand>(session, "/v1/brands", {
      method: "POST",
      body: JSON.stringify(value),
    }),
  listProducts: (session: Session, brandId: string) =>
    request<Product[]>(session, `/v1/brands/${brandId}/products`),
  createProduct: (session: Session, brandId: string, value: ProductCreate) =>
    request<Workspace>(session, `/v1/brands/${brandId}/products`, {
      method: "POST",
      body: JSON.stringify(value),
    }),
  getWorkspace: (session: Session, productId: string) =>
    request<Workspace>(session, `/v1/products/${productId}`),
  saveBrief: (session: Session, productId: string, value: BriefWrite) =>
    request<Brief>(session, `/v1/products/${productId}/brief`, {
      method: "PUT",
      body: JSON.stringify(value),
    }),
  createSnapshot: (session: Session, productId: string) =>
    request<Snapshot>(session, `/v1/products/${productId}/snapshots`, {
      method: "POST",
    }),
  listAssets: (session: Session, productId: string) =>
    request<Asset[]>(session, `/v1/products/${productId}/assets`),
  createAsset: (session: Session, value: AssetCreate) =>
    request<UploadGrant>(session, "/v1/assets", {
      method: "POST",
      body: JSON.stringify(value),
    }),
  finalizeAsset: (session: Session, assetId: string) =>
    request<Asset>(session, `/v1/assets/${assetId}/finalize`, {
      method: "POST",
    }),
  archiveAsset: (session: Session, assetId: string) =>
    request<Asset>(session, `/v1/assets/${assetId}/archive`, {
      method: "POST",
    }),
  downloadAsset: (session: Session, assetId: string) =>
    request<{ url: string; expires_at: string }>(
      session,
      `/v1/assets/${assetId}/download`,
      { method: "POST" },
    ),
  listResearchSources: (session: Session, productId: string) =>
    request<ResearchSource[]>(
      session,
      `/v1/products/${productId}/research-sources`,
    ),
  createResearchSource: (
    session: Session,
    productId: string,
    value: ResearchSourceCreate,
  ) =>
    request<{ source: ResearchSource; fetch: ResearchFetch | null }>(
      session,
      `/v1/products/${productId}/research-sources`,
      { method: "POST", body: JSON.stringify(value) },
    ),
  listResearchFetches: (session: Session, sourceId: string) =>
    request<ResearchFetch[]>(
      session,
      `/v1/research-sources/${sourceId}/fetches`,
    ),
  refreshResearchSource: (session: Session, sourceId: string) =>
    request<ResearchFetch>(
      session,
      `/v1/research-sources/${sourceId}/refresh`,
      { method: "POST" },
    ),
  archiveResearchSource: (session: Session, sourceId: string) =>
    request<ResearchSource>(
      session,
      `/v1/research-sources/${sourceId}/archive`,
      { method: "POST" },
    ),
  getResearchEvidence: (session: Session, evidenceId: string) =>
    request<ResearchEvidence>(session, `/v1/research-evidence/${evidenceId}`),
  getResearchManifest: (session: Session, productId: string) =>
    request<ResearchManifest>(
      session,
      `/v1/products/${productId}/research-context-manifest`,
    ),
  startResearcher: (
    session: Session,
    productId: string,
    idempotencyKey: string,
  ) =>
    request<AgentRun>(session, `/v1/products/${productId}/research/runs`, {
      method: "POST",
      body: JSON.stringify({ idempotency_key: idempotencyKey }),
    }),
  listResearcherRuns: (session: Session, productId: string) =>
    request<AgentRun[]>(session, `/v1/products/${productId}/research/runs`),
  getAgentRun: (session: Session, runId: string) =>
    request<AgentRun>(session, `/v1/agent-runs/${runId}`),
  listResearchSnapshots: (session: Session, productId: string) =>
    request<ResearchSnapshot[]>(
      session,
      `/v1/products/${productId}/research/snapshots`,
    ),
  startCreativeStrategist: (
    session: Session,
    productId: string,
    idempotencyKey: string,
    conceptCount = 5,
    channelIntent = "ORGANIC_SHORT_FORM",
  ) =>
    request<AgentRun>(session, `/v1/products/${productId}/creative/runs`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: idempotencyKey,
        concept_count: conceptCount,
        channel_intent: channelIntent,
      }),
    }),
  listCreativeRuns: (session: Session, productId: string) =>
    request<AgentRun[]>(session, `/v1/products/${productId}/creative/runs`),
  listCreativeConceptSets: (session: Session, productId: string) =>
    request<CreativeConceptSet[]>(
      session,
      `/v1/products/${productId}/creative/concept-sets`,
    ),
  decideCreativeConcept: (
    session: Session,
    conceptId: string,
    state: "SHORTLISTED" | "APPROVED_FOR_PRODUCTION" | "REJECTED",
  ) =>
    request<CreativeDecision>(
      session,
      `/v1/creative/concepts/${conceptId}/decision`,
      { method: "POST", body: JSON.stringify({ state }) },
    ),
  listProductionPlans: (session: Session, productId: string) =>
    request<ProductionPlan[]>(
      session,
      `/v1/products/${productId}/production/plans`,
    ),
  listProducerRuns: (session: Session, productId: string) =>
    request<AgentRun[]>(session, `/v1/products/${productId}/production/runs`),
  startProducer: (
    session: Session,
    conceptId: string,
    idempotencyKey: string,
  ) =>
    request<AgentRun>(
      session,
      `/v1/creative/concepts/${conceptId}/production/runs`,
      {
        method: "POST",
        body: JSON.stringify({ idempotency_key: idempotencyKey }),
      },
    ),
  approveProductionPlan: (session: Session, planId: string) =>
    request<ProductionPlan>(
      session,
      `/v1/production/plans/${planId}/approve-generation`,
      {
        method: "POST",
      },
    ),
  rejectProductionPlan: (session: Session, planId: string) =>
    request<ProductionPlan>(session, `/v1/production/plans/${planId}/reject`, {
      method: "POST",
    }),
  listProductionJobs: (session: Session, planId: string) =>
    request<ProductionJob[]>(session, `/v1/production/plans/${planId}/jobs`),
};

export async function uploadToGrant(
  grant: UploadGrant,
  file: File,
  onProgress: (percent: number) => void,
): Promise<void> {
  const body = new FormData();
  Object.entries(grant.fields).forEach(([key, value]) =>
    body.append(key, value),
  );
  body.append("file", file);
  await new Promise<void>((resolve, reject) => {
    const upload = new XMLHttpRequest();
    upload.open("POST", grant.url);
    upload.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable)
        onProgress(Math.round((event.loaded / event.total) * 100));
    });
    upload.addEventListener("load", () => {
      if (upload.status >= 200 && upload.status < 300) resolve();
      else reject(new Error("Object storage rejected the upload."));
    });
    upload.addEventListener("error", () =>
      reject(new Error("Object storage is unavailable.")),
    );
    upload.send(body);
  });
}

export function listText(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export async function obsidianOpenUrl(
  vaultName: string,
  nodeType: string,
  canonicalId: string,
): Promise<string> {
  const directories: Record<string, string> = {
    brand: "Products",
    product: "Products",
    product_knowledge_snapshot: "Products",
    agent_definition: "Agents",
    agent_version: "Agents",
    agent_run: "Runs",
    research_source: "Research",
    evidence_snapshot: "Research/Evidence",
    research_snapshot: "Research",
    research_finding: "Research/Findings",
    creative_concept_set: "Creative",
    creative_concept: "Creative",
    creative_concept_decision: "Creative",
    asset: "Assets",
    production_plan: "Production",
    production_shot: "Production/Shots",
    generation_segment: "Production/Segments",
    generation_job: "Production/Jobs",
  };
  const directory = directories[nodeType];
  if (!directory || !canonicalId.trim())
    throw new Error("Unsupported Obsidian node identity.");
  const material = new TextEncoder().encode(`${nodeType}:${canonicalId}`);
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", material));
  const digest = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  const filename = `${nodeType.replaceAll("_", "-")}--${digest}`;
  const query = new URLSearchParams({
    vault: vaultName,
    file: `${directory}/${filename}`,
  });
  return `obsidian://open?${query.toString()}`;
}
