import { describe, expect, it } from "vitest";

import { formatApiErrorPayload } from "./api-errors";

describe("API error formatting", () => {
  it("humanizes FastAPI validation arrays without echoing input", () => {
    const message = formatApiErrorPayload({
      detail: [
        {
          type: "string_too_long",
          loc: ["body", "primary_audience", "description"],
          msg: "String should have at most 8000 characters",
          input: "secret payload must never render",
        },
      ],
    });
    expect(message).toBe(
      "primary audience → description: String should have at most 8000 characters",
    );
    expect(message).not.toContain("secret payload");
  });

  it("handles nested, malformed, empty, and non-JSON-shaped errors safely", () => {
    expect(
      formatApiErrorPayload({ detail: { message: "Conflict detected" } }),
    ).toBe("Conflict detected");
    expect(formatApiErrorPayload({ detail: [{ input: "private" }] })).toBe(
      "The request contains invalid fields.",
    );
    expect(formatApiErrorPayload(null)).toBe(
      "The request could not be completed.",
    );
    expect(formatApiErrorPayload({ detail: {} })).not.toContain(
      "[object Object]",
    );
  });
});
