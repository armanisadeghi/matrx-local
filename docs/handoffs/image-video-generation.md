---
status: active
updated: 2026-07-09
repos: [matrx-local, aidream]
vision: [CURRENT_ERRORS.md]
---

# Image & video generation — finish the last mile

## Vision — Arman's words

- "image and video generation are a critical part of our offerings for this desktop app"
- "make sure that we are offering the latest opensource models because some better ones have been released"
- "the system not really telling you when things aren't working but having silent failures is a big problem"
- "It's not worth it for me to do this" — said after v1.3.94 still showed no image in the UI. The bar is: **click a button in the installed app, see an image.** Nothing else counts as working.

## Current state — read this before anything else

**The backend is PROVEN working on Arman's installed v1.3.94 app** (verified 2026-07-09 19:3x against the live packaged engine, port 22140):
FLUX.2-klein-4B loads in 8s (MPS, bf16) and `POST /image-gen/generate` returned a real, beautiful 1024×1024 PNG in 10.4s.
Arman's own generate clicks at 19:16:00 and 19:16:29 **also completed server-side** (`[image_gen_generate] Slow operation: 15417ms / 10530ms` in the unified log) — **but the UI showed him nothing**. The remaining defect is in the frontend result path, not the pipeline.

## Remaining work (priority order)

1. **UI never displays the generated image (THE bug).** Repro: installed app → Image & Video tab → FLUX.2-klein loaded → Generate. Server completes (watch `~/Library/Logs/MatrxLocal/system.log` for `image_gen_generate`), UI shows no image/error.
   Suspects, in order: `desktop/src/hooks/use-media-gen.ts` `generateImage` (sets `imageResult` only on `result.success && result.image_b64` — verify the parsed response shape against the live API; a 2.2MB JSON body), `ImageGenSection.tsx` generate-view result rendering (view state, result element visibility/scroll), and whether `imageGenerating` spinner even appears (if not, state updates aren't landing at all — check MediaGenProvider context delivery in the PROD build).
   Verification bar: a screenshot of the image visible in the packaged app. Consider adding a client log line when `imageResult` is set, then compare against the server log.
2. **Boot-ordering bug: resumed downloads fail before packages inject.** `app/main.py` Phase 0a starts the DownloadManager (which resumes incomplete downloads) BEFORE the image-gen packages sys.path injection, so a resumed HF download fails with "huggingface_hub is not importable" even though packages ARE installed. Live evidence: failed entry `Tongyi-MAI--Z-Image-Turbo` id f98e0567. Fix: inject packages before starting the DM, or make the DM's HF path retry after injection. Also give failed entries a visible Retry in the Download Manager UI.
3. **Video generation has never run end-to-end on real hardware.** Service/routes/UI exist (`app/services/video_gen/`, `/video-gen/*`, `VideoGenSection`); only load-path and TestClient verified. Download Wan2.1-T2V-1.3B (~29GB) or LTX, run a real T2V job in the packaged app, watch the job progress UI. Expect minutes-long jobs on MPS; fp8 never on Metal (bf16 only — see `video_gen/service.py` dtype notes).
4. **Packaged-build smoke gate in release.sh** — the class of bug that burned this project (works in dev, dead in frozen binary) is preventable: build sidecar → boot frozen engine on a test port → load smallest model → generate → assert non-trivial PNG. The full manual procedure is proven and documented in LESSONS.md (hidden-imports section).
5. **aidream-side (separate session, that repo):** `GET /api/ai-tools/app/matrx_local` → 404 (engine loads 0 tools every boot); scraper queue `GET /api/scraper/queue/pending` persistent 500. Both triaged in `CURRENT_ERRORS.md` (T001/T008).
6. **macOS grants UX:** notes sync needs Full Disk Access (now surfaced once, cleanly — but a guided grant flow in the UI would finish it); Screen Recording still shows denied in setup.

## Resources

- **Probe the live packaged engine** (fastest diagnostic loop; no rebuild): `API_KEY=$(grep -m1 '^API_KEY=' .env | cut -d= -f2)` then `curl -H "Authorization: Bearer $API_KEY" http://127.0.0.1:22140/image-gen/status` (port/pid truth: `~/.matrx/local.json`). Generate works via curl — see Current state.
- **Unified log** (all engines + client + tauri interleaved): `~/Library/Logs/MatrxLocal/system.log`. Arman also keeps a curated log export + task list in `CURRENT_ERRORS.md` (repo root — groom it when fixing).
- **Frozen-binary test loop:** `./scripts/build-sidecar.sh` (~4 min) → `MATRX_PORT=22156 TAURI_SIDECAR=1 nohup "./dist/Matrx Engine" &` → probe as above. The engine only deletes `~/.matrx/local.json` it owns (pid-guarded) — still verify after. NEVER run full pytest with a live engine (`tests/conftest.py` kills it — tracked); `pytest tests/smoke` is safe.
- **Key files:** `app/services/image_gen/` + `video_gen/` + `media_gen/` (paths, hardware gating, HF token), `app/services/downloads/manager.py` (`_download_hf_snapshot`, file filtering), `app/api/{image,video}_gen_routes.py`, `desktop/src/hooks/use-media-gen.ts`, `desktop/src/components/media-gen/`, `desktop/src/contexts/MediaGenContext.tsx`.
- **Models on disk:** `~/.matrx/image-models/` — FLUX.2-klein-4B (16GB, downloaded, WORKS) + sdxl-turbo (fp16, works, black-image fix verified). Video models: none yet.
- **Lessons that will bite again:** LESSONS.md — frozen hidden-imports (timeit/modulefinder/filecmp; frozen-load test is the ONLY verification), multiprocessing freeze_support (run.py header comment), HF repos need file filtering (55GB→7GB), MPS: no fp8, no attention-slicing (NaN → black images), always pass guidance_scale.
- Memory: `~/.claude/projects/-Users-armanisadeghi-code-matrx-local/memory/project_media_gen_overhaul.md`.
- Catalog research (July 2026, licenses/sizes verified): images FLUX.2-klein-4B default / Z-Image-Turbo / Qwen-Image (Apache 2.0); video Wan2.2-TI2V-5B / Wan2.1-1.3B / LTX-Video. sd-server (stable-diffusion.cpp) is the researched future migration that would delete the whole torch/pip/frozen-stdlib class — llama-server-style binary + GGUF.

## Done

- Media backend rebuilt: DownloadManager-routed HF downloads (progress/resume/filtering), refreshed catalogs, video-gen service + job API, hardware gating — `app/services/{image_gen,video_gen,media_gen}/`, shipped v1.3.91.
- Rogue-engine root cause fixed + frozen-verified (freeze_support + xet off) — `run.py` header, v1.3.93.
- Engine-URL self-healing + ~35 silent-failure fixes across UI/engine — v1.3.92–94, see git log.
- Frozen stdlib gaps (timeit, modulefinder) fixed + frozen-load-verified; friendly load errors — v1.3.94, LESSONS.md.
- HF token: dual-store gap closed (`read_hf_token()` reads hub store too); gated models name Settings → API Keys → Hugging Face — v1.3.94.
- LLM DM live progress + mmproj DM entries; Analyze timeout/cancel; 60s default request timeout; wake-word SSE progress — v1.3.94.

## Decisions needed (Arman)

- **Automated UI testing needs a login.** Situation: the app is Supabase-login-gated, so agents can only verify the UI by asking you to click; every "works via curl, dead in UI" bug slipped through this gap. Decide: provide a dedicated test account (email+password in `desktop/.env.test`, gitignored) so agents can drive the real UI with Playwright — or accept manual-only UI verification.
- **sd-server migration.** Situation: most of this project's pain (multi-GB pip installs, frozen-binary stdlib gaps, torch upgrades) comes from running diffusers inside the Python engine; stable-diffusion.cpp now ships an sd-server binary (llama-server pattern, GGUF models, image + some video). Decide: green-light a spike replacing the diffusers path with sd-server, or keep investing in the current architecture.
