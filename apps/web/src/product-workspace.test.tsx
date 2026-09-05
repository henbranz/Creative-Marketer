import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { catalogApi, type Workspace } from "./catalog-api";
import { ProductWorkspaceApp } from "./product-workspace";

const brand = {
  id: "10000000-0000-0000-0000-000000000001",
  tenant_id: "20000000-0000-0000-0000-000000000001",
  name: "Northstar",
  slug: "northstar",
  website_url: null,
  status: "active" as const,
  profile: {
    industry: "Outdoor",
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
  created_by: "30000000-0000-0000-0000-000000000001",
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z",
  can_edit: true,
};
const product = {
  id: "40000000-0000-0000-0000-000000000001",
  tenant_id: brand.tenant_id,
  brand_id: brand.id,
  name: "Atlas",
  slug: "atlas",
  sku: null,
  category: "Drinkware",
  short_description: "An everyday bottle",
  status: "draft" as const,
  profile: {
    description: "Bottle",
    features: ["Double wall"],
    benefits: ["Cold all day"],
    materials: [],
    variants: [],
    price: "29.90",
    currency: "USD",
    estimated_margin: null,
    target_audiences: [
      {
        name: "Commuters",
        description: "",
        pain_points: ["Waste"],
        desires: [],
        motivations: [],
        objections: [],
      },
    ],
    problems_solved: [],
    use_cases: [],
    differentiators: ["Repairable"],
    purchase_objections: [],
    allowed_claims: ["Reusable"],
    prohibited_claims: ["Unbreakable"],
    shipping_summary: null,
    seasonality_notes: null,
    landing_page_url: null,
    competitor_product_refs: [],
  },
  created_by: brand.created_by,
  created_at: brand.created_at,
  updated_at: brand.updated_at,
  can_edit: true,
};
const brief = {
  product_id: product.id,
  revision: 1,
  updated_at: product.updated_at,
  can_edit: true,
  product_why: "Less waste",
  emotional_benefits: [],
  primary_audience: {
    name: "Commuters",
    description: "",
    pain_points: ["Waste"],
    desires: [],
    motivations: [],
    objections: [],
  },
  secondary_audiences: [],
  positioning_statement: "Repairable bottle",
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
  prohibited_messaging: ["Health claims"],
  required_disclaimers: [],
  legal_safety_constraints: [],
  geographical_restrictions: [],
};
const workspace: Workspace = {
  brand,
  product,
  brief,
  completeness: {
    score: 90,
    missing_sections: ["Benefits and features"],
    missing_fields: ["profile.benefits"],
  },
  latest_snapshot: null,
};

function mocks() {
  vi.spyOn(catalogApi, "listBrands").mockResolvedValue([brand]);
  vi.spyOn(catalogApi, "listProducts").mockResolvedValue([product]);
  vi.spyOn(catalogApi, "getWorkspace").mockResolvedValue(workspace);
  vi.spyOn(catalogApi, "saveBrief").mockResolvedValue({
    ...brief,
    revision: 2,
  });
  vi.spyOn(catalogApi, "createBrand").mockResolvedValue(brand);
  vi.spyOn(catalogApi, "createProduct").mockResolvedValue(workspace);
}

async function renderConnected() {
  sessionStorage.setItem(
    "cm-session",
    JSON.stringify({ tenantId: brand.tenant_id, credential: "issuer|subject" }),
  );
  render(<ProductWorkspaceApp />);
  await screen.findByText("Atlas");
}

describe("Product Workspace", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
    mocks();
  });

  it("renders the authenticated product list from the API", async () => {
    await renderConnected();
    expect(screen.getByText("Northstar")).toBeInTheDocument();
    expect(screen.getByText("Atlas")).toBeInTheDocument();
  });

  it("opens a real overview with completeness progress", async () => {
    await renderConnected();
    fireEvent.click(screen.getByText("Atlas"));
    expect(await screen.findByText("90%")).toBeInTheDocument();
    expect(screen.getByText("Double wall")).toBeInTheDocument();
  });

  it("navigates the structured brief without a giant form", async () => {
    await renderConnected();
    fireEvent.click(screen.getByText("Atlas"));
    await screen.findByText("90%");
    fireEvent.click(screen.getByRole("button", { name: "Brief" }));
    fireEvent.click(screen.getByRole("button", { name: /Audience/ }));
    expect(screen.getByLabelText("Primary audience")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Positioning statement"),
    ).not.toBeInTheDocument();
  });

  it("saves only after server acknowledgement", async () => {
    await renderConnected();
    fireEvent.click(screen.getByText("Atlas"));
    await screen.findByText("90%");
    fireEvent.click(screen.getByRole("button", { name: "Brief" }));
    fireEvent.click(screen.getByRole("button", { name: /Constraints/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save brief" }));
    expect(screen.getByText("Saving…")).toBeInTheDocument();
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(catalogApi.saveBrief).toHaveBeenCalledOnce();
  });

  it("shows a validation or API error instead of fake success", async () => {
    vi.mocked(catalogApi.saveBrief).mockRejectedValue(
      new Error("Validation failed"),
    );
    await renderConnected();
    fireEvent.click(screen.getByText("Atlas"));
    await screen.findByText("90%");
    fireEvent.click(screen.getByRole("button", { name: "Brief" }));
    fireEvent.click(screen.getByRole("button", { name: /Constraints/ }));
    fireEvent.click(screen.getByRole("button", { name: "Save brief" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Validation failed",
    );
    expect(screen.getByText("Error saving")).toBeInTheDocument();
  });

  it("renders member access as read-only", async () => {
    vi.mocked(catalogApi.getWorkspace).mockResolvedValue({
      ...workspace,
      product: { ...product, can_edit: false },
      brief: { ...brief, can_edit: false },
    });
    await renderConnected();
    fireEvent.click(screen.getByText("Atlas"));
    await screen.findByText("90%");
    fireEvent.click(screen.getByRole("button", { name: "Brief" }));
    expect(screen.getByText(/read-only access/i)).toBeInTheDocument();
    expect(
      screen.getByLabelText("Why does this product exist?"),
    ).toBeDisabled();
  });

  it("uses honest future-tab empty states", async () => {
    await renderConnected();
    fireEvent.click(screen.getByText("Atlas"));
    await screen.findByText("90%");
    fireEvent.click(screen.getByRole("button", { name: "Assets" }));
    expect(screen.getByText("No assets yet")).toBeInTheDocument();
    expect(screen.getByText(/shared asset library/)).toBeInTheDocument();
  });

  it("shows backend loading errors and no invented product state", async () => {
    vi.mocked(catalogApi.listBrands).mockRejectedValue(
      new Error("API unavailable"),
    );
    sessionStorage.setItem(
      "cm-session",
      JSON.stringify({
        tenantId: brand.tenant_id,
        credential: "issuer|subject",
      }),
    );
    render(<ProductWorkspaceApp />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "API unavailable",
    );
    expect(screen.queryByText("Atlas")).not.toBeInTheDocument();
  });

  it("creates a product through the selected brand", async () => {
    await renderConnected();
    fireEvent.click(screen.getByRole("button", { name: "New product" }));
    fireEvent.change(screen.getByLabelText("Product name"), {
      target: { value: "Atlas" },
    });
    fireEvent.change(screen.getByLabelText("Category"), {
      target: { value: "Drinkware" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create product" }));
    await waitFor(() =>
      expect(catalogApi.createProduct).toHaveBeenCalledOnce(),
    );
  });
});
