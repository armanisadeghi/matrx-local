/**
 * Expand a prompt template + variable option map into concrete prompt lines.
 */

import {
  buildJobs,
  createBatchSnapshot,
  createEmptyPromptJob,
  createPromptTarget,
  extractVariableNames,
  PROMPT_TEMPLATE_FIELDS,
  type MatrixOption,
  type MatrixVariable,
} from "@/lib/prompt-matrix";
import { makeId } from "@/lib/prompt-matrix/storage";

export interface VariableOptionMap {
  name: string;
  options: string[];
}

export interface ExpandedVariation {
  prompt: string;
  negativePrompt: string;
  label: string;
}

export function extractTemplateVariableNames(
  prompt: string,
  negativePrompt: string,
): string[] {
  return extractVariableNames([prompt, negativePrompt]);
}

export function expandPromptVariations(
  templatePrompt: string,
  templateNegative: string,
  variables: readonly VariableOptionMap[],
): { variations: ExpandedVariation[]; errors: string[] } {
  const tokenNames = extractTemplateVariableNames(
    templatePrompt,
    templateNegative,
  );

  if (tokenNames.length === 0) {
    return {
      variations: [
        {
          prompt: templatePrompt.trim(),
          negativePrompt: templateNegative.trim(),
          label: "single",
        },
      ],
      errors: [],
    };
  }

  const byName = new Map(
    variables.map((v) => [v.name.trim().toLowerCase(), v]),
  );
  const matrixVariables: MatrixVariable[] = tokenNames.map((name) => {
    const row = byName.get(name.toLowerCase());
    const options: MatrixOption[] = (row?.options ?? [])
      .map((value) => value.trim())
      .filter((value) => value.length > 0)
      .map((value) => ({
        id: makeId(),
        value,
        enabled: true,
      }));
    return {
      id: makeId(),
      name,
      binding: { kind: "text" as const },
      options,
      baselineOptionId: null,
      linkGroup: null,
      enabled: true,
    };
  });

  const missing = matrixVariables.filter((v) => v.options.length === 0);
  if (missing.length > 0) {
    return {
      variations: [],
      errors: missing.map(
        (v) =>
          `Variable "{{${v.name}}}" has no options — map a list or add values.`,
      ),
    };
  }

  const spec = {
    fields: PROMPT_TEMPLATE_FIELDS.map((f) => ({
      ...f,
      text: f.id === "prompt" ? templatePrompt : templateNegative,
    })),
    variables: matrixVariables,
    pools: [],
    strategy: { kind: "cartesian" as const },
    seed: {
      mode: "fixed" as const,
      baseSeed: 1,
      repeats: 1,
      rngSeed: 1,
    },
  };

  const snapshot = createBatchSnapshot(spec);
  if (snapshot.errors.length > 0) {
    return { variations: [], errors: snapshot.errors };
  }

  const target = createPromptTarget();
  const built = buildJobs(
    target,
    createEmptyPromptJob(),
    snapshot.combinations,
    spec.variables,
  );
  if (built.errors.length > 0) {
    return { variations: [], errors: built.errors };
  }

  return {
    variations: built.jobs.map((b) => ({
      prompt: b.job.prompt,
      negativePrompt: b.job.negativePrompt,
      label: b.label,
    })),
    errors: [],
  };
}
