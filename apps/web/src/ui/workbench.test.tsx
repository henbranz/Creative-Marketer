import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NavigationIcon } from "./components";

describe("creative workbench foundation", () => {
  it("keeps all authored UI font sizes on the two canonical tokens", () => {
    for (const path of ["../../app/styles.css", "./workbench.css"]) {
      const css = readFileSync(new URL(path, import.meta.url), "utf8");
      const sizes = [...css.matchAll(/font-size:\s*([^;]+);/g)].map(
        (m) => m[1]!,
      );
      expect(sizes.length).toBeGreaterThan(0);
      expect(
        sizes.every((size) =>
          ["var(--font-body)", "var(--font-heading)", "100%"].includes(size),
        ),
      ).toBe(true);
    }
  });

  it("keeps font scaling, focus and minimum control sizes explicit", () => {
    const css = readFileSync("src/ui/workbench.css", "utf8");
    const tokens = readFileSync("src/ui/tokens.css", "utf8");
    expect(tokens).toContain("--font-body: 1rem");
    expect(tokens).toContain("--font-heading: 1.5rem");
    expect(css).toContain("min-height: 44px");
    expect(css).toContain("outline: 3px solid var(--focus-ring)");
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).not.toContain("linear-gradient");
  });

  it.each([
    "Command Center",
    "Products",
    "Agents",
    "Approvals",
    "Activity",
    "Settings",
  ])("uses a decorative vector beside the %s navigation label", (name) => {
    const { container } = render(
      <button>
        <NavigationIcon name={name} />
        {name}
      </button>,
    );
    expect(screen.getByRole("button", { name })).toBeTruthy();
    expect(container.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(container.querySelector("svg path")).toHaveAttribute("d");
    expect(screen.queryByRole("img")).toBeNull();
  });
});
