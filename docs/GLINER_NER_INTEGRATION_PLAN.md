# GLiNER NER Integration Plan

*Status: **URGENT** — pending Arman review before implementation.*  
*Created: 2026-07-09*  
*Context: Cloud NER API volume is a major cost driver. Local NER must become a first-class matrx-local capability.*

---

## Verdict

GLiNER / GLiNER2 can and should ship in matrx-local — but as a **Python NER subsystem** (service + tools + downloads), **not** as entries in the Local Models / llama-server GGUF catalog.

Encoder NER ≠ chat LLM. There is no practical GGUF / llama.cpp path.

---

## Why this is urgent

- NER is a critical product path.
- Cloud API cost scales with volume and is already painful.
- We already have the right host for this: the Python FastAPI sidecar (same home as TTS, wake word, image-gen).
- A small always-on local model can start cutting spend immediately; large/XXL can be opt-in for powerful machines.

---

## What will *not* work

| Existing piece | Works for GLiNER? |
|---|---|
| llama-server / GGUF / Local Models catalog | **No** — encoder NER, not a chat LLM |
| Rust whisper / TTS / LLM inference path | **No** for inference (download manager *can* be reused) |
| Prompting Qwen/Gemma for NER | Possible but **exactly the expensive pattern** we need to leave |

Do **not** put GLiNER next to Gemma/Qwen in the LLM picker. Put it next to TTS / wake-word / tools.

---

## What we already have (reuse)

| Capability | Where | Reuse for NER |
|---|---|---|
| Python FastAPI sidecar | `app/` | Host the NER service |
| Model download + progress UI | `app/services/downloads/` | New category `"ner"` |
| `~/.matrx/` model storage | TTS / wake-word / image-gen | `~/.matrx/ner-models/` |
| Service registry / lifecycle | `app/launcher.py` | Register `"ner"` |
| Hardware probe | `/hardware` | Status + “can load XXL?” |
| Tool dispatcher + cloud sync | `app/tools/dispatcher.py`, `app/tools/catalog.py` | `local_extract_entities` (+ friends) |
| Optional extras pattern | `pyproject.toml` `[transcription]`, `[image-gen]` | New `[ner]` extra |
| Desktop download UX patterns | TTS / LocalModels | Model picker + download for small→large |
| ONNX runtime (already core) | wake-word, TTS | Optional fast path for classic GLiNER later |

**Closest templates to copy**

1. **TTS service** — singleton + download + routes (`app/services/tts/`, `app/api/tts_routes.py`)
2. **Wake-word models catalog** — small model registry (`app/services/wake_word/models.py`)

---

## What must be built new

### 1. NER service (core)

`app/services/ner/` roughly:

- Model catalog (small → XXL)
- Lazy load / unload / hot-swap with a lock
- Extract API: text + labels → spans `{text, label, start, end, score}`
- Chunking for long docs (GLiNER defaults ~384–512 tokens)
- Request queue + batching (**critical for volume**)
- Optional: PII preset label sets, relation extract if we ship GLiNER2

### 2. FastAPI routes

- `GET  /ner/status`
- `GET  /ner/models`
- `POST /ner/download`
- `POST /ner/extract`
- `POST /ner/extract/batch`

### 3. Tools (how agents / cloud hit it)

- `local_extract_entities` — zero-shot NER
- Optionally `local_extract_pii`, `local_extract_relations` (GLiNER2)

### 4. Deps

New optional extra, e.g.:

```toml
[project.optional-dependencies]
ner = ["gliner", "gliner2", "torch", "transformers", "huggingface_hub"]
```

ONNX path can stay lighter later; **PyTorch is the reliable path for small and largest**.

### 5. Desktop (can be phase 2)

A small “Entity Extraction” panel or a section under Local Models that is **not** the LLM list: download small/medium/large, set default, test extract. Not required for agents if tools + API ship first.

### 6. PyInstaller / sidecar packaging

Hidden imports for `gliner` / `gliner2` / torch pieces if NER ships in the compiled sidecar (same rule as other extras — all 4 `.spec` files + `scripts/build-sidecar.sh`).

---

## Model tiers (small → largest)

| Tier | Example | Disk | RAM (rough) | Role |
|---|---|---|---|---|
| **Always-on default** | Knowledgator bi-edge (~60M) or GLiNER small-v2.5 | ~180–600 MB | ~0.5–1.5 GB | High-volume CPU NER |
| **Product default** | **GLiNER2-base** (~205M) | ~834 MB | ~2–4 GB | NER + relations + schema in one |
| **Strong** | GLiNER large / GLiNER2-large / PII-large | ~1.8–2 GB | ~4–8 GB | On-demand quality |
| **Largest local** | **GLiNER XXL v2.5** | **~6.4 GB** | **10–20+ GB** | Optional; powerful machines only |
| Skip | Pioneer XL 1B API-only | — | — | Not offline |

Keep **one active model** resident. Users download larger tiers and swap — same idea as server-grade LLMs, but in the NER subsystem.

For **volume**, the important architectural choice is:

1. **Default = bi-encoder edge/small** (fast with many labels), and/or
2. **GLiNER2-base** if we also need relations / structured extract
3. **XXL as opt-in** for hard docs on big machines — never the auto-default

Prefer **Apache-2.0** weights (v2+, community v2.5, GLiNER2, most Knowledgator). Avoid older CC-BY-NC v0/v1 for commercial shipping.

---

## Architecture

```
Cloud / matrx-extend / agents
        │
        ▼
  local_extract_entities  (tool)
        │
        ▼
  FastAPI /ner/extract[ /batch ]
        │
        ▼
  NER service (Python)  ←── downloads to ~/.matrx/ner-models/
        │
   ┌────┴────┐
   │ small   │  always-on CPU
   │ large   │  on-demand
   │ XXL     │  high-RAM only
   └─────────┘
        │
   NOT llama-server
```

Lifecycle ownership: **Python owns NER models and inference**, same as TTS. Rust does not load them.

---

## Phased effort

| Phase | What ships | Effort |
|---|---|---|
| **P1 — Stop the bleeding** | Service + `/ner/extract` + `/ner/extract/batch` + download small/medium + `local_extract_entities` tool | ~2–4 days |
| **P2 — Production volume** | Queue/batching, chunking, DownloadManager category, registry, capability probe, default model auto-load | ~2–3 days |
| **P3 — Full catalog** | GLiNER2 + PII presets + large/XXL gating + desktop UI | ~3–5 days |
| **P4 — Optional speed** | ONNX export / ORT path for the default small model | ~2–4 days extra |

Total to fully support small through largest in a production-grade way: roughly **1.5–2.5 weeks** of focused work. **P1 alone can start cutting API spend.**

---

## Recommended defaults (pending Arman approval)

1. Build as a first-class **Python NER subsystem** (service + tools + downloads), not an LLM catalog entry.
2. Ship **P1 with a small/bi-edge default** so volume traffic leaves the paid API immediately.
3. Add **GLiNER2-base** as the “full IE” model if relations/structured fields are needed.
4. Support **XXL as a downloadable large tier** with RAM gates.
5. Defer ONNX/C++ until the PyTorch path is proven under real label sets and QPS.

### Decisions needed from Arman

- [ ] Approve building NER as a Python sidecar subsystem (not LLM catalog)
- [ ] Choose default always-on model: **bi-edge** vs **GLiNER2-base** vs both (small default + GLiNER2 on demand)
- [ ] Confirm whether PII presets / relation extraction are in P1 or P3
- [ ] Confirm whether desktop UI is required for P1 or tools+API-only is enough to start
- [ ] Confirm XXL should be offered in-app (with hardware gate) or deferred

---

## Key references

- Classic GLiNER: https://github.com/urchade/GLiNER
- GLiNER2: https://github.com/fastino-ai/GLiNER2
- Community models: https://huggingface.co/gliner-community
- Knowledgator models: https://docs.knowledgator.com/docs/frameworks/gliner/pretrained-models/
- GLiNER.cpp (ONNX, not llama.cpp): https://github.com/Knowledgator/GLiNER.cpp

---

## Related task

Tracked in `.matrx/AGENT_TASKS.md` as **TASK-001** (urgent, needs Arman review before coding).
