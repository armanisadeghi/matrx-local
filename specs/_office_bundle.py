"""Office (docx/pptx/xlsx) codec bundling — one source of truth for four specs.

Single source of truth, consumed by every ``specs/*.spec`` and by
``scripts/build-sidecar.sh``. Never hand-add a ``collect_submodules`` /
``collect_data_files`` for one of these to a single spec — the four builds must
not drift.

Why this file exists
--------------------
The canonical Office codec (``matrx_files.specific_handlers.office``) and every
renderer it drives — python-docx, python-pptx, openpyxl, xlsxwriter — are
imported LAZILY, inside functions:

* ``app/tools/tools/file_ops.py::_read_office``     (Read of an Office file)
* ``app/tools/tools/media.py::tool_office_generate`` (the OfficeGenerate tool)

PyInstaller's static analysis therefore reaches NONE of it. Without explicit
collection the frozen sidecar raises ``ModuleNotFoundError`` the moment a user
reads or generates an Office document — while ``uv run`` and every source test
pass, which is exactly what lets it reach users.

Data files are the half that module lists cannot cover
------------------------------------------------------
``docx.Document()`` and ``pptx.Presentation()`` called with no argument load
``docx/templates/default.docx`` and ``pptx/templates/default.pptx`` from beside
the package. Those are DATA, not modules: a bundle can carry every python-docx
module and still fail at generation because the template never shipped. Both
templates are asserted present below.

A collected data file is not necessarily a REACHABLE one
--------------------------------------------------------
Both packages also load templates through a path that walks UP out of a
subpackage — ``pptx/oxml/__init__.py`` opens
``<dir of pptx/oxml>/../templates/notesMaster.xml``, and five modules under
``docx/parts/`` do the same for ``default-styles.xml`` and friends. In a
one-file bundle the modules live in the PYZ, so ``pptx/oxml/`` and
``docx/parts/`` are NOT real directories under ``sys._MEIPASS`` — and an OS
resolves ``a/b/../c`` only when ``a/b`` exists. The template is right there in
the archive and the open still raises ``FileNotFoundError``.

Observed 2026-08-09 in the first frozen Linux sidecar ever built with the Office
codec: generating a .pptx whose slides carry speaker notes died on
``/tmp/_MEIxxxx/pptx/oxml/../templates/notesMaster.xml``. The referencing
directories are DERIVED from the installed sources below, not hand-listed, so a
python-docx/python-pptx upgrade that adds another one is covered automatically.

Failures are FATAL, never skipped
---------------------------------
Each package here is a hard dependency of ``matrx-files``, which is a hard
dependency of this project — absence means the build environment is wrong, not
that Office support is optional. These collectors previously sat inside a bare
``except Exception: pass`` in all four specs, which is precisely the silent-skip
that shipped ``google.protobuf`` / ``jinja2`` / ``huggingface_hub`` / ``tqdm``
frozen-only outages (see ``specs/_managed_runtime_bundle.py``). A build host
missing python-docx must fail the build, not quietly produce a sidecar that
cannot open a Word file.
"""

import importlib.util
import re
from pathlib import Path


# Import roots whose submodules and data files must be collected whole.
OFFICE_PACKAGES = ("docx", "pptx", "openpyxl", "xlsxwriter", "et_xmlfile")

# Package-relative data files that MUST land in the artifact. python-docx and
# python-pptx load these when constructing an empty document/presentation.
OFFICE_REQUIRED_DATA_FILES = {
    "docx": "templates/default.docx",
    "pptx": "templates/default.pptx",
}

# An empty committed file, shipped into each directory that must exist inside
# sys._MEIPASS. PyInstaller creates a destination directory only when something
# lands in it, and a marker is the only inert way to say "this directory itself
# is the deliverable". Never ship a ``.py`` file for this — a source file beside
# the frozen modules is exactly the partial-shadow hazard this repo keeps
# getting bitten by.
DIR_MARKER = Path(__file__).resolve().parent / "_bundle_dir_marker.txt"

# A path built from ``__file__`` that steps UP through ``".."`` — the shape that
# needs its own directory to physically exist. Matched per LINE: the real call
# is ``os.path.join(os.path.split(__file__)[0], "..", …)``, whose nested
# parentheses defeat any ``[^)]*`` bound. Over-matching only costs an empty
# directory; under-matching costs a frozen-only FileNotFoundError.
_PARENT_RELATIVE_JOIN = re.compile(r"(?:os\.path\.join|Path)\(.*['\"]\.\.['\"]")


def _package_dir(package: str) -> Path | None:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def office_parent_relative_dirs() -> tuple[str, ...]:
    """Return archive directories that must exist for ``../`` lookups to work.

    Derived by reading the INSTALLED package sources rather than hand-listed,
    so an upstream release that moves or adds one of these lookups is picked up
    by the next build instead of shipping a frozen-only ``FileNotFoundError``.
    """
    directories: set[str] = set()
    for package in OFFICE_PACKAGES:
        root = _package_dir(package)
        if root is None:
            raise RuntimeError(
                f"required Office package {package!r} is not importable; "
                "run the exact release uv sync before PyInstaller"
            )
        for module in root.rglob("*.py"):
            relative = module.parent.relative_to(root)
            if relative == Path("."):
                continue  # the package root always materializes
            try:
                source = module.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "__file__" not in source:
                continue
            if any(_PARENT_RELATIVE_JOIN.search(line) for line in source.splitlines()):
                directories.add(f"{package}/{relative.as_posix()}")
    return tuple(sorted(directories))


def collect_office_modules(collect_submodules):
    """Return every submodule of every Office package, for ``hiddenimports``.

    ``collect_submodules`` is injected rather than imported so this module stays
    importable outside a PyInstaller build (the unit test imports it directly).
    """
    modules: list[str] = []
    for package in OFFICE_PACKAGES:
        try:
            collected = collect_submodules(package)
        except Exception as exc:
            raise RuntimeError(
                f"failed to collect required Office package {package!r}"
            ) from exc
        if not collected:
            raise RuntimeError(
                f"required Office package {package!r} is absent or has no modules; "
                "run the exact release uv sync before PyInstaller"
            )
        modules += collected
    return modules


def collect_office_datas(collect_data_files):
    """Return every Office package data file, asserting the default templates.

    A missing template is invisible to a module-level check and fatal at
    runtime, so it is verified here by destination path — the same path the
    frozen process resolves from ``sys._MEIPASS``.
    """
    datas: list[tuple[str, str]] = []
    for package in OFFICE_PACKAGES:
        try:
            collected = collect_data_files(package)
        except Exception as exc:
            raise RuntimeError(
                f"failed to collect data files for Office package {package!r}"
            ) from exc
        datas += collected

    bundled = {
        (Path(destination) / Path(source).name).as_posix()
        for source, destination in datas
    }
    for package, relative in OFFICE_REQUIRED_DATA_FILES.items():
        expected = f"{package}/{relative}"
        if expected not in bundled:
            raise RuntimeError(
                f"required Office template {expected!r} was not collected; "
                f"{package} would raise at document creation in the frozen app"
            )

    # Materialize every directory a ``../templates/…`` lookup walks up out of.
    if not DIR_MARKER.is_file():
        raise RuntimeError(f"bundle directory marker is missing: {DIR_MARKER}")
    existing = {Path(destination).as_posix() for _source, destination in datas}
    for directory in office_parent_relative_dirs():
        if directory not in existing:
            datas.append((str(DIR_MARKER), directory))
    return datas
