# prompt-matrix — {{variable}} templates → planned, countable, ordered batches

Durable rules for `desktop/src/lib/prompt-matrix/` and the UI it drives. Touch
this code → update this file in the same change.

---

## What it is

Write one prompt with `{{variables}}`, give each variable a list of options, pick
how to combine them, see **exactly** how many runs that is, and queue them all.

```
  const plan  = expandMatrix(spec);                                   // ordered runs + exact total
  const built = buildJobs(target, base, plan.combinations, spec.variables);
  await enqueueImageBatch(built.jobs.map(...), label);                // one request
```

## The core is media-agnostic — keep it that way

`types` / `parse` / `expand` / `rng` know about **variables, options, strategies
and combinations**. They contain no reference to images, models, or diffusion,
and they must not gain one.

A generator plugs in as a **`MatrixTarget`** (`targets.ts`), which supplies the
other half:

- `fields` — which text fields can carry `{{tokens}}` (prompt, negative prompt…)
- `axes` — which generation **parameters** can be swept (steps, guidance, seed,
  model, LoRA, size…), each with its own `parse` + `apply`
- `applyField` / `applySeed` — how a rendered value lands on a job payload

`imageTarget.ts` is the first one. **Video and text-prompt targets slot in beside
it without touching the engine** — that is the whole reason for the seam. A
`{{steps}}` sweep and a `{{subject}}` sweep are the same combinatorics; only the
axes differ.

## The rules that make it trustworthy

**1. The count is EXACT and never materializes.** `countPlan()` is arithmetic. A
6-variable cartesian product is 1,000,000 runs; the UI shows that number and
refuses it without ever allocating it. `expandMatrix` materializes at most
`MAX_MATERIALIZED` (5000) and sets `truncated`. Never compute a total by building
the list.

**2. Variable ORDER is loop nesting.** `variables[0]` is the outermost loop (the
one **held frozen** while the others sweep); the last is innermost (changes
fastest). That is the entire answer to "do you freeze one variable and sweep the
others?" — you drag it to the top. Results therefore arrive grouped the way a
human compares them. `syncVariablesWithTokens` **must never re-sort** existing
variables; a keystroke in the prompt cannot be allowed to undo the user's drag.

**3. Nothing is silently dropped or silently generated.**
- A `{{token}}` with no variable is an **error**, not an empty string — a 40-minute
  batch that bakes a literal `{{style}}` into 120 images is the failure mode this
  prevents.
- A bad option value fails the **whole batch** (`buildJobs` returns errors), never
  "skip that run" — 118 images where 120 were expected is worse than a refusal
  that names the two typos.
- Linked variables of unequal length truncate to the shortest **with a warning**.

**4. Randomness is seeded.** All of it goes through `Rng` (mulberry32). A "random
sample of 50" the user cannot reproduce is not a plan. `Math.random()` appears
only in `randomSeed()` for "surprise me".

**5. A fixed seed is the default.** It is what makes a sweep a comparison: the
variable becomes the only difference between two images. With a random seed you
cannot tell your change apart from the noise it started from.

**6. Never lose typed options.** `syncVariablesWithTokens` keeps a variable whose
token was momentarily deleted if it holds any hand-typed option. The working spec
is persisted (debounced) to localStorage per target, so a half-built matrix
survives a tab switch, a reload, and an app restart.

## Strategies

| Kind | Runs | For |
|---|---|---|
| `cartesian` | Πnᵢ | Every combination. Order = nesting (see rule 2). |
| `baseline` | 1 + Σ(nᵢ − 1) | Change ONE variable at a time from a baseline. Turns 3 × 10 into **12** runs, not 30 — the escape hatch when the product explodes. |
| `sample` | N | A reproducible random subset of the product. Probe a huge space before committing. |
| `zip` | min(nᵢ) | Everything steps in lockstep. |

Plus **link groups**: variables sharing a `linkGroup` step 1:1 instead of
multiplying (pair a style with its LoRA → 3 × 3 = 3 runs, not 9). And
`seed.repeats`, which **multiplies** the total (N seeds per combination, emitted
combination-major so a combination's runs land together).

## The UI

Built **once** in `desktop/src/components/media-gen/core/PromptMatrix/` so all
five layout variants inherit it rather than forking. State lives in
`PromptMatrixContext` (app-wide singleton) because the panel is deliberately
split — template/variables/strategy sit **above** the model's base settings, and
the run-count + Queue button **below** them.

- `TemplateEditor` — highlights `{{tokens}}` via a mirror div under a
  transparent-text textarea (native caret/undo/IME preserved). Unknown tokens are
  flagged in red. The mirror's typography MUST match the textarea exactly
  (`SHARED_BOX`) or the highlight drifts.
- `VariableCard` — sortable; paste a newline-separated list into any option row to
  create many options at once.
- `BatchConfirmDialog` — the count, a time estimate from **this machine's own**
  median generation time, and the actual first/last prompts. Nothing is queued
  without it.
- `BatchQueuePanel` — pause / drag-reorder / cancel-batch / retry, live.

`ImageGenerateForm` carries the Single ⇄ Batch toggle, so every variant gets it.

## Tests

`prompt-matrix.test.ts` (43 tests, `pnpm test:unit`) pins the counting, the
nesting order, every strategy, seed policy, validation, and the image target's
axes. The count and the emission order are the two things a bug in this library
turns into wasted GPU-hours — they are tested hardest.
