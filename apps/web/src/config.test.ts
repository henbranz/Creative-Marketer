import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { getPublicConfig } from "./config";

describe("getPublicConfig", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

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

  it("normalizes an empty optional Obsidian vault name to undefined", () => {
    expect(
      getPublicConfig({
        NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000",
        NEXT_PUBLIC_OBSIDIAN_VAULT_NAME: "",
      }).obsidianVaultName,
    ).toBeUndefined();
  });

  it("rejects an invalid API URL", () => {
    expect(() =>
      getPublicConfig({ NEXT_PUBLIC_API_BASE_URL: "not-a-url" }),
    ).toThrow();
  });

  it("returns only explicitly allowlisted public configuration", () => {
    expect(
      getPublicConfig({
        NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000",
        NEXT_PUBLIC_OBSIDIAN_VAULT_NAME: "Creative Marketer",
        OPENAI_API_KEY: "must-not-escape",
        BYTEPLUS_LAS_API_KEY: "must-not-escape",
        CM_API_TOKEN: "must-not-escape",
        DATABASE_URL: "must-not-escape",
        OBJECT_STORAGE_SECRET_ACCESS_KEY: "must-not-escape",
      }),
    ).toEqual({
      apiBaseUrl: "http://localhost:8000",
      obsidianVaultName: "Creative Marketer",
    });
  });

  it("uses direct static references for the default browser environment", async () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/config.ts"),
      "utf8",
    );
    expect(source).toContain(
      "NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL",
    );
    expect(source).toContain("process.env.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME");
    expect(source).not.toMatch(/environment[^=]*=\s*process\.env[,\n]/);

    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
    vi.stubEnv("NEXT_PUBLIC_OBSIDIAN_VAULT_NAME", "");
    vi.resetModules();
    const compiledConfig = await import("./config");
    expect(compiledConfig.getPublicConfig()).toEqual({
      apiBaseUrl: "https://api.example.test",
      obsidianVaultName: undefined,
    });
  });
});
