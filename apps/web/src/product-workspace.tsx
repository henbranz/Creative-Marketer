"use client";

import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  type Brand,
  type BriefWrite,
  catalogApi,
  listText,
  type Product,
  type Session,
  slugify,
  type Workspace,
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
