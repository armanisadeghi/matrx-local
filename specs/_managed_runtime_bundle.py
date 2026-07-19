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

Adding to this list
-------------------
Add a top-level package here as soon as ANY managed runtime installs it and the
engine also bundles it (directly or transitively). Being conservative is cheap;
each entry costs bundle size, but a missing entry ships a frozen-only crash.
``tests/unit/test_managed_runtime_bundle.py`` pins the invariant.
"""

# Top-level import names, not pip names.
MANAGED_RUNTIME_SHARED_PACKAGES = (
    # transformers imports jinja2.meta lazily for chat-template tool schemas.
    "jinja2",
    # transformers/diffusers/accelerate all import huggingface_hub submodules
    # that hf's own __init__ never names. See the module docstring.
    "huggingface_hub",
    # transformers imports tqdm.contrib.logging through AutoImageProcessor.
    # PyInstaller's static graph included only part of tqdm in v1.3.149, so the
    # frozen copy shadowed the complete managed-runtime package and every image
    # and video model load failed.
    "tqdm",
)


def collect_managed_runtime_modules(collect_submodules):
    """Return every submodule of every shared package, for ``hiddenimports``.

    ``collect_submodules`` is injected rather than imported so this module stays
    importable outside a PyInstaller build (the unit test imports it directly).
    A package absent from the build host is skipped: an optional extra that is
    not installed cannot be shadowing anything in that build.
    """
    modules: list[str] = []
    for package in MANAGED_RUNTIME_SHARED_PACKAGES:
        try:
            modules += collect_submodules(package)
        except Exception:
            # Absent on this build host — nothing to collect, nothing to shadow.
            pass
    return modules
