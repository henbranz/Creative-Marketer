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
  it("matches the bridge golden path for Production identities", async () => {
    const id = "00000000-0000-0000-0000-000000000001";
    await expect(
      obsidianOpenUrl("Creative Brain", "production_plan", id),
    ).resolves.toBe(
      "obsidian://open?vault=Creative+Brain&file=Production%2Fproduction-plan--5c81f666cc720c0fe27d45f159f68c12b7bb592484ba772e44eaa3b6231107b2",
    );
    await expect(obsidianOpenUrl("Creative Brain", "asset", id)).resolves.toBe(
      "obsidian://open?vault=Creative+Brain&file=Assets%2Fasset--4b60dacb33a2049ec4da0d7d23f06760c66731b5463ee5eb140647ae5fd298fa",
    );
  });
});
