/**
 * Expand a prompt template + variable option map into concrete prompt lines.
 *
 * Random mode uses the prompt-matrix sample engine (`sampleIndices` +
 * `createBatchSnapshot(secureRandom)`) so each generate pass draws distinct
 * combinations uniformly from the full cartesian product — never a cyclic walk.
 */

import {
  buildJobs,
  countPlan,
  createBatchSnapshot,
  createEmptyPromptJob,
  createPromptTarget,
  expandMatrix,
  extractVariableNames,
  PROMPT_TEMPLATE_FIELDS,
  secureRandom,
  type MatrixOption,
  type MatrixPlan,
  type MatrixSpec,
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

/** How generated rows are chosen from the cartesian product. */
export type VariationGenerateOrder = "random" | "sequence" | "reverse";

export interface ExpandPromptVariationsOptions {
  maxCount?: number;
  order?: VariationGenerateOrder;
}

export function extractTemplateVariableNames(
  prompt: string,
  negativePrompt: string,
): string[] {
  return extractVariableNames([prompt, negativePrompt]);
}

function buildMatrixVariables(
  tokenNames: readonly string[],
  variables: readonly VariableOptionMap[],
): { variables: MatrixVariable[]; errors: string[] } {
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
      variables: matrixVariables,
      errors: missing.map(
        (v) =>
          `Variable "{{${v.name}}}" has no options — map a list or add values.`,
      ),
    };
  }

  return { variables: matrixVariables, errors: [] };
}

function buildVariationSpec(
  templatePrompt: string,
  templateNegative: string,
  matrixVariables: readonly MatrixVariable[],
  strategy: MatrixSpec["strategy"],
): MatrixSpec {
  return {
    fields: PROMPT_TEMPLATE_FIELDS.map((f) => ({
      ...f,
      text: f.id === "prompt" ? templatePrompt : templateNegative,
    })),
    variables: [...matrixVariables],
    pools: [],
    strategy,
    seed: {
      mode: "fixed" as const,
      baseSeed: 1,
      repeats: 1,
      rngSeed: 1,
    },
  };
}

function resolveWantCount(total: number, maxCount?: number): number {
  if (maxCount === undefined) return total;
  return Math.min(total, Math.max(1, Math.trunc(maxCount)));
}

/** Exact cartesian total — null when any mapped variable has no options. */
export function countPromptVariations(
  templatePrompt: string,
  templateNegative: string,
  variables: readonly VariableOptionMap[],
): number | null {
  const tokenNames = extractTemplateVariableNames(
    templatePrompt,
    templateNegative,
  );
  if (tokenNames.length === 0) return 1;

  const built = buildMatrixVariables(tokenNames, variables);
  if (built.errors.length > 0) return null;

  return countPlan(
    buildVariationSpec(templatePrompt, templateNegative, built.variables, {
      kind: "cartesian",
    }),
  );
}

function expandRandomSnapshot(
  templatePrompt: string,
  templateNegative: string,
  matrixVariables: readonly MatrixVariable[],
  want: number,
): MatrixPlan {
  const sampleSpec = buildVariationSpec(
    templatePrompt,
    templateNegative,
    matrixVariables,
    { kind: "sample", count: want, seed: 1 },
  );
  return createBatchSnapshot(sampleSpec, secureRandom);
}

function expandSequentialSnapshot(
  templatePrompt: string,
  templateNegative: string,
  matrixVariables: readonly MatrixVariable[],
  want: number,
  order: "sequence" | "reverse",
  total: number,
): { plan: MatrixPlan | null; errors: string[] } {
  const cartSpec = buildVariationSpec(
    templatePrompt,
    templateNegative,
    matrixVariables,
    { kind: "cartesian" },
  );
  const analysed = expandMatrix(cartSpec);
  if (analysed.errors.length > 0) {
    return { plan: null, errors: analysed.errors };
  }

  if (order === "reverse" && total > analysed.combinations.length) {
    return {
      plan: null,
      errors: [
        `Reverse order only works when total options are at most ${analysed.combinations.length.toLocaleString()}. Use Random or Sequence, or shorten your lists.`,
      ],
    };
  }

  const picked =
    order === "reverse"
      ? analysed.combinations.slice(-want)
      : analysed.combinations.slice(0, want);

  return {
    plan: {
      ...analysed,
      combinations: picked,
      truncated: want < total,
    },
    errors: [],
  };
}

export function expandPromptVariations(
  templatePrompt: string,
  templateNegative: string,
  variables: readonly VariableOptionMap[],
  options?: ExpandPromptVariationsOptions,
): {
  variations: ExpandedVariation[];
  errors: string[];
  total: number;
  truncated: boolean;
} {
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
      total: 1,
      truncated: false,
    };
  }

  const built = buildMatrixVariables(tokenNames, variables);
  if (built.errors.length > 0) {
    return { variations: [], errors: built.errors, total: 0, truncated: false };
  }

  const cartesianSpec = buildVariationSpec(
    templatePrompt,
    templateNegative,
    built.variables,
    { kind: "cartesian" },
  );
  const total = countPlan(cartesianSpec);
  const order = options?.order ?? "random";
  const want = resolveWantCount(total, options?.maxCount);
  const truncated = want < total;

  let snapshot: MatrixPlan;
  if (order === "random") {
    snapshot = expandRandomSnapshot(
      templatePrompt,
      templateNegative,
      built.variables,
      want,
    );
  } else {
    const sequential = expandSequentialSnapshot(
      templatePrompt,
      templateNegative,
      built.variables,
      want,
      order,
      total,
    );
    if (sequential.errors.length > 0 || sequential.plan === null) {
      return {
        variations: [],
        errors: sequential.errors,
        total,
        truncated,
      };
    }
    snapshot = sequential.plan;
  }

  if (snapshot.errors.length > 0) {
    return { variations: [], errors: snapshot.errors, total, truncated };
  }

  const spec = buildVariationSpec(
    templatePrompt,
    templateNegative,
    built.variables,
    order === "random"
      ? { kind: "sample", count: want, seed: 1 }
      : { kind: "cartesian" },
  );

  const target = createPromptTarget();
  const jobs = buildJobs(
    target,
    createEmptyPromptJob(),
    snapshot.combinations,
    spec.variables,
  );
  if (jobs.errors.length > 0) {
    return { variations: [], errors: jobs.errors, total, truncated };
  }

  return {
    variations: jobs.jobs.map((b) => ({
      prompt: b.job.prompt,
      negativePrompt: b.job.negativePrompt,
      label: b.label,
    })),
    errors: [],
    total,
    truncated,
  };
}
