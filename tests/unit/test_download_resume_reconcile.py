"""The startup-resume RECONCILE contract (MXL "the system isn't checking if I
have the models").

`_resume_incomplete` must never blind-re-download a model already on disk. Its
single completion oracle is `artifact_present`, shared with `enqueue`'s
idempotency check so the two can never disagree. These tests pin that oracle
against every download shape, and pin the catalog self-validation that keeps one
bad LoRA entry from blanking the panel.

NETWORK-FREE, no engine, no DB — pure functions against a tmp dir.

    uv run --no-sync pytest tests/unit/test_download_resume_reconcile.py -v
"""

from __future__ import annotations

from pathlib import Path

from app.services.downloads.manager import artifact_present
from app.services.media_gen.paths import DOWNLOAD_COMPLETE_MARKER


def _marker(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")


def test_artifact_present_requires_dest_dir() -> None:
    assert artifact_present(None, "x") is False
    assert artifact_present({}, "x") is False


def test_hf_snapshot_present_only_with_marker(tmp_path: Path) -> None:
    dest = tmp_path / "model"
    md = {"dest_dir": str(dest), "hf_repo_id": "org/name"}
    # weights on disk but no marker → NOT complete → would re-download
    dest.mkdir()
    (dest / "model.safetensors").write_bytes(b"x")
    assert artifact_present(md, "org--name") is False
    # marker present → complete → never re-download
    _marker(dest)
    assert artifact_present(md, "org--name") is True


def test_civitai_marker_gated_needs_marker_and_file(tmp_path: Path) -> None:
    dest = tmp_path / "lora"
    md = {
        "dest_dir": str(dest),
        "civitai_download": True,
        "write_complete_marker": True,
        "dest_filename": "style.safetensors",
    }
    dest.mkdir()
    # marker but no file (or vice versa) → incomplete
    (dest / DOWNLOAD_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    assert artifact_present(md, "civitai--1-2") is False
    (dest / "style.safetensors").write_bytes(b"weights")
    assert artifact_present(md, "civitai--1-2") is True


def test_plain_single_file_present_when_file_exists(tmp_path: Path) -> None:
    dest = tmp_path / "gguf"
    dest.mkdir()
    md = {"dest_dir": str(dest), "dest_filename": "model.gguf"}
    assert artifact_present(md, "model") is False
    (dest / "model.gguf").write_bytes(b"x")
    assert artifact_present(md, "model") is True


def test_plain_file_falls_back_to_filename(tmp_path: Path) -> None:
    dest = tmp_path / "d"
    dest.mkdir()
    md = {"dest_dir": str(dest)}  # no dest_filename → use the download filename
    (dest / "cloudflared").write_bytes(b"bin")
    assert artifact_present(md, "cloudflared") is True


def test_curated_catalog_is_valid() -> None:
    """The shipped catalog must have zero problems — a bad entry would be
    skipped in GET /image-gen/loras, so keep it clean at the source."""
    from app.services.image_gen.loras import validate_catalog

    assert validate_catalog() == []


def test_validate_catalog_flags_missing_key_and_dupes(monkeypatch) -> None:
    from app.services.image_gen import loras

    bad = [
        {"repo_id": "a/b", "name": "n", "description": "d", "weight_name": "w",
         "base_family": "sdxl"},  # missing license
        {"repo_id": "a/b", "name": "n2", "description": "d", "weight_name": "w",
         "base_family": "sdxl", "license": "x"},  # duplicate repo_id
    ]
    monkeypatch.setattr(loras, "CURATED_LORA_CATALOG", bad)
    problems = loras.validate_catalog()
    assert any("license" in p for p in problems)
    assert any("duplicate" in p for p in problems)
