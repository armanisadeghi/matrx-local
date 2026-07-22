import { describe, expect, it } from "vitest";
import { clampNumber, isNumberDraft, parseNumberDraft } from "./number-input";

describe("isNumberDraft", () => {
  it("allows blank and minus while editing", () => {
    expect(isNumberDraft("", true)).toBe(true);
    expect(isNumberDraft("-", true)).toBe(true);
    expect(isNumberDraft("12", true)).toBe(true);
    expect(isNumberDraft("12.", true)).toBe(false);
    expect(isNumberDraft("12.5", false)).toBe(true);
    expect(isNumberDraft(".", false)).toBe(true);
    expect(isNumberDraft("abc", false)).toBe(false);
  });
});

describe("parseNumberDraft", () => {
  it("returns null for empty intermediate drafts", () => {
    expect(parseNumberDraft("", true)).toBeNull();
    expect(parseNumberDraft("-", true)).toBeNull();
    expect(parseNumberDraft(".", false)).toBeNull();
  });

  it("parses integers and floats", () => {
    expect(parseNumberDraft("42", true)).toBe(42);
    expect(parseNumberDraft("3.5", false)).toBe(3.5);
    expect(parseNumberDraft("3.5", true)).toBe(3);
  });
});

describe("clampNumber", () => {
  it("clamps to min/max when provided", () => {
    expect(clampNumber(0, 1, 10)).toBe(1);
    expect(clampNumber(99, 1, 10)).toBe(10);
    expect(clampNumber(5, 1, 10)).toBe(5);
  });
});
