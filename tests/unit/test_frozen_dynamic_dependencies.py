"""Frozen-sidecar contracts for dependencies imported by on-demand packages."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS = tuple((REPO_ROOT / "specs").glob("matrx-engine-*.spec"))


def test_every_frozen_build_collects_all_jinja_submodules() -> None:
    assert len(SPECS) == 4
    for spec_path in SPECS:
        spec = spec_path.read_text(encoding="utf-8")
        assert "_jinja2_mods = collect_submodules('jinja2')" in spec
        assert "_protobuf_mods + _jinja2_mods + _office_hidden" in spec

    fallback = (REPO_ROOT / "scripts" / "build-sidecar.sh").read_text(
        encoding="utf-8"
    )
    assert '"--collect-submodules", "jinja2"' in fallback


def test_jinja_is_a_direct_engine_dependency() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"jinja2>=3.1.6"' in pyproject
