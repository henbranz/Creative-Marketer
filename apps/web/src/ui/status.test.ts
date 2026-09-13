import { describe, expect, it } from "vitest";

import { statusVariant } from "./status";

describe("statusVariant", () => {
  it.each([
    ["APPROVED_FOR_PRODUCTION", "success"],
    ["Pending approval", "warning"],
    ["RUNNING", "info"],
    ["SHORTLISTED", "accent"],
    ["OUTCOME_UNKNOWN", "danger"],
    ["provider-specific-future-state", "neutral"],
  ])("maps %s to %s", (status, expected) => {
    expect(statusVariant(status)).toBe(expected);
  });
});
