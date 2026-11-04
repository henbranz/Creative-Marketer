/* eslint-disable @next/next/no-img-element -- private signed grants must not enter an image proxy/cache */
"use client";

import { useEffect, useState } from "react";
import { catalogApi, type Asset, type Session } from "../catalog-api";
import { Overlay } from "./overlay";
import { ReadError, WorkspaceSkeleton } from "./feedback";
import { StatusBadge } from "./components";

/** Reads an existing immutable Asset only. Never starts generation or edits source bytes. */
export function AssetDrawer({
  asset,
  session,
  onClose,
}: {
  asset: Asset;
  session: Session;
  onClose: () => void;
}) {
  const [request, setRequest] = useState(0);
  const [preview, setPreview] = useState<
    { url: string } | { error: string } | null
  >(null);
  useEffect(() => {
    let active = true;
    let expiry: ReturnType<typeof setTimeout> | undefined;
    void catalogApi
      .downloadAsset(session, asset.id)
      .then((grant) => {
        const url = new URL(grant.url);
        const ttl = Date.parse(grant.expires_at) - Date.now();
        if (
          !["http:", "https:"].includes(url.protocol) ||
          !Number.isFinite(ttl) ||
          ttl <= 0
        )
          throw new Error("Invalid preview grant");
        if (!active) return;
        setPreview({ url: grant.url });
        expiry = setTimeout(
          () => {
            if (active)
              setPreview({
                error:
                  "This preview expired. Request a fresh preview to continue.",
              });
          },
          Math.min(ttl, 2_147_483_647),
        );
      })
      .catch(() => {
        if (active)
          setPreview({
            error:
              "The asset preview could not be loaded. Your original asset is unchanged.",
          });
      });
    return () => {
      active = false;
      clearTimeout(expiry);
    };
  }, [asset.id, session, request]);
  const retry = () => {
    setPreview(null);
    setRequest((value) => value + 1);
  };
  const failed = () =>
    setPreview({
      error:
        "The media could not be displayed. Retry to request a fresh preview.",
    });
  return (
    <Overlay title="Asset details" onClose={onClose}>
      <h3 className="asset-detail-title">{asset.original_filename}</h3>
      <StatusBadge status={asset.status} />
      {!preview ? (
        <WorkspaceSkeleton label="Loading asset preview…" />
      ) : "error" in preview ? (
        <ReadError message={preview.error} onRetry={retry} />
      ) : (
        <div className="asset-media">
          {asset.kind === "image" ? (
            <img
              src={preview.url}
              alt={asset.original_filename}
              referrerPolicy="no-referrer"
              onError={failed}
            />
          ) : asset.kind === "video" ? (
            <video
              src={preview.url}
              controls
              playsInline
              preload="metadata"
              aria-label={`Preview ${asset.original_filename}`}
              onError={failed}
            />
          ) : (
            <a
              href={preview.url}
              target="_blank"
              rel="noopener noreferrer"
              referrerPolicy="no-referrer"
            >
              Open document
            </a>
          )}
        </div>
      )}
      <dl className="detail-fields">
        <div>
          <dt>Type</dt>
          <dd>{asset.kind}</dd>
        </div>
        <div>
          <dt>Role</dt>
          <dd>{asset.role.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>
            {asset.byte_size
              ? `${(asset.byte_size / 1024 / 1024).toFixed(1)} MB`
              : "—"}
          </dd>
        </div>
        <div>
          <dt>Asset ID</dt>
          <dd>
            <code>{asset.id}</code>
          </dd>
        </div>
      </dl>
      <p className="readonly-note">
        This is a preview of the original immutable asset. Opening it does not
        generate media or authorize publishing.
      </p>
    </Overlay>
  );
}
