import type { components } from "@creative-marketer/contracts";

import { getPublicConfig } from "./config";
import { readApiError } from "./api-errors";

export type Brand = components["schemas"]["BrandResponse"];
export type BrandWrite = components["schemas"]["BrandWrite"];
export type Brief = components["schemas"]["BriefResponse"];
export type BriefWrite = components["schemas"]["BriefContract"];
export type Product = components["schemas"]["ProductResponse"];
export type ProductCreate = components["schemas"]["ProductCreate"];
export type Workspace = components["schemas"]["WorkspaceResponse"];
export type Snapshot =
  components["schemas"]["creative_marketer_api__catalog_routes__SnapshotResponse"];
export type Asset = components["schemas"]["AssetResponse"];
export type AssetCreate = components["schemas"]["AssetCreate"];
export type UploadGrant = components["schemas"]["UploadGrantResponse"];
export type ResearchSource = components["schemas"]["SourceResponse"];
export type ResearchSourceCreate = components["schemas"]["SourceCreate"];
export type ResearchFetch = components["schemas"]["FetchResponse"];
export type ResearchEvidence = components["schemas"]["EvidenceResponse"];
export type ResearchManifest = components["schemas"]["ManifestResponse"];
export type ResearchTarget = components["schemas"]["ResearchTargetResponse"];
export type ResearchTargetCreate =
  components["schemas"]["ResearchTargetCreate"];
export type SocialEvidence = components["schemas"]["SocialEvidenceResponse"];
export type ManualSocialEvidenceCreate =
  components["schemas"]["ManualSocialEvidenceCreate"];
export type SocialCapability =
  components["schemas"]["SocialCapabilityResponse"];
export type AgentRun = components["schemas"]["AgentRunResponse"];
export type ResearchSnapshot =
  components["schemas"]["ResearchSnapshotResponse"];
export type CreativeConceptSet =
  components["schemas"]["CreativeConceptSetResponse"];
export type CreativeConcept = components["schemas"]["CreativeConceptResponse"];
export type CreativeDecision =
  components["schemas"]["CreativeDecisionResponse"];
export interface ProductionShot {
  id: string;
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
export interface AssemblyReadiness {
  status: string;
  ready: boolean;
  sources: { shot_key: string; status: string }[];
}
export interface AssemblyItem {
  item_key: string;
  ordinal: number;
  source_kind: string;
  source_asset_id: string;
  source_media_kind: string;
  production_shot_ids: string[];
  production_shot_keys: string[];
  generation_segment_id: string | null;
  timeline_start_ms: number;
  timeline_duration_ms: number;
  fit_mode: string;
  audio_behavior: string;
  transition_in: string;
  transition_out: string;
}
export interface AssemblyJob {
  id: string;
  assembly_plan_id: string;
  status: string;
  failure_code: string | null;
  output_asset_id: string | null;
  final_creative_id: string | null;
  renderer: string | null;
  renderer_version: string | null;
  updated_at: string;
}
export interface AssemblyPlan {
  id: string;
  production_plan_id: string;
  semantic_digest: string;
  render_profile_key: string;
  render_profile_version: number;
  timeline_duration_ms: number;
  items: AssemblyItem[];
  captions: { text: string; start_ms: number; end_ms: number; kind: string }[];
  overlays: {
    text: string;
    start_ms: number;
    end_ms: number;
    position: string;
    style_token: string;
  }[];
  job: AssemblyJob;
  created_at: string;
}
export interface FinalCreative {
  id: string;
  product_id: string;
  assembly_plan_id: string;
  production_plan_id: string;
  creative_concept_id: string;
  output_asset_id: string;
  semantic_digest: string;
  duration_ms: number;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
  source_count: number;
  render_profile_key: string;
  render_profile_version: number;
  renderer: string;
  renderer_version: string;
  decision_state: string | null;
  created_at: string;
}
export interface SocialAccount {
  id: string;
  platform: "facebook" | "instagram" | "tiktok";
  display_name: string;
  external_account_id: string;
  username: string | null;
  status: string;
  provider: "fake";
  capabilities: Record<string, unknown>;
}
export interface PublicationDraft {
  id: string;
  product_id: string;
  final_creative_id: string;
  output_asset_id: string;
  platform: string;
  social_account_id: string;
  external_destination_id: string;
  caption: string;
  title: string | null;
  hashtags: string[];
  destination_url: string | null;
  mode: "POST_NOW" | "SCHEDULE";
  scheduled_at: string | null;
  semantic_digest: string;
  decision_state: string | null;
  approval_action_digest: string | null;
  status: string;
  failure_code: string | null;
  created_at: string;
}
export interface Publication {
  id: string;
  product_id: string;
  publication_draft_id: string;
  final_creative_id: string;
  output_asset_id: string;
  platform: string;
  social_account_id: string;
  external_post_id: string;
  canonical_permalink: string | null;
  provider: "fake";
  status: "PUBLISHED";
  submitted_at: string;
  published_at: string | null;
}
export interface DerivedPerformanceMetric {
  key: "ctr" | "engagement_rate" | "conversion_rate";
  value: string | null;
  formula_version: string;
  unavailable_reason: string | null;
}
export interface PerformanceSnapshot {
  id: string;
  product_id: string;
  publication_id: string;
  observation_ids: string[];
  latest_metrics: Record<string, string>;
  derived_metrics: DerivedPerformanceMetric[];
  attributed_conversions: number;
  attributed_revenue: Record<string, string>;
  freshness: "CURRENT" | "STALE" | "NO_DATA";
  semantic_digest: string;
  created_at: string;
}
export interface PerformanceObservation {
  id: string;
  publication_id: string;
  metric_key: string;
  semantics: "CUMULATIVE" | "INTERVAL" | "DURATION" | "RATIO";
  value: string;
  unit: string;
  observed_at: string;
  provider: "fake";
  provider_version: string;
}
export interface CommerceWorkspace {
  mapping: {
    id: string;
    connection_id: string;
    external_product_id: string;
    external_variant_id: string | null;
    status: string;
  } | null;
  sync_status: {
    request_id: string;
    status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
    safe_failure_code: string | null;
  } | null;
  inventory: {
    id: string;
    external_variant_id: string;
    sku: string | null;
    available_quantity: number | null;
    indicator: "OUT_OF_STOCK" | "LOW_STOCK" | "IN_STOCK" | "UNAVAILABLE";
    captured_at: string;
    source: "Observed";
  }[];
  orders: {
    id: string;
    order_reference: string;
    external_order_id: string;
    currency: string;
    total: string;
    payment_state: string;
    fulfillment_state: string;
    captured_at: string;
    attributed: boolean;
    source: "Observed";
  }[];
  proposals: {
    id: string;
    action_type: "INVENTORY_ADJUSTMENT" | "REFUND";
    external_product_id: string | null;
    external_variant_id: string | null;
    external_order_id: string | null;
    exact_quantity: number | null;
    exact_amount: string | null;
    currency: string | null;
    reason: string;
    risk_level: "R5" | "R6";
    semantic_digest: string;
    approval_state:
      | "NEEDS_APPROVAL"
      | "APPROVED"
      | "QUEUED"
      | "SUBMITTING"
      | "OUTCOME_UNKNOWN"
      | "SUCCEEDED"
      | "FAILED";
    job_status: string | null;
    approval_request_id: string | null;
    store: string;
    sku_or_variant: string | null;
    current_quantity: number | null;
    order_reference: string | null;
    result_ref: string | null;
    safe_failure_code: string | null;
    source: "AI Proposal";
    created_at: string;
  }[];
  inventory_exceptions: {
    kind: string;
    observation_id: string;
    external_variant_id: string;
    available_quantity: number;
    rule_version: string;
  }[];
  order_exceptions: {
    kind: string;
    observation_id: string;
    external_order_id: string;
    rule_version: string;
  }[];
  is_fake: boolean;
}
export interface CommerceConnection {
  id: string;
  provider: "fake";
  display_name: string;
  safe_store_identifier: string;
  status: string;
  capabilities: string[];
  is_fake: boolean;
  created_at: string;
}
export interface IntelligenceCandidate {
  id: string;
  statement: string;
  sample_size: number;
  metric: string;
  baseline: string | null;
  observed_delta: string | null;
  confidence: "LOW" | "MODERATE" | "HIGH";
  scope: Record<string, unknown>;
  limitations: string[];
  data_trust_level: "SYNTHETIC" | "OBSERVED";
  status: "CANDIDATE";
  semantic_digest: string;
  created_at: string;
}
export interface ExperimentProposal {
  id: string;
  hypothesis: string;
  primary_variable: string;
  controlled_elements: string[];
  target_metric: string;
  platform: string;
  recommended_measurement_window: string;
  creative_direction: string;
  rationale: string;
  expected_learning: string;
  data_trust_level: "SYNTHETIC" | "OBSERVED";
  semantic_digest: string;
  created_at: string;
}
export interface IntelligenceReport {
  id: string;
  product_id: string;
  agent_run_id: string;
  context_manifest_id: string;
  context_manifest_digest: string;
  data_trust_level: "SYNTHETIC" | "OBSERVED";
  summary: string;
  observations: { statement: string; source_ref: string; window: string }[];
  comparative_findings: { comparison_id: string; interpretation: string }[];
  limitations: string[];
  semantic_digest: string;
  created_at: string;
  candidates: IntelligenceCandidate[];
  proposals: ExperimentProposal[];
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
    throw new ApiError(response.status, await readApiError(response));
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
  listResearchTargets: (session: Session, productId: string) =>
    request<ResearchTarget[]>(
      session,
      `/v1/products/${productId}/research-targets`,
    ),
  createResearchTarget: (
    session: Session,
    productId: string,
    value: ResearchTargetCreate,
  ) =>
    request<ResearchTarget>(
      session,
      `/v1/products/${productId}/research-targets`,
      { method: "POST", body: JSON.stringify(value) },
    ),
  archiveResearchTarget: (session: Session, targetId: string) =>
    request<ResearchTarget>(
      session,
      `/v1/research-targets/${targetId}/archive`,
      {
        method: "POST",
      },
    ),
  listSocialEvidence: (session: Session, productId: string) =>
    request<SocialEvidence[]>(
      session,
      `/v1/products/${productId}/social-evidence`,
    ),
  getSocialEvidence: (session: Session, evidenceId: string) =>
    request<SocialEvidence>(session, `/v1/social-evidence/${evidenceId}`),
  addManualSocialEvidence: (
    session: Session,
    targetId: string,
    value: ManualSocialEvidenceCreate,
  ) =>
    request<SocialEvidence>(
      session,
      `/v1/research-targets/${targetId}/social-evidence`,
      { method: "POST", body: JSON.stringify(value) },
    ),
  listSocialCapabilities: (session: Session) =>
    request<SocialCapability[]>(session, "/v1/social-research/capabilities"),
  querySocialProvider: (
    session: Session,
    targetId: string,
    capability: string,
  ) =>
    request<SocialEvidence[]>(
      session,
      `/v1/research-targets/${targetId}/provider-query`,
      { method: "POST", body: JSON.stringify({ capability }) },
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
  getAssemblyReadiness: (session: Session, planId: string) =>
    request<AssemblyReadiness>(
      session,
      `/v1/production/plans/${planId}/assembly-readiness`,
    ),
  bindManualSource: (session: Session, shotId: string, assetId: string) =>
    request<AssemblyReadiness>(
      session,
      `/v1/production/shots/${shotId}/manual-source`,
      {
        method: "PUT",
        body: JSON.stringify({ asset_id: assetId }),
      },
    ),
  createAssemblyPlan: (session: Session, planId: string) =>
    request<AssemblyPlan>(
      session,
      `/v1/production/plans/${planId}/assembly-plans`,
      { method: "POST" },
    ),
  listAssemblyPlans: (session: Session, planId: string) =>
    request<AssemblyPlan[]>(
      session,
      `/v1/production/plans/${planId}/assembly-plans`,
    ),
  getAssemblyJob: (session: Session, jobId: string) =>
    request<AssemblyJob>(session, `/v1/assembly/jobs/${jobId}`),
  getFinalCreative: (session: Session, finalId: string) =>
    request<FinalCreative>(session, `/v1/final-creatives/${finalId}`),
  approveFinalCreative: (session: Session, finalId: string) =>
    request<FinalCreative>(
      session,
      `/v1/final-creatives/${finalId}/approve-publishing`,
      { method: "POST" },
    ),
  rejectFinalCreative: (session: Session, finalId: string) =>
    request<FinalCreative>(session, `/v1/final-creatives/${finalId}/reject`, {
      method: "POST",
    }),
  listSocialAccounts: (session: Session) =>
    request<SocialAccount[]>(session, "/v1/social/accounts"),
  listPublicationDrafts: (session: Session, productId: string) =>
    request<PublicationDraft[]>(
      session,
      `/v1/products/${productId}/publication-drafts`,
    ),
  createPublicationDraft: (
    session: Session,
    productId: string,
    value: {
      final_creative_id: string;
      social_account_id: string;
      caption: string;
      title: string | null;
      hashtags: string[];
      destination_url: string | null;
      mode: "POST_NOW" | "SCHEDULE";
      scheduled_at: string | null;
      platform_settings: Record<string, unknown>;
    },
  ) =>
    request<PublicationDraft>(
      session,
      `/v1/products/${productId}/publication-drafts`,
      { method: "POST", body: JSON.stringify(value) },
    ),
  approvePublicationDraft: (session: Session, draftId: string) =>
    request<PublicationDraft>(
      session,
      `/v1/publication-drafts/${draftId}/approve`,
      { method: "POST" },
    ),
  rejectPublicationDraft: (session: Session, draftId: string) =>
    request<PublicationDraft>(
      session,
      `/v1/publication-drafts/${draftId}/reject`,
      { method: "POST" },
    ),
  executePublicationDraft: (session: Session, draftId: string) =>
    request<PublicationDraft>(
      session,
      `/v1/publication-drafts/${draftId}/execute`,
      { method: "POST" },
    ),
  cancelPublicationDraft: (session: Session, draftId: string) =>
    request<PublicationDraft>(
      session,
      `/v1/publication-drafts/${draftId}/cancel`,
      { method: "POST" },
    ),
  reconcilePublicationDraft: (session: Session, draftId: string) =>
    request<PublicationDraft>(
      session,
      `/v1/publication-drafts/${draftId}/reconcile`,
      { method: "POST" },
    ),
  listPublications: (session: Session, productId: string) =>
    request<Publication[]>(session, `/v1/products/${productId}/publications`),
  listProductPerformance: (session: Session, productId: string) =>
    request<PerformanceSnapshot[]>(
      session,
      `/v1/products/${productId}/performance`,
    ),
  collectPublicationPerformance: (session: Session, publicationId: string) =>
    request<PerformanceSnapshot>(
      session,
      `/v1/publications/${publicationId}/performance/collect`,
      { method: "POST" },
    ),
  performanceHistory: (session: Session, publicationId: string) =>
    request<PerformanceObservation[]>(
      session,
      `/v1/publications/${publicationId}/performance/history`,
    ),
  listIntelligenceReports: (session: Session, productId: string) =>
    request<IntelligenceReport[]>(
      session,
      `/v1/products/${productId}/intelligence/reports`,
    ),
  getCommerceWorkspace: (session: Session, productId: string) =>
    request<CommerceWorkspace>(session, `/v1/products/${productId}/commerce`),
  listCommerceConnections: (session: Session) =>
    request<CommerceConnection[]>(session, "/v1/commerce/connections"),
  createFakeCommerceConnection: (session: Session) =>
    request<CommerceConnection>(session, "/v1/commerce/connections/fake", {
      method: "POST",
      body: JSON.stringify({ display_name: "Fake Store" }),
    }),
  mapProductCommerce: (
    session: Session,
    value: {
      product_id: string;
      connection_id: string;
      external_product_id: string;
      external_variant_id: string | null;
    },
  ) =>
    request<{ id: string }>(session, "/v1/commerce/mappings", {
      method: "POST",
      body: JSON.stringify(value),
    }),
  syncCommerce: (session: Session, connectionId: string) =>
    request<{ sync_request_id: string; status: string }>(
      session,
      `/v1/commerce/connections/${connectionId}/sync`,
      {
        method: "POST",
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      },
    ),
  requestCommerceAction: (session: Session, proposalId: string) =>
    request<{
      proposal_id: string;
      state: string;
      approval_request_id: string;
    }>(session, `/v1/commerce/actions/${proposalId}/request`, {
      method: "POST",
    }),
  decideCommerceApproval: (
    session: Session,
    approvalId: string,
    decision: "APPROVE" | "DENY",
  ) =>
    request<{
      approval_request_id: string;
      decision: string;
      proposal_id: string;
    }>(session, `/v1/approvals/${approvalId}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  analyzeCommerce: (session: Session, productId: string) =>
    request<AgentRun>(session, `/v1/products/${productId}/commerce/analyze`, {
      method: "POST",
      body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
    }),
  analyzePerformance: (session: Session, productId: string) =>
    request<AgentRun>(
      session,
      `/v1/products/${productId}/intelligence/analyze`,
      {
        method: "POST",
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      },
    ),
  decideInsight: (
    session: Session,
    candidateId: string,
    decision: "PROPOSE_FOR_TESTING" | "REJECT",
  ) =>
    request<void>(
      session,
      `/v1/intelligence/insights/${candidateId}/decision`,
      {
        method: "POST",
        body: JSON.stringify({ decision }),
      },
    ),
  decideExperiment: (
    session: Session,
    proposalId: string,
    decision: "APPROVED_FOR_CREATIVE" | "REJECTED",
  ) =>
    request<void>(
      session,
      `/v1/intelligence/experiments/${proposalId}/decision`,
      {
        method: "POST",
        body: JSON.stringify({ decision }),
      },
    ),
  generateExperimentConcepts: (session: Session, proposalId: string) =>
    request<AgentRun>(
      session,
      `/v1/intelligence/experiments/${proposalId}/generate-concepts`,
      {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: crypto.randomUUID(),
          concept_count: 5,
          channel_intent: "ORGANIC_SHORT_FORM",
        }),
      },
    ),
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
    research_target: "Research/Targets",
    social_evidence_snapshot: "Research/Social Evidence",
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
    assembly_plan: "Production/Assembly",
    assembly_job: "Production/Assembly",
    final_creative: "Production/Finals",
    final_creative_decision: "Production/Finals",
    social_account: "Publishing/Accounts",
    publication_draft: "Publishing/Drafts",
    publication_decision: "Publishing/Decisions",
    publication: "Publishing/Published",
    performance_snapshot: "Performance/Snapshots",
    attribution_result: "Performance/Attribution",
    intelligence_report: "Insights/Reports",
    insight_candidate: "Insights/Candidates",
    experiment_proposal: "Experiments/Proposals",
    commerce_connection: "Commerce",
    product_commerce_mapping: "Commerce",
    commerce_operations_report: "Commerce/Operations",
    commerce_action_proposal: "Commerce/Actions",
    commerce_action_result: "Commerce/Actions",
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
