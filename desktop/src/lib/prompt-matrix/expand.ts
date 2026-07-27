/**
 * The combination engine: MatrixSpec → the ordered list of runs to enqueue.
 *
 * Two invariants worth stating up front, because everything else follows:
 *
 * 1. `total` is computed ARITHMETICALLY, never by materializing. A 6-variable
 *    cartesian product can be millions of runs; the UI must be able to show
 *    that number (and refuse it) without allocating it.
 *
 * 2. Analysis order is never execution order. The deterministic expansion is
 *    used for counts, validation, and template inspection only. Every actual
 *    batch goes through createBatchSnapshot(), which shuffles full valid rows
 *    and assigns fresh seeds immediately before Preview or Queue.
 *
 * Pools (`{{color#1}}` …) are additive: every DISTINCT slot is one independent
 * axis backed by the pool's once-declared option list. Repeating a slot token
 * reuses its axis; different slots draw with replacement and may match.
 */

import {
  MAX_MATERIALIZED,
  type MatrixCombination,
  type MatrixOption,
  type MatrixPlan,
  type MatrixPool,
  type MatrixSpec,
  type MatrixVariable,
} from "./types";
import {
  extractPoolRefs,
  findTokens,
  hasUnclosedToken,
  poolSlotName,
  renderTemplate,
  sortSlots,
  tidyPrompt,
  variableKey,
} from "./parse";
import {
  MAX_SEED,
  Rng,
  type RandomSource,
  sampleIndices,
  secureRandom,
  shuffled,
} from "./rng";

/**
 * An axis is ONE independent dimension of the product. Usually one variable;
 * several when they are link-grouped (zipped); or one numbered pool slot.
 */
interface Axis {
  variables: MatrixVariable[];
  pool: MatrixPool | null;
  /** The single slot id when this is a pool axis; empty otherwise. */
  slots: string[];
  /** steps[i] = the option each member (variable or slot) takes at position i. */
  steps: MatrixOption[][];
  /** Index of the baseline step (used by the `baseline` strategy). */
  baselineStep: number;
}

const enabledOptions = (v: MatrixVariable | MatrixPool): MatrixOption[] =>
  v.options.filter((o) => o.enabled);

const activeVariables = (spec: MatrixSpec): MatrixVariable[] =>
  spec.variables.filter((v) => v.enabled && enabledOptions(v).length > 0);

const activePools = (spec: MatrixSpec): MatrixPool[] =>
  (spec.pools ?? []).filter((p) => p.enabled && enabledOptions(p).length > 0);

/** Slots currently referenced in the template, keyed by pool identity. */
function slotsByPoolKey(spec: MatrixSpec): Map<string, string[]> {
  const refs = extractPoolRefs(spec.fields.map((f) => f.text));
  return new Map(refs.map((r) => [r.key, sortSlots(r.slots)]));
}

/**
 * Build the axes, preserving user order. Linked variables collapse into one
 * axis at the position of the FIRST member, so dragging any member of a link
 * group moves the group. Active pool slots append after variables, one
 * independent axis per distinct slot.
 */
function buildAxes(spec: MatrixSpec): { axes: Axis[]; warnings: string[] } {
  const warnings: string[] = [];
  const vars = activeVariables(spec);
  const axes: Axis[] = [];
  const groupAxis = new Map<string, Axis>();

  for (const v of vars) {
    const opts = enabledOptions(v);
    if (v.linkGroup === null) {
      const baselineStep = Math.max(
        0,
        opts.findIndex((o) => o.id === v.baselineOptionId),
      );
      axes.push({
        variables: [v],
        pool: null,
        slots: [],
        steps: opts.map((o) => [o]),
        baselineStep,
      });
      continue;
    }
    const existing = groupAxis.get(v.linkGroup);
    if (existing === undefined) {
      const axis: Axis = {
        variables: [v],
        pool: null,
        slots: [],
        steps: opts.map((o) => [o]),
        baselineStep: 0,
      };
      groupAxis.set(v.linkGroup, axis);
      axes.push(axis);
      continue;
    }
    // Zip into the existing group axis. Unequal lengths truncate to the
    // shortest — loudly, because silently dropping the tail of a 10-option
    // list paired against a 3-option one is exactly the kind of quiet data
    // loss that makes a user distrust the tool.
    const before = existing.steps.length;
    const len = Math.min(before, opts.length);
    if (before !== opts.length) {
      warnings.push(
        `Linked variables in group "${v.linkGroup}" have different option ` +
          `counts (${before} vs ${opts.length}) — the group runs ${len} step` +
          `${len === 1 ? "" : "s"} and the extra options are ignored.`,
      );
    }
    existing.variables.push(v);
    existing.steps = existing.steps
      .slice(0, len)
      .map((step, i) => [...step, opts[i] as MatrixOption]);
  }

  // Pools: one independent axis per distinct slot. Every slot uses the same
  // once-declared option list, so n options across k slots produces n^k valid
  // assignments. Repeated occurrences of a slot are still one axis because
  // extractPoolRefs de-duplicates the slot id.
  const slotMap = slotsByPoolKey(spec);
  for (const pool of activePools(spec)) {
    const slots = slotMap.get(variableKey(pool.name));
    if (slots === undefined || slots.length === 0) continue; // unused — warned in validate

    const opts = enabledOptions(pool);
    const baselineStep = Math.max(
      0,
      opts.findIndex((o) => o.id === pool.baselineOptionId),
    );
    for (const slot of slots) {
      axes.push({
        variables: [],
        pool,
        slots: [slot],
        steps: opts.map((option) => [option]),
        baselineStep,
      });
    }
  }

  return { axes, warnings };
}

/** Exact run count for a set of axes under a strategy — no allocation. */
function countCombinations(axes: Axis[], spec: MatrixSpec): number {
  if (axes.length === 0) return 1; // a template with no variables is one run
  const lengths = axes.map((a) => a.steps.length);

  switch (spec.strategy.kind) {
    case "cartesian":
      return lengths.reduce((acc, n) => acc * n, 1);
    case "baseline":
      // The baseline run itself, plus every OTHER option of every axis.
      return 1 + lengths.reduce((acc, n) => acc + (n - 1), 0);
    case "zip":
      return Math.min(...lengths);
    case "sample": {
      const full = lengths.reduce((acc, n) => acc * n, 1);
      return Math.min(Math.max(0, Math.trunc(spec.strategy.count)), full);
    }
  }
}

/**
 * Decode a flat cartesian index into one step per axis.
 * The LAST axis varies fastest, so axis[0] is the outermost loop.
 */
function decodeCartesian(index: number, axes: Axis[]): number[] {
  const picks = new Array<number>(axes.length).fill(0);
  let rest = index;
  for (let i = axes.length - 1; i >= 0; i -= 1) {
    const len = (axes[i] as Axis).steps.length;
    picks[i] = rest % len;
    rest = Math.floor(rest / len);
  }
  return picks;
}

/** The per-axis step indices for each run, in emission order. */
function stepIndicesForPlan(
  axes: Axis[],
  spec: MatrixSpec,
  sampleRandom?: Pick<RandomSource, "int">,
): number[][] {
  if (axes.length === 0) return [[]];

  switch (spec.strategy.kind) {
    case "cartesian": {
      const total = countCombinations(axes, spec);
      const capped = Math.min(total, MAX_MATERIALIZED);
      return Array.from({ length: capped }, (_, i) => decodeCartesian(i, axes));
    }
    case "zip": {
      const len = Math.min(...axes.map((a) => a.steps.length));
      return Array.from({ length: len }, (_, i) => axes.map(() => i));
    }
    case "baseline": {
      const base = axes.map((a) => a.baselineStep);
      const out: number[][] = [[...base]];
      axes.forEach((axis, axisIdx) => {
        axis.steps.forEach((_step, stepIdx) => {
          if (stepIdx === axis.baselineStep) return;
          const row = [...base];
          row[axisIdx] = stepIdx;
          out.push(row);
        });
      });
      return out.slice(0, MAX_MATERIALIZED);
    }
    case "sample": {
      const full = axes
        .map((a) => a.steps.length)
        .reduce((acc, n) => acc * n, 1);
      const want = Math.min(
        Math.max(0, Math.trunc(spec.strategy.count)),
        full,
        MAX_MATERIALIZED,
      );
      const random = sampleRandom ?? new Rng(spec.strategy.seed);
      return sampleIndices(full, want, random).map((i) =>
        decodeCartesian(i, axes),
      );
    }
  }
}

/** Seed for run `runIndex`, repeat `repeat`, under the spec's seed policy. */
function seedFor(
  spec: MatrixSpec,
  runIndex: number,
  repeat: number,
  rng: Rng,
): number {
  const base = Math.max(0, Math.trunc(spec.seed.baseSeed)) % (MAX_SEED + 1);
  switch (spec.seed.mode) {
    case "fixed":
      // Repeats must still differ, or they would be byte-identical images.
      return (base + repeat) % (MAX_SEED + 1);
    case "increment":
      return (base + runIndex) % (MAX_SEED + 1);
    case "random":
      return rng.seed();
  }
}

/** `subject=cat · color#1=red · color#2=blue` — the label shown on the queued job. */
function labelFor(
  entries: readonly { name: string; option: MatrixOption }[],
): string {
  return entries
    .map(({ name, option }) => {
      const shown = option.label ?? option.value;
      const text = shown.trim().length > 0 ? shown.trim() : "∅";
      return `${name}=${text.length > 28 ? `${text.slice(0, 27)}…` : text}`;
    })
    .join(" · ");
}

export interface PlanValidation {
  errors: string[];
  warnings: string[];
}

/**
 * Everything wrong with the spec, BEFORE anything is generated. Errors block
 * the run; warnings inform it. A 90-minute batch that produces 120 images with
 * a literal "{{style}}" baked into every prompt is the failure mode this
 * function exists to prevent.
 */
export function validateSpec(spec: MatrixSpec): PlanValidation {
  const errors: string[] = [];
  const warnings: string[] = [];

  const pools = spec.pools ?? [];
  const declaredVars = new Set(spec.variables.map((v) => variableKey(v.name)));
  const declaredPools = new Set(pools.map((p) => variableKey(p.name)));
  const usedPlain = new Set<string>();
  const usedPoolKeys = new Set<string>();

  for (const field of spec.fields) {
    for (const tok of findTokens(field.text)) {
      if (tok.slot !== null && tok.poolName !== null) {
        usedPoolKeys.add(variableKey(tok.poolName));
      } else {
        usedPlain.add(tok.key);
      }
    }
    if (hasUnclosedToken(field.text)) {
      errors.push(
        `${field.label} has an unclosed "{{" — every variable must be written {{like_this}}.`,
      );
    }
  }

  // Bare {{color}} and pool {{color#1}} share a name space — colliding them
  // would make it ambiguous which options list applies.
  for (const key of usedPlain) {
    if (declaredPools.has(key) || usedPoolKeys.has(key)) {
      errors.push(
        `"${key}" is used both as {{${key}}} and as a pool ({{${key}#…}}). ` +
          `Pick one: a normal variable, or pool slots.`,
      );
    }
  }
  for (const v of spec.variables) {
    if (declaredPools.has(variableKey(v.name))) {
      errors.push(
        `"${v.name}" is both a variable and a pool — rename one of them.`,
      );
    }
  }

  for (const key of usedPlain) {
    if (!declaredVars.has(key)) {
      errors.push(
        `{{${key}}} appears in the prompt but has no options defined. Add at least one option or remove the token.`,
      );
    }
  }

  for (const key of usedPoolKeys) {
    if (!declaredPools.has(key)) {
      errors.push(
        `Pool {{${key}#…}} appears in the prompt but has no options defined. Add at least one option or remove the slots.`,
      );
    }
  }

  for (const v of spec.variables) {
    if (!v.enabled) continue;
    const opts = enabledOptions(v);
    if (opts.length === 0) {
      errors.push(
        `"${v.name}" has no enabled options — add one, disable the variable, or remove it.`,
      );
      continue;
    }
    if (v.binding.kind === "text" && !usedPlain.has(variableKey(v.name))) {
      warnings.push(
        `"${v.name}" has options but never appears in the prompt — it will not affect any generation.`,
      );
    }
    const values = opts.map((o) => o.value.trim());
    const dupes = values.filter((val, i) => values.indexOf(val) !== i);
    if (dupes.length > 0) {
      warnings.push(
        `"${v.name}" has duplicate options (${[...new Set(dupes)].join(", ")}) — they will generate identical runs.`,
      );
    }
  }

  for (const pool of pools) {
    if (!pool.enabled) continue;
    const opts = enabledOptions(pool);
    if (opts.length === 0) {
      errors.push(
        `Pool "${pool.name}" has no enabled options — add one, disable the pool, or remove it.`,
      );
      continue;
    }
    if (!usedPoolKeys.has(variableKey(pool.name))) {
      warnings.push(
        `Pool "${pool.name}" has options but no {{${pool.name}#…}} slots in the prompt — it will not affect any generation.`,
      );
    }
    const values = opts.map((o) => o.value.trim());
    const dupes = values.filter((val, i) => values.indexOf(val) !== i);
    if (dupes.length > 0) {
      warnings.push(
        `Pool "${pool.name}" has duplicate options (${[...new Set(dupes)].join(", ")}) — they will generate identical runs.`,
      );
    }
  }

  if (spec.seed.repeats < 1) {
    errors.push("Runs per combination must be at least 1.");
  }
  if (spec.strategy.kind === "sample" && spec.strategy.count < 1) {
    errors.push("Sample size must be at least 1.");
  }

  return { errors, warnings };
}

/** Exact number of runs the spec will enqueue — cheap, never materializes. */
export function countPlan(spec: MatrixSpec): number {
  const { axes } = buildAxes(spec);
  const repeats = Math.max(1, Math.trunc(spec.seed.repeats));
  return countCombinations(axes, spec) * repeats;
}

/**
 * Expand the spec into concrete runs.
 *
 * Emission order: combination-major, repeats inner — all N seeds of one
 * combination land together, so the queue reads as a sequence of comparable
 * groups rather than an interleaved smear.
 */
export function expandMatrix(
  spec: MatrixSpec,
  sampleRandom?: Pick<RandomSource, "int">,
): MatrixPlan {
  const { errors, warnings: specWarnings } = validateSpec(spec);
  const { axes, warnings: axisWarnings } = buildAxes(spec);
  const warnings = [...specWarnings, ...axisWarnings];

  const repeats = Math.max(1, Math.trunc(spec.seed.repeats));
  const total = countCombinations(axes, spec) * repeats;

  if (errors.length > 0) {
    return { combinations: [], total, truncated: false, errors, warnings };
  }

  const rng = new Rng(spec.seed.rngSeed);
  const rows = stepIndicesForPlan(axes, spec, sampleRandom);
  const combinations: MatrixCombination[] = [];

  outer: for (const [rowIdx, picks] of rows.entries()) {
    const labelEntries: { name: string; option: MatrixOption }[] = [];
    const values: Record<string, string> = {};
    const optionIds: Record<string, string> = {};
    const substitutions = new Map<string, string>();

    axes.forEach((axis, axisIdx) => {
      const step = axis.steps[picks[axisIdx] ?? 0];
      if (step === undefined) return;

      if (axis.pool !== null) {
        const pool = axis.pool;
        axis.slots.forEach((slot, memberIdx) => {
          const opt = step[memberIdx];
          if (opt === undefined) return;
          const name = poolSlotName(pool.name, slot);
          labelEntries.push({ name, option: opt });
          values[name] = opt.value;
          optionIds[name] = opt.id;
          substitutions.set(variableKey(name), opt.value);
        });
        return;
      }

      axis.variables.forEach((v, memberIdx) => {
        const opt = step[memberIdx];
        if (opt === undefined) return;
        labelEntries.push({ name: v.name, option: opt });
        values[v.name] = opt.value;
        optionIds[v.name] = opt.id;
        substitutions.set(variableKey(v.name), opt.value);
      });
    });

    const label = labelFor(labelEntries);

    const rendered: Record<string, string> = {};
    for (const field of spec.fields) {
      const { text } = renderTemplate(field.text, substitutions);
      rendered[field.id] = tidyPrompt(text);
    }

    for (let r = 0; r < repeats; r += 1) {
      if (combinations.length >= MAX_MATERIALIZED) break outer;
      const index = rowIdx * repeats + r;
      combinations.push({
        index,
        values,
        optionIds,
        seed: seedFor(spec, index, r, rng),
        repeat: r,
        label: repeats > 1 ? `${label} · run ${r + 1}/${repeats}` : label,
        rendered,
      });
    }
  }

  return {
    combinations,
    total,
    truncated: combinations.length < total,
    errors,
    warnings,
  };
}

/**
 * Create the concrete work for ONE new batch attempt.
 *
 * `expandMatrix` remains the deterministic analyser used for counts,
 * validation, imports, and saved-template inspection. A submission must never
 * use that reusable analysis result directly: it needs fresh entropy each time
 * so a stopped batch cannot begin from the same combinations or noise again.
 *
 * We shuffle whole combinations rather than each axis independently. That
 * preserves linked-variable and pool relationships while making every position
 * in the queue an unbiased member of the selected combination set.
 */
export function createBatchSnapshot(
  spec: MatrixSpec,
  random: RandomSource = secureRandom,
): MatrixPlan {
  // Sampled strategies draw their subset directly from this attempt's random
  // source. Production uses secureRandom.int (rejection sampled), so selection
  // is uniform without reducing the attempt to a deterministic 32-bit stream.
  const analyzed = expandMatrix(
    spec,
    spec.strategy.kind === "sample" ? random : undefined,
  );
  const combinations = shuffled(analyzed.combinations, random).map(
    (combination, index) => ({
      ...combination,
      // A batch attempt owns fresh, independent diffusion noise for every run.
      // Saved fixed/increment/random policies are legacy comparison metadata;
      // they must not leak into new randomized executions.
      index,
      seed: random.seed(),
      values: { ...combination.values },
      optionIds: { ...combination.optionIds },
      rendered: { ...combination.rendered },
    }),
  );
  return { ...analyzed, combinations };
}
