"""Bundled delvewheel ``<pkg>.libs`` DLLs must land at their OWN paths.

numpy 2.4.2 and pandas 3.0.1 Windows wheels both vendor
``msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll``. PyInstaller's binary
dependency analysis collected that name once — under ``pandas.libs`` when the
build-time search order put pandas first — and the v1.4.311 Windows sidecar died
on ``import numpy`` (``DLL load failed while importing _multiarray_umath``).
See specs/_delvewheel_libs.py.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import SimpleNamespace

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SPECS_DIR = _REPO_ROOT / "specs"
sys.path.insert(0, str(_SPECS_DIR))

from _delvewheel_libs import (  # noqa: E402
    complete_delvewheel_libs,
    missing_delvewheel_dlls,
)

SHARED = "msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll"
OPENBLAS = "libscipy_openblas64_-74a408729250596b0973e69fdd954eea.dll"
SCIPY_BLAS = "libscipy_openblas-64eda39e79589aedb16f58e5547eb599.dll"


@pytest.fixture
def site_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    for libs, names in {
        "numpy.libs": [OPENBLAS, SHARED],
        "pandas.libs": [SHARED],
        "scipy.libs": [SCIPY_BLAS],  # excluded from the bundle
    }.items():
        (tmp_path / libs).mkdir()
        for name in names:
            (tmp_path / libs / name).write_bytes(b"MZ")
    return tmp_path


def _broken_analysis(site_dir: pathlib.Path) -> SimpleNamespace:
    """What PyInstaller produced in v1.4.311: the shared DLL only in pandas.libs."""
    return SimpleNamespace(
        pure=[("numpy", "", "PYMODULE"), ("pandas", "", "PYMODULE")],
        binaries=[
            ("numpy/_core/_multiarray_umath.cp313-win_amd64.pyd", "x", "EXTENSION"),
            (f"numpy.libs/{OPENBLAS}", str(site_dir / "numpy.libs" / OPENBLAS), "BINARY"),
            (f"pandas.libs/{SHARED}", str(site_dir / "pandas.libs" / SHARED), "BINARY"),
        ],
    )


def test_gate_names_the_dll_the_broken_build_lost(site_dir: pathlib.Path) -> None:
    analysis = _broken_analysis(site_dir)
    payload = {dest for dest, _src, _type in analysis.binaries}
    modules = {"numpy", "numpy._core._multiarray_umath", "pandas"}
    assert missing_delvewheel_dlls(payload, modules, [site_dir]) == [
        f"numpy.libs/{SHARED}"
    ]


def test_completion_restores_it_and_the_gate_passes(site_dir: pathlib.Path) -> None:
    analysis = _broken_analysis(site_dir)
    additions = complete_delvewheel_libs(analysis, [site_dir])
    assert additions == [
        (f"numpy.libs/{SHARED}", str(site_dir / "numpy.libs" / SHARED), "BINARY")
    ]
    analysis.binaries += additions
    payload = {dest for dest, _src, _type in analysis.binaries}
    assert missing_delvewheel_dlls(payload, {"numpy", "pandas"}, [site_dir]) == []


def test_windows_separators_and_case_count_as_present(site_dir: pathlib.Path) -> None:
    payload = {
        f"numpy.libs\\{OPENBLAS.upper()}",
        f"numpy.libs\\{SHARED}",
        f"pandas.libs\\{SHARED}",
    }
    assert missing_delvewheel_dlls(payload, {"numpy", "pandas"}, [site_dir]) == []


def test_excluded_packages_are_never_collected(site_dir: pathlib.Path) -> None:
    analysis = _broken_analysis(site_dir)
    dests = [entry[0] for entry in complete_delvewheel_libs(analysis, [site_dir])]
    assert not any(dest.startswith("scipy.libs/") for dest in dests)


def test_every_spec_completes_delvewheel_libs_before_pyz() -> None:
    for spec in sorted(_SPECS_DIR.glob("matrx-engine-*.spec")):
        text = spec.read_text(encoding="utf-8")
        call = text.index("a.binaries += complete_delvewheel_libs(a)")
        assert call < text.index("pyz = PYZ(a.pure)"), spec.name


def test_release_verifier_runs_the_gate() -> None:
    path = _REPO_ROOT / "scripts" / "verify-frozen-runtime.py"
    module_spec = importlib.util.spec_from_file_location("verify_frozen_runtime_dw", path)
    assert module_spec is not None and module_spec.loader is not None
    verifier = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(verifier)
    assert "check_delvewheel_libs_archive(binary)" in path.read_text(encoding="utf-8")
    assert callable(verifier.check_delvewheel_libs_archive)
