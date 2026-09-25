/**
 * AI Polish styles are Mandates. A built-in style runs its own Holder; a custom
 * style runs local.polish_style_custom with the person's text as a variable —
 * and a style saved before the conversion (with the platform's JSON instruction
 * appended) produces the SAME system text it always did.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const SUFFIX = "\nPLATFORM JSON INSTRUCTION.";
const resolveLocalMandate = vi.fn();

vi.mock("@/lib/local-mandates", async (orig) => ({
  ...(await orig<typeof import("@/lib/local-mandates")>()),
  resolveLocalMandate: (...a: unknown[]) => resolveLocalMandate(...a),
}));

import { substituteVariables } from "@/lib/local-mandates";
import { BUILT_IN_PRESETS, personStyleText, polishRunFor } from "@/lib/polish-presets";

describe("polish styles through mandates", () => {
  beforeEach(() => {
    resolveLocalMandate.mockReset();
    resolveLocalMandate.mockResolvedValue({
      messages: [
        { role: "system", content: `{{style_instructions}}${SUFFIX}` },
        { role: "user", content: "Transcript:\n\n{{transcript}}" },
      ],
    });
  });

  it("every built-in style names a local.polish_style_* mandate and carries no prompt", () => {
    expect(BUILT_IN_PRESETS).toHaveLength(6);
    for (const p of BUILT_IN_PRESETS) {
      expect(p.mandateKey).toMatch(/^local\.polish_style_[a-z_]+$/);
      expect(p.systemPrompt).toBe("");
    }
  });

  it("a built-in style runs its own Holder with just the transcript", async () => {
    const run = await polishRunFor(BUILT_IN_PRESETS[1]!, "raw words");
    expect(run).toEqual({ mandateKey: "local.polish_style_formal", vars: { transcript: "raw words" } });
    expect(resolveLocalMandate).not.toHaveBeenCalled();
  });

  it("a legacy saved custom style yields the identical system text", async () => {
    const legacy = `Make it pirate speak.${SUFFIX}`;
    const run = await polishRunFor(
      { id: "custom-1", name: "Pirate", systemPrompt: legacy, isBuiltIn: false, updatedAt: "" },
      "raw words",
    );
    expect(run.mandateKey).toBe("local.polish_style_custom");
    expect(run.vars.style_instructions).toBe("Make it pirate speak.");
    expect(substituteVariables(`{{style_instructions}}${SUFFIX}`, run.vars)).toBe(legacy);
  });

  it("a new custom style is stored as the person's own text only", () => {
    expect(personStyleText("Make it pirate speak.", SUFFIX)).toBe("Make it pirate speak.");
    expect(personStyleText("Mine.", null)).toBe("Mine.");
  });
});
