"""Civitai LoRAs cannot be marked installed until safetensors is complete."""

from __future__ import annotations

import json
import hashlib
import struct
from pathlib import Path

import pytest

from app.services.downloads.manager import _validate_safetensors_file
from app.services.image_gen import loras


def _write_safetensors(path: Path, *, truncate: bool = False) -> None:
    header = json.dumps(
        {"tensor": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    ).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + (b"\0\0" if truncate else b"\0\0\0\0"))


def test_validate_safetensors_file_accepts_complete_weight(tmp_path: Path) -> None:
    path = tmp_path / "style.safetensors"
    _write_safetensors(path)
    _validate_safetensors_file(path)


def test_validate_safetensors_file_rejects_truncated_weight(tmp_path: Path) -> None:
    path = tmp_path / "style.safetensors"
    _write_safetensors(path, truncate=True)
    with pytest.raises(ValueError, match="incomplete"):
        _validate_safetensors_file(path)


def test_validate_safetensors_file_rejects_wrong_civitai_digest(tmp_path: Path) -> None:
    path = tmp_path / "style.safetensors"
    _write_safetensors(path)
    with pytest.raises(ValueError, match="SHA-256"):
        _validate_safetensors_file(path, expected_sha256="0" * 64)
    _validate_safetensors_file(
        path, expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )


def test_catalog_family_backfill_repairs_unknown_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "party-time"
    install.mkdir()
    meta = {
        "id": "party-time",
        "repo_id": "civitai:2458332@3028757",
        "base_family": "unknown",
        "weight_name": "party.safetensors",
    }
    (install / "lora.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(loras, "lora_dir", lambda _lora_id: install)

    repaired = loras.backfill_lora_family_from_catalog(
        meta,
        catalog_by_repo={
            "civitai:2458332@3028757": {"base_family": "flux2"}
        },
    )

    assert repaired["base_family"] == "flux2"
    assert json.loads((install / "lora.json").read_text())["base_family"] == "flux2"


def test_catalog_family_backfill_never_overwrites_known_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "known"
    install.mkdir()
    meta = {
        "id": "known",
        "repo_id": "civitai:1@2",
        "base_family": "z-image",
    }
    (install / "lora.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(loras, "lora_dir", lambda _lora_id: install)

    result = loras.backfill_lora_family_from_catalog(
        meta,
        catalog_by_repo={"civitai:1@2": {"base_family": "flux2"}},
    )

    assert result["base_family"] == "z-image"
    assert json.loads((install / "lora.json").read_text())["base_family"] == "z-image"
