"""Packages present BOTH in the frozen bundle and in a managed runtime dir.

Single source of truth, consumed by every ``specs/*.spec`` and by
``scripts/build-sidecar.sh``. Never hand-add a ``collect_submodules`` for one
of these to a single spec — the four builds must not drift.

Why this file exists
--------------------
``hooks/runtime_hook.py`` and ``app/services/image_gen/installer.py`` APPEND the
managed runtime dirs (``~/.matrx/image-gen-packages``, ``~/.matrx/ner-packages``)
to ``sys.path``. That is deliberate: an optional pip ``--target`` install ships
transitive copies of FastAPI/anyio/httpx, and letting those outrank the engine's
own stack produced real shutdown/runtime breakage.

The cost of appending is that for any package present in BOTH places, the
BUNDLED copy wins — PyInstaller 6 has no ``meta_path`` FrozenImporter, so frozen
imports resolve through a ``sys.path`` hook IN PATH ORDER.

That makes a PARTIALLY collected bundle copy actively dangerous. The complete
copy on disk becomes unreachable, and the first import of a submodule that
PyInstaller's static analysis never reached raises ModuleNotFoundError — in the
frozen app ONLY. Source tests and ``uv run`` cannot reproduce it, which is what
lets it reach users.

This exact bug has now shipped four times:

* ``google.protobuf``   v1.3.107, 2026-07-12
* ``jinja2``            2026-07-18 (cb17d4eef) — ``jinja2.meta``
* ``huggingface_hub``   v1.3.145, 2026-07-19 — ``huggingface_hub.dataclasses``
* ``tqdm``               v1.3.149, 2026-07-19 — ``tqdm.contrib.logging``

The huggingface_hub case is the template for the whole class: the bundle carried
138 of 181 modules. Nothing in hf 1.8's ``__init__.py`` names ``.dataclasses``,
so analysis never reached it — while ``transformers>=5.4`` imports it directly
(``from huggingface_hub.dataclasses import strict``). Every image-gen model load
died with ``ModuleNotFoundError: No module named 'huggingface_hub.dataclasses'``
even though a complete huggingface_hub 1.24.0 sat on disk, unreachable.

Collecting these packages WHOLE guarantees that if the bundle copy shadows the
managed one, it is at least complete.

Maintaining the contract
------------------------
The shared-package set is generated from the exact frozen-build and managed
runtime dependency closures. Run ``scripts/generate-runtime-manifests.py`` after
any dependency change and commit its target manifests. The release gate runs it
with ``--check`` and refuses stale or incomplete contracts.
"""

import json
import importlib.metadata
from pathlib import Path


_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "runtime-manifests"
    / "image-gen-contract.json"
)
if not _CONTRACT_PATH.is_file():
    raise RuntimeError(
        f"managed-runtime contract missing: {_CONTRACT_PATH}; run "
        "scripts/generate-runtime-manifests.py"
    )
_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
if _CONTRACT.get("schema_version") != 1:
    raise RuntimeError(
        f"unsupported managed-runtime contract schema in {_CONTRACT_PATH}"
    )

MANAGED_RUNTIME_SHARED_DISTRIBUTIONS_BY_TARGET = {
    target: tuple(distributions)
    for target, distributions in _CONTRACT["shared_distributions_by_target"].items()
}
MANAGED_RUNTIME_SHARED_PACKAGES_BY_TARGET = {
    target: tuple(packages)
    for target, packages in _CONTRACT["shared_import_packages_by_target"].items()
}


def managed_runtime_shared_packages(target: str) -> tuple[str, ...]:
    """Validate target build versions and return deterministic import roots."""
    try:
        distributions = MANAGED_RUNTIME_SHARED_DISTRIBUTIONS_BY_TARGET[target]
        expected_versions = _CONTRACT["shared_versions_by_target"][target]
        import_roots = MANAGED_RUNTIME_SHARED_PACKAGES_BY_TARGET[target]
    except KeyError as exc:
        raise RuntimeError(f"no managed-runtime shared contract for {target}") from exc
    for distribution in distributions:
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                f"required target distribution {distribution!r} is absent"
            ) from exc
        if actual != expected_versions[distribution]:
            raise RuntimeError(
                f"target distribution {distribution!r} has version {actual!r}; "
                f"contract requires {expected_versions[distribution]!r}"
            )
    return tuple(import_roots)


def collect_managed_runtime_modules(collect_submodules, *, target: str):
    """Return every submodule of every shared package, for ``hiddenimports``.

    ``collect_submodules`` is injected rather than imported so this module stays
    importable outside a PyInstaller build (the unit test imports it directly).
    Every package in the generated contract is required on the release build
    host. Missing packages and collection failures are fatal: silently skipping
    one is how frozen-only outages reached production.
    """
    modules: list[str] = []
    for package in managed_runtime_shared_packages(target):
        try:
            collected = collect_submodules(package)
        except Exception as exc:
            raise RuntimeError(
                f"failed to collect required shared package {package!r}"
            ) from exc
        if not collected:
            raise RuntimeError(
                f"required shared package {package!r} is absent or has no modules; "
                "run the exact release uv sync before PyInstaller"
            )
        modules += collected
    return modules
