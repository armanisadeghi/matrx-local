// @vitest-environment jsdom
/**
 * Load law for the built-in prompt library: listing the built-ins never asks
 * the engine to fill a Holder; using one fills exactly that one, once per
 * conversation.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const engineGet = vi.fn();
const enginePost = vi.fn();

vi.mock("@/lib/api", () => ({
  engine: {
    get: (...args: unknown[]) => engineGet(...args),
    post: (...args: unknown[]) => enginePost(...args),
  },
}));

import { clearLocalMandateCache } from "@/lib/local-mandates";
import { builtinPromptRunText, builtinPrompts, refreshBuiltinPrompts } from "@/lib/system-prompts";

function holderAnswer(path: string) {
  const key = decodeURIComponent(path.split("/").pop() ?? "");
  return {
    mandate_key: key,
    resolution: { agent_id: `agent-${key}`, is_version: false },
    definition: {
      definition_id: "d",
      agent_id: "a",
      model_id: "m",
      messages: [{ role: "system", content: `RAW ${key} {{x}}` }],
      settings: {},
    },
    resolved_at: "t",
    stale: false,
    stale_reason: null,
  };
}

describe("built-in prompt library", () => {
  beforeEach(() => {
    clearLocalMandateCache();
    engineGet.mockReset();
    enginePost.mockReset();
    engineGet.mockImplementation(async (path: string) => holderAnswer(path));
    enginePost.mockImplementation(async (path: string) => ({
      mandate_key: path,
      messages: [{ role: "system", content: "FILLED" }],
    }));
  });

  it("a refresh lists every built-in and makes zero fill calls", async () => {
    await refreshBuiltinPrompts();
    expect(builtinPrompts()).toHaveLength(7);
    expect(enginePost).not.toHaveBeenCalled();
  });

  it("using a built-in makes exactly one fill call, frozen for its conversation", async () => {
    await refreshBuiltinPrompts();

    const first = await builtinPromptRunText("builtin-summarize", "conv-1");
    const again = await builtinPromptRunText("builtin-summarize", "conv-1");

    expect(first).toBe("FILLED");
    expect(again).toBe("FILLED");
    expect(enginePost).toHaveBeenCalledTimes(1);
    expect(enginePost).toHaveBeenCalledWith(
      "/local-mandates/local.chat_persona_summarize/fill",
      { variables: {} },
      undefined,
    );
  });

  it("a person's own prompt is not a built-in and is never filled", async () => {
    expect(await builtinPromptRunText("user-123")).toBeNull();
    expect(enginePost).not.toHaveBeenCalled();
  });
});
