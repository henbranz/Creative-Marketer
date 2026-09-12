import { describe, expect, it } from "vitest";

import { listText, obsidianOpenUrl, slugify } from "./catalog-api";

describe("catalog form utilities", () => {
  it("creates canonical product slugs", () =>
    expect(slugify(" Atlas Bottle 2 ")).toBe("atlas-bottle-2"));
  it("turns multiline fields into clean structured lists", () =>
    expect(listText(" First \n\nSecond\n ")).toEqual(["First", "Second"]));
  it("builds a safe deterministic Obsidian product deep link", async () => {
    const first = await obsidianOpenUrl(
      "Creative Brain",
      "product",
      "stable-id",
    );
    const second = await obsidianOpenUrl(
      "Creative Brain",
      "product",
      "stable-id",
    );
    expect(first).toBe(second);
    expect(first).toMatch(
      /^obsidian:\/\/open\?vault=Creative\+Brain&file=Products%2Fproduct--[a-f0-9]{64}$/,
    );
  });
});
