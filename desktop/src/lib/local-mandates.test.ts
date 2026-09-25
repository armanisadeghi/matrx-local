/**
 * The local-model Mandate door: the Holder's authored messages and settings
 * are what runs, a stale (offline) answer says so, and a resolution failure
 * REFUSES — there is no fallback prompt anywhere on this path.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const engineGet = vi.fn();

vi.mock("@/lib/api", () => ({
  engine: { get: (...args: unknown[]) => engineGet(...args) },
}));

import {
  LOCAL_MODEL_MANDATE_KEYS,
  clearLocalMandateCache,
  resolveLocalMandate,
  substituteVariables,
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

  it("substitutes supplied variables and leaves unknown ones as authored", () => {
    expect(substituteVariables("A {{text}} B {{other}}", { text: "x" })).toBe(
      "A x B {{other}}",
    );
  });
});
