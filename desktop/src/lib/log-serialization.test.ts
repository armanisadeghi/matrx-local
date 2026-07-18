import { describe, expect, it } from "vitest";
import { formatConsoleArguments, serializeLogValue } from "./log-serialization";

describe("log serialization", () => {
  it("preserves useful Error fields", () => {
    const error = new TypeError("Load failed");
    const parsed = JSON.parse(serializeLogValue(error)) as Record<string, unknown>;

    expect(parsed).toMatchObject({ name: "TypeError", message: "Load failed" });
    expect(parsed.stack).toEqual(expect.stringContaining("TypeError: Load failed"));
  });

  it("preserves nested errors and survives circular data", () => {
    const payload: Record<string, unknown> = { error: new Error("nested") };
    payload.self = payload;

    expect(serializeLogValue(payload)).toContain('"message":"nested"');
    expect(serializeLogValue(payload)).toContain('"self":"[Circular]"');
  });

  it("formats mixed console arguments without losing the error message", () => {
    expect(formatConsoleArguments(["[cloud-chat] stream failure", new Error("Load failed")]))
      .toContain('"message":"Load failed"');
  });
});
