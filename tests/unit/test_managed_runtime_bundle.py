"""The frozen bundle must collect shared packages WHOLE.

Guards the bug class that has now shipped four times (google.protobuf 2026-07-12,
jinja2 2026-07-18, huggingface_hub and tqdm 2026-07-19): a package present in BOTH the
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
import json
import importlib.util
import sys

import pytest

_SPECS_DIR = pathlib.Path(__file__).resolve().parents[2] / "specs"
sys.path.insert(0, str(_SPECS_DIR))

from _managed_runtime_bundle import (  # noqa: E402
    MANAGED_RUNTIME_SHARED_PACKAGES_BY_TARGET,
    collect_managed_runtime_modules,
    managed_runtime_excluded_packages,
)

_SPEC_FILES = sorted(_SPECS_DIR.glob("*.spec"))
_TARGET_BY_SPEC = {
    "matrx-engine-aarch64-apple-darwin.spec": "aarch64-apple-darwin",
    "matrx-engine-x86_64-apple-darwin.spec": "x86_64-apple-darwin",
    "matrx-engine-x86_64-pc-windows-msvc.spec": "x86_64-pc-windows-msvc",
    "matrx-engine-x86_64-unknown-linux-gnu.spec": "x86_64-unknown-linux-gnu",
}
_ALL_SHARED_PACKAGES = {
    package
    for packages in MANAGED_RUNTIME_SHARED_PACKAGES_BY_TARGET.values()
    for package in packages
}
_LOCAL_TEST_TARGET = "aarch64-apple-darwin"


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
        target = _TARGET_BY_SPEC[spec.name]
        assert "_managed_runtime_bundle" in text, f"{spec.name} bypasses the shared list"
        assert "_shared_runtime_mods" in text, f"{spec.name} never uses the collected mods"
        assert "_managed_runtime_excludes" in text, (
            f"{spec.name} does not exclude managed-only packages"
        )
        assert f"target='{target}'" in text
        for package in MANAGED_RUNTIME_SHARED_PACKAGES_BY_TARGET[target]:
            # Both quote styles — a double-quoted hand-add is the same drift.
            for literal in (f"collect_submodules('{package}')",
                            f'collect_submodules("{package}")'):
                assert literal not in text, (
                    f"{spec.name} hand-collects {package}; add it to "
                    "the generated target contract instead"
                )


def test_build_sidecar_fallback_consumes_the_shared_list() -> None:
    """scripts/build-sidecar.sh is a real build path, not a dead fallback."""
    build_script = _SPECS_DIR.parent / "scripts" / "build-sidecar.sh"
    text = build_script.read_text()
    assert "_managed_runtime_bundle" in text
    assert "managed_runtime_shared_packages" in text
    assert "managed_runtime_excluded_packages" in text
    for package in _ALL_SHARED_PACKAGES:
        assert f'"--collect-submodules", "{package}"' not in text, (
            f"build-sidecar.sh hand-collects {package}; it must read the shared list"
        )


def test_outage_packages_are_declared_shared() -> None:
    """Frozen-only outages. Do not remove without reading the module docstring."""
    assert "huggingface_hub" in _ALL_SHARED_PACKAGES
    assert "jinja2" in _ALL_SHARED_PACKAGES
    assert "tqdm" in _ALL_SHARED_PACKAGES


def test_heavy_managed_packages_are_excluded_from_every_frozen_target() -> None:
    expected = {
        "accelerate",
        "diffusers",
        "gguf",
        "peft",
        "sentencepiece",
        "torch",
        "torchvision",
        "transformers",
    }
    for target in MANAGED_RUNTIME_SHARED_PACKAGES_BY_TARGET:
        assert set(managed_runtime_excluded_packages(target)) == expected


def test_collection_reaches_submodules_static_analysis_misses() -> None:
    """The actual regression.

    `huggingface_hub.dataclasses` is imported by transformers>=5.4
    (`from huggingface_hub.dataclasses import strict`) but is named nowhere in
    huggingface_hub's own __init__, so PyInstaller's static analysis never
    reaches it. Same story for `jinja2.meta`, which transformers imports lazily
    for chat-template tool schemas. Transformers 5.14's AutoImageProcessor
    reaches `tqdm.contrib.logging`, which the v1.3.149 bundle omitted.
    """
    try:
        from PyInstaller.utils.hooks import collect_submodules
    except ImportError:  # pragma: no cover - PyInstaller is a build-host dep
        import pytest

        pytest.skip("PyInstaller not installed on this host")

    modules = collect_managed_runtime_modules(
        collect_submodules, target=_LOCAL_TEST_TARGET
    )

    assert "huggingface_hub.dataclasses" in modules, (
        "huggingface_hub.dataclasses missing -- every image-gen model load dies "
        "with ModuleNotFoundError in the frozen app (v1.3.145)"
    )
    assert "jinja2.meta" in modules
    assert "tqdm.contrib.logging" in modules, (
        "tqdm.contrib.logging missing -- Transformers AutoImageProcessor and "
        "therefore every Diffusers image/video model load dies in the frozen app"
    )


def test_absent_required_shared_package_fails_the_build() -> None:
    """A missing shared package invalidates the frozen/runtime contract."""

    def exploding_collect(package: str) -> list[str]:
        raise ModuleNotFoundError(package)

    with pytest.raises(RuntimeError, match="required shared package"):
        collect_managed_runtime_modules(
            exploding_collect, target=_LOCAL_TEST_TARGET
        )


def test_release_probe_matches_runtime_activation_contract() -> None:
    """Installer validation and frozen release proof must exercise identical paths."""
    from app.services.image_gen.installer import (
        CRITICAL_PIPELINE_CLASSES,
        CRITICAL_RUNTIME_IMPORTS,
    )

    contract_path = (
        _SPECS_DIR.parent
        / "config"
        / "runtime-manifests"
        / "image-gen-contract.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert set(contract["runtime_imports"]) == set(CRITICAL_RUNTIME_IMPORTS)
    assert set(contract["runtime_attributes"]["diffusers"]) == set(
        CRITICAL_PIPELINE_CLASSES
    )
    assert {"filecmp", "doctest", "modulefinder", "timeit"} <= set(
        contract["critical_frozen_modules"]
    )


def test_lock_graph_markers_are_host_independent() -> None:
    """The same lock produces correct Linux/macOS closures on every host."""
    script = _SPECS_DIR.parent / "scripts" / "generate-runtime-manifests.py"
    spec = importlib.util.spec_from_file_location("runtime_manifest_generator", script)
    assert spec and spec.loader
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    core, _ = generator._read_project_contract()
    _, _, packages = generator._lock_payload()
    linux = generator._locked_distribution_closure(
        core, target="x86_64-unknown-linux-gnu", packages=packages
    )
    mac = generator._locked_distribution_closure(
        core, target="aarch64-apple-darwin", packages=packages
    )
    assert "tflite-runtime" not in linux
    assert "pyobjc-framework-quartz" not in linux
    assert "pyobjc-framework-quartz" in mac
    first = generator._contract_payload()
    assert first == generator._contract_payload()
    assert "colorama" in first["shared_distributions_by_target"][
        "x86_64-pc-windows-msvc"
    ]
    assert "colorama" not in first["shared_distributions_by_target"][
        "x86_64-unknown-linux-gnu"
    ]


def test_accelerator_and_linux_floor_are_explicit() -> None:
    manifests = _SPECS_DIR.parent / "config" / "runtime-manifests"
    for target in (
        "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu",
    ):
        manifest = json.loads((manifests / f"image-gen-{target}.json").read_text())
        versions = {item["name"]: item["version"] for item in manifest["packages"]}
        assert manifest["torch_variant"] == "cu126"
        assert versions["torch"].endswith("+cu126")
        assert versions["torchvision"].endswith("+cu126")
    linux = json.loads(
        (manifests / "image-gen-x86_64-unknown-linux-gnu.json").read_text()
    )
    assert linux["minimum_glibc"] == "2.28"


def test_frozen_archive_normalizes_native_extension_modules() -> None:
    script = _SPECS_DIR.parent / "scripts" / "verify-frozen-runtime.py"
    spec = importlib.util.spec_from_file_location("frozen_runtime_verifier", script)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)

    assert (
        verifier._extension_module_name(
            "numpy/_core/_multiarray_umath.cpython-313-darwin.so"
        )
        == "numpy._core._multiarray_umath"
    )
    assert (
        verifier._extension_module_name("tokenizers/tokenizers.abi3.so")
        == "tokenizers.tokenizers"
    )
    assert (
        verifier._extension_module_name(r"regex\_regex.cp313-win_amd64.pyd")
        == "regex._regex"
    )
    assert verifier._extension_module_name("not-a-module.dylib") is None
