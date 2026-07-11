---
status: active
updated: 2026-07-10
repos: [matrx-local, aidream]
owner-context: media generation (images + video), Media Generation page, media library, Private vault
---

# Media generation — handoff

## Where things stand (30 seconds of past, then all future)

Image generation works end-to-end in the installed app. Released: **v1.3.98**
(user-facing) — media-gen page with 5 layout variants, full parameter
exposure, image job queue with priority-next + single-generation gate,
mid-flight cancel, media library, lightbox, encrypted Private vault.
Committed locally but **NOT pushed/released**: img2img + LoRA (backend +
UI), the core/ unification of all layouts, the Playwright E2E harness, and
the sd-server spike doc (commit `20fe69da1`). Full detail lives in git log
and `.matrx/AGENT_TASKS.md` — do not re-derive it here.

## ⛔ THE GATE — read before doing anything

`main` is ~36 commits ahead of `origin/main` and includes an in-flight
**matrx-ai 0.3.0 migration** (Phases 3–5, from a parallel session).
`pyproject.toml` requires `matrx-ai>=0.3.0` which is **not on PyPI yet**
(see the ⚠️ comment at pyproject.toml:59). Consequences:

- **Do not push** until matrx-ai 0.3.0 is published (publishing happens in
  the aidream repo — see memory `reference_aidream_packages_location`).
  Pushing earlier breaks CI and any release build.
- `uv run` re-resolution fails → use **`uv run --no-sync pytest tests/smoke`**
  until the publish lands. The existing venv is fully working.
- Once published: `uv sync --all-extras` (plain `uv sync` STRIPS extras —
  real incident, see AGENT_TASKS) → full verify → `git push` → cut
  **v1.3.99** via `./scripts/release.sh --patch --monitor`.

## Priority work queue

### 1. Ship v1.3.99 (after the gate opens)
Everything in `20fe69da1` + `d56ab5bdb` is verified (220 smoke tests,
tsc, vite build, E2E 5/5) but has never run on real GPU hardware.
`d56ab5bdb` adds CUSTOM models + LoRAs from HF/Civitai (one-paste
add-model dialog, Civitai API key in Settings → API Keys, single-file
checkpoints via from_single_file for SD/SDXL/FLUX/Z-Image). Live pass
additions: add one Civitai SDXL checkpoint + one Civitai LoRA with a
real key (needs Arman's key), plus the original items below. Before or
immediately after release, do a live pass on Arman's machine (engine on
port 22140, `~/.matrx/local.json` is truth; probe with the API key from
`.env`):
- img2img: SDXL-Turbo (watch `steps*strength >= 1` at 1 step) and
  FLUX.2-klein (native reference-edit — NO strength; UI hides the slider).
- LoRA: download one catalog SDXL LoRA + one FLUX LoRA, generate with
  scale ~0.8, confirm unload-after-generate leaves the next plain
  generation unaffected.
- "Use as input" round trip: result → lightbox → Use as input → generate.

### 2. Variant strategy (Arman's explicit direction — do not relitigate)
Keep ALL five layouts for now. The core/ unification made their internals
identical (`desktop/src/components/media-gen/core/` is the ONLY place
logic lives; variants are thin shells). Standing rules:
- **Never add logic to a variant file.** New capability → core/, layout
  affordance → variant. If you catch yourself copying between variants,
  you are doing it wrong.
- Arman will keep ≥2 layouts after more real use; the E2E spec
  "all 5 layout variants mount" is the drift canary — keep it green.
- Merge remaining best-of features across layouts as they're noticed;
  track them in `.matrx/AGENT_TASKS.md`.

### 3. Video generation — still never run on real hardware
Service/routes/UI exist and are queue-based with cancel. Missing: one real
T2V run in the packaged app (Wan2.1-T2V-1.3B ~29GB or LTX). Expect
minutes-long jobs on MPS, bf16 only (fp8 crashes Metal — service.py dtype
notes). This is the biggest untested surface.

### 4. sd-server migration — decision made: PARTIAL GO with a benchmark gate
Read `docs/research/sd-server-spike.md` (verdict, parity matrix, kill
criteria). Next concrete step is **Wave 1 only**: benchmark sd-server
FLUX.2-klein GGUF vs our 10.4s diffusers baseline on Arman's M-series.
Kill criterion: >2× slower → stop, keep diffusers. Do NOT start deeper
integration before the benchmark passes.

### 5. E2E — use it, extend it
`cd desktop && pnpm test:e2e` (docs/UI_TESTING.md). Dedicated test login
in gitignored `desktop/.env.test` (never commit/print credentials).
Engine-dependent specs are read-only against a live engine. Extend specs
when touching media-gen UI; run before every release.

### 6. Platform issues found tonight (not media-gen, but real)
- **Supabase SMTP is broken instance-wide** — all signup/reset emails fail
  (KNOWN_DEFECTS.md MXL-D-028). Arman must fix SMTP in the Supabase
  dashboard; until then no self-serve signups work in production.
- aidream-side: `GET /api/ai-tools/app/matrx_local` 404, scraper queue 500
  (CURRENT_ERRORS.md T001/T008 — aidream repo sessions).

### 7. Tracked follow-ups (see .matrx/AGENT_TASKS.md for full list)
Model-LOAD phase isn't cancellable (cancel lands right after load);
`num_images_per_prompt>1` persists only the first image; FLUX LoRA
catalog entries are dev-trained (schnell compat unproven); vault
auto-lock not configurable; vault arbitrary-file ingest (PDFs etc.) is
designed-for but not exposed; packaged-build smoke gate in release.sh
still unbuilt (LESSONS.md has the manual procedure).

## Private vault — operational notes
Escrow PRIVATE key: backed up by Arman (done, 2026-07-10). Public key
embedded in `app/services/media_vault/escrow.py`. Recovery:
`uv run python scripts/vault-recover.py --private-key <pem> --vault-dir
~/.matrx/media/vault {--out <dir> | --new-password <pw>}`. A vault
cannot be created without the escrow slot — keep it that way.

## Verify loop (memorize this)
```bash
cd desktop && npx tsc --noEmit && pnpm build   # frontend
uv run --no-sync pytest tests/smoke -q          # backend (drop --no-sync after matrx-ai publish)
cd desktop && pnpm test:e2e                     # real UI, real login
# NEVER run full pytest with a live engine — tests/conftest.py kills it.
```

## Suggested skills
- `verify` — after any media-gen change, drive the affected flow for real.
- `code-review` — before pushing large waves.
- `build-sub-feature` — for adding capabilities INTO media-gen (they go in core/).
- `feature-deep-dive` — if taking over an adjacent whole feature (e.g. video E2E).
- `handoff` (installed at .claude/skills/handoff) — regenerate this doc at session end.

## Decisions already made — do not reopen
Studio is the default layout; all 5 variants stay (for now) on shared
core internals. img2img + LoRA: approved and built. sd-server: partial-go,
benchmark-gated. Escrow: embedded RSA-4096 public key, NOT the redaction
KMS key; optional later upgrade to a dedicated asymmetric KMS key. Client
never calls Python for DB reads (platform rule). Prompt caps are 10k chars
with per-family token hints — never re-add arbitrary caps.
