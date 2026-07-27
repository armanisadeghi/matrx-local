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
  extractPoolRefs,
  extractTokenDeclarationNames,
  extractVariableNames,
  PROMPT_TEMPLATE_FIELDS,
  secureRandom,
  type MatrixOption,
  type MatrixPlan,
  type MatrixPool,
  type MatrixSpec,
  type MatrixVariable,
  type RandomSource,
  variableKey,
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
  /** Injectable only for deterministic tests; production uses Web Crypto. */
  random?: RandomSource;
}

export function extractTemplateVariableNames(
  prompt: string,
  negativePrompt: string,
): string[] {
  return extractTokenDeclarationNames([prompt, negativePrompt]);
}

interface MatrixInputs {
  variables: MatrixVariable[];
  pools: MatrixPool[];
  errors: string[];
}

function buildMatrixInputs(
  templatePrompt: string,
  templateNegative: string,
  variables: readonly VariableOptionMap[],
): MatrixInputs {
  const texts = [templatePrompt, templateNegative];
  const byName = new Map(
    variables.map((v) => [variableKey(v.name), v]),
  );
  const declarations = extractTokenDeclarationNames(texts);

  const optionsFor = (name: string): MatrixOption[] => {
    const row = byName.get(variableKey(name));
    return (row?.options ?? [])
      .map((value) => value.trim())
      .filter((value) => value.length > 0)
      .map((value) => ({
        id: makeId(),
        value,
        enabled: true,
      }));
  };

  const optionsByDeclaration = new Map(
    declarations.map((name) => [variableKey(name), optionsFor(name)]),
  );
  const missing = declarations.filter(
    (name) => (optionsByDeclaration.get(variableKey(name)) ?? []).length === 0,
  );

  const matrixVariables: MatrixVariable[] = extractVariableNames(texts).map(
    (name) => ({
      id: makeId(),
      name,
      binding: { kind: "text" as const },
      options: (optionsByDeclaration.get(variableKey(name)) ?? []).map(
        (option) => ({ ...option, id: makeId() }),
      ),
      baselineOptionId: null,
      linkGroup: null,
      enabled: true,
    }),
  );

  const pools: MatrixPool[] = extractPoolRefs(texts).map((ref) => {
    return {
      id: makeId(),
      name: ref.name,
      options: (
        optionsByDeclaration.get(variableKey(ref.name)) ?? []
      ).map((option) => ({ ...option, id: makeId() })),
      baselineOptionId: null,
      enabled: true,
    };
  });

  if (missing.length > 0) {
    return {
      variables: matrixVariables,
      pools,
      errors: missing.map(
        (name) =>
          `Variable "{{${name}}}" has no options — map a list or add values.`,
      ),
    };
  }

  return { variables: matrixVariables, pools, errors: [] };
}

function buildVariationSpec(
  templatePrompt: string,
  templateNegative: string,
  inputs: Pick<MatrixInputs, "variables" | "pools">,
  strategy: MatrixSpec["strategy"],
): MatrixSpec {
  return {
    fields: PROMPT_TEMPLATE_FIELDS.map((f) => ({
      ...f,
      text: f.id === "prompt" ? templatePrompt : templateNegative,
    })),
    variables: [...inputs.variables],
    pools: [...inputs.pools],
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

  const built = buildMatrixInputs(
    templatePrompt,
    templateNegative,
    variables,
  );
  if (built.errors.length > 0) return null;

  return countPlan(
    buildVariationSpec(templatePrompt, templateNegative, built, {
      kind: "cartesian",
    }),
  );
}

function expandRandomSnapshot(
  templatePrompt: string,
  templateNegative: string,
  inputs: Pick<MatrixInputs, "variables" | "pools">,
  want: number,
  random: RandomSource,
): MatrixPlan {
  const sampleSpec = buildVariationSpec(
    templatePrompt,
    templateNegative,
    inputs,
    { kind: "sample", count: want, seed: 1 },
  );
  return createBatchSnapshot(sampleSpec, random);
}

function expandSequentialSnapshot(
  templatePrompt: string,
  templateNegative: string,
  inputs: Pick<MatrixInputs, "variables" | "pools">,
  want: number,
  order: "sequence" | "reverse",
  total: number,
): { plan: MatrixPlan | null; errors: string[] } {
  const cartSpec = buildVariationSpec(
    templatePrompt,
    templateNegative,
    inputs,
    { kind: "cartesian" },
  );
  const analyzed = expandMatrix(cartSpec);
  if (analyzed.errors.length > 0) {
    return { plan: null, errors: analyzed.errors };
  }

  if (order === "reverse" && total > analyzed.combinations.length) {
    return {
      plan: null,
      errors: [
        `Reverse order only works when total options are at most ${analyzed.combinations.length.toLocaleString()}. Use Random or Sequence, or shorten your lists.`,
      ],
    };
  }

  const picked =
    order === "reverse"
      ? analyzed.combinations.slice(-want)
      : analyzed.combinations.slice(0, want);

  return {
    plan: {
      ...analyzed,
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

  const built = buildMatrixInputs(
    templatePrompt,
    templateNegative,
    variables,
  );
  if (built.errors.length > 0) {
    return { variations: [], errors: built.errors, total: 0, truncated: false };
  }

  const cartesianSpec = buildVariationSpec(
    templatePrompt,
    templateNegative,
    built,
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
      built,
      want,
      options?.random ?? secureRandom,
    );
  } else {
    const sequential = expandSequentialSnapshot(
      templatePrompt,
      templateNegative,
      built,
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
    built,
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
