"""The frozen bundle must collect shared packages WHOLE.

Guards the bug class that has now shipped three times (google.protobuf 2026-07-12,
jinja2 2026-07-18, huggingface_hub 2026-07-19): a package present in BOTH the
frozen bundle and a managed runtime dir resolves to the BUNDLED copy, because
hooks/runtime_hook.py APPENDS the managed dir and PyInstaller 6 resolves frozen
imports through a sys.path hook in path order.

A partially collected bundle copy therefore makes the complete on-disk copy
unreachable, and the failure appears in the frozen app ONLY -- source tests and
`uv run` import the complete copy and pass. That is precisely why this file
asserts on the SPEC INPUTS rather than on runtime imports.
"""

from __future__ import annotations

import pathlib
import sys

_SPECS_DIR = pathlib.Path(__file__).resolve().parents[2] / "specs"
sys.path.insert(0, str(_SPECS_DIR))

from _managed_runtime_bundle import (  # noqa: E402
    MANAGED_RUNTIME_SHARED_PACKAGES,
    collect_managed_runtime_modules,
)

_SPEC_FILES = sorted(_SPECS_DIR.glob("*.spec"))


def test_all_four_specs_exist() -> None:
    # macOS arm64 + macOS x86_64 + Windows + Linux. A dropped spec means a
    # platform silently stops getting the fix.
    assert len(_SPEC_FILES) == 4, [p.name for p in _SPEC_FILES]


def test_every_spec_consumes_the_shared_list() -> None:
    """No spec may hand-roll its own collect_submodules for a shared package.

    Four copies of the same list is how the builds drift; jinja2 was fixed in
    the specs while huggingface_hub was never added at all.
    """
    for spec in _SPEC_FILES:
        text = spec.read_text()
        assert "_managed_runtime_bundle" in text, f"{spec.name} bypasses the shared list"
        assert "_shared_runtime_mods" in text, f"{spec.name} never uses the collected mods"
        for package in MANAGED_RUNTIME_SHARED_PACKAGES:
            # Both quote styles — a double-quoted hand-add is the same drift.
            for literal in (f"collect_submodules('{package}')",
                            f'collect_submodules("{package}")'):
                assert literal not in text, (
                    f"{spec.name} hand-collects {package}; add it to "
                    "MANAGED_RUNTIME_SHARED_PACKAGES instead"
                )


def test_build_sidecar_fallback_consumes_the_shared_list() -> None:
    """scripts/build-sidecar.sh is a real build path, not a dead fallback."""
    build_script = _SPECS_DIR.parent / "scripts" / "build-sidecar.sh"
    text = build_script.read_text()
    assert "_managed_runtime_bundle" in text
    assert "MANAGED_RUNTIME_SHARED_PACKAGES" in text
    for package in MANAGED_RUNTIME_SHARED_PACKAGES:
        assert f'"--collect-submodules", "{package}"' not in text, (
            f"build-sidecar.sh hand-collects {package}; it must read the shared list"
        )


def test_huggingface_hub_is_declared_shared() -> None:
    """The v1.3.145 outage. Do not remove without reading the module docstring."""
    assert "huggingface_hub" in MANAGED_RUNTIME_SHARED_PACKAGES
    assert "jinja2" in MANAGED_RUNTIME_SHARED_PACKAGES


def test_collection_reaches_submodules_static_analysis_misses() -> None:
    """The actual regression.

    `huggingface_hub.dataclasses` is imported by transformers>=5.4
    (`from huggingface_hub.dataclasses import strict`) but is named nowhere in
    huggingface_hub's own __init__, so PyInstaller's static analysis never
    reaches it. Same story for `jinja2.meta`, which transformers imports lazily
    for chat-template tool schemas.
    """
    try:
        from PyInstaller.utils.hooks import collect_submodules
    except ImportError:  # pragma: no cover - PyInstaller is a build-host dep
        import pytest

        pytest.skip("PyInstaller not installed on this host")

    modules = collect_managed_runtime_modules(collect_submodules)

    assert "huggingface_hub.dataclasses" in modules, (
        "huggingface_hub.dataclasses missing -- every image-gen model load dies "
        "with ModuleNotFoundError in the frozen app (v1.3.145)"
    )
    assert "jinja2.meta" in modules


def test_absent_package_does_not_break_the_build() -> None:
    """An optional extra missing from the build host cannot shadow anything."""

    def exploding_collect(package: str) -> list[str]:
        raise ModuleNotFoundError(package)

    assert collect_managed_runtime_modules(exploding_collect) == []
