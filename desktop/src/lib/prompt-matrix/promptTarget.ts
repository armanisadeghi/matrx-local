/**
 * Text-only prompt target — expand {{variables}} into prompt variations without
 * any image model, queue, or param axes.
 */

import type { MatrixTarget } from "./targets";
import type { TemplateField } from "./types";

export interface PromptJob {
  prompt: string;
  negativePrompt: string;
  seed: number;
}

export const PROMPT_TARGET_ID = "prompt";

export const PROMPT_TEMPLATE_FIELDS: TemplateField[] = [
  { id: "prompt", label: "Prompt", text: "" },
  { id: "negative_prompt", label: "Negative prompt", text: "" },
];

export function createEmptyPromptJob(): PromptJob {
  return { prompt: "", negativePrompt: "", seed: 0 };
}

export function createPromptTarget(): MatrixTarget<PromptJob> {
  return {
    id: PROMPT_TARGET_ID,
    label: "Prompt",
    fields: PROMPT_TEMPLATE_FIELDS,
    axes: [],
    resolveAxis: () => null,
    applyField: (job, fieldId, text) => {
      if (fieldId === "prompt") return { ...job, prompt: text };
      if (fieldId === "negative_prompt")
        return { ...job, negativePrompt: text };
      return job;
    },
    applySeed: (job, seed) => ({ ...job, seed }),
  };
}
