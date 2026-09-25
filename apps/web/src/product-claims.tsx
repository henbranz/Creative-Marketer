"use client";

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  catalogApi,
  type ClaimsWrite,
  type Session,
  type Workspace,
} from "./catalog-api";

type Scope = ClaimsWrite["scope"];
const labels = { product: "Product", brand: "Brand" } as const;

// Verbatim stored Product/Brief fields only; never Research or generated suggestions.
function candidates(workspace: Workspace) {
  return [
    ...(workspace.product.profile.features ?? []).map((text) => ({
      text,
      source: "Product feature",
    })),
    ...(workspace.product.profile.materials ?? []).map((text) => ({
      text,
      source: "Product material",
    })),
    {
      text: workspace.brief.product_why ?? "",
      source: "Brief · product purpose",
    },
    ...(workspace.brief.offers ?? []).map((text) => ({
      text,
      source: "Brief · offer (confirm it is current)",
    })),
  ]
    .filter((item) => item.text.trim())
    .slice(0, 12);
}

export function ProductClaims({
  workspace,
  session,
  onSaved,
  onDirtyChange,
}: {
  workspace: Workspace;
  session: Session;
  onSaved: (workspace: Workspace) => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const approved = {
    product: workspace.product.profile.allowed_claims ?? [],
    brand: workspace.brand.profile.allowed_claims ?? [],
  };
  const [drafts, setDrafts] = useState(approved);
  // Keep each draft's original authority even if saving the other scope refreshes
  // the workspace. A concurrent edit must still conflict rather than be overwritten.
  const [bases, setBases] = useState(approved);
  const [candidateDrafts, setCandidateDrafts] = useState(() =>
    candidates(workspace),
  );
  const [busy, setBusy] = useState<Scope | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const editable = workspace.product.can_edit && workspace.brand.can_edit;
  const dirty = JSON.stringify(drafts) !== JSON.stringify(bases);
  useEffect(() => {
    onDirtyChange(dirty);
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, onDirtyChange]);

  const save = async (scope: Scope) => {
    setBusy(scope);
    setError("");
    setNotice("");
    try {
      const next = await catalogApi.saveClaims(session, workspace.product.id, {
        scope,
        allowed_claims: drafts[scope],
        expected_claims: bases[scope],
      });
      if (!mounted.current) return;
      setDrafts((current) => ({
        ...current,
        [scope]:
          scope === "product"
            ? (next.product.profile.allowed_claims ?? [])
            : (next.brand.profile.allowed_claims ?? []),
      }));
      setBases((current) => ({
        ...current,
        [scope]:
          scope === "product"
            ? (next.product.profile.allowed_claims ?? [])
            : (next.brand.profile.allowed_claims ?? []),
      }));
      onSaved(next);
      setNotice(
        `${labels[scope]} claims saved. ${next.latest_snapshot?.id !== workspace.latest_snapshot?.id ? "A new knowledge snapshot was created. Refresh Research before starting Creative." : "The approved authority is unchanged."}`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 409
          ? "Claims changed in another session. Keep a copy of your draft and reload before saving."
          : caught instanceof ApiError && caught.status === 403
            ? "Only an active Owner or Admin can approve claims."
            : "Claims could not be saved. Check for blank, duplicate, overlong or prohibited claims and try again.",
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="product-claims">
      <header className="claims-intro">
        <p className="eyebrow">Human-controlled authority</p>
        <h2>Product Claims</h2>
        <p>
          Approved Product Claims are factual statements that Creative agents
          are allowed to use as product facts.
        </p>
        <p>
          You are responsible for confirming accuracy and scope. These
          statements are not independently verified by AI. Only an Owner or
          Admin can explicitly approve and save.
        </p>
        <p>
          Saving changes creates new frozen authority. Existing runs and
          snapshots stay unchanged; Research must be refreshed before a new
          Creative run.
        </p>
      </header>
      {error && (
        <p role="alert" className="claims-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {(["product", "brand"] as const).map((scope) => (
        <section
          className="claims-card"
          key={scope}
          aria-label={`${labels[scope]} Claims`}
        >
          <h3>{labels[scope]} Claims</h3>
          <p>
            {scope === "product"
              ? "Applies only to this product."
              : "Applies to every product in this brand. Saving updates their snapshots too; confirm each statement is true across the brand."}
          </p>
          <h4>Approved {labels[scope]} Claims</h4>
          {approved[scope].length ? (
            <ul>
              {approved[scope].map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">No approved {scope} claims.</p>
          )}
          {editable && (
            <fieldset disabled={busy !== null}>
              <legend>
                {labels[scope]} claim drafts · not approved until saved
              </legend>
              {drafts[scope].map((text, index) => (
                <div className="claim-draft" key={index}>
                  <label className="field">
                    <span>
                      {labels[scope]} claim {index + 1}
                    </span>
                    <textarea
                      maxLength={500}
                      rows={2}
                      value={text}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [scope]: current[scope].map((value, at) =>
                            at === index ? event.target.value : value,
                          ),
                        }))
                      }
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() =>
                      setDrafts((current) => ({
                        ...current,
                        [scope]: current[scope].filter((_, at) => at !== index),
                      }))
                    }
                  >
                    Remove {scope} claim {index + 1}
                  </button>
                </div>
              ))}
              <div className="claims-actions">
                <button
                  type="button"
                  disabled={drafts[scope].length >= 30}
                  onClick={() =>
                    setDrafts((current) => ({
                      ...current,
                      [scope]: [...current[scope], ""],
                    }))
                  }
                >
                  Add {scope} claim
                </button>
                <button
                  type="button"
                  className="claims-save"
                  disabled={
                    JSON.stringify(drafts[scope]) ===
                      JSON.stringify(bases[scope]) ||
                    drafts[scope].some((text) => !text.trim())
                  }
                  onClick={() => void save(scope)}
                >
                  {busy === scope
                    ? "Saving…"
                    : `Approve & save ${scope} claims`}
                </button>
              </div>
            </fieldset>
          )}
        </section>
      ))}
      <section
        className="claims-card claims-candidates"
        aria-label="Claim candidates"
      >
        <p className="eyebrow">Candidates · not approved</p>
        <h3>Claim candidates</h3>
        <p>
          Verbatim Product or Brief text, not verified facts. Confirm wording,
          edition scope and current offers. Edit and add to the draft, then
          explicitly approve and save Product Claims. Research and competitor
          statements are never promoted here.
        </p>
        {!candidateDrafts.length && (
          <p className="muted">No candidates to review.</p>
        )}
        {candidateDrafts.map((candidate, index) => (
          <article key={index}>
            <p>{candidate.source} · candidate only</p>
            <label className="field">
              <span>Candidate {index + 1}</span>
              <textarea
                rows={3}
                value={candidate.text}
                disabled={!editable || busy !== null}
                onChange={(event) =>
                  setCandidateDrafts((current) =>
                    current.map((value, at) =>
                      at === index
                        ? { ...value, text: event.target.value }
                        : value,
                    ),
                  )
                }
              />
            </label>
            {editable && (
              <div className="claims-actions">
                <button
                  type="button"
                  disabled={
                    busy !== null ||
                    !candidate.text.trim() ||
                    candidate.text.trim().length > 500 ||
                    drafts.product.length >= 30
                  }
                  onClick={() => {
                    setDrafts((current) => ({
                      ...current,
                      product: [...current.product, candidate.text.trim()],
                    }));
                    setCandidateDrafts((current) =>
                      current.filter((_, at) => at !== index),
                    );
                    setNotice(
                      "Candidate added to draft only. Review it, then Approve & save product claims.",
                    );
                  }}
                >
                  Add candidate {index + 1} to Product draft
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    setCandidateDrafts((current) =>
                      current.filter((_, at) => at !== index),
                    )
                  }
                >
                  Dismiss candidate {index + 1}
                </button>
              </div>
            )}
          </article>
        ))}
      </section>
    </div>
  );
}
