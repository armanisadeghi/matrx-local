/**
 * A local-model mandate run sends the Holder's messages exactly as the
 * ENGINE filled them (matrx-ai's one first-turn pass) — the desktop never
 * fills `{{name}}` itself, so an undeclared key the caller sends cannot reach
 * the model's text.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const engineGet = vi.fn();
const enginePost = vi.fn();
const chatCompletion = vi.fn();

vi.mock("@/lib/api", () => ({
  engine: {
    get: (...args: unknown[]) => engineGet(...args),
    post: (...args: unknown[]) => enginePost(...args),
  },
}));

vi.mock("@/lib/llm/api", () => ({
  chatCompletion: (...args: unknown[]) => chatCompletion(...args),
  structuredOutput: vi.fn(),
}));

import { runLocalMandate } from "@/hooks/use-llm-pipeline";
import { LOCAL_MODEL_MANDATE_KEYS, clearLocalMandateCache } from "@/lib/local-mandates";

const RAW = [
  { role: "system", content: "S {{other}} [{{empty}}] {{!text}}" },
  { role: "user", content: "Text: {{text}}" },
];

describe("runLocalMandate", () => {
  beforeEach(() => {
    clearLocalMandateCache();
    engineGet.mockReset();
    enginePost.mockReset();
    chatCompletion.mockReset();
    engineGet.mockResolvedValue({
      mandate_key: "local.summarize_text",
      resolution: { agent_id: "a", is_version: false },
      definition: { definition_id: "a", agent_id: "a", model_id: "m", messages: RAW, settings: {} },
      resolved_at: "t",
      stale: false,
      stale_reason: null,
    });
    chatCompletion.mockResolvedValue("ok");
  });

  it("runs the engine-filled messages, never a client-side fill", async () => {
    const filled = [
      { role: "system", content: "S {{other}} [] {{text}}" },
      { role: "user", content: "Text: hello" },
    ];
    enginePost.mockResolvedValue({ mandate_key: "local.summarize_text", messages: filled });

    await runLocalMandate(1234, LOCAL_MODEL_MANDATE_KEYS.summarizeText, {
      text: "hello",
      other: "LEAK",
    });

    expect(enginePost).toHaveBeenCalledWith(
      "/local-mandates/local.summarize_text/fill",
      { variables: { text: "hello", other: "LEAK" } },
      undefined,
    );
    expect(chatCompletion).toHaveBeenCalledWith(1234, filled, {});
  });

  it("refuses the run, naming the block, when the engine cannot resolve it", async () => {
    enginePost.mockRejectedValue(new Error("HTTP 422 Prompt block(s) not found: house"));
    await expect(
      runLocalMandate(1234, LOCAL_MODEL_MANDATE_KEYS.summarizeText, { text: "x" }),
    ).rejects.toThrow(/local\.summarize_text.*house/);
    expect(chatCompletion).not.toHaveBeenCalled();
  });
});
