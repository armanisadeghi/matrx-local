import { describe, expect, it } from "vitest";
import { displaySessionDetailValue } from "./SessionDetailsComparisonTable";

describe("session detail comparison truthfulness", () => {
  it("distinguishes an unobserved field from an observed empty value", () => {
    expect(displaySessionDetailValue(null, false)).toBe("Not reported by AI Matrx");
    expect(displaySessionDetailValue(null, true)).toBe("Empty");
  });

  it("renders structured values without losing their fields", () => {
    expect(displaySessionDetailValue({ pinned: true })).toBe('{"pinned":true}');
  });
});
