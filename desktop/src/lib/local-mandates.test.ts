/**
 * The local-model Mandate door: the Holder's authored messages and settings
 * are what runs, and a resolution failure REFUSES — there is no fallback
 * prompt anywhere on this path.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchMandateResolution = vi.fn();
const fetchAgentExecutionDefinition = vi.fn();

vi.mock("@/lib/aidream-client", () => ({
  fetchMandateResolution: (...args: unknown[]) => fetchMandateResolution(...args),
  fetchAgentExecutionDefinition: (...args: unknown[]) =>
    fetchAgentExecutionDefinition(...args),
}));
vi.mock("@/lib/custodian", () => ({
  getAuthedSession: async () => ({ access_token: "jwt" }),
}));
vi.mock("@/lib/org/active-org", () => ({
  requireActiveOrganizationId: async () => "org-1",
}));

import {
  LOCAL_MODEL_MANDATE_KEYS,
  clearLocalMandateCache,
  resolveLocalMandate,
  substituteVariables,
} from "@/lib/local-mandates";

describe("resolveLocalMandate", () => {
  beforeEach(() => {
    clearLocalMandateCache();
    fetchMandateResolution.mockReset();
    fetchAgentExecutionDefinition.mockReset();
  });

  it("runs what the resolved Holder authored, with its settings and schema", async () => {
    fetchMandateResolution.mockResolvedValue({
      mandate_key: "local.polish_transcript",
      agent_id: "agent-9",
      is_version: true,
    });
    fetchAgentExecutionDefinition.mockResolvedValue({
      definition_id: "agent-9",
      agent_id: "agent-9",
      model_id: "m",
      messages: [
        { role: "system", content: [{ type: "text", text: "HOLDER SYSTEM" }] },
        { role: "user", content: "Transcript:\n\n{{transcript}}" },
      ],
      settings: { temperature: 0.2, max_output_tokens: 4096, top_p: 0.9 },
      output_schema: { type: "object" },
    });

    const holder = await resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.polishTranscript);

    expect(fetchAgentExecutionDefinition).toHaveBeenCalledWith(
      "agent-9",
      true,
      "jwt",
      "org-1",
      undefined,
    );
    expect(holder.messages).toEqual([
      { role: "system", content: "HOLDER SYSTEM" },
      { role: "user", content: "Transcript:\n\n{{transcript}}" },
    ]);
    expect(holder.settings).toEqual({ temperature: 0.2, topP: 0.9, maxTokens: 4096 });
    expect(holder.outputSchema).toEqual({ type: "object" });
  });

  it("refuses, naming the mandate, when the platform cannot resolve it", async () => {
    fetchMandateResolution.mockRejectedValue(new Error("HTTP 409 holderless"));
    await expect(
      resolveLocalMandate(LOCAL_MODEL_MANDATE_KEYS.summarizeText),
    ).rejects.toThrow(/local\.summarize_text.*409/);
    expect(fetchAgentExecutionDefinition).not.toHaveBeenCalled();
  });

  it("substitutes supplied variables and leaves unknown ones as authored", () => {
    expect(substituteVariables("A {{text}} B {{other}}", { text: "x" })).toBe(
      "A x B {{other}}",
    );
  });
});
