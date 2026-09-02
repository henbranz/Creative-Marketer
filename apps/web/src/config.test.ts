import { describe, expect, it } from "vitest";

import { getPublicConfig } from "./config";

describe("getPublicConfig", () => {
  it("returns validated public configuration", () => {
    expect(
      getPublicConfig({ NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000" }),
    ).toEqual({
      apiBaseUrl: "http://localhost:8000",
    });
  });

  it("rejects an invalid API URL", () => {
    expect(() =>
      getPublicConfig({ NEXT_PUBLIC_API_BASE_URL: "not-a-url" }),
    ).toThrow();
  });
});
