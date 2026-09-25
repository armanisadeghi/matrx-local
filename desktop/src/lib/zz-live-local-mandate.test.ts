// SCRATCH live check — not committed. Real aidream resolution + real llama-server.
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
const SP = "/private/tmp/claude-501/-Users-armanisadeghi-code/824aa9b6-f4de-4b69-b980-93ed86dafe05/scratchpad";
vi.mock("@/lib/custodian", () => ({ getAuthedSession: async () => ({ access_token: readFileSync(`${SP}/jwt`, "utf8").trim() }) }));
vi.mock("@/lib/org/active-org", () => ({ requireActiveOrganizationId: async () => readFileSync(`${SP}/org`, "utf8").trim() }));
vi.mock("@/lib/app-config", () => ({ getAIDreamServerUrl: async () => "https://server.app.matrxserver.com" }));
vi.mock("@/lib/settings", () => ({ loadSettings: async () => ({ llmChatMaxTokens: 1024, llmStreamMaxTokens: 2048, llmStructuredOutputTemperature: 0.1, llmChatTemperature: 0.7, llmChatTopP: 0.8, llmChatTopK: 20, llmReasoningTemperature: 0.6, llmReasoningTopP: 0.95, llmReasoningTopK: 20, llmEnableThinking: false }) }));
import { runLocalMandate, parsePolishOutput } from "@/hooks/use-llm-pipeline";
import { LOCAL_MODEL_MANDATE_KEYS, resolveLocalMandate } from "@/lib/local-mandates";

const PORT = 18089;
const TRANSCRIPT = "um so today i wanted to uh talk about the the quarterly budget you know we're like over by about ten percent on travel and um i think we should sort of cut the offsite and move the team sync to video calls instead";

describe("live local mandate run", () => {
  it("resolves every local mandate to its Holder", async () => {
    for (const key of Object.values(LOCAL_MODEL_MANDATE_KEYS)) {
      const h = await resolveLocalMandate(key);
      console.log("RESOLVED", key, h.resolution.agent_id, h.resolution.provenance, JSON.stringify(h.settings), h.messages.map((m) => `${m.role}:${m.content.length}`).join(","));
    }
  }, 60_000);
  it("polishes a transcript on the local model", async () => {
    const raw = await runLocalMandate(PORT, LOCAL_MODEL_MANDATE_KEYS.polishTranscript, { transcript: TRANSCRIPT });
    const out = parsePolishOutput(raw, "", TRANSCRIPT);
    console.log("POLISH RAW", JSON.stringify(raw));
    expect(out.cleaned.length).toBeGreaterThan(20);
    expect(out.title.length).toBeGreaterThan(0);
  }, 240_000);
  it("summarizes on the local model", async () => {
    const text = await runLocalMandate<string>(PORT, LOCAL_MODEL_MANDATE_KEYS.summarizeText, { text: TRANSCRIPT });
    console.log("SUMMARY", JSON.stringify(text));
    expect(text.length).toBeGreaterThan(10);
  }, 240_000);
});
