/**
 * The combination engine: MatrixSpec → the ordered list of runs to enqueue.
 *
 * Two invariants worth stating up front, because everything else follows:
 *
 * 1. `total` is computed ARITHMETICALLY, never by materializing. A 6-variable
 *    cartesian product can be millions of runs; the UI must be able to show
 *    that number (and refuse it) without allocating it.
 *
 * 2. Emission order is meaningful, not incidental. Variable order = loop
 *    nesting: variable[0] is the OUTERMOST loop (changes slowest — the one
 *    that stays "frozen" while everything else sweeps), the last variable is
 *    the innermost (changes fastest). That is the whole answer to "do you
 *    freeze one variable and sweep the others?" — you drag it to the top.
 *    Results therefore arrive grouped the way a human wants to compare them.
 */

import {
  MAX_MATERIALIZED,
  type MatrixCombination,
  type MatrixOption,
  type MatrixPlan,
  type MatrixSpec,
  type MatrixVariable,
} from "./types";
import { renderTemplate, tidyPrompt, variableKey } from "./parse";
import { MAX_SEED, Rng, sampleIndices } from "./rng";

/**
 * An axis is ONE independent dimension of the product. Usually one variable;
 * several when they are link-grouped (zipped), in which case they advance
 * together and contribute a single dimension rather than multiplying.
 */
interface Axis {
  variables: MatrixVariable[];
  /** steps[i] = the option each member variable takes at position i. */
  steps: MatrixOption[][];
  /** Index of the baseline step (used by the `baseline` strategy). */
  baselineStep: number;
}

const enabledOptions = (v: MatrixVariable): MatrixOption[] =>
  v.options.filter((o) => o.enabled);

const activeVariables = (spec: MatrixSpec): MatrixVariable[] =>
  spec.variables.filter((v) => v.enabled && enabledOptions(v).length > 0);

/**
 * Build the axes, preserving user order. Linked variables collapse into one
 * axis at the position of the FIRST member, so dragging any member of a link
 * group moves the group.
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
        steps: opts.map((o) => [o]),
        baselineStep,
      });
      continue;
    }
    const existing = groupAxis.get(v.linkGroup);
    if (existing === undefined) {
      const axis: Axis = {
        variables: [v],
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
function stepIndicesForPlan(axes: Axis[], spec: MatrixSpec): number[][] {
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
      const rng = new Rng(spec.strategy.seed);
      return sampleIndices(full, want, rng).map((i) => decodeCartesian(i, axes));
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

/** `subject=cat · style=noir` — the label shown on the queued job. */
function labelFor(
  variables: readonly MatrixVariable[],
  options: readonly MatrixOption[],
): string {
  return variables
    .map((v, i) => {
      const opt = options[i];
      if (opt === undefined) return v.name;
      const shown = opt.label ?? opt.value;
      const text = shown.trim().length > 0 ? shown.trim() : "∅";
      return `${v.name}=${text.length > 28 ? `${text.slice(0, 27)}…` : text}`;
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

  const declared = new Set(spec.variables.map((v) => variableKey(v.name)));
  const usedInText = new Set<string>();

  for (const field of spec.fields) {
    for (const tok of field.text.matchAll(/\{\{\s*([A-Za-z0-9_\- ]+?)\s*\}\}/g)) {
      usedInText.add(variableKey(tok[1] ?? ""));
    }
    if (/\{\{(?![^{}]*\}\})/.test(field.text)) {
      errors.push(
        `${field.label} has an unclosed "{{" — every variable must be written {{like_this}}.`,
      );
    }
  }

  for (const key of usedInText) {
    if (!declared.has(key)) {
      errors.push(
        `{{${key}}} appears in the prompt but has no options defined. Add at least one option or remove the token.`,
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
    if (v.binding.kind === "text" && !usedInText.has(variableKey(v.name))) {
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
export function expandMatrix(spec: MatrixSpec): MatrixPlan {
  const { errors, warnings: specWarnings } = validateSpec(spec);
  const { axes, warnings: axisWarnings } = buildAxes(spec);
  const warnings = [...specWarnings, ...axisWarnings];

  const repeats = Math.max(1, Math.trunc(spec.seed.repeats));
  const total = countCombinations(axes, spec) * repeats;

  if (errors.length > 0) {
    return { combinations: [], total, truncated: false, errors, warnings };
  }

  const rng = new Rng(spec.seed.rngSeed);
  const rows = stepIndicesForPlan(axes, spec);
  const combinations: MatrixCombination[] = [];

  outer: for (const [rowIdx, picks] of rows.entries()) {
    const vars: MatrixVariable[] = [];
    const opts: MatrixOption[] = [];
    const values: Record<string, string> = {};
    const optionIds: Record<string, string> = {};
    const substitutions = new Map<string, string>();

    axes.forEach((axis, axisIdx) => {
      const step = axis.steps[picks[axisIdx] ?? 0];
      if (step === undefined) return;
      axis.variables.forEach((v, memberIdx) => {
        const opt = step[memberIdx];
        if (opt === undefined) return;
        vars.push(v);
        opts.push(opt);
        values[v.name] = opt.value;
        optionIds[v.name] = opt.id;
        substitutions.set(variableKey(v.name), opt.value);
      });
    });

    const label = labelFor(vars, opts);

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
