import { describe, expect, it } from "vitest";

import type { Brief, BriefWrite } from "./catalog-api";
import {
  briefDraftKey,
  draftDigest,
  normalizeList,
  readStoredBriefDraft,
  serializeBriefDraft,
  toBriefDraft,
  toBriefWrite,
  type StoredBriefDraftV1,
} from "./brief-draft";

const write: BriefWrite = {
  product_why: "Why  this exists\n\nWith detail",
  emotional_benefits: ["Calm", "Pride"],
  primary_audience: {
    name: "Founders",
    description: "A detailed\n\nmulti-paragraph persona.",
    pain_points: ["No time"],
    desires: [],
    motivations: [],
    objections: [],
  },
  secondary_audiences: [],
  positioning_statement: "Position",
  competitive_alternatives: [],
  why_choose_us: [],
  current_channels: [],
  priority_channels: [],
  conversion_goal: "Purchase",
  offers: [],
  cta_preferences: [],
  desired_creative_style: "Editorial",
  tones_to_explore: [],
  tones_to_avoid: [],
  creative_references: [],
  mandatory_messaging: [],
  prohibited_messaging: [],
  required_disclaimers: [],
  legal_safety_constraints: [],
  geographical_restrictions: [],
};

const response: Brief = {
  ...write,
  product_id: "40000000-0000-0000-0000-000000000001",
  revision: 7,
  updated_at: "2026-09-13T00:00:00Z",
  can_edit: true,
  provenance: "user_provided",
};

describe("BriefDraftV1", () => {
  it("converts BriefResponse to an exact Brief write contract", () => {
    const converted = toBriefWrite(response);
    expect(converted).toEqual(write);
    expect(converted).not.toHaveProperty("product_id");
    expect(converted).not.toHaveProperty("revision");
    expect(converted).not.toHaveProperty("updated_at");
    expect(converted).not.toHaveProperty("can_edit");
    expect(converted).not.toHaveProperty("provenance");
  });

  it("preserves spaces, Enter, and repeated blank lines while editing", () => {
    const draft = toBriefDraft(write);
    expect(draft.product_why).toBe("Why  this exists\n\nWith detail");
    expect(draft.primary_audience?.description).toBe(
      "A detailed\n\nmulti-paragraph persona.",
    );
  });

  it("normalizes lists only during Save serialization", () => {
    const draft = toBriefDraft(write);
    draft.emotional_benefits = " First item  \n\n Second item ";
    expect(draft.emotional_benefits).toContain("\n\n");
    expect(serializeBriefDraft(draft).emotional_benefits).toEqual([
      "First item",
      "Second item",
    ]);
    expect(() => normalizeList("same\n SAME", "Benefits")).toThrow(
      /duplicate/i,
    );
  });

  it("supports a long persona but enforces the short audience name", () => {
    const draft = toBriefDraft(write);
    draft.primary_audience!.description = "x".repeat(7000);
    expect(
      serializeBriefDraft(draft).primary_audience?.description,
    ).toHaveLength(7000);
    draft.primary_audience!.name = "n".repeat(121);
    expect(() => serializeBriefDraft(draft)).toThrow(/120 characters/i);
  });

  it("migrates a legacy long audience name byte-for-byte", () => {
    const legacy = `Persona line one\n\n  Persona line two with spaces  ${"detail ".repeat(20)}`;
    const draft = toBriefDraft({
      ...write,
      primary_audience: {
        ...write.primary_audience!,
        name: legacy,
        description: "",
      },
    });
    expect(draft.legacyAudienceMigrated).toBe(true);
    expect(draft.primary_audience?.name).toBe("Primary audience");
    expect(draft.primary_audience?.description).toBe(legacy);
  });

  it("isolates stored recovery drafts by tenant and Product", () => {
    const draft = toBriefDraft(write);
    const stored: StoredBriefDraftV1 = {
      version: 1,
      tenantId: "tenant-a",
      productId: "product-a",
      baseRevision: 7,
      savedAt: "2026-09-13T00:00:00Z",
      dirty: true,
      digest: draftDigest(draft),
      draft,
    };
    window.localStorage.setItem(
      briefDraftKey("tenant-a", "product-a"),
      JSON.stringify(stored),
    );
    expect(
      readStoredBriefDraft(window.localStorage, "tenant-a", "product-a"),
    ).toEqual(stored);
    expect(
      readStoredBriefDraft(window.localStorage, "tenant-b", "product-a"),
    ).toBeNull();
    expect(JSON.stringify(stored)).not.toMatch(
      /credential|authorization|api_key/i,
    );
  });

  it("round-trips every advanced field without changing BriefDraftV1", () => {
    const enriched: BriefWrite = {
      ...write,
      secondary_audiences: [
        {
          name: "Students",
          description: "Campus commuters",
          pain_points: ["Budget"],
          desires: ["Prepared drinks"],
          motivations: ["Lower waste"],
          objections: ["Price"],
        },
      ],
      current_channels: ["Retail", "Email"],
      tones_to_avoid: ["Alarmist"],
    };
    const draft = toBriefDraft(enriched);
    expect(draft.version).toBe(1);
    expect(serializeBriefDraft(draft)).toEqual(enriched);
  });
});
