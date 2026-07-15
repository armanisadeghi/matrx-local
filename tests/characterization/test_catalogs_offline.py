"""Characterization: total network failure still yields EVERY catalog kind.

Pins the compiled-fallback acceptance criterion (common-docs/remote-catalogs/
FEATURE.md): a fresh install with NO network and NO disk cache boots on the
compiled catalogs, every kind in KNOWN_KINDS is non-empty, every consumer
accessor answers with real data, and refresh attempts degrade loudly but
never raise.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# Import the service first: it loads app.common before app.config, dodging
# the pre-existing config↔common import cycle (see app_config service note).
from app.services.catalogs.models import KNOWN_KINDS, CatalogEntry
from app.services.catalogs.service import CatalogsService


def _offline_service(tmp_path: Path) -> CatalogsService:
    async def fetcher() -> list[CatalogEntry]:
        raise ConnectionError("simulated total network failure")

    return CatalogsService(
        cache_path=tmp_path / "catalogs.json",  # does not exist — fresh install
        app_version="1.3.112",
        fetcher=fetcher,
        use_registry=False,
    )


def test_total_network_failure_yields_every_kind(tmp_path: Path) -> None:
    svc = _offline_service(tmp_path)

    # Boot resolution is synchronous and offline: compiled fallback applied.
    assert svc.resolved.tier == "defaults"
    assert svc.resolved.fetched_at is None

    # EVERY known kind must be non-empty on the compiled tier — a kind that
    # can go empty offline is a kind that breaks first boot.
    for kind in sorted(KNOWN_KINDS):
        entries = svc.get_catalog(kind)
        assert entries, f"compiled fallback yields ZERO entries for kind {kind!r}"

    # Exact expected compiled counts (the shipped in-code catalogs).
    counts = {kind: len(svc.get_catalog(kind)) for kind in sorted(KNOWN_KINDS)}
    assert counts == {
        "api_key_provider": 12,
        "image_gen_model": 5,
        "llm_model": 38,
        "lora": 13,
        "ner_model": 6,
        "ner_pii_labels": 1,
        "system_prompt": 7,
        "tts_language": 9,
        "tts_model_file": 2,
        "tts_voice": 54,
        "video_gen_model": 3,
        "wake_word_model": 4,
        "whisper_model": 4,
        "workflow_preset": 6,
    }

    # A refresh attempt fails LOUDLY-but-gracefully: no exception, tier
    # unchanged, reason captured.
    after = asyncio.run(svc.refresh_now())
    assert after.tier == "defaults"
    assert svc.last_error is not None
    assert "simulated total network failure" in svc.last_error


def test_compiled_fallback_adapts_into_every_consumer(
    tmp_path: Path, monkeypatch
) -> None:
    """Every consumer accessor answers with adapted legacy-typed data offline."""
    import app.services.catalogs.service as svc_mod

    svc = _offline_service(tmp_path)
    monkeypatch.setattr(svc_mod, "_service", svc)

    from app.services.image_gen.loras import get_curated_lora_catalog
    from app.services.image_gen.models import (
        get_default_image_model_id,
        get_image_gen_models,
        get_workflow_presets,
    )
    from app.services.ner.models import (
        get_default_ner_model_id,
        get_ner_models,
        get_pii_labels,
    )
    from app.services.tts.models import (
        get_builtin_voices,
        get_default_voice_id,
        get_language_map,
        get_tts_model_files,
    )
    from app.services.video_gen.models import (
        get_default_video_model_id,
        get_video_gen_models,
    )
    from app.services.wake_word.models import (
        get_bundled_models,
        get_pretrained_registry,
    )

    assert len(get_image_gen_models()) == 5
    assert get_default_image_model_id() == "black-forest-labs/FLUX.2-klein-4B"
    assert len(get_workflow_presets()) == 6
    assert len(get_curated_lora_catalog()) == 13
    assert len(get_video_gen_models()) == 3
    assert get_default_video_model_id() == "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
    assert len(get_builtin_voices()) == 54
    assert get_default_voice_id() == "af_heart"
    assert len(get_language_map()) == 9
    files = get_tts_model_files()
    assert {f.filename for f in files} == {"kokoro-v1.0.onnx", "voices-v1.0.bin"}
    assert all(f.url and f.sha256 and f.size_bytes > 0 for f in files)
    assert len(get_ner_models()) == 6
    assert get_default_ner_model_id() == "gliner2-base"
    assert len(get_pii_labels()) == 15
    registry = get_pretrained_registry()
    assert set(registry) == {"hey_jarvis", "alexa", "hey_mycroft", "ok_nabu"}
    assert get_bundled_models() == ["hey_jarvis", "alexa"]
