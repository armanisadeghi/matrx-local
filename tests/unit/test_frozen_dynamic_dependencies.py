"""Frozen-sidecar contracts for dependencies imported by on-demand packages."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS = tuple((REPO_ROOT / "specs").glob("matrx-engine-*.spec"))


def test_every_frozen_build_collects_all_jinja_submodules() -> None:
    """jinja2 must reach every frozen build — now via the shared list.

    The per-spec ``_jinja2_mods`` line this used to assert on was replaced by
    ``specs/_managed_runtime_bundle.py`` after huggingface_hub hit the SAME
    bug jinja2 had (a partial bundle copy shadowing the complete managed one,
    failing in the frozen app only). Four hand-maintained copies of that list
    are what let huggingface_hub be missed entirely; the wiring invariant now
    lives in tests/unit/test_managed_runtime_bundle.py.
    """
    assert len(SPECS) == 4
    for spec_path in SPECS:
        spec = spec_path.read_text(encoding="utf-8")
        assert "_managed_runtime_bundle" in spec
        assert "_protobuf_mods + _shared_runtime_mods + _office_hidden" in spec

    fallback = (REPO_ROOT / "scripts" / "build-sidecar.sh").read_text(
        encoding="utf-8"
    )
    assert "MANAGED_RUNTIME_SHARED_PACKAGES" in fallback


def test_jinja_is_a_direct_engine_dependency() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"jinja2>=3.1.6"' in pyproject
