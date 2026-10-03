"""Collect every bundled package's delvewheel ``<pkg>.libs`` DLLs WHOLE.

Single source of truth, consumed by every ``specs/*.spec`` and by
``scripts/verify-frozen-runtime.py``. A no-op off Windows (it only looks at
``*.dll`` files).

Why this file exists
--------------------
Windows PyPI wheels built with delvewheel ship their DLLs in a sibling
``<pkg>.libs`` directory, and each package's ``__init__`` calls
``os.add_dll_directory(<pkg>.libs)`` for ITS OWN directory only. Two wheels can
vendor the SAME mangled file: numpy 2.4.2 and pandas 3.0.1 both carry
``msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll``.

PyInstaller's numpy hook collects ``numpy.libs`` explicitly only when
``get_installer('numpy') == 'pip'``. This repo installs with uv
(``INSTALLER`` reads ``uv``), so the hook skips it and every ``.libs`` DLL is
picked up only through binary dependency analysis — which resolves a DLL NAME
once, against the extra search directories in whatever order the build-time
import pass registered them, and collects it ONCE. When ``pandas.libs`` sorts
first, the shared msvcp140 lands only in ``pandas.libs``, ``numpy.libs`` lacks
it, and the frozen app dies on ``import numpy`` with ``DLL load failed while
importing _multiarray_umath`` (v1.4.311, Windows only). Any unrelated import
graph change can flip that order, so the break is silent and non-local.

Collecting each bundled package's ``.libs`` directory explicitly — every DLL at
its own ``<pkg>.libs/<name>`` path — removes the order dependence for every
delvewheel wheel, not just numpy.
"""

from __future__ import annotations

import site
from pathlib import Path


def _site_packages_dirs() -> list[Path]:
    dirs: list[Path] = []
    for raw in [*site.getsitepackages(), site.getusersitepackages()]:
        path = Path(raw)
        if path.is_dir() and path not in dirs:
            dirs.append(path)
    return dirs


def bundled_top_level_packages(module_names, binary_dest_names) -> set[str]:
    """Top-level import names present in the bundle (pure modules + binaries)."""
    tops = {name.split(".", 1)[0] for name in module_names}
    for dest in binary_dest_names:
        first = dest.replace("\\", "/").split("/", 1)[0]
        tops.add(first)
    return tops


def expected_delvewheel_dlls(
    bundled_packages: set[str], site_dirs: list[Path] | None = None
) -> list[tuple[str, str]]:
    """``(dest, src)`` for every DLL in a bundled package's ``.libs`` dir.

    ``dest`` uses forward slashes (``numpy.libs/msvcp140-<hash>.dll``).
    Packages that are excluded from the bundle (e.g. scipy) are skipped so
    their DLLs never bloat the sidecar.
    """
    found: dict[str, str] = {}
    for site_dir in site_dirs if site_dirs is not None else _site_packages_dirs():
        for libs_dir in sorted(site_dir.glob("*.libs")):
            if not libs_dir.is_dir():
                continue
            package = libs_dir.name[: -len(".libs")]
            if package not in bundled_packages:
                continue
            for dll in sorted(libs_dir.glob("*.dll")):
                dest = f"{libs_dir.name}/{dll.name}"
                found.setdefault(dest, str(dll))
    return sorted(found.items())


def complete_delvewheel_libs(analysis, site_dirs: list[Path] | None = None) -> list:
    """Return the TOC entries a spec must append to ``a.binaries``.

    Called after ``Analysis`` and before ``PYZ``/``EXE``:
    ``a.binaries += complete_delvewheel_libs(a)``.
    """
    module_names = [entry[0] for entry in analysis.pure]
    binary_dests = [entry[0] for entry in analysis.binaries]
    bundled = bundled_top_level_packages(module_names, binary_dests)
    present = {dest.replace("\\", "/").lower() for dest in binary_dests}
    additions = []
    for dest, src in expected_delvewheel_dlls(bundled, site_dirs):
        if dest.lower() in present:
            continue
        # PyInstaller normpath()s every dest, so "/" is correct on Windows too.
        additions.append((dest, src, "BINARY"))
    if additions:
        print(
            "delvewheel .libs completion: collecting "
            + ", ".join(entry[0] for entry in additions)
        )
    return additions


def missing_delvewheel_dlls(
    archive_payload_paths: set[str],
    archive_module_names: set[str],
    site_dirs: list[Path] | None = None,
) -> list[str]:
    """Release-gate check: bundled ``.libs`` DLLs absent from the artifact."""
    normalized = {path.replace("\\", "/").lower() for path in archive_payload_paths}
    bundled = bundled_top_level_packages(archive_module_names, archive_payload_paths)
    return [
        dest
        for dest, _src in expected_delvewheel_dlls(bundled, site_dirs)
        if dest.lower() not in normalized
    ]
