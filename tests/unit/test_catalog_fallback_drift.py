"""Drift guard for the generated compiled-fallback catalog data.

``app/services/catalogs/compiled_data.py`` vendors the Rust/TS-sourced
catalog kinds (llm_model / whisper_model / system_prompt /
api_key_provider). It has no build-time generation step, so an edit to
``desktop/src-tauri/src/llm/model_selector.rs`` (or the other sources)
would silently rot the offline fallback — THIS test re-extracts from the
Rust/TS sources at test time and asserts equality, failing the suite until
the fallback is regenerated:

    uv run python scripts/generate_catalog_fallback.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_GENERATOR = REPO / "scripts" / "generate_catalog_fallback.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_catalog_fallback", _GENERATOR
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_gen = _load_generator()
_FRESH = _gen.extract_all()

from app.services.catalogs import compiled_data  # noqa: E402


@pytest.mark.parametrize(
    ("kind", "const_name"),
    [(kind, const) for kind, const, _e, _s in _gen.KINDS],
)
def test_compiled_fallback_matches_rust_ts_sources(kind: str, const_name: str) -> None:
    committed = getattr(compiled_data, const_name)
    fresh = _FRESH[kind]
    assert committed == fresh, (
        f"compiled_data.py has DRIFTED from the Rust/TS source for kind "
        f"{kind!r} — the source constants changed without regenerating the "
        "offline fallback. Run:\n"
        "    uv run python scripts/generate_catalog_fallback.py"
    )


def test_generator_check_mode_agrees() -> None:
    """`--check` (used by humans/CI ad hoc) sees the same truth as this test."""
    current = _gen.load_current()
    assert set(current) == set(_FRESH)
