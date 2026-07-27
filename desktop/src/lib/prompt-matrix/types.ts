/**
 * prompt-matrix — the media-agnostic variable/combination engine.
 *
 * A "matrix" is a template (any number of text fields) containing {{variable}}
 * tokens, a set of variables each holding N options, and a strategy describing
 * WHICH combinations of those options to actually run.
 *
 * Nothing in this module knows about images, video, or any specific generator.
 * A consumer supplies a MatrixTarget (see ./targets) that declares which
 * generation parameters can be swept and how to turn one combination into a
 * concrete job payload. Images are the first target; video and text prompts
 * plug in the same way without touching this core.
 */

/** One choice for a variable — the value substituted into the template. */
export interface MatrixOption {
  id: string;
  /** The literal text substituted for {{name}} (or the raw param value). */
  value: string;
  /** Optional short display label; falls back to `value`. */
  label?: string;
  /** Disabled options stay in the UI but never enter the plan. */
  enabled: boolean;
}

/**
 * Where a variable's value lands.
 *
 * - `text`   — substituted into every template field containing {{name}}.
 *              Discovered automatically by parsing the prompt.
 * - `param`  — overrides a generation parameter (steps, guidance, seed, model,
 *              LoRA scale, …) declared by the target's axes. Added explicitly
 *              by the user; needs no token in the prompt. If a {{name}} token
 *              DOES exist for it, the value is substituted there as well.
 */
export type VariableBinding =
  | { kind: "text" }
  | { kind: "param"; axisId: string };

export interface MatrixVariable {
  id: string;
  /** Token name as written in the template, e.g. `subject` for {{subject}}. */
  name: string;
  binding: VariableBinding;
  options: MatrixOption[];
  /**
   * Option used as the fixed value under the `baseline` strategy (the value
   * held constant while OTHER variables sweep). Null → the first enabled option.
   */
  baselineOptionId: string | null;
  /**
   * Variables sharing a link group step together (zipped 1:1) instead of
   * multiplying: {{style}} + {{lora}} paired, 3 styles + 3 loras = 3 runs, not 9.
   */
  linkGroup: string | null;
  enabled: boolean;
}

/**
 * How the option lists are combined.
 *
 * - `cartesian` — every combination of every variable. Each submitted batch
 *                 shuffles this complete set before execution.
 * - `baseline`  — hold every variable at its baseline option and change ONE
 *                 at a time. 1 + Σ(nᵢ − 1) runs instead of Πnᵢ — the escape
 *                 hatch when the product explodes (3×10 → 12 runs, not 30).
 * - `sample`    — a fresh random sample of `count` distinct combinations drawn
 *                 from the full cartesian product for each submitted batch.
 * - `zip`       — ALL variables step in lockstep (like one big link group).
 *                 Run count = the shortest option list.
 */
export type MatrixStrategy =
  | { kind: "cartesian" }
  | { kind: "baseline" }
  | { kind: "sample"; count: number; seed: number }
  | { kind: "zip" };

export type StrategyKind = MatrixStrategy["kind"];

/**
 * Legacy persisted comparison metadata. New submitted batches always receive
 * fresh random seeds; only `repeats` affects execution. The remaining fields
 * stay serializable so saved/imported matrices from older releases remain valid.
 */
export type SeedMode = "fixed" | "random" | "increment";

export interface SeedPolicy {
  mode: SeedMode;
  /** Legacy base seed retained for saved-template compatibility. */
  baseSeed: number;
  /** Runs per combination (each gets its own seed). */
  repeats: number;
  /** Legacy planner RNG seed retained for saved-template compatibility. */
  rngSeed: number;
}

/** A template field that may contain {{tokens}} (prompt, negative prompt, …). */
export interface TemplateField {
  id: string;
  label: string;
  text: string;
}

/**
 * A shared option list referenced by `{{name#slot}}` tokens.
 *
 * Declared once; every distinct slot is an independent draw from it. Repeating
 * the same slot token reuses that draw, while different slots draw with
 * replacement and may therefore choose the same value. Slots themselves are
 * NOT stored here — they are derived from the template at sync/expand time.
 */
export interface MatrixPool {
  id: string;
  /** Pool name as written in the template, e.g. `color` for {{color#1}}. */
  name: string;
  options: MatrixOption[];
  /**
   * Option used as the fixed value under the `baseline` strategy.
   * Null → the first enabled option.
   */
  baselineOptionId: string | null;
  enabled: boolean;
}

/** The complete, serializable description of a matrix run. */
export interface MatrixSpec {
  fields: TemplateField[];
  variables: MatrixVariable[];
  /** Shared option pools for {{name#slot}} tokens. Default []. */
  pools: MatrixPool[];
  strategy: MatrixStrategy;
  seed: SeedPolicy;
}

/** One planned run. */
export interface MatrixCombination {
  index: number;
  /** variable name → chosen option value (what gets substituted). */
  values: Record<string, string>;
  /** variable name → chosen option id (for UI highlighting / dedupe). */
  optionIds: Record<string, string>;
  /** Concrete seed for this run. */
  seed: number;
  /** Which repeat of this combination (0-based). */
  repeat: number;
  /** Human-readable summary, e.g. `subject=cat · style=noir`. */
  label: string;
  /** Rendered template fields with every {{token}} substituted. */
  rendered: Record<string, string>;
}

export interface MatrixPlan {
  combinations: MatrixCombination[];
  /** Exact run count — computed without materializing, so it is always right. */
  total: number;
  /** True when `combinations` was capped and holds fewer than `total`. */
  truncated: boolean;
  errors: string[];
  warnings: string[];
}

/**
 * Materialization cap. `total` is always exact; only the concrete list of
 * combinations is capped, so the UI can preview a 1,000,000-run product
 * without allocating it.
 */
export const MAX_MATERIALIZED = 5000;

/** Enqueueing more than this many jobs at once requires explicit confirmation. */
export const LARGE_BATCH_THRESHOLD = 100;

/** Hard ceiling on a single batch — refuses to enqueue beyond this. */
export const MAX_BATCH_SIZE = 2000;
