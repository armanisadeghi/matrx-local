# prompt-matrix — {{variable}} templates → planned, countable, randomized batches

Durable rules for `desktop/src/lib/prompt-matrix/` and the UI it drives. Touch
this code → update this file in the same change.

---

## What it is

Write one prompt with `{{variables}}`, give each variable a list of options, pick
how to combine them, see **exactly** how many runs that is, and queue them all.

```
  const plan  = createBatchSnapshot(spec);                            // fresh randomized attempt
  const built = buildJobs(target, base, plan.combinations, spec.variables);
  await enqueueImageBatch(built.jobs.map(...), label);                // one request
```

### Pools — shared option lists via `{{name#slot}}`

`{{color#1}}` / `{{color#2}}` / `{{color#3}}` declare **one pool** (`color`) with
many slots. Options are typed once; each pool is **one axis** of length n
(option count). Strategies / seed policy / RNG never special-case pools.

| Assign | Within one run |
|---|---|
| `rotate` (default) | slot i at step s → `options[(s + i) % n]` — reuse when slots > options |
| `same` | every slot gets `options[s]` |

Bare `{{color}}` remains a normal variable. A name cannot be both a variable and
a pool — `validateSpec` hard-errors on the collision. `syncPoolsWithTokens`
mirrors variable sync: never re-sort, never discard typed options.

## The core is media-agnostic — keep it that way

`types` / `parse` / `expand` / `rng` know about **variables, pools, options,
strategies and combinations**. They contain no reference to images, models, or
diffusion, and they must not gain one.

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

**2. Variable order never determines execution order.** It is retained only as
the stable structure of a saved template. `createBatchSnapshot()` shuffles whole
valid combinations with Fisher–Yates before any work is queued, so no variable
is accidentally held frozen across the first runs. Linked variables and pools
stay paired because combinations—not individual axes—are shuffled.

**3. Nothing is silently dropped or silently generated.**
- A `{{token}}` with no variable is an **error**, not an empty string — a 40-minute
  batch that bakes a literal `{{style}}` into 120 images is the failure mode this
  prevents.
- A bad option value fails the **whole batch** (`buildJobs` returns errors), never
  "skip that run" — 118 images where 120 were expected is worse than a refusal
  that names the two typos.
- Linked variables of unequal length truncate to the shortest **with a warning**.

**4. Every new batch attempt is random.** `createBatchSnapshot()` uses Web Crypto
entropy for its subset (when sampled), execution order, and one independent
diffusion seed per image. Stopping and starting a new batch therefore cannot
return the same leading run sequence. Preview freezes one such snapshot, so the
jobs a user approves are precisely the jobs that are queued.

**5. Recovery is not a new batch.** Retry and restart-resume retain the original
job's durable seed; they recover an existing attempt. Creating another batch is
always a fresh random draw.

**6. Never lose typed options.** `syncVariablesWithTokens` keeps a variable whose
token was momentarily deleted if it holds any hand-typed option. The working spec
is persisted (debounced) to localStorage per target, so a half-built matrix
survives a tab switch, a reload, and an app restart.

**7. Library + templates live on disk.** Reusable pools/variables and named
templates are written by the engine to `~/.matrx/prompt-matrix/`
(`library.json`, `templates.json`) — not buried in browser storage. The Library
panel sits in the matrix UI itself (with the absolute path shown). See
`app/services/prompt_matrix/FEATURE.md`.

## Strategies

| Kind | Runs | For |
|---|---|---|
| `cartesian` | Πnᵢ | Every combination, in a fresh randomized order. |
| `baseline` | 1 + Σ(nᵢ − 1) | Change ONE variable at a time from a baseline. Turns 3 × 10 into **12** runs, not 30 — the escape hatch when the product explodes. |
| `sample` | N | A fresh random subset of the product, randomly ordered. |
| `zip` | min(nᵢ) | Lockstep combinations, randomly ordered for execution. |

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

- `TemplateEditor` — highlights `{{tokens}}` via a mirror div under a native
  textarea. The textarea draws the real text and caret; the mirror paints only
  token backgrounds. Unknown tokens are flagged in red. The mirror's typography
  MUST match the textarea exactly (`SHARED_BOX`) or highlights drift.
- `VariableCard` — sortable; paste a newline-separated list into any option row to
  create many options at once. Text-variable names are editable in-place and
  rename the matching `{{token}}` everywhere in the template; parameter-variable
  names are label-only.
- `BatchConfirmDialog` — the count, a time estimate from **this machine's own**
  median generation time, and the actual first/last prompts from a fresh random
  snapshot. Nothing is queued without it.
- `BatchPreviewDialog` — **Preview** creates a fresh snapshot with the real
  `buildJobs` path, freezes it, and lets you review every prompt, copy runs, and
  checkbox which ones to queue. Queue-from-preview never re-expands—selection
  only filters that frozen snapshot.
- `BatchQueuePanel` — pause / drag-reorder / cancel-batch / retry, live.
- **Library panel** — always visible above the variable cards. Save a pool or
  variable with **Save to library**; **Insert** drops it into the current
  matrix. Inserting a saved variable also inserts its `{{token}}` into the
  prompt field when missing, so the option list is immediately active. Path to
  `library.json` is shown and copyable.
- **Templates menu** — save/load named matrices (on-disk `templates.json`);
  **export** / **import** JSON for chat/agent workflows.

`ImageGenerateForm` carries the Single ⇄ Batch toggle, so every variant gets it.

## Tests

`prompt-matrix.test.ts` (`pnpm test:unit`) pins counting, validation, pool/link
integrity, and injected-entropy snapshots—including the regression contract that
two new attempts have different leading runs and seeds. The test source is
deterministic only to make assertions stable; production snapshots use Web Crypto.
