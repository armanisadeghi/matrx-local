---
status: active
updated: 2026-07-14
repos: [matrx-local]
owner-context: local GLiNER / GLiNER2 NER subsystem for cutting cloud NER API spend
---

# GLiNER local NER — handoff

## Done

- Built the Python-owned NER subsystem with catalog, lazy load/unload, snapshot download, long-document chunking, PII preset, and structured spans — `app/services/ner/service.py`, `app/services/ner/models.py`.
- Added FastAPI routes for status, models, download, extract, and batch extract — `app/api/ner_routes.py`, mounted in `app/main.py`.
- Added the managed optional runtime install path for packaged builds instead of bundling torch into every sidecar — `app/services/capabilities/installer.py`, `app/api/capabilities_routes.py`, `pyproject.toml`.
- Registered local tools and cloud names `local_extract_entities` / `local_extract_pii` — `app/tools/tools/ner.py`, `app/tools/arg_models/ner_args.py`, `app/tools/dispatcher.py`, `app/tools/catalog.py`.
- Applied and verified the Supabase tool registry rows for both NER tools; left the tracked changeset as a no-op applied record to prevent replay — `.matrx/tool_registry_changeset.sql`.
- Updated tool registry pins and characterization coverage for the 115-tool catalog — `tests/characterization/tool_surface_snapshot.json`, `tests/characterization/test_tool_registry_shape.py`, `tests/parity/test_tool_count.py`.
- Added smoke/characterization tests for route shape, model catalog default, validation, span normalization, partial-download detection, schema union handling, and managed installer wiring — `tests/smoke/test_ner.py`, `tests/characterization/test_ner_service.py`.
- Added PyInstaller hidden imports for the NER tool module so frozen builds advertise the tools and fail gently until the optional runtime is installed — `scripts/build-sidecar.sh`, `specs/matrx-engine-*.spec`.

## Remaining Work

### Remaining work — verification

1. **End-to-end local model drill.** Install the NER capability (`POST /capabilities/install` with `{"capability_id":"ner"}` or Settings UI), download `gliner2-base`, run `/ner/extract` and `ExtractEntities` against real text, and verify returned offsets against the original request body. This was not exercised with a real GLiNER model in this pass.
2. **Packaged-build drill.** Build a frozen sidecar, install the NER capability into the managed package dir, restart, verify startup injects `ner-packages`, then load `gliner2-base` and run one extraction. Current tests prove the code path and installer wiring, not the packaged runtime.

### Remaining work — implementation

3. **Desktop UX.** Add an Entity Extraction panel or Local Models-adjacent section that is not the LLM picker: show `/ner/models`, install NER runtime, download/swap models, hardware gate XXL, and test extraction. Delete no code for this; it is new UI work.
4. **Production batching/queue.** `/ner/extract/batch` currently loops sequentially through `extract()`. Add the planned request queue and model-level batching once real traffic/QPS is measured; keep one active model resident.
5. **Relations / structured extraction.** GLiNER2-base is the default because it matches the server direction, but relation extraction is not exposed yet. Add `local_extract_relations` only after the coordinator confirms the server-side schema/wire shape.
6. **Download manager integration.** The service uses `huggingface_hub.snapshot_download` directly because transformer repos are snapshots, not single files. If the universal DownloadManager later gains HF snapshot jobs with progress/resume semantics, replace the direct download path and delete the private `.snapshot-complete` marker in `app/services/ner/service.py` once DownloadManager writes an equivalent completion marker.
7. **ONNX / GLiNER.cpp fast path.** Not started. Add only after PyTorch quality/QPS is proven against real label sets; keep PyTorch as the correctness baseline.
8. **Lockfile refresh.** `uv lock` is blocked by the existing Python 3.14 + Windows resolver split around `matrx-ai>=0.4.0`. Once that upstream/package-range issue is fixed, run `uv lock` so the `ner` optional extra is recorded in `uv.lock`.
9. **Tracked changeset cleanup.** `.matrx/tool_registry_changeset.sql` is now a no-op applied record. Delete it only after the repo stops tracking generated tool changesets or `tool_sync` writes per-run SQL to an ignored/temp path; until then, keep the no-op record so old insert SQL is not replayed.

## Progress Log

### 2026-07-14 — implementation pass

**Exercised end-to-end**

- Booted the test engine and made real HTTP requests through `tests/smoke/test_ner.py`: `/ner/status`, `/ner/models`, `/ner/extract` validation, and invalid-overlap error mapping.
- Queried the live Supabase project `txzxabzwovsujtloxrus` and verified `local_extract_entities` / `local_extract_pii` definitions and `matrx-local` bindings are active.
- Ran `uv run --no-sync python -m app.tools.tool_sync status`; code catalog `115` and cloud canon `115` were in sync.

**Typechecked/unit-tested only**

- NER model loading and inference logic, including GLiNER2 long-extract threshold forwarding.
- Hugging Face snapshot download and `.snapshot-complete` partial-download guard.
- Managed NER package installer wiring and PyInstaller hidden-import coverage.
- Tool invocation behavior through `/tools/invoke`; the catalog and schemas are tested, but no real tool call with a loaded model was exercised.

**Verification commands run**

- `uv run --no-sync pytest tests/characterization/test_ner_service.py tests/characterization/test_tool_registry_shape.py tests/parity/test_tool_count.py tests/characterization/test_local_db_sync_characterization.py tests/smoke/test_ner.py -q` — 38 passed.
- `uv run --no-sync ruff check app/services/ner app/api/ner_routes.py app/tools/tools/ner.py app/tools/arg_models/ner_args.py app/tools/dispatcher.py app/tools/catalog.py app/tools/tool_schemas.py app/services/capabilities/installer.py app/api/capabilities_routes.py tests/characterization/test_ner_service.py tests/smoke/test_ner.py` — passed.
- `uv run --no-sync python -m py_compile app/services/ner/models.py app/services/ner/service.py app/api/ner_routes.py app/tools/tools/ner.py app/tools/arg_models/ner_args.py app/tools/dispatcher.py app/tools/catalog.py app/tools/tool_schemas.py app/services/capabilities/installer.py app/api/capabilities_routes.py` — passed.
- `uv run --no-sync python -m app.tools.tool_sync status` — no drift.

## Commits

- `0f4d1c729 feat(ner): add local GLiNER service`
- `927b75a17 feat(tools): expose local NER tools`
