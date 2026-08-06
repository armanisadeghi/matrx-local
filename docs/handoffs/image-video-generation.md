---
status: active
updated: 2026-08-06
repos: [matrx-local, aidream]
owner-context: media generation (images + video), Media Generation page, media library, Private vault
---

# Media generation — handoff (groomed 2026-08-06, docs-hygiene sweep)

> History stripped: the 2026-07-10 snapshot (v1.3.98 state, "gate is open" PyPI
> wave, ship-v1.3.99 framing) is long superseded — the repo is at v1.4.x, the
> core/ unification, img2img + LoRA, custom models, and the E2E harness are all
> shipped in-tree (`desktop/src/components/media-gen/core/`,
> `custom_models.py`, `LoraManager.tsx`, `docs/research/sd-server-spike.md`).
> What remains below is ONLY the unfinished work.

## Remaining work

### 1. Live GPU verification pass (needs Arman's machine — never run on real hardware)
All built + smoke/E2E-verified, but never exercised on a real M-series GPU:
- img2img: SDXL-Turbo (watch `steps*strength >= 1` at 1 step) and
  FLUX.2-klein (native reference-edit — NO strength; UI hides the slider).
- LoRA: one catalog SDXL LoRA + one FLUX LoRA, scale ~0.8, confirm
  unload-after-generate leaves the next plain generation unaffected.
- Custom models: add one Civitai SDXL checkpoint + one Civitai LoRA with a
  real key (needs Arman's Civitai key).
- "Use as input" round trip: result → lightbox → Use as input → generate.

### 2. Video generation — still never run on real hardware
Service/routes/UI exist and are queue-based with cancel. Missing: one real
T2V run in the packaged app (Wan2.1-T2V-1.3B ~29GB or LTX). Expect
minutes-long jobs on MPS, bf16 only (fp8 crashes Metal — service.py dtype
notes). This is the biggest untested surface.

### 3. sd-server migration — PARTIAL GO, benchmark-gated
Read `docs/research/sd-server-spike.md` (verdict, parity matrix, kill
criteria). Next concrete step is **Wave 1 only**: benchmark sd-server
FLUX.2-klein GGUF vs our 10.4s diffusers baseline on Arman's M-series.
Kill criterion: >2× slower → stop, keep diffusers. Do NOT start deeper
integration before the benchmark passes.

### 4. Tracked follow-ups (full list in `.matrx/AGENT_TASKS.md`)
Model-LOAD phase isn't cancellable; `num_images_per_prompt>1` persists only
the first image; FLUX LoRA catalog entries are dev-trained (schnell compat
unproven); vault auto-lock not configurable; vault arbitrary-file ingest
designed-for but not exposed; packaged-build smoke gate in release.sh
unbuilt (docs/official/build-lessons.md has the manual procedure).

## Standing rules (Arman's direction — do not relitigate)

- Keep ALL five layout variants on shared core internals
  (`desktop/src/components/media-gen/core/` is the ONLY place logic lives;
  variants are thin shells). **Never add logic to a variant file.** The E2E
  spec "all 5 layout variants mount" is the drift canary — keep it green.
- Studio is the default layout. img2img + LoRA: approved and built.
  sd-server: partial-go, benchmark-gated (above). Escrow: embedded RSA-4096
  public key, NOT the redaction KMS key. Client never calls Python for DB
  reads (platform rule). Prompt caps are 10k chars with per-family token
  hints — never re-add arbitrary caps.

## Private vault — operational notes
Escrow PRIVATE key: backed up by Arman (done, 2026-07-10). Public key
embedded in `app/services/media_vault/escrow.py`. Recovery:
`uv run python scripts/vault-recover.py --private-key <pem> --vault-dir
~/.matrx/media/vault {--out <dir> | --new-password <pw>}`. A vault
cannot be created without the escrow slot — keep it that way.

## Verify loop
```bash
cd desktop && npx tsc --noEmit && pnpm build   # frontend
uv run pytest tests/smoke -q                    # backend
cd desktop && pnpm test:e2e                     # real UI, real login
# NEVER run full pytest with a live engine — tests/conftest.py kills it.
```
