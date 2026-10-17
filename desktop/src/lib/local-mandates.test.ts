/**
 * The local-model Mandate door: the Holder's authored messages and settings
 * are what runs, a stale (offline) answer says so, and a resolution failure
 * REFUSES — there is no fallback prompt anywhere on this path.
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

import {
  LOCAL_MODEL_MANDATE_KEYS,
  clearLocalMandateCache,
  fillHolderMessages,
  holderLeadMessages,
  resolveLocalMandate,
} from "@/lib/local-mandates";

function answer(stale: boolean) {
  return {
    mandate_key: "local.polish_transcript",
    resolution: { mandate_key: "local.polish_transcript", agent_id: "agent-9", is_version: true },
    definition: {
      definition_id: "agent-9",
      agent_id: "agent-9",
      model_id: "m",
      messages: [
        { role: "system", content: [{ type: "text", text: "HOLDER SYSTEM" }] },
        { role: "user", content: "Transcript:\n\n{{transcript}}" },
      ],
      settings: { temperature: 0.2, max_output_tokens: 4096, top_p: 0.9 },
      output_schema: { type: "object" },
    },
    resolved_at: "2026-09-25T10:00:00+00:00",
    stale,
    stale_reason: stale ? "AI Matrx is unreachable (down)" : null,
  };
}

describe("resolveLocalMandate", () => {
  beforeEach(() => {
    clearLocalMandateCache();
    engineGet.mockReset();
  });

  it("runs what the resolved Holder authored, with its settings and schema", async () => {
    engineGet.mockResolvedValue(answer(false));

    const holder = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);

    expect(engineGet).toHaveBeenCalledWith("/local-mandates/local.polish_transcript", undefined);
    expect(holder.messages).toEqual([
      { role: "system", content: "HOLDER SYSTEM" },
      { role: "user", content: "Transcript:\n\n{{transcript}}" },
    ]);
    expect(holder.settings).toEqual({ temperature: 0.2, topP: 0.9, maxTokens: 4096 });
    expect(holder.outputSchema).toEqual({ type: "object" });
    expect(holder.stale).toBe(false);
    expect(holder.staleReason).toBeNull();
  });

  it("an offline answer is the last platform answer, flagged stale, and never cached", async () => {
    engineGet.mockResolvedValue(answer(true));

    const first = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);
    await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);

    expect(first.stale).toBe(true);
    expect(first.staleReason).toMatch(/unreachable/);
    expect(first.messages[0]?.content).toBe("HOLDER SYSTEM");
    expect(engineGet).toHaveBeenCalledTimes(2);
  });

  it("a fresh answer is cached briefly", async () => {
    engineGet.mockResolvedValue(answer(false));
    await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);
    await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);
    expect(engineGet).toHaveBeenCalledTimes(1);
  });

  it("refuses, naming the mandate, when the platform cannot resolve it", async () => {
    engineGet.mockRejectedValue(new Error("HTTP 409 holderless"));
    await expect(
      resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.summarizeText),
    ).rejects.toThrow(/local\.summarize_text.*409/);
  });

});

/**
 * THE VARIABLE RULE: the Holder is filled by the ENGINE (matrx-ai's one
 * first-turn pass, POST /local-mandates/{key}/fill) — there is no fill in
 * TypeScript — and a conversation's lead is filled once, then frozen.
 */
describe("filling a Holder", () => {
  beforeEach(() => {
    clearLocalMandateCache();
    engineGet.mockReset();
    enginePost.mockReset();
    engineGet.mockResolvedValue(answer(false));
    if (typeof localStorage !== "undefined") localStorage.clear();
  });

  function filled(messages: Array<{ role: string; content: string }>) {
    return { mandate_key: "local.polish_transcript", messages, stale: false, stale_reason: null };
  }

  it("asks the engine with the run's variables and uses its answer verbatim", async () => {
    // What the engine's one pass answers: the undeclared {{other}} is left as
    // written, an empty declared value became "", the escape and the sections
    // are already applied, and the declared block is wrapped.
    const engineAnswer = [
      { role: "system", content: "S {{other}} [] {{text}} STD <house>\nRULES\n</house>" },
      { role: "user", content: "Transcript:\n\nhello" },
    ];
    enginePost.mockResolvedValue(filled(engineAnswer));
    const holder = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);

    const out = await fillHolderMessages(holder, { transcript: "hello", other: "LEAK" });

    expect(enginePost).toHaveBeenCalledWith(
      "/local-mandates/local.polish_transcript/fill",
      { variables: { transcript: "hello", other: "LEAK" } },
      undefined,
    );
    expect(out).toEqual(engineAnswer);
  });

  it("a missing prompt block refuses the run, naming the mandate and the block", async () => {
    enginePost.mockRejectedValue(new Error("HTTP 422 Prompt block(s) not found: house"));
    const holder = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);
    await expect(fillHolderMessages(holder, {})).rejects.toThrow(
      /local\.polish_transcript.*house/,
    );
  });

  it("a conversation's lead is filled on its first turn and never refilled", async () => {
    enginePost.mockResolvedValueOnce(
      filled([
        { role: "system", content: "TURN ONE" },
        { role: "user", content: "u" },
      ]),
    );
    enginePost.mockResolvedValueOnce(filled([{ role: "system", content: "REFILLED" }]));
    const holder = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);

    const first = await holderLeadMessages(holder, { conversationKey: "conv-1" });
    const second = await holderLeadMessages(holder, { conversationKey: "conv-1" });

    expect(first[0]?.content).toBe("TURN ONE");
    expect(second).toEqual(first);
    expect(enginePost).toHaveBeenCalledTimes(1);
  });

  it("drops a blank system message from the lead", async () => {
    enginePost.mockResolvedValue(
      filled([
        { role: "system", content: "  " },
        { role: "user", content: "u" },
      ]),
    );
    const holder = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);
    expect(await holderLeadMessages(holder)).toEqual([{ role: "user", content: "u" }]);
  });
});
