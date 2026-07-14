# Matrx Local — CDN Assets Plan

> **Purpose.** Move matrx-local's model weights and heavy binaries onto our own
> AWS bucket → public CDN (a new `matrx-local/` asset prefix), so downloads are
> fast, reliable, and not at the mercy of HuggingFace / GitHub rate limits or
> outages. Also **slim the installer** by shipping fewer bytes and fetching the
> rest on first use.
>
> **Status:** planning doc (2026-07-14). Produced from a full code audit of the
> catalogs, the installer bundle, and the download plumbing. Nothing here is
> wired yet — the "planned CDN paths" in older docs and ARMAN_TASKS were never
> implemented.
>
> **Config posture (CLAUDE.md § Security & configuration posture):** the CDN
> base URL is a **non-secret runtime value** → it belongs in **remote app
> config** (anon-readable, compiled-in public default), NOT an env var. All CDN
> assets are **public** (durable, anonymous-readable) — never signed/expiring
> URLs. No secret is ever attached to a CDN request.

---

## 1. How downloads work today (the seams)

Every asset is fetched **direct from HuggingFace `resolve/main`** (or GitHub for
Kokoro TTS, Civitai for some LoRAs). There is **no mirror or CDN indirection in
code** — `assets.aimatrx.com` appears only in stale docs.

- **Python** `app/services/downloads/manager.py` already accepts arbitrary
  `urls=[...]`. A plain CDN URL "just works" through the generic path and — via
  the `_is_hf_url` allowlist (`manager.py:306`) — correctly attaches **no** auth
  header to a non-HF host. **No manager change needed for a plain CDN download.**
- **Rust** `desktop/src-tauri/src/downloads/manager.rs:1144` attaches the HF
  Bearer token to **every** URL unconditionally. ⚠️ **Must add an is-HF host
  check before routing CDN URLs through it**, or we leak the token to our own CDN.
- **No CDN→HF fallback semantics exist.** In both managers, multiple URLs =
  sequential **parts of one file**, not alternate mirrors. CDN-first-then-HF
  fallback is net-new logic (do it in the caller, or add per-part fallback).

**Recommended single point of indirection:** one `ASSETS_CDN_BASE` constant in
`app/config.py` (near `MATRX_FILES_URL`, line ~157) for Python + a matching Rust
`const` (mirror `transcription/downloader.rs`'s pattern). Sourced from remote
app config, public default `https://assets.aimatrx.com`. Each catalog computes
`{CDN_BASE}/matrx-local/<category>/...` with the existing HF URL retained as
fallback.

### Code seams to touch (per asset type)

| Asset | Seam (file:line) |
|---|---|
| GGUF LLM (Rust) | `llm/model_selector.rs` `hf_url`/`hf_parts` + `all_part_urls()` (~87/177); consumers `llm/commands.rs:1183` |
| Whisper (Rust) | `transcription/downloader.rs:8-9` base consts + fallback in `download_model`/`try_download` (42-115) |
| Rust token leak | `downloads/manager.rs:1144` — add is-HF host guard |
| Image/Video/NER/wake-word/LoRA (Python) | callers building `urls=`; `wake_word/models.py:27`, `setup_routes.py:870`, image/video `models.py` |
| llama-server (build-time) | `scripts/download-llama-server.sh:38-40` + `llama_asset_for_triple()` (~87) |
| Config constant | `app/config.py:~157` (`ASSETS_CDN_BASE`) + Rust const |

---

## 2. The bucket layout (proposed)

New prefix `matrx-local/` under our existing assets bucket (auto-served by the
current public CDN). Version binaries by their upstream version so old clients
keep resolving.

```
matrx-local/
  llm-models/<repo-owner>/<repo-name>/<filename>.gguf   # mirror HF path 1:1
  image-models/<repo-id>/...                            # diffusers repo tree
  video-models/<repo-id>/...
  ner-models/<repo-id>/...
  whisper-models/ggml-*.bin, ggml-silero-*.bin
  wake-word/hey_matrix.onnx  (+ stock oww models)
  loras/<family>/<name>.safetensors
  tts/kokoro-v1.0.onnx, voices-v1.0.bin
  llama-server/v<VERSION>/<triple>/...                  # binary + dylibs/DLLs
  cloudflared/<version>/<triple>/cloudflared
  ffmpeg/<version>/<triple>/ffmpeg
```

---

## 3. Priority A — installer slimming (bundled → download-on-install)

These ship **inside the installer today** and already have (or trivially reuse)
download tooling. Moving them to first-run/optional CDN download is the biggest
UX win. Target: **~150–200 MB** off the installer.

| Asset | Now | Size | Move to CDN? | Notes / seam |
|---|---|---|---|---|
| **cloudflared** | bundled externalBin | ~36 MB | **YES (strong)** | only needed if tunneling; `tauri.conf.json:45` |
| **llama-server + llama.cpp dylibs/DLLs** | bundled resources | ~50–90 MB | **YES (cleanest)** | download machinery already exists (`download-llama-server.sh`); dylibs tripled by 3-variant rpath copies |
| **ffmpeg** (imageio-ffmpeg) | frozen in sidecar | ~48 MB | **YES** | `build-sidecar.sh:210`; fetch on first media use |
| **onnxruntime** | frozen in sidecar | ~64 MB | maybe | needed for TTS/wake-word/NER — bundle in a "voice/AI" optional pack, not core |
| **espeakng_loader** (+dicts) | frozen in sidecar | ~20 MB | maybe | Kokoro TTS only |
| **tessdata** (OCR) | bundled if on build host | 15–30 MB/lang | YES | often already skipped |

### Stale artifacts to verify & purge (NOT necessarily shipped — confirm in CI)
- `desktop/src-tauri/binaries/llama-b8377-bin-macos-x64.tar.gz` — **93 MB** dead
  archive; dylibs are `b9076`, this tarball is `b8377`. If a `resources` glob
  catches it, it ships dead weight.
- `desktop/src-tauri/sidecar/aimatrx-engine-aarch64-apple-darwin` — **215 MB**
  old-named flat sidecar next to the current `Matrx Engine.app`. Active
  externalBin is `sidecar/matrx-engine` / the `.app` — this looks stale.
  → File as a FOUND_DEFECTS cleanup once confirmed with a real build.

### Already correctly download-on-demand — DO NOT re-touch
Whisper `.bin`, Kokoro TTS `.onnx`, wake-word models, torch/CUDA (excluded),
and all image/video ML weights are already runtime-fetched into `~/.matrx/`.
The CDN work for these is **repointing the source URL**, not changing *when*
they download.

---

## 4. Priority B — model catalog mirror (the bucket-upload list)

Full inventory the CDN should host. **Bold = default/recommended** (upload
first; these are what most users pull). All are HF `resolve/main` today unless
noted. Sizes as stated in code.

### 4.1 LLM / text (GGUF) — `llm-models/`
Default is hardware-selected. Priority uploads = the tiers most machines land on:
- **Qwen3.5-9B** `unsloth/Qwen3.5-9B-GGUF` Q4_K_M 5.7 GB (+`mmproj-F16.gguf`) — **primary default**
- **Qwen3.5-4B** `unsloth/Qwen3.5-4B-GGUF` Q4_K_M 2.7 GB (+mmproj) — **small-machine default**
- Qwen3.5-2B 1.3 GB, Phi-4-mini 2.5 GB, gemma-4-E2B/E4B 3.1/5.0 GB, Llama-3.1-8B 4.9 GB, DeepSeek-R1-0528-Qwen3-8B 5.0 GB, GLM-Z1-9B 6.2 GB, gemma-4-12B 7.1 GB
- Larger/desktop-class (upload as space allows): gemma-4 26B/31B, Qwen3.5-27B/35B, Qwen3-Coder-30B, GLM-4.7-Flash, Mistral-Small-3.1-24B, Devstral-24B, gpt-oss-20b (+ server-tier 70B–397B on demand)
- **mmproj**: each multimodal repo ships `mmproj-F16.gguf` — mirror alongside.
- Full 30+ row table with repo ids, filenames, quants, line numbers: see the
  catalog `desktop/src-tauri/src/llm/model_selector.rs`.
- Note: users can also paste ANY HF/`.gguf` URL (dynamic, `model_repo_routes.py`) — those bypass the mirror by design.

### 4.2 Image — `image-models/` (diffusers full repos)
- **`black-forest-labs/FLUX.2-klein-4B`** 16 GB — **DEFAULT**
- `Tongyi-MAI/Z-Image-Turbo` 33 GB · `stabilityai/sdxl-turbo` 7 GB (fp16 only) ·
  `black-forest-labs/FLUX.1-schnell` 34 GB (**gated — needs user HF token**, mirror the public files only) · `Qwen/Qwen-Image` 58 GB

### 4.3 Video — `video-models/` (diffusers full repos)
- **`Wan-AI/Wan2.2-TI2V-5B-Diffusers`** 34 GB — **DEFAULT**
- `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` 29 GB · `Lightricks/LTX-Video` 28 GB

### 4.4 Whisper — `whisper-models/`
- **`ggml-base.en.bin`** 142 MB — **shipped default** · `ggml-tiny.en.bin` 75 MB (also used by wake word) · `ggml-small.en.bin` 466 MB · VAD `ggml-silero-v6.2.0.bin` (from `ggml-org/whisper-vad`)

### 4.5 NER / GLiNER — `ner-models/`
- **`fastino/gliner2-base-v1`** 850 MB — **DEFAULT** · gliner2-large 1.8 GB · gliner-bi-edge 600 MB · gliner-community small/large/xxl (600 MB / 1.9 GB / 6.5 GB)

### 4.6 Wake word — `wake-word/`
- **`hey_matrix.onnx`** ~3 MB — **our custom model** (see § 5; being trained now).
  This is the strongest CDN candidate: tiny, ours, and voice is opt-in.
- Stock oww fallbacks from `davidscripka/openWakeWord`: hey_jarvis, alexa (bundled today), hey_mycroft, ok_nabu.

### 4.7 TTS — `tts/` (GitHub today, not HF)
- **`kokoro-v1.0.onnx`** ~310 MB + `voices-v1.0.bin` ~27 MB (54 voices inside).
  From `thewh1teagle/kokoro-onnx` GitHub release — mirror to CDN.

### 4.8 LoRAs — `loras/`
- HF (mirrorable): LCM-LoRA-SDXL, Pixel-Art-XL, Toy-Face, FLUX-RealismLora, FLUX.1-Turbo-Alpha.
- 8 Z-Image LoRAs use `civitai:<id>@<ver>` — **Civitai direct, not HF**; mirror from Civitai if desired.

---

## 5. Implementation phasing

1. **Bucket + upload** (operator): create `matrx-local/` prefix; upload the
   **bold defaults** first (§4), then Priority-A binaries (§3).
2. **Config seam**: add `ASSETS_CDN_BASE` (Python `config.py` + Rust const),
   sourced from remote app config, public default. No secret.
3. **Repoint catalogs**: compute CDN URL, keep HF/GitHub as **fallback**
   (net-new fallback logic — see §1). Fix the Rust token-leak guard first.
4. **Installer slim**: drop cloudflared + llama-server/dylibs + ffmpeg from the
   bundle; fetch on first use via the DownloadManager (with proactive
   notify+deep-link UX per the "Proactive prompts" task). Verify with
   `./scripts/smoke.sh packaged`.
5. **Purge stale artifacts** (§3) after a real build confirms they're unused.

Each phase is independently shippable. Phase 1+3 for the default models alone
already delivers most of the reliability win.
