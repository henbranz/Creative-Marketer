import { describe, expect, it } from "vitest";

import { getPublicConfig } from "./config";

describe("getPublicConfig", () => {
  it("returns validated public configuration", () => {
    expect(
      getPublicConfig({ NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000" }),
    ).toEqual({
      apiBaseUrl: "http://localhost:8000",
      obsidianVaultName: undefined,
    });
  });

  it("accepts an optional local Obsidian vault display name", () => {
    expect(
      getPublicConfig({
        NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000",
        NEXT_PUBLIC_OBSIDIAN_VAULT_NAME: "Creative Brain",
      }).obsidianVaultName,
    ).toBe("Creative Brain");
  });

  it("rejects an invalid API URL", () => {
    expect(() =>
      getPublicConfig({ NEXT_PUBLIC_API_BASE_URL: "not-a-url" }),
    ).toThrow();
  });
});
