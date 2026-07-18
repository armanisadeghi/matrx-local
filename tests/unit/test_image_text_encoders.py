from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.image_gen.models import AlternativeTextEncoder, ImageGenModel


def _encoder(**overrides) -> AlternativeTextEncoder:
    values = {
        "encoder_id": "candidate-q4",
        "name": "Candidate Q4",
        "description": "Unverified alternative",
        "repo_id": "org/candidate",
        "format": "gguf",
        "files": ["candidate.gguf"],
        "revision": "a" * 40,
        "weight_name": "candidate.gguf",
        "license": "apache-2.0",
    }
    values.update(overrides)
    return AlternativeTextEncoder(**values)


def _model(encoder: AlternativeTextEncoder) -> ImageGenModel:
    return ImageGenModel(
        model_id="org/klein-4b",
        name="Klein 4B",
        provider="Org",
        pipeline_type="flux2-klein",
        vram_gb=1,
        ram_gb=1,
        description="test",
        quality_rating=1,
        speed_rating=1,
        recommended_steps=4,
        recommended_guidance=1,
        supports_negative_prompt=False,
        model_card_url="https://example.test/model",
        text_encoders=[encoder],
    )


def test_install_requires_marker_every_file_and_exact_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.services.image_gen import text_encoders

    monkeypatch.setattr(text_encoders, "image_text_encoders_dir", lambda: tmp_path)
    spec = _encoder()
    text_encoders._write_pending_meta(spec)
    root = text_encoders.encoder_dir(spec.encoder_id)
    (root / spec.weight_name).write_bytes(b"gguf")
    assert not text_encoders.is_encoder_installed(spec)

    (root / ".download-complete").write_text("ok", encoding="utf-8")
    assert text_encoders.is_encoder_installed(spec)
    assert not text_encoders.is_encoder_installed(_encoder(revision="b" * 40))


def test_download_is_revision_pinned_and_exactly_allowlisted(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.services.downloads import manager as manager_module
    from app.services.image_gen import text_encoders

    monkeypatch.setattr(text_encoders, "image_text_encoders_dir", lambda: tmp_path)
    spec = _encoder(files=["candidate.gguf", "tokenizer.json"])
    model = _model(spec)
    captured = {}

    class FakeManager:
        async def enqueue(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="download-1")

    monkeypatch.setattr(manager_module, "get_download_manager", lambda: FakeManager())
    result = asyncio.run(text_encoders.start_encoder_download(model, spec.encoder_id))
    assert result["queued"] is True
    assert captured["category"] == "image_gen_text_encoder"
    assert captured["metadata"]["hf_revision"] == "a" * 40
    assert captured["metadata"]["hf_allow_files"] == spec.files


def test_gated_encoder_requires_token_before_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.services.image_gen import text_encoders

    monkeypatch.setattr(text_encoders, "image_text_encoders_dir", lambda: tmp_path)
    monkeypatch.setattr(text_encoders, "read_hf_token", lambda: None)
    spec = _encoder(requires_hf_token=True)
    result = asyncio.run(
        text_encoders.start_encoder_download(_model(spec), spec.encoder_id)
    )
    assert result["queued"] is False
    assert result["needs_hf_token"] is True


def test_encoder_selection_survives_job_store_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from app.services.image_gen import jobs as jobs_module
    from app.services.image_gen.jobs import ImageJobStore

    monkeypatch.setattr(
        jobs_module, "generated_images_dir", lambda: tmp_path / "image-jobs"
    )
    store = ImageJobStore()
    job = store.create(
        prompt="test",
        model_id="org/klein-4b",
        text_encoder_id="candidate-q4",
    )

    restored_store = ImageJobStore()
    restored_store.load()
    restored = restored_store.get(job.job_id)
    assert restored is not None
    assert restored.text_encoder_id == "candidate-q4"
