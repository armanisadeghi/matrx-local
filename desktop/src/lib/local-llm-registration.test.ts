import { describe, expect, it } from "vitest";
import { matchesRegisteredLocalLlm } from "./local-llm-registration";

describe("local LLM registration reconciliation", () => {
  it("does not reconnect a matching registration during a transient probe failure", () => {
    expect(
      matchesRegisteredLocalLlm(
        {
          registered: true,
          available: false,
          port: 22199,
          model_name: "qwen-local",
        },
        22199,
        "qwen-local",
      ),
    ).toBe(true);
  });

  it("requires the registered port and model to match", () => {
    const status = {
      registered: true,
      available: true,
      port: 22199,
      model_name: "qwen-local",
    };
    expect(matchesRegisteredLocalLlm(status, 22200, "qwen-local")).toBe(false);
    expect(matchesRegisteredLocalLlm(status, 22199, "other-model")).toBe(false);
  });

  it("remains compatible with engines that only expose available", () => {
    expect(
      matchesRegisteredLocalLlm(
        { available: true, port: 22199, model_name: "qwen-local" },
        22199,
        "qwen-local",
      ),
    ).toBe(true);
  });
});
