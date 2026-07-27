/**
 * MatrixTarget — the seam between the media-agnostic matrix engine and a
 * concrete generator.
 *
 * The engine (parse/expand) knows only about variables, options, strategies
 * and combinations. It has no idea what a "model" or a "denoising step" is.
 * A target supplies the other half:
 *
 *   • `fields`   — which text fields can carry {{tokens}} (prompt, negative…)
 *   • `axes`     — which generation PARAMETERS can be swept as a variable
 *                  (steps, guidance, seed, model, LoRA, …), incl. how to parse
 *                  and apply a value
 *   • `applyField` / `applySeed` — how a rendered value lands on a job payload
 *
 * Images are the first target. Video is the same shape with different axes
 * (frames, fps) and text-prompt batching is the same shape with no axes at
 * all. None of them require a change to the engine.
 */

import type {
  MatrixCombination,
  MatrixPool,
  MatrixVariable,
  TemplateField,
} from "./types";
import type { PoolRef } from "./parse";
import { variableKey } from "./parse";

export type ParseResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

/** One sweepable generation parameter. */
export interface ParamAxis<TJob> {
  id: string;
  label: string;
  /** Grouping for the axis picker menu, e.g. "Sampling", "Model". */
  group: string;
  hint?: string;
  /** Example values offered as one-click starters in the option editor. */
  suggestions?: string[];
  /** Validate + coerce the user's raw text. Errors surface before any run. */
  parse: (raw: string) => ParseResult;
  /** Apply a parsed value to the job payload. Must not mutate `job`. */
  apply: (job: TJob, value: unknown) => TJob;
}

export interface MatrixTarget<TJob> {
  id: string;
  label: string;
  /** Text fields that may contain {{tokens}}, in display order. */
  fields: TemplateField[];
  /** Statically-known sweepable parameters. */
  axes: ParamAxis<TJob>[];
  /**
   * Resolve an axis id, including dynamic ones (e.g. `extra:eta` for an
   * arbitrary advanced pipeline kwarg). Returns null for unknown ids.
   */
  resolveAxis: (axisId: string) => ParamAxis<TJob> | null;
  /** Put a rendered template field onto the job (e.g. fieldId "prompt"). */
  applyField: (job: TJob, fieldId: string, text: string) => TJob;
  /** Put the combination's concrete seed onto the job. */
  applySeed: (job: TJob, seed: number) => TJob;
}

export interface BuiltJob<TJob> {
  job: TJob;
  /** Index within the batch — also the enqueue order. */
  index: number;
  /** `subject=cat · style=noir` — carried onto the queued job for the UI. */
  label: string;
  /** variable name → chosen value, stored on the job for filtering/reuse. */
  values: Record<string, string>;
  seed: number;
}

export interface BuildJobsResult<TJob> {
  jobs: BuiltJob<TJob>[];
  /** Anything that made a run impossible — blocks the whole batch. */
  errors: string[];
}

/**
 * Turn planned combinations into concrete job payloads.
 *
 * A parse failure on ANY run fails the WHOLE batch rather than silently
 * skipping that run: a 120-image batch that quietly became 118 because two
 * option values were typos is worse than one that refuses to start and says
 * which values are bad.
 */
export function buildJobs<TJob>(
  target: MatrixTarget<TJob>,
  base: TJob,
  combinations: readonly MatrixCombination[],
  variables: readonly MatrixVariable[],
): BuildJobsResult<TJob> {
  const errors: string[] = [];
  const jobs: BuiltJob<TJob>[] = [];

  const paramVars = variables.filter(
    (v) => v.enabled && v.binding.kind === "param",
  );

  for (const combo of combinations) {
    let job = base;

    // 1. Rendered text fields (prompt, negative prompt, …).
    for (const [fieldId, text] of Object.entries(combo.rendered)) {
      job = target.applyField(job, fieldId, text);
    }

    // 2. Parameter axes.
    for (const v of paramVars) {
      if (v.binding.kind !== "param") continue;
      const raw = combo.values[v.name];
      if (raw === undefined) continue;
      const axis = target.resolveAxis(v.binding.axisId);
      if (axis === null) {
        errors.push(
          `"${v.name}" is bound to an unknown parameter (${v.binding.axisId}).`,
        );
        continue;
      }
      const parsed = axis.parse(raw);
      if (!parsed.ok) {
        errors.push(`${axis.label} — "${raw}": ${parsed.error}`);
        continue;
      }
      job = axis.apply(job, parsed.value);
    }

    // 3. A batch snapshot owns its noise. Apply its fresh seed last so no
    //    parameter axis (including an old imported `seed` axis) can silently
    //    make consecutive generations deterministic again.
    job = target.applySeed(job, combo.seed);

    jobs.push({
      job,
      index: combo.index,
      label: combo.label,
      values: combo.values,
      seed: combo.seed,
    });
  }

  return { jobs, errors: [...new Set(errors)] };
}

/**
 * Sync the variable list against the tokens currently in the template.
 *
 * Called on every prompt keystroke, so it must be pure and cheap:
 *  • a NEW token gets a variable with one empty starter option
 *  • a variable whose token disappeared is dropped — UNLESS it is param-bound
 *    (those live independently of the prompt text) or the user typed options
 *    into it, in which case it is kept and flagged unused by validateSpec.
 *    Deleting a list of ten hand-typed options because the user momentarily
 *    backspaced through a token would be unforgivable.
 */
export function syncVariablesWithTokens(
  variables: readonly MatrixVariable[],
  tokenNames: readonly string[],
  makeId: () => string,
): MatrixVariable[] {
  const tokens = new Map(tokenNames.map((n) => [variableKey(n), n]));
  const kept: MatrixVariable[] = [];
  const seen = new Set<string>();

  for (const v of variables) {
    const key = variableKey(v.name);
    seen.add(key);
    const stillPresent = tokens.has(key);
    const isParam = v.binding.kind === "param";
    const hasContent = v.options.some((o) => o.value.trim().length > 0);
    if (stillPresent || isParam || hasContent) kept.push(v);
  }

  // New tokens append in the order they appear in the prompt. Existing
  // variables KEEP their current position — the list order is the loop nesting
  // the user set by dragging, and a keystroke in the prompt must never
  // reshuffle it.
  for (const [key, name] of tokens) {
    if (seen.has(key)) continue;
    kept.push({
      id: makeId(),
      name,
      binding: { kind: "text" },
      options: [{ id: makeId(), value: "", enabled: true }],
      baselineOptionId: null,
      linkGroup: null,
      enabled: true,
    });
  }

  return kept;
}

/**
 * Sync the pool list against `{{name#slot}}` tokens in the template.
 *
 * Same contract as syncVariablesWithTokens: pure, cheap, never re-sorts, never
 * discards a pool the user has typed options into.
 */
export function syncPoolsWithTokens(
  pools: readonly MatrixPool[],
  poolRefs: readonly PoolRef[],
  makeId: () => string,
): MatrixPool[] {
  const refs = new Map(poolRefs.map((r) => [r.key, r]));
  const kept: MatrixPool[] = [];
  const seen = new Set<string>();

  for (const p of pools) {
    const key = variableKey(p.name);
    seen.add(key);
    const stillPresent = refs.has(key);
    const hasContent = p.options.some((o) => o.value.trim().length > 0);
    if (stillPresent || hasContent) kept.push(p);
  }

  for (const [key, ref] of refs) {
    if (seen.has(key)) continue;
    kept.push({
      id: makeId(),
      name: ref.name,
      options: [{ id: makeId(), value: "", enabled: true }],
      baselineOptionId: null,
      enabled: true,
    });
  }

  return kept;
}
