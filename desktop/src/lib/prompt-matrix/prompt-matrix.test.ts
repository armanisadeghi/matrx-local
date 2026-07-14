import { describe, expect, it } from "vitest";

import { countPlan, expandMatrix, validateSpec } from "./expand";
import {
  extractPoolRefs,
  extractVariableNames,
  findTokens,
  renderTemplate,
  tidyPrompt,
  variableKey,
} from "./parse";
import { Rng, sampleIndices } from "./rng";
import {
  buildJobs,
  syncPoolsWithTokens,
  syncVariablesWithTokens,
} from "./targets";
import { createImageTarget } from "./imageTarget";
import {
  matrixExportFilename,
  parseMatrixImport,
  serializeMatrixExport,
} from "./io";
import { insertLibraryEntryInSpec, renameVariableInSpec } from "./edit";
import type { ImageGenModelInfo } from "@/lib/api";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";
import type { LibraryEntry } from "./library";
import type {
  MatrixOption,
  MatrixPool,
  MatrixSpec,
  MatrixStrategy,
  MatrixVariable,
  SeedPolicy,
} from "./types";

// ── builders ────────────────────────────────────────────────────────────────

let seq = 0;
const nextId = (): string => `id-${(seq += 1)}`;

function opts(...values: string[]): MatrixOption[] {
  return values.map((value) => ({ id: nextId(), value, enabled: true }));
}

function variable(
  name: string,
  values: string[],
  over: Partial<MatrixVariable> = {},
): MatrixVariable {
  return {
    id: nextId(),
    name,
    binding: { kind: "text" },
    options: opts(...values),
    baselineOptionId: null,
    linkGroup: null,
    enabled: true,
    ...over,
  };
}

function pool(
  name: string,
  values: string[],
  over: Partial<MatrixPool> = {},
): MatrixPool {
  return {
    id: nextId(),
    name,
    options: opts(...values),
    assign: "rotate",
    baselineOptionId: null,
    enabled: true,
    ...over,
  };
}

const SEED: SeedPolicy = {
  mode: "fixed",
  baseSeed: 1234,
  repeats: 1,
  rngSeed: 99,
};

function spec(
  prompt: string,
  variables: MatrixVariable[],
  strategy: MatrixStrategy = { kind: "cartesian" },
  seed: Partial<SeedPolicy> = {},
  pools: MatrixPool[] = [],
): MatrixSpec {
  return {
    fields: [{ id: "prompt", label: "Prompt", text: prompt }],
    variables,
    pools,
    strategy,
    seed: { ...SEED, ...seed },
  };
}

// ── parsing ─────────────────────────────────────────────────────────────────

describe("parse", () => {
  it("finds tokens and folds case for identity", () => {
    const toks = findTokens("a {{Subject}} in {{ style }} and {{subject}}");
    expect(toks.map((t) => t.name)).toEqual(["Subject", "style", "subject"]);
    expect(toks.map((t) => t.key)).toEqual(["subject", "style", "subject"]);
  });

  it("collapses internal whitespace in names", () => {
    expect(variableKey("  Art   Style ")).toBe("art style");
  });

  it("leaves unknown tokens verbatim and reports them", () => {
    const { text, unresolved } = renderTemplate(
      "a {{subject}} in {{style}}",
      new Map([["subject", "cat"]]),
    );
    expect(text).toBe("a cat in {{style}}");
    expect(unresolved).toEqual(["style"]);
  });

  it("tidies the punctuation an empty option leaves behind", () => {
    const { text } = renderTemplate(
      "a cat, {{style}}, at night",
      new Map([["style", ""]]),
    );
    expect(text).toBe("a cat, , at night");
    expect(tidyPrompt(text)).toBe("a cat, at night");
  });

  it("does not mangle a normal prompt", () => {
    const p = "a cat, film noir, at night";
    expect(tidyPrompt(p)).toBe(p);
  });

  it("parses pool slots and keeps them out of plain variable names", () => {
    const text =
      "man in {{color#1}} shirt, woman in {{color#2}} skirt, {{pose}}";
    const toks = findTokens(text);
    expect(toks.map((t) => t.name)).toEqual(["color#1", "color#2", "pose"]);
    expect(toks[0]?.poolName).toBe("color");
    expect(toks[0]?.slot).toBe("1");
    expect(extractVariableNames([text])).toEqual(["pose"]);
    const refs = extractPoolRefs([text]);
    expect(refs).toHaveLength(1);
    expect(refs[0]?.name).toBe("color");
    expect(refs[0]?.slots).toEqual(["1", "2"]);
  });

  it("substitutes pool slot keys", () => {
    const { text, unresolved } = renderTemplate(
      "{{color#1}} and {{color#2}}",
      new Map([
        ["color#1", "red"],
        ["color#2", "blue"],
      ]),
    );
    expect(text).toBe("red and blue");
    expect(unresolved).toEqual([]);
  });
});

// ── counting + strategies ───────────────────────────────────────────────────

describe("strategies", () => {
  const s = variable("subject", ["cat", "dog", "fox"]); // 3
  const t = variable("style", ["noir", "anime", "oil", "3d", "pixel"]); // 5

  it("cartesian multiplies", () => {
    const sp = spec("a {{subject}} in {{style}}", [s, t]);
    expect(countPlan(sp)).toBe(15);
    expect(expandMatrix(sp).combinations).toHaveLength(15);
  });

  it("variable order is loop nesting — the first is frozen longest", () => {
    const plan = expandMatrix(spec("{{subject}} {{style}}", [s, t]));
    // subject (outer) holds for 5 runs while style (inner) sweeps.
    expect(
      plan.combinations.slice(0, 5).map((c) => c.values["subject"]),
    ).toEqual(["cat", "cat", "cat", "cat", "cat"]);
    expect(plan.combinations.slice(0, 5).map((c) => c.values["style"])).toEqual(
      ["noir", "anime", "oil", "3d", "pixel"],
    );
    expect(plan.combinations[5]?.values["subject"]).toBe("dog");
  });

  it("reordering the variables swaps which one is frozen", () => {
    const plan = expandMatrix(spec("{{subject}} {{style}}", [t, s]));
    expect(plan.combinations.slice(0, 3).map((c) => c.values["style"])).toEqual(
      ["noir", "noir", "noir"],
    );
    expect(
      plan.combinations.slice(0, 3).map((c) => c.values["subject"]),
    ).toEqual(["cat", "dog", "fox"]);
  });

  it("baseline changes one variable at a time: 1 + Σ(n−1), not Πn", () => {
    const sp = spec("{{subject}} {{style}}", [s, t], { kind: "baseline" });
    expect(countPlan(sp)).toBe(1 + 2 + 4); // 7, vs 15 for cartesian
    const plan = expandMatrix(sp);
    expect(plan.combinations).toHaveLength(7);
    // The baseline run itself, then only ONE variable ever differs from it.
    const base = plan.combinations[0] as (typeof plan.combinations)[number];
    expect(base.values).toEqual({ subject: "cat", style: "noir" });
    for (const c of plan.combinations.slice(1)) {
      const diffs = (["subject", "style"] as const).filter(
        (k) => c.values[k] !== base.values[k],
      );
      expect(diffs).toHaveLength(1);
    }
  });

  it("baseline honours an explicit baseline option", () => {
    const withBaseline = variable("subject", ["cat", "dog", "fox"]);
    withBaseline.baselineOptionId = withBaseline.options[2]?.id ?? null;
    const plan = expandMatrix(
      spec("{{subject}} {{style}}", [withBaseline, t], { kind: "baseline" }),
    );
    expect(plan.combinations[0]?.values["subject"]).toBe("fox");
  });

  it("zip steps everything together — shortest list wins", () => {
    const sp = spec("{{subject}} {{style}}", [s, t], { kind: "zip" });
    expect(countPlan(sp)).toBe(3);
    const plan = expandMatrix(sp);
    expect(plan.combinations.map((c) => c.values["subject"])).toEqual([
      "cat",
      "dog",
      "fox",
    ]);
    expect(plan.combinations.map((c) => c.values["style"])).toEqual([
      "noir",
      "anime",
      "oil",
    ]);
  });

  it("linked variables pair 1:1 instead of multiplying", () => {
    const a = variable("style", ["noir", "anime", "oil"], { linkGroup: "g" });
    const b = variable("lora", ["l1", "l2", "l3"], { linkGroup: "g" });
    const sp = spec("{{subject}} {{style}} {{lora}}", [s, a, b]);
    expect(countPlan(sp)).toBe(9); // 3 subjects × 3 linked pairs — not 27
    const plan = expandMatrix(sp);
    for (const c of plan.combinations) {
      const i = ["noir", "anime", "oil"].indexOf(c.values["style"] as string);
      expect(c.values["lora"]).toBe(["l1", "l2", "l3"][i]);
    }
  });

  it("warns (never silently truncates) when linked lists differ in length", () => {
    const a = variable("style", ["noir", "anime", "oil"], { linkGroup: "g" });
    const b = variable("lora", ["l1", "l2"], { linkGroup: "g" });
    const plan = expandMatrix(spec("{{style}} {{lora}}", [a, b]));
    expect(plan.total).toBe(2);
    expect(plan.warnings.join(" ")).toContain("different option counts");
  });

  it("sample is bounded, distinct, and reproducible for a given seed", () => {
    const sp = spec("{{subject}} {{style}}", [s, t], {
      kind: "sample",
      count: 6,
      seed: 42,
    });
    expect(countPlan(sp)).toBe(6);
    const a = expandMatrix(sp).combinations.map((c) => c.label);
    const b = expandMatrix(sp).combinations.map((c) => c.label);
    expect(a).toEqual(b);
    expect(new Set(a).size).toBe(6);

    const other = expandMatrix(
      spec("{{subject}} {{style}}", [s, t], {
        kind: "sample",
        count: 6,
        seed: 7,
      }),
    ).combinations.map((c) => c.label);
    expect(other).not.toEqual(a);
  });

  it("sample clamps to the population when asked for more than exists", () => {
    const sp = spec("{{subject}}", [s], { kind: "sample", count: 99, seed: 1 });
    expect(countPlan(sp)).toBe(3);
    expect(expandMatrix(sp).combinations).toHaveLength(3);
  });

  it("counts a template with no variables as a single run", () => {
    expect(countPlan(spec("a plain cat", []))).toBe(1);
  });

  it("disabled variables and options drop out of the product", () => {
    const partial = variable("style", ["noir", "anime", "oil"]);
    const second = partial.options[1];
    if (second) second.enabled = false;
    expect(countPlan(spec("{{subject}} {{style}}", [s, partial]))).toBe(6);

    const off = variable("mood", ["a", "b"], { enabled: false });
    expect(countPlan(spec("{{subject}} {{mood}}", [s, off]))).toBe(3);
  });

  it("computes huge totals without materializing them", () => {
    const many = Array.from({ length: 6 }, (_, i) =>
      variable(
        `v${i}`,
        Array.from({ length: 10 }, (_, j) => `o${j}`),
      ),
    );
    const sp = spec(many.map((v) => `{{${v.name}}}`).join(" "), many);
    expect(countPlan(sp)).toBe(1_000_000);
    const plan = expandMatrix(sp);
    expect(plan.total).toBe(1_000_000);
    expect(plan.truncated).toBe(true);
    expect(plan.combinations.length).toBeLessThanOrEqual(5000);
  });
});

// ── seeds ───────────────────────────────────────────────────────────────────

describe("seed policy", () => {
  const s = variable("subject", ["cat", "dog"]);

  it("fixed keeps the seed constant so the variable is the only difference", () => {
    const plan = expandMatrix(spec("{{subject}}", [s], { kind: "cartesian" }));
    expect(plan.combinations.map((c) => c.seed)).toEqual([1234, 1234]);
  });

  it("repeats multiply the run count and step the seed", () => {
    const sp = spec("{{subject}}", [s], { kind: "cartesian" }, { repeats: 3 });
    expect(countPlan(sp)).toBe(6);
    const plan = expandMatrix(sp);
    // Combination-major: all 3 seeds of "cat" land before "dog" starts.
    expect(plan.combinations.map((c) => c.values["subject"])).toEqual([
      "cat",
      "cat",
      "cat",
      "dog",
      "dog",
      "dog",
    ]);
    expect(plan.combinations.map((c) => c.seed)).toEqual([
      1234, 1235, 1236, 1234, 1235, 1236,
    ]);
  });

  it("increment walks the seed across runs", () => {
    const plan = expandMatrix(
      spec("{{subject}}", [s], { kind: "cartesian" }, { mode: "increment" }),
    );
    expect(plan.combinations.map((c) => c.seed)).toEqual([1234, 1235]);
  });

  it("random seeds are drawn from the plan RNG, so a plan replays identically", () => {
    const sp = spec(
      "{{subject}}",
      [s],
      { kind: "cartesian" },
      { mode: "random" },
    );
    const a = expandMatrix(sp).combinations.map((c) => c.seed);
    const b = expandMatrix(sp).combinations.map((c) => c.seed);
    expect(a).toEqual(b);
    expect(a[0]).not.toBe(a[1]);
    for (const seed of a) {
      expect(seed).toBeGreaterThanOrEqual(0);
      expect(seed).toBeLessThanOrEqual(4294967295);
    }
  });
});

// ── validation ──────────────────────────────────────────────────────────────

describe("validation", () => {
  it("blocks a token that has no variable — never generates a literal {{token}}", () => {
    const { errors } = validateSpec(spec("a {{subject}}", []));
    expect(errors.join(" ")).toContain("{{subject}}");
    expect(expandMatrix(spec("a {{subject}}", [])).combinations).toHaveLength(
      0,
    );
  });

  it("blocks an unclosed brace", () => {
    const { errors } = validateSpec(spec("a {{subject", []));
    expect(errors.join(" ")).toContain("unclosed");
  });

  it("blocks a variable with no enabled options", () => {
    const empty = variable("style", ["noir"]);
    const only = empty.options[0];
    if (only) only.enabled = false;
    const { errors } = validateSpec(spec("{{style}}", [empty]));
    expect(errors.join(" ")).toContain("no enabled options");
  });

  it("warns about an unused variable and duplicate options", () => {
    const unused = variable("mood", ["calm", "calm"]);
    const { warnings } = validateSpec(spec("a cat", [unused]));
    expect(warnings.join(" ")).toContain("never appears in the prompt");
    expect(warnings.join(" ")).toContain("duplicate options");
  });

  it("blocks colliding bare {{color}} with pool {{color#1}}", () => {
    const colorPool = pool("color", ["red", "blue"]);
    const { errors } = validateSpec(
      spec("{{color}} and {{color#1}}", [], { kind: "cartesian" }, {}, [
        colorPool,
      ]),
    );
    expect(errors.join(" ")).toContain("both as");
  });
});

// ── pools ───────────────────────────────────────────────────────────────────

describe("pools", () => {
  const colors = pool("color", ["red", "blue", "green"]);
  const pose = variable("pose", ["standing", "driving"]);

  it("rotate assigns different slots from one list — axis length is n, not n^k", () => {
    const sp = spec(
      "man in {{color#1}} shirt, woman in {{color#2}} skirt, {{color#3}} car",
      [],
      { kind: "cartesian" },
      {},
      [colors],
    );
    expect(countPlan(sp)).toBe(3);
    const plan = expandMatrix(sp);
    expect(plan.combinations).toHaveLength(3);
    expect(plan.combinations[0]?.values).toEqual({
      "color#1": "red",
      "color#2": "blue",
      "color#3": "green",
    });
    expect(plan.combinations[1]?.values).toEqual({
      "color#1": "blue",
      "color#2": "green",
      "color#3": "red",
    });
    expect(plan.combinations[0]?.rendered["prompt"]).toBe(
      "man in red shirt, woman in blue skirt, green car",
    );
  });

  it("rotate reuses options when slots outnumber values", () => {
    const small = pool("color", ["red", "blue", "green"]);
    const sp = spec(
      "{{color#1}} {{color#2}} {{color#3}} {{color#4}} {{color#5}} {{color#6}} {{color#7}} {{color#8}}",
      [],
      { kind: "cartesian" },
      {},
      [small],
    );
    expect(countPlan(sp)).toBe(3);
    const plan = expandMatrix(sp);
    expect(plan.warnings.join(" ")).toContain("will repeat");
    expect(plan.combinations[0]?.values).toEqual({
      "color#1": "red",
      "color#2": "blue",
      "color#3": "green",
      "color#4": "red",
      "color#5": "blue",
      "color#6": "green",
      "color#7": "red",
      "color#8": "blue",
    });
  });

  it("same assign puts the identical value in every slot", () => {
    const samePool = pool("color", ["red", "blue"], { assign: "same" });
    const sp = spec(
      "{{color#1}} / {{color#2}}",
      [],
      { kind: "cartesian" },
      {},
      [samePool],
    );
    const plan = expandMatrix(sp);
    expect(plan.combinations.map((c) => c.values)).toEqual([
      { "color#1": "red", "color#2": "red" },
      { "color#1": "blue", "color#2": "blue" },
    ]);
  });

  it("multiplies with normal variables via existing cartesian", () => {
    const sp = spec(
      "{{color#1}} shirt, {{pose}}",
      [pose],
      { kind: "cartesian" },
      {},
      [colors],
    );
    // pose (3? no — 2) × color pool (3) = 6. Variable axes first, then pools.
    expect(countPlan(sp)).toBe(6);
    const plan = expandMatrix(sp);
    // pose is outer (first variable axis); color rotates innermost.
    expect(plan.combinations.slice(0, 3).map((c) => c.values["pose"])).toEqual([
      "standing",
      "standing",
      "standing",
    ]);
    expect(
      plan.combinations.slice(0, 3).map((c) => c.values["color#1"]),
    ).toEqual(["red", "blue", "green"]);
  });

  it("sample over a pool product stays reproducible", () => {
    const sp = spec(
      "{{color#1}} {{pose}}",
      [pose],
      { kind: "sample", count: 3, seed: 11 },
      {},
      [colors],
    );
    const a = expandMatrix(sp).combinations.map((c) => c.label);
    const b = expandMatrix(sp).combinations.map((c) => c.label);
    expect(a).toEqual(b);
    expect(a).toHaveLength(3);
  });

  it("does not disturb a matrix with only normal variables", () => {
    const s = variable("subject", ["cat", "dog"]);
    const sp = spec("a {{subject}}", [s]);
    expect(sp.pools).toEqual([]);
    expect(countPlan(sp)).toBe(2);
    expect(
      expandMatrix(sp).combinations.map((c) => c.rendered["prompt"]),
    ).toEqual(["a cat", "a dog"]);
  });
});

// ── token sync ──────────────────────────────────────────────────────────────

describe("syncVariablesWithTokens", () => {
  it("adds a variable for a new token", () => {
    const out = syncVariablesWithTokens([], ["subject"], nextId);
    expect(out).toHaveLength(1);
    expect(out[0]?.name).toBe("subject");
    expect(out[0]?.options).toHaveLength(1);
  });

  it("NEVER discards hand-typed options when the token is momentarily deleted", () => {
    const typed = variable("style", ["noir", "anime", "oil"]);
    const out = syncVariablesWithTokens([typed], [], nextId);
    expect(out).toHaveLength(1);
    expect(out[0]?.options.map((o) => o.value)).toEqual([
      "noir",
      "anime",
      "oil",
    ]);
  });

  it("drops an empty auto-created variable whose token is gone", () => {
    const auto = variable("ghost", [""]);
    expect(syncVariablesWithTokens([auto], [], nextId)).toHaveLength(0);
  });

  it("keeps param-bound variables that never appear in the prompt", () => {
    const steps = variable("steps", ["20", "30"], {
      binding: { kind: "param", axisId: "steps" },
    });
    expect(syncVariablesWithTokens([steps], [], nextId)).toHaveLength(1);
  });

  it("preserves the user's drag order and appends new tokens at the end", () => {
    const a = variable("b", ["1"]);
    const b = variable("a", ["1"]);
    const out = syncVariablesWithTokens([a, b], ["b", "a", "c"], nextId);
    expect(out.map((v) => v.name)).toEqual(["b", "a", "c"]);
  });
});

describe("syncPoolsWithTokens", () => {
  it("creates a pool for a new slot token", () => {
    const refs = extractPoolRefs(["{{color#1}} and {{color#2}}"]);
    const out = syncPoolsWithTokens([], refs, nextId);
    expect(out).toHaveLength(1);
    expect(out[0]?.name).toBe("color");
    expect(out[0]?.assign).toBe("rotate");
  });

  it("keeps typed pool options when slots are momentarily deleted", () => {
    const typed = pool("color", ["red", "blue", "green"]);
    const out = syncPoolsWithTokens([typed], [], nextId);
    expect(out).toHaveLength(1);
    expect(out[0]?.options.map((o) => o.value)).toEqual([
      "red",
      "blue",
      "green",
    ]);
  });

  it("drops an empty auto-created pool whose slots are gone", () => {
    const auto = pool("ghost", [""]);
    expect(syncPoolsWithTokens([auto], [], nextId)).toHaveLength(0);
  });
});

// ── spec edits ──────────────────────────────────────────────────────────────

describe("prompt matrix edits", () => {
  it("renames a text variable and every matching template token", () => {
    const subject = variable("subject", ["cat", "dog"]);
    const out = renameVariableInSpec(
      spec("a {{subject}} beside {{ Subject }}", [subject]),
      subject.id,
      "animal",
    );

    expect(out.fields[0]?.text).toBe("a {{animal}} beside {{animal}}");
    expect(out.variables.map((v) => v.name)).toEqual(["animal"]);
    expect(validateSpec(out).errors).toEqual([]);
  });

  it("renames a parameter variable without writing it into the prompt", () => {
    const steps = variable("Steps", ["20", "30"], {
      binding: { kind: "param", axisId: "steps" },
    });
    const out = renameVariableInSpec(spec("a cat", [steps]), steps.id, "Step Count");

    expect(out.fields[0]?.text).toBe("a cat");
    expect(out.variables[0]?.name).toBe("Step Count");
  });

  it("inserts a saved library variable into the prompt and variable list", () => {
    const subject = variable("subject", ["cat"]);
    const entry: LibraryEntry = {
      id: "entry-style",
      name: "style",
      kind: "variable",
      options: opts("noir", "watercolor"),
      updatedAt: 1,
    };

    const out = insertLibraryEntryInSpec(spec("a {{subject}}", [subject]), entry);

    expect(out.fields[0]?.text).toBe("a {{subject}} {{style}}");
    expect(out.variables.map((v) => v.name)).toEqual(["subject", "style"]);
    expect(out.variables[1]?.options.map((o) => o.value)).toEqual([
      "noir",
      "watercolor",
    ]);
    expect(validateSpec(out).errors).toEqual([]);
  });

  it("replaces an existing library variable's options and inserts its missing token", () => {
    const style = variable("style", ["old"]);
    const entry: LibraryEntry = {
      id: "entry-style",
      name: "style",
      kind: "variable",
      options: opts("noir", "watercolor"),
      updatedAt: 1,
    };

    const out = insertLibraryEntryInSpec(spec("a portrait", [style]), entry);

    expect(out.fields[0]?.text).toBe("a portrait {{style}}");
    expect(out.variables).toHaveLength(1);
    expect(out.variables[0]?.options.map((o) => o.value)).toEqual([
      "noir",
      "watercolor",
    ]);
    expect(validateSpec(out).errors).toEqual([]);
  });
});

// ── image target ────────────────────────────────────────────────────────────

const MODEL = (id: string, downloaded: boolean): ImageGenModelInfo =>
  ({ model_id: id, is_downloaded: downloaded }) as ImageGenModelInfo;

describe("image target", () => {
  const target = createImageTarget({
    models: [MODEL("sdxl-turbo", true), MODEL("flux-schnell", false)],
    loras: [{ id: "my-lora" } as never],
  });
  const base: ImageGenerateInput = { prompt: "", model_id: "sdxl-turbo" };

  it("renders the prompt and applies the seed", () => {
    const s = variable("subject", ["cat", "dog"]);
    const plan = expandMatrix(spec("a {{subject}}", [s]));
    const { jobs, errors } = buildJobs(target, base, plan.combinations, [s]);
    expect(errors).toEqual([]);
    expect(jobs.map((j) => j.job.prompt)).toEqual(["a cat", "a dog"]);
    expect(jobs[0]?.job.seed).toBe(1234);
    expect(jobs[0]?.label).toBe("subject=cat");
  });

  it("sweeps a numeric parameter axis", () => {
    const steps = variable("steps", ["8", "20"], {
      binding: { kind: "param", axisId: "steps" },
    });
    const plan = expandMatrix(spec("a cat", [steps]));
    const { jobs, errors } = buildJobs(target, base, plan.combinations, [
      steps,
    ]);
    expect(errors).toEqual([]);
    expect(jobs.map((j) => j.job.steps)).toEqual([8, 20]);
  });

  it("sweeps size as one axis", () => {
    const size = variable("size", ["1024x1024", "832x1216"], {
      binding: { kind: "param", axisId: "size" },
    });
    const plan = expandMatrix(spec("a cat", [size]));
    const { jobs } = buildJobs(target, base, plan.combinations, [size]);
    expect(jobs[1]?.job.width).toBe(832);
    expect(jobs[1]?.job.height).toBe(1216);
  });

  it("a swept seed axis overrides the batch seed policy", () => {
    const seedVar = variable("seed", ["7", "8"], {
      binding: { kind: "param", axisId: "seed" },
    });
    const plan = expandMatrix(spec("a cat", [seedVar]));
    const { jobs } = buildJobs(target, base, plan.combinations, [seedVar]);
    expect(jobs.map((j) => j.job.seed)).toEqual([7, 8]);
  });

  it("sweeps LoRAs, with an empty arm meaning no LoRA", () => {
    const lora = variable("lora", ["", "my-lora", "my-lora@0.5"], {
      binding: { kind: "param", axisId: "lora" },
    });
    const plan = expandMatrix(spec("a cat", [lora]));
    const { jobs, errors } = buildJobs(target, base, plan.combinations, [lora]);
    expect(errors).toEqual([]);
    expect(jobs[0]?.job.loras).toBeUndefined();
    expect(jobs[1]?.job.loras).toEqual([{ id: "my-lora", scale: 1 }]);
    expect(jobs[2]?.job.loras).toEqual([{ id: "my-lora", scale: 0.5 }]);
  });

  it("FAILS THE WHOLE BATCH on a bad option rather than silently skipping runs", () => {
    const steps = variable("steps", ["20", "999"], {
      binding: { kind: "param", axisId: "steps" },
    });
    const plan = expandMatrix(spec("a cat", [steps]));
    const { errors } = buildJobs(target, base, plan.combinations, [steps]);
    expect(errors.join(" ")).toContain("between 1 and 150");
  });

  it("refuses a model sweep that names an undownloaded model", () => {
    const model = variable("model", ["sdxl-turbo", "flux-schnell"], {
      binding: { kind: "param", axisId: "model" },
    });
    const plan = expandMatrix(spec("a cat", [model]));
    const { errors } = buildJobs(target, base, plan.combinations, [model]);
    expect(errors.join(" ")).toContain("not downloaded");
  });

  it("sweeps an arbitrary advanced pipeline kwarg via extra:<key>", () => {
    const eta = variable("eta", ["0.1", "0.9"], {
      binding: { kind: "param", axisId: "extra:eta" },
    });
    const plan = expandMatrix(spec("a cat", [eta]));
    const { jobs, errors } = buildJobs(target, base, plan.combinations, [eta]);
    expect(errors).toEqual([]);
    expect(jobs[0]?.job.extra_params).toEqual({ eta: 0.1 });
  });

  it("substitutes a param variable into the prompt too, when tokenized", () => {
    const steps = variable("steps", ["8", "20"], {
      binding: { kind: "param", axisId: "steps" },
    });
    const plan = expandMatrix(spec("a cat, {{steps}} steps", [steps]));
    const { jobs } = buildJobs(target, base, plan.combinations, [steps]);
    expect(jobs[0]?.job.prompt).toBe("a cat, 8 steps");
    expect(jobs[0]?.job.steps).toBe(8);
  });
});

// ── rng ─────────────────────────────────────────────────────────────────────

describe("rng", () => {
  it("sampleIndices returns distinct, ascending, in-range indices (sparse)", () => {
    const out = sampleIndices(10_000, 50, new Rng(3));
    expect(out).toHaveLength(50);
    expect(new Set(out).size).toBe(50);
    expect([...out].sort((a, b) => a - b)).toEqual(out);
    expect(Math.max(...out)).toBeLessThan(10_000);
  });

  it("sampleIndices handles a near-total sample without degenerating (dense)", () => {
    const out = sampleIndices(100, 95, new Rng(3));
    expect(out).toHaveLength(95);
    expect(new Set(out).size).toBe(95);
  });

  it("survives a zero seed", () => {
    expect(new Rng(0).next()).toBeGreaterThanOrEqual(0);
  });
});

// ── JSON export / import ────────────────────────────────────────────────────

describe("matrix JSON io", () => {
  const s = variable("subject", ["cat", "dog"]);

  it("round-trips a wrapped export", () => {
    const sp = spec("{{subject}}", [s]);
    const text = serializeMatrixExport("image", sp, "Portrait sweep");
    const parsed = parseMatrixImport(text);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.targetId).toBe("image");
    expect(parsed.name).toBe("Portrait sweep");
    expect(parsed.spec.variables).toHaveLength(1);
  });

  it("accepts a bare MatrixSpec without the envelope", () => {
    const sp = spec("{{subject}}", [s]);
    const parsed = parseMatrixImport(JSON.stringify(sp));
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.name).toBeNull();
    expect(parsed.targetId).toBe("image");
  });

  it("rejects invalid JSON and bad versions", () => {
    expect(parseMatrixImport("not json").ok).toBe(false);
    expect(
      parseMatrixImport(JSON.stringify({ v: 99, targetId: "image", spec: {} }))
        .ok,
    ).toBe(false);
  });

  it("suggests a safe download filename", () => {
    expect(matrixExportFilename("Portrait Sweep!")).toBe("portrait-sweep.json");
    expect(matrixExportFilename("")).toBe("matrix-template.json");
  });
});
