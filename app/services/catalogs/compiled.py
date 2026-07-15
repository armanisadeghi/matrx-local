"""Compiled-fallback catalog entries — the DEFAULTS precedence tier.

``compiled_catalog_entries()`` adapts every legacy in-code catalog into
``CatalogEntry`` form so a fresh install with no network and no disk cache
still has every kind populated (zero data loss, never-block-startup — the
same invariant as app_config's compiled defaults).

Two sources, deliberately split:

  - **Python-sourced kinds** (image/video gen models, workflow presets,
    LoRAs, TTS voices/languages/model files, NER models/PII labels,
    wake-word models) adapt LIVE from the legacy lists in their
    ``app/services/*/models.py`` — those lists stay in code as explicitly
    demoted fallback data, so this tier can never drift from them.
  - **Rust/TS-sourced kinds** (llm_model, whisper_model, system_prompt,
    api_key_provider) come from ``compiled_data.py`` — a generated,
    mechanically extracted mirror of the Rust/TS constants (the desktop
    side keeps its own compiled fallbacks; this copy exists so the ENGINE
    endpoints ``GET /catalogs/{kind}`` answer for every kind even offline).

The adaptation logic mirrors the seed extraction (catalog-seeds/
build_seeds.py) 1:1 — keys, payload shapes, and added fields
(``is_default`` / ``bundled`` / tts_model_file roles) are identical to
what was seeded into the live DB, pinned by tests/parity/test_catalogs_live.py.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from app.services.catalogs.models import CatalogEntry

_SCHEMA_VERSION = 1


def _entry(
    kind: str,
    key: str,
    payload: dict[str, Any],
    *,
    artifact_url: str | None = None,
    artifact_sha256: str | None = None,
    artifact_size_bytes: int | None = None,
    sort_order: int = 0,
    notes: str | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        kind=kind,
        key=key,
        schema_version=_SCHEMA_VERSION,
        payload=payload,
        artifact_url=artifact_url,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=artifact_size_bytes,
        min_app_version=None,
        is_active=True,
        sort_order=sort_order,
        notes=notes,
    )


def _python_sourced_entries() -> list[CatalogEntry]:
    # Function-level imports: the legacy modules' accessor functions import
    # this package back at call time — module-level imports here would make
    # that cycle order-dependent.
    from app.services.image_gen import models as img  # noqa: PLC0415
    from app.services.image_gen.loras import CURATED_LORA_CATALOG  # noqa: PLC0415
    from app.services.ner import models as ner  # noqa: PLC0415
    from app.services.tts import models as tts  # noqa: PLC0415
    from app.services.video_gen import models as vid  # noqa: PLC0415
    from app.services.wake_word.models import (  # noqa: PLC0415
        BUNDLED_MODELS,
        _PRETRAINED_REGISTRY,
    )

    out: list[CatalogEntry] = []

    for i, m in enumerate(img.IMAGE_GEN_MODELS):
        p = dataclasses.asdict(m)
        p["is_default"] = m.model_id == img.DEFAULT_IMAGE_MODEL_ID
        out.append(_entry("image_gen_model", m.model_id, p, sort_order=i * 10))

    for i, pr in enumerate(img.WORKFLOW_PRESETS):
        out.append(
            _entry("workflow_preset", pr.preset_id, dataclasses.asdict(pr), sort_order=i * 10)
        )

    for i, lora in enumerate(CURATED_LORA_CATALOG):
        out.append(_entry("lora", str(lora["repo_id"]), dict(lora), sort_order=i * 10))

    for i, vm in enumerate(vid.VIDEO_GEN_MODELS):
        p = dataclasses.asdict(vm)
        p["is_default"] = vm.model_id == vid.DEFAULT_VIDEO_MODEL_ID
        out.append(_entry("video_gen_model", vm.model_id, p, sort_order=i * 10))

    for i, v in enumerate(tts.BUILTIN_VOICES):
        out.append(_entry("tts_voice", v.voice_id, dataclasses.asdict(v), sort_order=i * 10))

    for i, lang in enumerate(tts.LANGUAGES):
        out.append(
            _entry("tts_language", lang.lang_code, dataclasses.asdict(lang), sort_order=i * 10)
        )

    for i, (filename, url, size, sha, role) in enumerate(
        (
            (tts.ONNX_MODEL_FILENAME, tts.ONNX_MODEL_URL, tts.ONNX_MODEL_SIZE_BYTES,
             tts.ONNX_MODEL_SHA256, "onnx_model"),
            (tts.VOICES_BIN_FILENAME, tts.VOICES_BIN_URL, tts.VOICES_BIN_SIZE_BYTES,
             tts.VOICES_BIN_SHA256, "voices_bin"),
        )
    ):
        out.append(
            _entry(
                "tts_model_file",
                filename,
                {
                    "filename": filename,
                    "url": url,
                    "size_bytes": size,
                    "sha256": sha,
                    "role": role,
                    "sample_rate": tts.SAMPLE_RATE,
                },
                artifact_url=url,
                artifact_sha256=sha,
                artifact_size_bytes=size,
                sort_order=i * 10,
            )
        )

    for i, spec in enumerate(ner.NER_MODELS):
        p = dataclasses.asdict(spec)
        p["estimated_ram_mb"] = list(p["estimated_ram_mb"])  # JSON has no tuples
        out.append(_entry("ner_model", spec.model_id, p, sort_order=i * 10))

    out.append(
        _entry("ner_pii_labels", "default", {"labels": list(ner.PII_LABELS)}, sort_order=0)
    )

    for i, (name, meta) in enumerate(_PRETRAINED_REGISTRY.items()):
        p = {"name": name, **meta, "bundled": name in BUNDLED_MODELS}
        out.append(
            _entry(
                "wake_word_model",
                name,
                p,
                artifact_url=str(meta["url"]),
                sort_order=i * 10,
            )
        )

    return out


def _vendored_entries() -> list[CatalogEntry]:
    from app.services.catalogs.compiled_data import (  # noqa: PLC0415 — 90KB literal, load lazily
        COMPILED_API_KEY_PROVIDER_ENTRIES,
        COMPILED_LLM_MODEL_ENTRIES,
        COMPILED_SYSTEM_PROMPT_ENTRIES,
        COMPILED_WHISPER_MODEL_ENTRIES,
    )

    out: list[CatalogEntry] = []
    for kind, rows in (
        ("llm_model", COMPILED_LLM_MODEL_ENTRIES),
        ("whisper_model", COMPILED_WHISPER_MODEL_ENTRIES),
        ("system_prompt", COMPILED_SYSTEM_PROMPT_ENTRIES),
        ("api_key_provider", COMPILED_API_KEY_PROVIDER_ENTRIES),
    ):
        for row in rows:
            out.append(
                _entry(
                    kind,
                    row["key"],
                    row["payload"],
                    artifact_url=row.get("artifact_url"),
                    artifact_sha256=row.get("artifact_sha256"),
                    artifact_size_bytes=row.get("artifact_size_bytes"),
                    sort_order=row.get("sort_order", 0),
                    notes=row.get("notes"),
                )
            )
    return out


def compiled_catalog_entries() -> list[CatalogEntry]:
    """Every legacy compiled catalog, adapted to ``CatalogEntry`` form.

    Covers ALL 14 kinds in ``KNOWN_KINDS`` — pinned by the characterization
    test (total network failure must still yield every kind non-empty).
    """
    return _python_sourced_entries() + _vendored_entries()
