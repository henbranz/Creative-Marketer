import { describe, expect, it } from "vitest";

import { listText, slugify } from "./catalog-api";

describe("catalog form utilities", () => {
  it("creates canonical product slugs", () =>
    expect(slugify(" Atlas Bottle 2 ")).toBe("atlas-bottle-2"));
  it("turns multiline fields into clean structured lists", () =>
    expect(listText(" First \n\nSecond\n ")).toEqual(["First", "Second"]));
});
