# Spike: migrating image generation from diffusers to stable-diffusion.cpp `sd-server`

**Status:** research complete — awaiting go on Wave 1 (hands-on benchmark spike)
**Date:** 2026-07-10
**Author:** research agent (web evidence verified against primary sources on this date)
**Decides:** whether matrx-local replaces the in-engine torch/diffusers image pipeline with a managed `sd-server` binary child (llama-server pattern, GGUF models).

---

## Executive summary and recommendation

**Recommendation: PARTIAL GO.** Run the hands-on Wave-1 benchmark spike now; commit to migration only if the performance gate passes. Do **not** delete the diffusers path in the same release that introduces sd-server.

The one-paragraph verdict:

- **The strategic fit is real and better than expected.** stable-diffusion.cpp is MIT-licensed, releases near-daily (latest 2026-07-10), ships a macOS arm64 binary, supports **every model in our image catalog** (FLUX.2-klein, Z-Image, Qwen-Image, FLUX.1-schnell, SDXL-Turbo — several with GGUFs published by the sd.cpp author himself), and its official `sd-server` exposes an async job API with img2img, LoRA, seeds, and negative prompts. Migrating would delete the entire class of pain this project has suffered: multi-GB runtime pip installs, PyInstaller frozen-stdlib gaps, torch/MPS dtype traps. Download sizes collapse (Qwen-Image: 57.8 GB → ~12–22 GB).
- **Two hard blockers must clear first.** (1) **Parity:** `sd-server` today reports **no per-step progress** and **cannot cancel a running generation** (`"cancel_generating": false` in its own capabilities response; source-verified). We just shipped both. The core C library already has `sd_progress_cb_t` and `sd_cancel_generation()` — the gap is a server-layer wiring issue, upstream-PR-sized, but it is a gap **today**. (2) **Performance on Apple Silicon is unproven and possibly worse:** there are open upstream issues about Metal being significantly slower than CUDA on quantized models, and zero credible head-to-head numbers vs torch+MPS. Our proven baseline (FLUX.2-klein-4B, 1024², ~10.4 s on Arman's machine via MPS bf16) is the bar; if sd.cpp Metal can't get within ~2× of it, the migration dies for FLUX-class models and survives at most as an SDXL-class option.
- **Video: sd.cpp genuinely supports Wan 2.1/2.2 (and an LTX variant) with a `vid_gen` server endpoint** — so "split-stack forever" is not foreordained. But community evidence (82 min for a 2-s Wan 2.2 clip on M1 Max via sd.cpp GGUF) says local video is brutally slow on any stack. Since our diffusers video path has *never run E2E on real hardware either*, video has no parity to lose — evaluate both stacks in the same Wave-1 bench and keep whichever works.

**Sequencing in one line:** Wave 1 benchmark on Arman's machine → if gate passes, add sd-server as an *engine-owned child* behind a per-model backend flag → close the progress/cancel gap (upstream PR preferred) → only then delete the diffusers image path.

---

## 1. stable-diffusion.cpp / sd-server: current state (verified 2026-07-10)

### Project health
- Repo: [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp). **MIT license.** ~6.5k stars, 693 forks, 773 commits, extremely active: rolling `master-NNN-<sha>` releases, **multiple per day in July 2026** (latest observed: `master-773` on 2026-07-10). Recent commits touch Z-Image quantized-matmul fixes, ControlNet hot-reload, safetensors index loading — i.e., active on exactly the models we care about. ([releases](https://github.com/leejet/stable-diffusion.cpp/releases))
- Backends: CPU (AVX/AVX2/AVX512), CUDA, **Metal**, Vulkan, ROCm, OpenCL, SYCL. Built on ggml — same foundation as the llama.cpp/whisper.cpp binaries we already ship.
- Model support (README, 2026-07): SD1.x/2.x/SD-Turbo, SDXL/SDXL-Turbo, SD3/3.5, **FLUX.1-dev/schnell, FLUX.2-dev/klein**, Chroma, **Qwen-Image (+ Edit series)**, **Z-Image**, and more; image-edit models (FLUX.1-Kontext, Qwen-Image-Edit); **video: Wan 2.1/Wan 2.2 and an LTX generation** (README lists "LTX-2.3" — exact LTX lineage vs our `Lightricks/LTX-Video` catalog entry is **unverified; check in Wave 1**). PhotoMaker, ControlNet, TAESD.
- Formats: loads `.safetensors`/`.ckpt` **directly** (GGUF is optional, not required — SDXL-Turbo needs no conversion at all) plus GGUF quants q2_K…q8_0/bf16; has a built-in convert mode.

### Prebuilt binaries (latest release, 2026-07-10)
- **macOS arm64: yes** (Metal). **macOS x86_64: NOT in the release asset list** — Intel Macs would need a CI source build (cmake; llama.cpp precedent says this is routine) or CPU-only operation. Our release CI currently downloads `llama-server` for `x86_64-apple-darwin` from upstream llama.cpp releases, so sd.cpp is strictly worse here today. **Risk, mitigable in CI.**
- Windows: CPU/CUDA12/ROCm. Linux: CPU/ROCm/Vulkan. (Exact asset names for a pinned release to be recorded in Wave 1 when we script `scripts/download-sd-server.sh`.)
- macOS Gatekeeper: same story as llama-server — upstream binaries are ad-hoc signed; we must re-sign with our identity in CI (Hard Rule 7 already covers this; the mechanism exists in `release.yml`).

### `sd-server` (official, in-repo: `examples/server/`)
Source-verified against [`examples/server/api.md`](https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/api.md), [`routes_sdcpp.cpp`](https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/routes_sdcpp.cpp), [`async_jobs.cpp`](https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/async_jobs.cpp) on 2026-07-10:

- **Three API families** on one port: native async **`/sdcpp/v1/*`** (`capabilities`, `img_gen`, `vid_gen`, `jobs/{id}`, `jobs/{id}/cancel`), **OpenAI-compatible** (`POST /v1/images/generations`, `/v1/images/edits`, `GET /v1/models`, `b64_json` output), and **A1111/WebUI-compatible** (`/sdapi/v1/txt2img`, `img2img`, `loras`, `samplers`, …). Plus an embedded web UI.
- **Native `img_gen` request** is rich: `prompt`, `negative_prompt`, `seed`, `width`, `height`, `init_image` + `strength` (img2img), `mask_image` (inpaint), `ref_images`, `control_image`, `batch_count`, `clip_skip`, full `sample_params` (scheduler, sample_method, steps, eta, flow_shift, custom_sigmas, guidance), **`lora[]` with `path` + `multiplier`**, hires-fix block, VAE tiling, `output_format`/`output_compression`.
- **Job model:** single worker thread, sequential FIFO (matches our engine's queue semantics). Job status = `Queued | Generating | Completed | Failed | Cancelled` with timestamps and `queue_position`.
- **The two verified gaps:**
  - **No per-step progress in job status.** Job JSON has no step/fraction/preview fields (`async_jobs.cpp` has no progress state at all).
  - **No mid-flight cancel.** `routes_sdcpp.cpp` literally returns 409 `"job is currently generating and cannot be interrupted yet"` and advertises `"cancel_queued": true, "cancel_generating": false` in capabilities.
  - **However**, the core C API (`include/stable-diffusion.h`) exposes `typedef void (*sd_progress_cb_t)(int step, int steps, float time, void* data)` **and** `SD_API void sd_cancel_generation(sd_ctx_t*, enum sd_cancel_mode_t)` (`SD_CANCEL_ALL` / `SD_CANCEL_NEW_LATENTS` / `SD_CANCEL_RESET`). The primitives exist; the server just hasn't wired them. The word "yet" in the server's own error string suggests upstream intends to. **An upstream PR wiring these two into the server is the single highest-leverage de-risking move of this migration.**
- **One model per process, startup flags only** (`--diffusion-model`, `--vae`, `--llm` text encoder, `--lora-model-dir` implied by lora paths). No runtime model switching. Model switch = process restart — identical to how we already treat llama-server, and roughly equivalent in UX to today's diffusers pipeline load (FLUX.2-klein loads in ~8 s on MPS today; sd-server load time is a Wave-1 measurement).

---

## 2. Model coverage vs our catalog

Our catalog (`app/services/image_gen/models.py`): FLUX.2-klein-4B (default, 16 GB), Z-Image-Turbo (32.9 GB), Qwen-Image (57.8 GB), FLUX.1-schnell (33.8 GB), SDXL-Turbo (7 GB, fp16-filtered). All diffusers-format today.

| Model | sd.cpp support | GGUF publisher(s) | Sizes (diffusion model only) | Notes |
|---|---|---|---|---|
| **FLUX.2-klein-4B** | Yes (FLUX.2-klein listed in README) | **[leejet/FLUX.2-klein-4B-GGUF](https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF)** (the sd.cpp author), also unsloth, QuantStack | 9B sibling: Q4_0 5.62 GB / Q8_0 9.98 GB; **4B exact quant sizes unverified** (expect roughly Q8 ≈ 4–5 GB) | Author-published GGUF = first-class support signal. **Unknown: total download incl. LLM text encoder + VAE GGUFs** — FLUX.2 needs a separate `--llm` text encoder; budget in Wave 1. |
| **Z-Image-Turbo** | Yes (Z-Image listed; [docs/z_image.md](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/z_image.md)) | **[leejet/Z-Image-Turbo-GGUF](https://huggingface.co/leejet/Z-Image-Turbo-GGUF)**, wbruna, unsloth, jayn7 | bf16 → q2_K ladder published; runs in ~4 GB VRAM at aggressive quants | Actively maintained (f16-overflow fix in Z-Image quantized matmuls landed **2026-07-10**). But see perf risk: [issue #1145](https://github.com/leejet/stable-diffusion.cpp/issues/1145) is *specifically* Z-Image slow on M1 Metal. |
| **Qwen-Image** | Yes ([city96/Qwen-Image-gguf](https://huggingface.co/city96/Qwen-Image-gguf), QuantStack) | city96, QuantStack | Q4_0 11.9 GB / Q4_K_M 13.1 GB / Q8_0 21.8 GB (+ Qwen2.5-VL text encoder) | **Biggest download win: 57.8 GB → ~12–22 GB + encoder.** city96 quants are ComfyUI-oriented but standard GGUF; sd.cpp loads them. Qwen-Image-Edit also covered — a capability we don't have today. |
| **FLUX.1-schnell** | Yes (first-class since 2024) | [city96/FLUX.1-schnell-gguf](https://huggingface.co/city96/FLUX.1-schnell-gguf) | Q4_0 6.77 GB / Q8_0 12.7 GB (+ t5xxl + clip_l + ae) | 33.8 GB → ~9–16 GB total. Mature path, widely used. |
| **SDXL-Turbo** | Yes (first-class) | Not needed — sd.cpp loads the fp16 `.safetensors` directly | ~7 GB unchanged (or quantize to ~3.5 GB q8) | Zero conversion risk; the ideal Wave-1 smoke model. |

**Coverage verdict: 5/5.** Notably, three of five have GGUFs published by the sd.cpp maintainer or the dominant community quantizer (city96). No model in our catalog is unsupported.

**What changes for downloads:** GGUFs are single files per component (diffusion model + text encoder(s) + VAE) fetched from HF — simpler than today's snapshot-filtered multi-folder diffusers repos, and they flow through the existing `DownloadManager` HTTP/HF path with exact progress. The 55 GB→7 GB filtering hack class disappears.

---

## 3. Parity matrix — honest accounting

"Diffusers today" = what is live in `app/services/image_gen/` + `/image-gen/*` routes (v1.3.98) plus the assessed-and-greenlit img2img+LoRA work. "sd-server today" = source-verified 2026-07-10.

| Capability | diffusers today (our engine) | sd-server today | Gap severity |
|---|---|---|---|
| txt2img, width/height/steps/guidance | ✅ | ✅ native + OpenAI + A1111 | none |
| Negative prompt | ✅ (per-model gating) | ✅ | none |
| Concrete seeds, reproducible | ✅ (server-generated, always returned) | ✅ `seed` in request; **verify seed echo in job result in Wave 1** | low |
| img2img (`init_image` + strength) | ✅ (just shipped / landing) | ✅ `init_image`, `strength`, plus `mask_image` inpaint we don't have | none — sd.cpp is *ahead* (inpaint, ref_images, kontext/edit models) |
| LoRA | ✅ diffusers `.safetensors` LoRAs | ✅ `lora[] {path, multiplier}`; `/sdapi/v1/loras` | **medium** — works, but known quality degradation applying LoRAs over quantized weights ([discussion #245](https://github.com/leejet/stable-diffusion.cpp/discussions/245), [issue #370](https://github.com/leejet/stable-diffusion.cpp/issues/370), [docs/lora.md](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/lora.md)); community guidance: FLUX LoRAs reliable only at q8_0+; `--lora-apply-mode at_runtime` trades speed for correctness. Mitigation: default LoRA-bearing jobs to Q8/bf16 quants. |
| **Per-step progress (0..1, current_step/total_steps)** | ✅ (`ImageJobResponse.progress`, UI progress bar) | ❌ **absent from job status** | **HIGH — shipped UI regresses** |
| **Mid-flight cancel** | ✅ (next-denoising-step abort, `cancel_requested`, "Cancelling…" UI) | ❌ `"cancel_generating": false`; queued-only cancel | **HIGH — shipped UI regresses** |
| ...but core library primitives | n/a | ✅ `sd_progress_cb_t` + `sd_cancel_generation()` exist in `stable-diffusion.h` | gap is server-layer only → upstream PR or thin fork is feasible; crude interim: kill+respawn child (~model-reload cost per cancel) and/or parse per-step stderr log lines |
| Job queue, priority-next, FIFO | ✅ engine-side | Keep engine-side (sd-server used one-job-at-a-time); its own queue is also sequential | none — queue stays ours |
| `extra_params` advanced JSON | ✅ arbitrary diffusers kwargs, unknown-kwarg fails loudly | ✅ equivalent surface (`sample_params`, `clip_skip`, hires, vae_tiling…) but **different vocabulary** | **medium** — the `/image-gen/params/{model}` contract shape survives, but advanced keys change meaning; saved user presets with diffusers kwargs break. Needs a mapping layer + loud rejection of unknown keys (already our doctrine). |
| Media library persistence, seeds-in-PNG | ✅ engine-side | Engine-side unchanged; sd.cpp even embeds params in PNG metadata natively | none |
| `num_images_per_prompt` / batch | ⚠️ partial (only first image persists — known defect) | ✅ `batch_count` | sd-server slightly ahead |
| Multi-model hot memory | one pipeline loaded at a time | one model per process; switch = restart | none in practice (same UX) |
| Frozen-binary risk class | ❌ the whole pain: runtime pip, hidden imports, torch upgrades | ✅ eliminated (one signed binary, no Python deps) | **the entire motivation** |
| VRAM/RAM footprint | bf16 full weights (16 GB FLUX.2-klein on disk, ~9+ GB RAM) | Q8/Q4 quants, `--offload-to-cpu`, flash attention | sd-server ahead **if** Metal perf holds |
| Video (Wan/LTX) | routes+service exist, **never run E2E on real hardware** | `/sdcpp/v1/vid_gen` (Wan 2.1/2.2, frames/fps/end_image/control_frames; webm/webp/avi out) | no incumbent to regress; both unproven locally — bench both in Wave 1 |

**Summary:** sd-server is at-or-ahead on generation features (img2img, inpaint, batch, edit models) and behind on exactly two things we shipped last week: live progress and mid-flight cancel. Those two are wireable (core API exists) but are **hard gate items** — shipping a Media Generation page whose progress bar goes dead and whose Cancel button stops working is not acceptable regression.

---

## 4. Performance on Apple Silicon — the honest unknown

**There is no credible published head-to-head of sd.cpp-Metal vs torch-MPS for our model class. Treat all optimism here as unverified.** What the evidence does say:

- **Our baseline (primary source, our own logs):** FLUX.2-klein-4B on MPS bf16 — model load ~8 s, 1024×1024 generation 10.4–15.4 s on Arman's machine (v1.3.94 verification, 2026-07-09). This is the number to beat or match.
- **Negative signals, cited:**
  - [Issue #1145](https://github.com/leejet/stable-diffusion.cpp/issues/1145) (Dec 2025): Z-Image Q3 "significantly slower" on M1 Metal than the same settings on NVIDIA — quantized-matmul performance on Metal is a live problem, on one of *our* catalog models.
  - The project's own Python-binding docs note Metal inefficiency on very large matrix ops ([stable-diffusion-cpp-python, PyPI](https://pypi.org/project/stable-diffusion-cpp-python/)).
  - [Issue #1040](https://github.com/leejet/stable-diffusion.cpp/issues/1040): Metal op-coverage gaps (`GGML_OP_DIAG_MASK_INF` unimplemented) — ggml-Metal coverage trails CUDA; missing ops mean CPU fallback or failure depending on model.
- **Positive signals:** ggml Metal is the exact stack our llama-server + whisper already run happily on this hardware; July-2026 commits show active Metal/quant work (f16-overflow fix for Z-Image quantized matmuls, flash-attention flag `--diffusion-fa`); quantized weights mean far less memory traffic, which on unified memory *can* win.
- **Video reality check:** [lilting.ch M1 Max 64 GB test](https://lilting.ch/en/articles/ltx2-wan22-mac-local-video-gen): Wan 2.2 GGUF via sd.cpp-class tooling = **82 minutes for a 2-second clip**; FP8 paths fail on Metal (consistent with what we already learned in `video_gen/service.py`). Local video on Apple Silicon is minutes-to-hours per clip on *any* stack today.

**Conclusion:** performance is decidable only by benchmarking on Arman's machine. That is the core of Wave 1, and the numeric gate is defined in §7.

---

## 5. Integration design for this codebase

### 5.1 Ownership: **engine-owned child** (not Rust-owned)

Recommendation: the Python engine spawns and owns `sd-server`, exactly as it owns cloudflared and the scraper proxy. Justification against the Lifecycle & Ownership rules (CLAUDE.md Hard Rule 0):

- **The feature lives in the engine.** The entire `/image-gen/*` contract — jobs, priority queue, cancel, params, media library, DownloadManager — is engine code. If Rust owned the process, the layer that *proxies every request to sd-server* (Python) would not own the child it depends on, recreating exactly the cross-level reach the rules exist to prevent (the llama-server rule is the same principle mirrored: Rust owns llama-server because Confidential Chat is a Rust-side feature, and Python is forbidden to touch it).
- **Lifecycle cascade stays clean.** Rust signals the engine (`POST /admin/shutdown`); the engine's lifespan teardown stops sd-server along with its other children and reports done only after. No new pkill in `lib.rs`, ever. Add `sd-server` to the launcher registry (`registry.starting/ready/failed/...` — two lines per the contract) so `[launcher] sd-server → ready` lines and diagnostic snapshots come free. The Rust safety-net parachute may add an `sd-server` pattern as a no-op-when-healthy backstop, per existing precedent — not as the primary mechanism.
- **Restart-per-model-switch** (sd-server loads one model at startup) is a natural engine responsibility: `/image-gen/load` becomes "stop child if running, spawn with new `--diffusion-model/--vae/--llm` flags, poll until ready"; `/image-gen/unload` stops the child. Port from the engine's existing scan range (a dedicated sub-range, discovery recorded like other engine children — never hardcoded).
- **GPU-state caveat:** llama-server teardown ordering exists because GGML Metal state must die before app exit. sd-server has the same nature; engine-owned teardown must stop it with the same SIGTERM→SIGKILL ladder discipline during lifespan shutdown.

### 5.2 Binary distribution
Mirror llama-server exactly: `scripts/download-sd-server.sh` pins an upstream release tag per target; CI downloads (arm64-macos, windows, linux) — **plus a cmake source build step for `x86_64-apple-darwin`, which upstream does not prebuild** — re-signs on macOS (Hard Rule 7), ships via `externalBin`. Rust passes the resolved binary path to the engine (env var at sidecar spawn, e.g. `MATRX_SD_SERVER_PATH`), because the engine can't guess the Tauri resources layout. Dev mode: engine falls back to a `~/.matrx/bin/sd-server` path, and per doctrine a missing binary **fails loudly** at `/image-gen/load`, never silently reverts to another backend.

### 5.3 Frontend contract preservation (`/image-gen/*` stays byte-identical in shape)
The swap is confined to the service layer: `ImageGenService` gains a backend that is an HTTP client of the local sd-server child instead of an in-process diffusers pipeline. Everything the frontend sees stays engine-side and unchanged:

- **`/status`, `/models`, `/download`** — unchanged; catalog entries gain `gguf` fields (repo, per-quant files+sizes, text-encoder/VAE component files, chosen quant by RAM tier via existing `media_gen/hardware.py` gating). Downloads go through DownloadManager's existing HF path — single files, exact bytes, resume, no filtering hacks.
- **`/load`, `/unload`** — spawn/stop the child (§5.1). `load_progress` maps to child startup phases (spawn → weights load → HTTP ready-probe).
- **`/generate`, `/jobs` (+priority-next), `/jobs/{id}`, DELETE, `/cancel`** — the engine **keeps its own queue** and submits exactly one native `POST /sdcpp/v1/img_gen` at a time, polling `GET /sdcpp/v1/jobs/{id}`. `ImageJobResponse` shape unchanged. Seeds: pass our server-generated concrete seed in the request (preserving the "always reproducible" guarantee) — verify echo semantics in Wave 1.
- **Progress + mid-flight cancel** — the parity gap (§3). Target state: upstream `sd-server` wires `sd_progress_cb_t` into job status and `sd_cancel_generation` into the cancel route (PR from us if needed; core API already exists). Interim options, in order of preference: (a) parse the child's per-step stderr lines into `progress` (fragile; format-pinned by our smoke test), (b) cancel-by-restart — kill the child mid-generation and respawn (correct semantics, costs a model reload; `mid_flight: false` in `CancelGenerationResponse` already models this degraded mode honestly). **If neither is acceptable to Arman, the migration waits for the upstream wiring. Do not ship a dead progress bar.**
- **`/params/{model_id}`** — same response shape; `advanced` dict now surfaces sd.cpp-native knobs (`sample_params.*`, `clip_skip`, `vae_tiling_params`, hires). **Breaking nuance to disclose:** `extra_params` keys change vocabulary (diffusers kwargs → sd.cpp fields). Unknown keys keep failing loudly with the parameter name. Any saved presets carrying diffusers kwargs must be migrated or rejected loudly.
- **img2img / LoRA** — native `init_image` (b64) + `strength`; `lora[] {path, multiplier}` pointing at files under a `~/.matrx/image-models/loras/` dir. LoRA UX rule from the evidence: when a LoRA is attached, prefer the Q8/bf16 quant of the model (quality cliff on low quants, §3).
- **Media library, vault, lightbox** — untouched; the engine still receives PNG bytes (`b64_json`/result payload) and persists via the existing `media_gen/library.py` path.

### 5.4 What gets deleted at end-state
`app/services/image_gen/installer.py` and the whole `~/.matrx/image-gen-packages/` runtime-pip mechanism, the image-gen share of frozen hidden-import risk, torch/MPS dtype gating for images, `/image-gen/install*` routes (contract removal — coordinate with frontend), and multi-GB torch from the optional extras. Video keeps diffusers until §6 resolves. Per doctrine: deletion is total when it happens — no dormant fallback path.

---

## 6. Video: split-stack or not?

- sd.cpp **does** have a video path: Wan 2.1/2.2 (T2V/I2V, `moe_boundary` for 2.2's dual-model MoE, VACE controls) and an LTX entry, served via `POST /sdcpp/v1/vid_gen` (webm/webp/avi out). So the "sd.cpp for images, diffusers for video, forever" split is **not** structurally forced.
- But: local video on Apple Silicon is not real-time on any stack (82 min / 2 s Wan 2.2 clip, M1 Max, cited §4), fp8 is off the table on Metal everywhere, and **our diffusers video path has itself never run E2E on hardware** (open item #3 in the handoff). There is no incumbent to protect.
- **Recommendation:** decide video *empirically* in Wave 1 — run one Wan 1.3B/5B job on both stacks on the same machine. If sd.cpp's vid_gen works at comparable-or-better wall time, the end-state is single-stack (sd-server for both) and the entire torch dependency eventually leaves the sidecar — the maximal prize. If it doesn't, video stays diffusers and we accept the split; the image migration's value stands on its own either way. LTX support lineage (README's "LTX-2.3" vs our `Lightricks/LTX-Video`) is **unverified — treat as unknown**.

---

## 7. Risks, kill criteria, and staged plan

### Risks (ranked)
1. **Metal performance on quantized DiT models** (Z-Image issue #1145 class). Could kill the whole thing for FLUX/Z/Qwen while leaving SDXL-class fine. → measured in Wave 1, gated below.
2. **Progress/cancel regression** — shipped UI features. → hard gate; upstream-PR path identified (core API exists); interim modes defined (§5.3); if all rejected, migration waits.
3. **LoRA quality on quants** — known upstream caveat. → policy: LoRA ⇒ Q8/bf16 quant; verify visually in Wave 1.
4. **Rolling releases, no semver** — `master-NNN` daily tags mean we must pin a tested tag in `download-sd-server.sh` and bump deliberately (same discipline as the pinned llama.cpp build `b8377`).
5. **macOS x86_64 not prebuilt upstream** — CI source-build required for Intel targets; or accept CPU-only there. Small, but it's release-pipeline work.
6. **FLUX.2-klein-4B component sizing unknown** — exact GGUF sizes for the 4B diffusion model + required LLM text encoder + VAE are unverified; total download could erode the size win. → Wave-1 measurement.
7. **Server maturity** — `sd-server` is an in-repo example, embedded-frontend synced "every 1–2 weeks"; API surface could shift under us. Pinning + a contract smoke test against the pinned binary contains this.

### Kill criteria (any one stops the migration for the affected scope)
- FLUX.2-klein-4B (Q8) 1024², recommended steps, on Arman's machine: **> 2× slower end-to-end than the 10.4 s diffusers baseline** → no-go for FLUX-class; re-scope to SDXL-class-only or full no-go.
- Black/garbage images or crashes on ≥1 catalog model at its chosen quant that upstream can't/won't fix within the spike window.
- Progress + mid-flight cancel unachievable via upstream PR *and* both interim modes rejected → migration parked (revisit when upstream wires the existing core primitives).
- LoRA output at Q8 visibly degraded vs diffusers on the same LoRA/seed → LoRA jobs stay on diffusers → the "delete torch" prize dies → likely full no-go (a permanent dual stack forfeits most of the win).

### Staged plan (agent-waves)
- **Wave 1 — hands-on spike (1 wave, no product code).** Download pinned macOS arm64 binary; run sd-server manually with (a) SDXL-Turbo safetensors, (b) FLUX.2-klein-4B GGUF Q8 (+ encoder/VAE — record real total download), (c) Z-Image-Turbo Q8. Benchmark vs the live diffusers engine on the same machine (load time, s/image at catalog defaults, peak RSS). One img2img + one LoRA (Q8) visual check. One Wan vid_gen attempt (§6). Record seed-echo behavior. Output: numbers appended to this doc → go/no-go against the gates.
- **Wave 2 — parity gap upstream (1 wave, parallel-able with Wave 3).** PR to leejet/stable-diffusion.cpp wiring `sd_progress_cb_t` into job status + `sd_cancel_generation` into the cancel route (flip `cancel_generating` to true). Fallback if unmergeable: carry the patch in our CI build.
- **Wave 3 — engine backend (2 waves).** `sd-server` child under launcher registry + lifecycle teardown; download script + CI (incl. macOS x64 source build + re-sign); catalog GGUF fields + DownloadManager wiring; `ImageGenService` sd-server backend behind a per-model `backend` field; params mapping; contract smoke test against the pinned binary; packaged-build smoke gate (handoff item #4 — cheaper here than on diffusers: no hidden-imports class at all).
- **Wave 4 — cutover + deletion (1 wave, only after gates green + Arman sign-off).** Flip catalog models to sd-server, delete installer/runtime-pip/torch-image path and `/image-gen/install*` (frontend coordinated), update FEATURE.md/handoff. Video per §6 outcome.

**Total: ~4–5 agent-waves** to full image-gen migration, with Wave 1 cheap and decisive, and every wave individually abortable.

---

## Sources

Primary (verified 2026-07-10): [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) (README: models/backends/MIT), [releases](https://github.com/leejet/stable-diffusion.cpp/releases) (cadence, macOS arm64 assets), [`examples/server/README.md`](https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/README.md), [`examples/server/api.md`](https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/api.md) (endpoints/params), [`examples/server/routes_sdcpp.cpp`](https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/routes_sdcpp.cpp) (`cancel_generating: false`, 409 string), [`examples/server/async_jobs.cpp`](https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/async_jobs.cpp) (no progress state, single worker), [`include/stable-diffusion.h`](https://github.com/leejet/stable-diffusion.cpp/blob/master/include/stable-diffusion.h) (`sd_progress_cb_t`, `sd_cancel_generation`), [docs/lora.md](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/lora.md), [docs/z_image.md](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/z_image.md).
Models: [leejet/FLUX.2-klein-4B-GGUF](https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF), [leejet/FLUX.2-klein-9B-GGUF](https://huggingface.co/leejet/FLUX.2-klein-9B-GGUF), [leejet/Z-Image-Turbo-GGUF](https://huggingface.co/leejet/Z-Image-Turbo-GGUF), [city96/Qwen-Image-gguf](https://huggingface.co/city96/Qwen-Image-gguf), [QuantStack/Qwen-Image-GGUF](https://huggingface.co/QuantStack/Qwen-Image-GGUF), [city96/FLUX.1-schnell-gguf](https://huggingface.co/city96/FLUX.1-schnell-gguf).
Parity/perf caveats: [discussion #245 (LoRA+quant)](https://github.com/leejet/stable-diffusion.cpp/discussions/245), [issue #370](https://github.com/leejet/stable-diffusion.cpp/issues/370), [issue #1145 (Z-Image slow on M1 Metal, Dec 2025)](https://github.com/leejet/stable-diffusion.cpp/issues/1145), [issue #1040 (Metal op gap)](https://github.com/leejet/stable-diffusion.cpp/issues/1040), [stable-diffusion-cpp-python PyPI notes](https://pypi.org/project/stable-diffusion-cpp-python/), [lilting.ch Wan2.2/LTX on M1 Max](https://lilting.ch/en/articles/ltx2-wan22-mac-local-video-gen).
Internal: `docs/handoffs/image-video-generation.md` (baseline perf, catalog), `app/services/image_gen/models.py`, `app/api/image_gen_routes.py` (contract), `desktop/src-tauri/src/lib.rs` + `.github/workflows/release.yml` (llama-server pattern).
Secondary/derived (used for orientation, claims re-verified against primary where load-bearing): [DeepWiki server-api page](https://deepwiki.com/leejet/stable-diffusion.cpp/5.2-server-api).
