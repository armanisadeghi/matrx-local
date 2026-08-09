"""The frozen bundle must carry the Office codec WHOLE — modules and templates.

The codec (``matrx_files.specific_handlers.office``) and every renderer it
drives (python-docx, python-pptx, openpyxl, xlsxwriter) are imported lazily
INSIDE functions — ``app/tools/tools/file_ops.py::_read_office`` and
``app/tools/tools/media.py::tool_office_generate``. PyInstaller's static
analysis reaches none of them, so the four specs must collect them explicitly,
and a build host missing one must FAIL rather than silently ship a sidecar that
cannot open a Word file (the silent-skip that produced four frozen-only
outages; see specs/_managed_runtime_bundle.py).

Half of the contract is DATA, not modules: ``docx.Document()`` and
``pptx.Presentation()`` with no argument load ``templates/default.docx`` /
``templates/default.pptx`` from beside the package. A bundle can carry every
module and still fail at document creation.
"""

from __future__ import annotations

import pathlib
import importlib.util
import sys
from types import SimpleNamespace

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SPECS_DIR = _REPO_ROOT / "specs"
sys.path.insert(0, str(_SPECS_DIR))

from _office_bundle import (  # noqa: E402
    DIR_MARKER,
    OFFICE_PACKAGES,
    OFFICE_REQUIRED_DATA_FILES,
    collect_office_datas,
    collect_office_modules,
    office_parent_relative_dirs,
)

_SPEC_FILES = sorted(_SPECS_DIR.glob("matrx-engine-*.spec"))


def test_archive_payload_paths_include_windows_binary_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows classifies ZIP-based Office templates as binary payloads."""
    verifier_path = _REPO_ROOT / "scripts" / "verify-frozen-runtime.py"
    module_spec = importlib.util.spec_from_file_location(
        "verify_frozen_runtime", verifier_path
    )
    assert module_spec is not None and module_spec.loader is not None
    verifier = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(verifier)

    archive = SimpleNamespace(
        toc={
            r"docx\templates\default.docx": (0, 0, 0, 0, "b"),
            r"pptx\templates\default.pptx": (0, 0, 0, 0, "b"),
            r"pptx\oxml\_bundle_dir_marker.txt": (0, 0, 0, 0, "x"),
            "PYZ.pyz": (0, 0, 0, 0, "z"),
        }
    )
    monkeypatch.setattr(verifier, "_open_archive", lambda _binary: archive)

    assert verifier.archive_data_files(pathlib.Path("engine.exe")) == {
        "docx/templates/default.docx",
        "pptx/templates/default.pptx",
        "pptx/oxml/_bundle_dir_marker.txt",
    }


def test_every_spec_uses_the_shared_office_collector() -> None:
    """Four hand-maintained copies of a collection list is how one drifts."""
    assert len(_SPEC_FILES) == 4
    for spec_path in _SPEC_FILES:
        spec = spec_path.read_text(encoding="utf-8")
        assert "from _office_bundle import" in spec, spec_path
        assert "collect_office_datas(collect_data_files)" in spec, spec_path
        assert "collect_office_modules(" in spec, spec_path
        # The silent skip this replaced must not come back.
        assert "for _office_pkg in" not in spec, spec_path


def test_build_sidecar_fallback_shares_the_office_source_of_truth() -> None:
    fallback = (_REPO_ROOT / "scripts" / "build-sidecar.sh").read_text(
        encoding="utf-8"
    )
    assert "from _office_bundle import" in fallback
    assert "OFFICE_PACKAGES" in fallback
    # The specs get the marker entries from collect_office_datas' RETURN value;
    # the flag-based builder has to add them explicitly. Asserting the templates
    # are collectable and then not shipping the directories would rebuild the
    # exact frozen-only failure this module exists to prevent.
    assert "office_parent_relative_dirs()" in fallback
    assert "--add-data" in fallback and "DIR_MARKER" in fallback


def test_build_sidecar_fallback_python_block_is_valid() -> None:
    """The inline builder is a heredoc, so nothing else compiles it."""
    fallback = (_REPO_ROOT / "scripts" / "build-sidecar.sh").read_text(
        encoding="utf-8"
    )
    _, _, rest = fallback.partition("cat > \"$CMD_FILE\" << 'PYINSTALLER_EOF'\n")
    block, _, _ = rest.partition("\nPYINSTALLER_EOF")
    assert block.strip(), "inline PyInstaller builder block not found"
    compile(block, "build-sidecar.sh::PYINSTALLER_EOF", "exec")


def test_missing_office_package_is_fatal_not_skipped() -> None:
    def empty(_package: str) -> list[str]:
        return []

    with pytest.raises(RuntimeError, match="docx"):
        collect_office_modules(empty)


def test_missing_office_template_is_fatal_not_skipped() -> None:
    def without_templates(package: str) -> list[tuple[str, str]]:
        return [(f"/site-packages/{package}/__init__.pyi", package)]

    with pytest.raises(RuntimeError, match="default.docx"):
        collect_office_datas(without_templates)


def test_collectors_resolve_against_the_real_build_environment() -> None:
    """The packages are hard deps of matrx-files — absence is a broken env."""
    pyinstaller_hooks = pytest.importorskip("PyInstaller.utils.hooks")

    modules = collect_office_modules(pyinstaller_hooks.collect_submodules)
    for package in OFFICE_PACKAGES:
        assert package in modules, package

    datas = collect_office_datas(pyinstaller_hooks.collect_data_files)
    bundled = {
        (pathlib.PurePosixPath(destination) / pathlib.Path(source).name).as_posix()
        for source, destination in datas
    }
    for package, relative in OFFICE_REQUIRED_DATA_FILES.items():
        assert f"{package}/{relative}" in bundled


def test_parent_relative_lookups_are_derived_from_the_installed_sources() -> None:
    """The frozen-only FileNotFoundError class, guarded structurally.

    ``pptx/oxml/__init__.py`` and the five ``docx/parts/*.py`` template loaders
    open ``<their dir>/../templates/…``. Frozen modules live in the PYZ, so
    those directories are not real paths under ``sys._MEIPASS`` and the OS
    cannot resolve the ``..`` — the template ships and is still unreachable.
    Derived, never hand-listed, so an upstream release that adds another one is
    covered by the next build.
    """
    pytest.importorskip("docx")
    pytest.importorskip("pptx")

    directories = office_parent_relative_dirs()
    assert "pptx/oxml" in directories  # notesMaster.xml — speaker notes
    assert "docx/parts" in directories  # default-header/footer/styles/settings


def test_every_parent_relative_dir_gets_a_marker() -> None:
    pyinstaller_hooks = pytest.importorskip("PyInstaller.utils.hooks")

    assert DIR_MARKER.is_file()
    datas = collect_office_datas(pyinstaller_hooks.collect_data_files)
    destinations = {destination for _source, destination in datas}
    for directory in office_parent_relative_dirs():
        assert directory in destinations, directory


def test_office_codec_round_trips_from_source() -> None:
    """The same probe the frozen artifact runs, executed against the venv.

    A green result here and a red one in the frozen binary isolates the failure
    to bundling rather than to the codec.
    """
    pytest.importorskip("matrx_files")
    hook_path = _REPO_ROOT / "scripts" / "frozen_runtime_verifier_hook.py"
    namespace: dict[str, object] = {"__file__": str(hook_path)}
    exec(compile(hook_path.read_text(encoding="utf-8"), str(hook_path), "exec"), namespace)

    details = namespace["_verify_office_codec"]()  # type: ignore[operator]
    assert set(details["generated_bytes"]) == {"docx", "pptx", "xlsx"}
    assert all(size > 0 for size in details["generated_bytes"].values())
    assert all(size > 0 for size in details["extracted_chars"].values())
