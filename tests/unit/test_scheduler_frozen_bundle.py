"""The optional scheduler host must be complete in every release sidecar."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_FILES = sorted((REPO_ROOT / "specs").glob("matrx-engine-*.spec"))


def _load_verifier():
    path = REPO_ROOT / "scripts" / "verify-frozen-runtime.py"
    spec = importlib.util.spec_from_file_location("scheduler_frozen_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_build_syncs_scheduler_extra_and_all_five_build_paths_bundle_it() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'scheduler = [' in pyproject
    assert '"matrx-scheduler>=0.3.5"' in pyproject
    assert "it is bundled\n    # into release builds" in pyproject

    fallback = (REPO_ROOT / "scripts" / "build-sidecar.sh").read_text(
        encoding="utf-8"
    )
    assert "--extra transcription --extra scheduler" in fallback
    assert fallback.count('"--hidden-import", "matrx_scheduler"') == 1

    assert len(SPEC_FILES) == 4
    for spec_path in SPEC_FILES:
        text = spec_path.read_text(encoding="utf-8")
        assert text.count("'matrx_scheduler'") == 1, spec_path


def test_scheduler_archive_check_rejects_a_warning_level_hidden_import_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    monkeypatch.setattr(verifier, "archive_modules", lambda _binary: set())

    with pytest.raises(RuntimeError, match="matrx_scheduler"):
        verifier.check_scheduler_archive(Path("Matrx Engine"))


def test_scheduler_archive_check_accepts_the_operational_module_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    monkeypatch.setattr(
        verifier,
        "archive_modules",
        lambda _binary: set(verifier.SCHEDULER_REQUIRED_MODULES),
    )

    verifier.check_scheduler_archive(Path("Matrx Engine"))
