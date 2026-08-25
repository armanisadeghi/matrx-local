# Optional Packages — Single-ML-Stack Doctrine

> Code-local rules for `app/services/optional_packages/` and every install
> path that puts packages on the engine's `sys.path`. If you are adding a
> capability, recipe, or dependency ANYWHERE in this repo that touches
> torch/transformers/numpy-family packages, read this first.

> The local NER subsystem this slot hosts (`app/services/ner/`, GLiNER/GLiNER2) has its
> cross-repo system-of-record at
> `/Users/armanisadeghi/code/common-docs/systems/knowledge/knowledge-graph/STATE.md`
> (rulings in `DECISIONS.md`, remaining work in `HANDOFF.md`) — read it before touching
> local NER in ANY repo.

## The one rule

**The managed media runtime slot is the ONLY provider of the ML stack.**
One Python process can hold exactly one copy of torch, torchvision,
transformers, tokenizers, numpy, huggingface-hub, safetensors, diffusers,
accelerate, peft, sentencepiece, and gguf (`SLOT_OWNED_DISTRIBUTIONS` in
[guardrails.py](guardrails.py)). Two copies on `sys.path` means whichever
loses precedence still shadows its partners' ABI expectations the moment
ordering regresses — that is exactly how the July 2026 image-gen production
outage happened (`'_ClassNamespace' object is not iterable`, MXL-D-070: the
NER capability's own torch 2.13 shadowed the slot's torch 2.10 while
torchvision came from the slot).

The slot's versions are release-owned and hash-locked in
`config/runtime-manifests/image-gen-contract.json`. Those pins are law for
every in-process ML consumer: image gen, video gen, NER (gliner/gliner2),
transcription (whisper), text encoders — all of them.

## Certified sys.path precedence

```
frozen/core engine packages  →  managed runtime slot  →  capability dirs
```

- The slot is positioned by `_insert_runtime_sys_path()`
  (`app/services/image_gen/installer.py`) — always ahead of every capability
  dir, never ahead of frozen/core.
- Capability dirs are ONLY ever appended (`_append_optional_package_path`) —
  they fill gaps, they never override.
- `critical_runtime_import_check` validates import origins before exercising
  any native op, so a future ordering regression fails with an explicit
  "resolved outside candidate runtime" diagnostic instead of a cryptic
  native-registration error.

## How a capability gets torch (the `requires_ml_runtime` contract)

A recipe in `app/services/capabilities/installer.py` that needs the ML stack:

1. Declares `"requires_ml_runtime": True` and lists ONLY its thin, non-ML
   packages (e.g. `openai-whisper`, `gliner2[local]`). Never a slot-owned
   distribution — `screen_install_packages` refuses the install and
   `tests/unit/test_ml_stack_guardrails.py` fails the build.
2. Install-time, `_install_with_ml_runtime` ensures the managed runtime exists
   first (installing it when absent — it is the shared base runtime, not an
   image-gen implementation detail).
3. Dependency resolution runs against the slot's exact pins via a generated
   constraints file (`write_slot_constraints_file`), so pip picks consumer
   versions that actually work with the slot's numpy/transformers (e.g. a
   numba that accepts the slot's numpy). A `--dry-run --report` resolve then
   installs only the non-slot-owned remainder with `--no-deps` — torch is
   never downloaded twice. If the resolver can't produce a report, the
   fallback is a full install followed by sanitization.
4. Post-install, `sanitize_target_dir` deletes any slot-owned distribution
   that landed in the capability dir (using each dist's RECORD), and the
   import verify runs under **production precedence** (slot before capability
   dir) with a torch-origin assertion — the stack that gets certified is the
   stack the engine will run.
5. At startup, `sanitize_ml_shadowing_at_startup()` sweeps installed
   capability dirs and loudly strips stale ML copies left by legacy installs
   (one-time migration, frozen builds only, gated on the runtime being ready).

Lightweight capabilities (`app/api/capabilities_routes.py`) get the same
screening and post-install sanitization; their specs must not list slot-owned
packages either (numpy comes from the core bundle/slot, never a spec).

## Pin-bump checklist (things change all the time — this is the process)

Bumping any version in the runtime contract is a **platform event**, not an
image-gen event. In the same change:

1. Regenerate the contract/locks as usual.
2. Run `tests/unit/test_ml_stack_guardrails.py` — it encodes the known
   consumer constraints and fails on breach:
   - `transformers` must stay inside gliner's supported range (`<5.7` as of
     gliner 0.2.27 — re-verify on gliner releases).
   - `numpy` must stay under numba's ceiling (`<2.5` as of numba 0.66) or
     whisper stops resolving.
   - `huggingface-hub` must stay in transformers' `>=1.3,<2` window.
   - Every new managed requirement must be added to
     `SLOT_OWNED_DISTRIBUTIONS`.
3. Re-validate the consumers against the new pins (metadata + an import/
   forward-pass test) and update the bounds in the guardrail test with the
   evidence. Last full validation: 2026-07-19 (whisper 20250625, gliner
   0.2.27, gliner2 1.3.2, kokoro-onnx 0.5.0 — all compatible with torch
   2.10.0 / transformers 5.3.0 / numpy 2.4.2).
4. Installed capabilities are re-verified against the new slot on their next
   install; a capability that breaks on new pins is a STATE (reinstall
   prompt), never a silent fallback to its own stack.

## What an agent must never do

- Add `torch`/`numpy`/`transformers`/any slot-owned name to a recipe, spec,
  or ad-hoc pip call that targets a dir on the engine's `sys.path`. The
  screen + tests exist to make this impossible to do quietly.
- "Fix" a capability by prepending its dir on `sys.path` or re-appending the
  slot. Precedence is part of the certified contract.
- Weaken or delete the guardrail tests to make an install work. If a test
  fires, the dependency plan is wrong, not the test.
- Introduce a new in-engine ML consumer without routing its stack through the
  slot (or an isolated subprocess with its own interpreter — the escape hatch
  for a consumer that genuinely cannot share the pins; none exist today).

Related docs: [app/services/image_gen/FEATURE.md](../image_gen/FEATURE.md)
(slot system, activation, verification), `app/tools/FEATURE.md` (tool-side
consumers).
