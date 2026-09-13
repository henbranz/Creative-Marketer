import type { Brief, BriefWrite } from "./catalog-api";

export const BRIEF_DRAFT_VERSION = 1 as const;
export const AUDIENCE_NAME_MAX = 120;
export const AUDIENCE_DESCRIPTION_MAX = 8000;
export const LIST_ITEM_MAX = 500;

const proseLimits = {
  product_why: 3000,
  positioning_statement: 3000,
  conversion_goal: 500,
  desired_creative_style: 2000,
} as const;

const listFields = [
  "emotional_benefits",
  "competitive_alternatives",
  "why_choose_us",
  "current_channels",
  "priority_channels",
  "offers",
  "cta_preferences",
  "tones_to_explore",
  "tones_to_avoid",
  "creative_references",
  "mandatory_messaging",
  "prohibited_messaging",
  "required_disclaimers",
  "legal_safety_constraints",
  "geographical_restrictions",
] as const;

export type BriefListField = (typeof listFields)[number];
type SecondaryAudienceWrite = NonNullable<
  BriefWrite["secondary_audiences"]
>[number];

export interface AudienceDraftV1 {
  name: string;
  description: string;
  pain_points: string;
  desires: string;
  motivations: string;
  objections: string;
}

export interface BriefDraftV1 {
  version: typeof BRIEF_DRAFT_VERSION;
  product_why: string;
  emotional_benefits: string;
  primary_audience: AudienceDraftV1 | null;
  secondary_audiences: NonNullable<BriefWrite["secondary_audiences"]>;
  positioning_statement: string;
  competitive_alternatives: string;
  why_choose_us: string;
  current_channels: string;
  priority_channels: string;
  conversion_goal: string;
  offers: string;
  cta_preferences: string;
  desired_creative_style: string;
  tones_to_explore: string;
  tones_to_avoid: string;
  creative_references: string;
  mandatory_messaging: string;
  prohibited_messaging: string;
  required_disclaimers: string;
  legal_safety_constraints: string;
  geographical_restrictions: string;
  legacyAudienceMigrated: boolean;
}

export interface StoredBriefDraftV1 {
  version: typeof BRIEF_DRAFT_VERSION;
  tenantId: string;
  productId: string;
  baseRevision: number;
  savedAt: string;
  dirty: boolean;
  digest: string;
  draft: BriefDraftV1;
}

export class BriefDraftValidationError extends Error {
  constructor(
    readonly field: string,
    message: string,
  ) {
    super(message);
  }
}

function audienceToDraft(
  value: BriefWrite["primary_audience"],
): AudienceDraftV1 | null {
  if (!value) return null;
  return {
    name: value.name,
    description: value.description,
    pain_points: (value.pain_points ?? []).join("\n"),
    desires: (value.desires ?? []).join("\n"),
    motivations: (value.motivations ?? []).join("\n"),
    objections: (value.objections ?? []).join("\n"),
  };
}

/** Explicitly selects the writable API contract; response metadata can never cross PUT. */
export function toBriefWrite(response: Brief): BriefWrite {
  return {
    product_why: response.product_why,
    emotional_benefits: [...(response.emotional_benefits ?? [])],
    primary_audience: response.primary_audience
      ? {
          name: response.primary_audience.name,
          description: response.primary_audience.description,
          pain_points: [...(response.primary_audience.pain_points ?? [])],
          desires: [...(response.primary_audience.desires ?? [])],
          motivations: [...(response.primary_audience.motivations ?? [])],
          objections: [...(response.primary_audience.objections ?? [])],
        }
      : null,
    secondary_audiences: (response.secondary_audiences ?? []).map((item) => ({
      name: item.name,
      description: item.description,
      pain_points: [...(item.pain_points ?? [])],
      desires: [...(item.desires ?? [])],
      motivations: [...(item.motivations ?? [])],
      objections: [...(item.objections ?? [])],
    })),
    positioning_statement: response.positioning_statement,
    competitive_alternatives: [...(response.competitive_alternatives ?? [])],
    why_choose_us: [...(response.why_choose_us ?? [])],
    current_channels: [...(response.current_channels ?? [])],
    priority_channels: [...(response.priority_channels ?? [])],
    conversion_goal: response.conversion_goal,
    offers: [...(response.offers ?? [])],
    cta_preferences: [...(response.cta_preferences ?? [])],
    desired_creative_style: response.desired_creative_style,
    tones_to_explore: [...(response.tones_to_explore ?? [])],
    tones_to_avoid: [...(response.tones_to_avoid ?? [])],
    creative_references: [...(response.creative_references ?? [])],
    mandatory_messaging: [...(response.mandatory_messaging ?? [])],
    prohibited_messaging: [...(response.prohibited_messaging ?? [])],
    required_disclaimers: [...(response.required_disclaimers ?? [])],
    legal_safety_constraints: [...(response.legal_safety_constraints ?? [])],
    geographical_restrictions: [...(response.geographical_restrictions ?? [])],
  };
}

export function toBriefDraft(value: BriefWrite): BriefDraftV1 {
  const draft: BriefDraftV1 = {
    version: BRIEF_DRAFT_VERSION,
    product_why: value.product_why,
    emotional_benefits: (value.emotional_benefits ?? []).join("\n"),
    primary_audience: audienceToDraft(value.primary_audience),
    secondary_audiences: value.secondary_audiences ?? [],
    positioning_statement: value.positioning_statement,
    competitive_alternatives: (value.competitive_alternatives ?? []).join("\n"),
    why_choose_us: (value.why_choose_us ?? []).join("\n"),
    current_channels: (value.current_channels ?? []).join("\n"),
    priority_channels: (value.priority_channels ?? []).join("\n"),
    conversion_goal: value.conversion_goal,
    offers: (value.offers ?? []).join("\n"),
    cta_preferences: (value.cta_preferences ?? []).join("\n"),
    desired_creative_style: value.desired_creative_style,
    tones_to_explore: (value.tones_to_explore ?? []).join("\n"),
    tones_to_avoid: (value.tones_to_avoid ?? []).join("\n"),
    creative_references: (value.creative_references ?? []).join("\n"),
    mandatory_messaging: (value.mandatory_messaging ?? []).join("\n"),
    prohibited_messaging: (value.prohibited_messaging ?? []).join("\n"),
    required_disclaimers: (value.required_disclaimers ?? []).join("\n"),
    legal_safety_constraints: (value.legal_safety_constraints ?? []).join("\n"),
    geographical_restrictions: (value.geographical_restrictions ?? []).join(
      "\n",
    ),
    legacyAudienceMigrated: false,
  };
  if (
    draft.primary_audience &&
    draft.primary_audience.name.length > AUDIENCE_NAME_MAX &&
    draft.primary_audience.description.length === 0
  ) {
    draft.primary_audience = {
      ...draft.primary_audience,
      description: draft.primary_audience.name,
      name: "Primary audience",
    };
    draft.legacyAudienceMigrated = true;
  }
  return draft;
}

export function normalizeList(
  value: string,
  field: string,
  maximum = 30,
): string[] {
  const items = value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  if (items.length > maximum)
    throw new BriefDraftValidationError(
      field,
      `${field} supports at most ${maximum} items.`,
    );
  const duplicate = items.find(
    (item, index) =>
      items.findIndex(
        (candidate) =>
          candidate.toLocaleLowerCase() === item.toLocaleLowerCase(),
      ) !== index,
  );
  if (duplicate)
    throw new BriefDraftValidationError(
      field,
      `${field} contains a duplicate item.`,
    );
  if (items.some((item) => item.length > LIST_ITEM_MAX))
    throw new BriefDraftValidationError(
      field,
      `${field} items must be ${LIST_ITEM_MAX} characters or fewer.`,
    );
  return items;
}

function serializeAudience(value: AudienceDraftV1, field: string) {
  const name = value.name.trim();
  if (!name)
    throw new BriefDraftValidationError(
      `${field}.name`,
      "Audience name is required.",
    );
  if (name.length > AUDIENCE_NAME_MAX)
    throw new BriefDraftValidationError(
      `${field}.name`,
      `Audience name must be ${AUDIENCE_NAME_MAX} characters or fewer.`,
    );
  if (value.description.length > AUDIENCE_DESCRIPTION_MAX)
    throw new BriefDraftValidationError(
      `${field}.description`,
      `Audience description must be ${AUDIENCE_DESCRIPTION_MAX} characters or fewer.`,
    );
  return {
    name,
    description: value.description,
    pain_points: normalizeList(value.pain_points, "Pain points", 20),
    desires: normalizeList(value.desires, "Desires", 20),
    motivations: normalizeList(value.motivations, "Motivations", 20),
    objections: normalizeList(value.objections, "Purchase objections", 20),
  };
}

function serializeSecondaryAudience(
  value: SecondaryAudienceWrite,
  index: number,
) {
  const field = `secondary_audiences.${index}`;
  const name = value.name.trim();
  if (!name)
    throw new BriefDraftValidationError(
      `${field}.name`,
      "Audience name is required.",
    );
  if (name.length > AUDIENCE_NAME_MAX)
    throw new BriefDraftValidationError(
      `${field}.name`,
      `Audience name must be ${AUDIENCE_NAME_MAX} characters or fewer.`,
    );
  if (value.description.length > AUDIENCE_DESCRIPTION_MAX)
    throw new BriefDraftValidationError(
      `${field}.description`,
      `Audience description must be ${AUDIENCE_DESCRIPTION_MAX} characters or fewer.`,
    );
  return {
    name,
    description: value.description,
    pain_points: normalizeList(
      (value.pain_points ?? []).join("\n"),
      `${field}.pain_points`,
      20,
    ),
    desires: normalizeList(
      (value.desires ?? []).join("\n"),
      `${field}.desires`,
      20,
    ),
    motivations: normalizeList(
      (value.motivations ?? []).join("\n"),
      `${field}.motivations`,
      20,
    ),
    objections: normalizeList(
      (value.objections ?? []).join("\n"),
      `${field}.objections`,
      20,
    ),
  };
}

export function serializeBriefDraft(draft: BriefDraftV1): BriefWrite {
  for (const [field, maximum] of Object.entries(proseLimits)) {
    if (draft[field as keyof typeof proseLimits].length > maximum)
      throw new BriefDraftValidationError(
        field,
        `${field.replaceAll("_", " ")} must be ${maximum} characters or fewer.`,
      );
  }
  const lists = Object.fromEntries(
    listFields.map((field) => [
      field,
      normalizeList(draft[field], field.replaceAll("_", " ")),
    ]),
  ) as Pick<BriefWrite, BriefListField>;
  return {
    product_why: draft.product_why,
    ...lists,
    primary_audience: draft.primary_audience
      ? serializeAudience(draft.primary_audience, "primary_audience")
      : null,
    secondary_audiences: (draft.secondary_audiences ?? []).map(
      serializeSecondaryAudience,
    ),
    positioning_statement: draft.positioning_statement,
    conversion_goal: draft.conversion_goal,
    desired_creative_style: draft.desired_creative_style,
  };
}

export function briefDraftKey(tenantId: string, productId: string): string {
  return `creative-marketer:brief-draft:v1:${tenantId}:${productId}`;
}

export function draftDigest(draft: BriefDraftV1): string {
  const value = JSON.stringify(draft);
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function readStoredBriefDraft(
  storage: Storage,
  tenantId: string,
  productId: string,
): StoredBriefDraftV1 | null {
  try {
    const parsed = JSON.parse(
      storage.getItem(briefDraftKey(tenantId, productId)) ?? "null",
    ) as StoredBriefDraftV1 | null;
    if (
      !parsed ||
      parsed.version !== BRIEF_DRAFT_VERSION ||
      parsed.tenantId !== tenantId ||
      parsed.productId !== productId ||
      !parsed.dirty ||
      parsed.draft.version !== BRIEF_DRAFT_VERSION
    )
      return null;
    return parsed;
  } catch {
    return null;
  }
}
