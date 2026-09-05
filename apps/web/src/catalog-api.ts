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
};

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
