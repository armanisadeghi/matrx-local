"""Civitai LoRAs cannot be marked installed until safetensors is complete."""

from __future__ import annotations

import json
import hashlib
import struct
from pathlib import Path

import pytest

from app.services.downloads.manager import _validate_safetensors_file


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
