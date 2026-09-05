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
