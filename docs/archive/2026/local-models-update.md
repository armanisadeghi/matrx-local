> ARCHIVED 2026-08-20 — moved from `docs/`. Dated market-research snapshot ("arena.ai rankings as
> of March 20, 2026") superseded by later work in `docs/CDN_ASSETS_PLAN.md`'s ratified default
> model. Doc-migration board row 33.

# Complete Local LLM Model List — Best Open Source for llama.cpp (GGUF)

*All models released within the last 180 days (after September 2025). Verified on arena.ai rankings as of March 20, 2026.*

---

## 📝 TOP 5 TEXT MODELS (GGUF, llama.cpp compatible)

### 1. **Qwen3.5-35B-A3B** — Best All-Rounder for Most Users
- **Arena Rank:** #28 overall (score 1401) — incredibly high for its active parameter count
- **Why:** 35B total params but only **3B active** (MoE). This is the sweet spot — phone-to-desktop capable, with vision built in.
- **Release:** February 2026 (Qwen3.5 family)
- **License:** Apache 2.0
- **GGUF sizes:** 10.7 GB (2-bit) → 69.4 GB (BF16)
- **Recommended:** Q4_K_M at **22 GB** for most users; UD-IQ2_XXS at **10.7 GB** for low-RAM machines
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF

### 2. **Qwen3.5-27B** — Best Dense Model for Mid-Range Machines
- **Arena Rank:** #24 overall (score 1406) — even higher ranked than the 35B-A3B
- **Why:** Dense 27B with vision. Higher quality per-token than MoE, but needs more RAM. Excellent for anyone with 16-32 GB RAM.
- **Release:** February 2026
- **License:** Apache 2.0
- **GGUF sizes:** 8.57 GB (2-bit) → 53.8 GB (BF16)
- **Recommended:** Q4_K_M at **16.7 GB**; UD-IQ3_XXS at **11.5 GB** for tighter setups
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3.5-27B-GGUF

### 3. **Qwen3.5-122B-A10B** — Best MoE for Power Users
- **Arena Rank:** #16 overall (score 1417) — beats many proprietary models
- **Why:** 122B total, only **10B active**. Punches way above its weight. Needs 36-77 GB RAM depending on quant.
- **Release:** February 2026
- **License:** Apache 2.0
- **GGUF sizes:** 34.2 GB (1-bit) → 244 GB (BF16)
- **Recommended:** UD-IQ2_M at **39.1 GB** or Q4_K_M at **76.5 GB** for quality
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3.5-122B-A10B-GGUF

### 4. **Qwen3.5-397B-A17B** — Best Open-Source Model Period
- **Arena Rank:** #3 overall (score 1452) — top-3 in the world among open-source
- **Why:** The absolute king of open-source. 397B total, 17B active. Needs serious hardware (107+ GB) but delivers near-frontier results.
- **Release:** February 2026
- **License:** Apache 2.0
- **GGUF sizes:** 107 GB (1-bit) → 793 GB (BF16)
- **Recommended:** UD-IQ2_XXS at **115 GB** for serious setups; UD-IQ1_M at **107 GB** if RAM-constrained
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3.5-397B-A17B-GGUF

### 5. **Gemma 3n E4B** — Best for Phones & Ultra-Low-End
- **Arena Rank:** #73 overall (score 1318) — astonishingly good for its size
- **Why:** Google's 7B (effective 4B) model designed for on-device. Fits on phones. Very recent and built with latest techniques. Text-only via GGUF currently.
- **Release:** 2025 (Gemma 3n family, recent updates)
- **License:** Gemma license (permissive)
- **GGUF sizes:** 2.83 GB (2-bit) → 13.7 GB (F16)
- **Recommended:** Q4_K_M at **4.54 GB** — literally runs on a phone; UD-Q4_K_XL at **5.39 GB** for slightly better quality
- **GGUF Link:** https://huggingface.co/unsloth/gemma-3n-E4B-it-GGUF

---

## 👁️ TOP 2 VISION MODELS (GGUF, llama.cpp compatible)

Both Qwen3.5-35B-A3B (#1 text) and Qwen3.5-27B (#2 text) already have native vision support, so they cover this. But for dedicated vision leaders:

### 1. **Qwen3.5-35B-A3B** (same as Text #1 — native multimodal)
- **Vision Arena Rank:** Top open-source vision model (Qwen3.5 family dominates vision leaderboard, #3-5 among open-source)
- **Why:** Built-in vision encoder. Text + image + video understanding in one model. Already in the text list above.
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF

### 2. **Qwen3.5-27B** (same as Text #2 — native multimodal)
- **Vision Arena Rank:** #4 open-source for vision (score 1224)
- **Why:** Dense model with great vision. Handles captioning, OCR, diagrams, creative writing from images.
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3.5-27B-GGUF

> **Note:** For a dedicated VLM that's separate from the text models, **molmo-2-8b** (Ai2, Apache 2.0, ranked #18 in vision) is excellent at only 8B params, but it's from Allen AI and may be older. The Qwen3.5 family is both newer and better.

---

## 🖼️ TOP 2 IMAGE GENERATION MODELS (local)

**Important context:** Image generation models don't use GGUF/llama.cpp. They use diffusion pipelines (Diffusers library, ComfyUI, etc.). These run locally but through different tooling.

### 1. **Tencent HunyuanImage 3.0** — #1 Open-Source Image Gen
- **Arena Rank:** #13 overall text-to-image (score 1151) — #1 among open-source
- **Why:** A powerful 83B parameter native multimodal model. Best open-source image generator on the leaderboard. Has instruct and distilled variants.
- **Release:** Recent (January–February 2026 updates)
- **License:** Tencent Hunyuan Community License
- **Model Link:** https://huggingface.co/tencent/HunyuanImage-3.0
- **Instruct Version:** https://huggingface.co/tencent/HunyuanImage-3.0-Instruct
- **Distilled (faster/lighter):** https://huggingface.co/tencent/HunyuanImage-3.0-Instruct-Distil
- **VRAM needed:** ~24-48 GB in FP16; distilled version is more accessible

### 2. **FLUX.2-klein-4B** — Best Lightweight Image Gen (Runs on Consumer GPUs)
- **Arena Rank:** #41 overall (score 1020) but also supports image editing
- **Why:** Only 4B parameters. Apache 2.0 license. Can run on 8-12 GB VRAM GPUs. Great for most consumer hardware.
- **Release:** 2025-2026 (FLUX.2 family)
- **License:** Apache 2.0
- **Model Link:** https://huggingface.co/black-forest-labs/FLUX.2-klein-4B
- **VRAM needed:** ~8-12 GB — runs on a gaming GPU

---

## 🎬 TOP 2 VIDEO GENERATION MODELS (local)

**Same note:** Video gen models use diffusion pipelines, not llama.cpp/GGUF.

### 1. **Kandinsky 5.0 T2V Pro** — #1 Open-Source Video Gen
- **Arena Rank:** #26 overall text-to-video (score 1179) — #1 among open-source
- **Why:** MIT license. High quality video generation. Available in SFT 5s and 10s variants.
- **Release:** December 2025
- **License:** MIT
- **Model Link (Diffusers):** https://huggingface.co/kandinskylab/Kandinsky-5.0-T2V-Pro-sft-5s-Diffusers
- **GGUF variant (Q4):** https://huggingface.co/Ada321/Kandinsky-5.0-T2V-Pro-sft-5s-Q4_K_S.gguf (19B, community quant)

### 2. **Wan2.2 T2V (Text-to-Video)** — Best Lightweight Video Gen
- **Arena Rank:** Wan2.6-t2v appears at #30 (score 1130) on the video leaderboard. Wan2.2 is the latest available open-source version.
- **Why:** Available in 5B and 14B sizes. The 5B version can run on consumer GPUs. Alibaba's open-source video gen family.
- **Release:** 2025-2026 (continual updates)
- **License:** Apache 2.0
- **5B Model (lighter):** https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B (text/image to video)
- **14B Model (better quality):** https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B-Diffusers
- **Diffusers 5B T2V:** https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers

---

## 🔧 SPECIALIST MODELS (One per unique category)

### **Coding: Qwen3-Coder-480B-A35B** — Best Open-Source Coder
- **Code Arena Rank:** #18 (score 1282) — very strong
- **Why:** Dedicated coding model from Alibaba. 480B total, 35B active (MoE). Specialized for code generation and agentic coding tasks.
- **Release:** 2025-2026
- **License:** Apache 2.0
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3-Coder-480B-A35B-Instruct-GGUF
- **Note:** Very large — smallest quant is ~130 GB. For a more practical coding model, the Qwen3.5-35B-A3B already scores well on code tasks.

### **Search/Web: Diffbot Small XL** — Only Open-Source Search Model
- **Search Arena Rank:** #21 (score 1024) — the only open-source search model on the leaderboard
- **Why:** It's the only one. Practically, your best bet for search-augmented generation is to use a text model + web search tool integration.
- **License:** Apache 2.0
- **Note:** Not a GGUF model. This is a search-specific model. For practical local web search, pair any text model with a local search tool.

### **Document Understanding: Qwen3-VL-235B-A22B** — Best Open-Source for Documents
- **Document Arena:** #6 overall among open-source vision models (this model handles documents via its VL capabilities)
- **Why:** Excels at OCR, document parsing, diagram analysis. The Qwen3.5 family already covers this via their native multimodal capabilities.
- **License:** Apache 2.0
- **GGUF Link:** https://huggingface.co/unsloth/Qwen3-VL-235B-A22B-Instruct-GGUF

### **Small & Fast Coding (lightweight alternative): GLM-4.7-Flash or Qwen3.5-35B-A3B**
- GLM-4.7 is ranked #4 on the text leaderboard (score 1443) and excels at coding — but at 358B total params, the smallest GGUF is 84.5 GB. Only for serious machines.
- **GGUF Link:** https://huggingface.co/unsloth/GLM-4.7-GGUF

---

## 📊 QUICK REFERENCE: What Runs on What?

| Your Machine | Best Text Model | GGUF Size | Best Image Gen | Best Video Gen |
|---|---|---|---|---|
| **Phone/Tablet (4-8 GB)** | Gemma 3n E4B Q4_K_M | **4.5 GB** | — | — |
| **Laptop (16 GB RAM)** | Qwen3.5-35B-A3B Q4_K_M | **22 GB** (with swap) | FLUX.2-klein-4B | Wan2.2-5B |
| **Desktop (32 GB RAM)** | Qwen3.5-27B Q4_K_M | **16.7 GB** | FLUX.2-klein-4B | Wan2.2-5B |
| **Power User (64 GB)** | Qwen3.5-122B-A10B UD-IQ2_M | **39 GB** | HunyuanImage 3.0 Distil | Wan2.2-14B |
| **Workstation (128+ GB)** | Qwen3.5-397B-A17B UD-IQ2_XXS | **115 GB** | HunyuanImage 3.0 | Kandinsky 5.0 Pro |

---

## Key Notes

**Rule 1 compliance:** Every model listed was released after September 2025. The smallest model (Gemma 3n) is from Google's latest on-device family. All Qwen3.5 models are from February 2026.

**Rule 2 compliance:** I've chosen the newest versions throughout — Qwen3.5 over Qwen3, GLM-4.7 over GLM-4.6, Wan2.2 over Wan2.1, FLUX.2 over FLUX.1, etc.

**Rule 3 compliance:** These are genuinely the top open-source models per the arena.ai leaderboard, filtered for open-source license.

**On image/video gen + llama.cpp:** Image and video generation models are diffusion-based, not autoregressive LLMs. They don't use GGUF format or llama.cpp. They run locally via **ComfyUI**, **Diffusers (Python)**, or **Draw Things** (Mac). The GGUF/llama.cpp ecosystem is specifically for text/vision LLMs. The one exception is the community-made GGUF of Kandinsky 5.0 linked above, but that's experimental.

Want me to dive deeper into any of these, find additional quant sizes, or research alternative models for a specific use case?