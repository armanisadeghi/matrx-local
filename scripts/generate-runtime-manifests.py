#!/usr/bin/env python3
"""Generate/check the frozen-engine ↔ managed-runtime release contract.

The managed image runtime is installed outside the PyInstaller executable and
appended to ``sys.path``.  Every distribution present in both environments is
therefore load-bearing from the frozen bundle.  This tool derives that overlap
from the exact build environment and records it in target manifests consumed by
the PyInstaller specs and release verification.

Run after an exact ``uv sync`` with the release extras, including ``image-gen``:

    python scripts/generate-runtime-manifests.py          # update manifests
    python scripts/generate-runtime-manifests.py --check  # release gate
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import sys
import tomllib
from pathlib import Path
from typing import Iterable

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "config" / "runtime-manifests"
FROZEN_EXCLUDED_DISTRIBUTIONS = {
    "torch",
    "torchvision",
    "torchaudio",
}
CRITICAL_FROZEN_MODULES = (
    "doctest",
    "filecmp",
    "huggingface_hub.dataclasses",
    "jinja2.meta",
    "modulefinder",
    "timeit",
    "tqdm.contrib.logging",
)
RUNTIME_IMPORTS = (
    "accelerate",
    "diffusers",
    "diffusers.loaders.single_file_model",
    "diffusers.models.autoencoders.autoencoder_kl",
    "diffusers.models.autoencoders.autoencoder_kl_wan",
    "diffusers.pipelines.pipeline_utils",
    "filecmp",
    "gguf",
    "huggingface_hub.dataclasses",
    "jinja2.meta",
    "peft",
    "sentencepiece",
    "torch",
    "torchvision",
    "tqdm.contrib.logging",
    "transformers",
)
RUNTIME_ATTRIBUTES = {
    "diffusers": (
        "DiffusionPipeline",
        "FluxPipeline",
        "Flux2KleinPipeline",
        "LTX2Pipeline",
        "LTXImageToVideoPipeline",
        "LTXPipeline",
        "QwenImagePipeline",
        "StableDiffusionPipeline",
        "StableDiffusionXLPipeline",
        "WanPipeline",
        "WanImageToVideoPipeline",
        "ZImagePipeline",
    ),
    "transformers": (
        "AutoImageProcessor",
        "AutoTokenizer",
    ),
}


def _normalize(name: str) -> str:
    return canonicalize_name(name)


def _read_project_contract() -> tuple[list[str], list[str]]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = list(project["project"]["dependencies"])
    optional = project["project"]["optional-dependencies"]
    for extra in ("transcription", "scheduler"):
        core.extend(optional.get(extra, ()))
    return core, list(optional["image-gen"])


def _read_runtime_input() -> list[str]:
    path = MANIFEST_DIR / "image-gen.in"
    requirements = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for raw in requirements:
        requirement = Requirement(raw)
        if str(requirement.specifier).count("==") != 1 or "," in str(requirement.specifier):
            raise RuntimeError(f"runtime input must use one exact == pin: {raw}")
    return requirements


def _requirement_names(requirements: Iterable[str]) -> set[str]:
    return {_normalize(Requirement(requirement).name) for requirement in requirements}


def _distribution_closure(requirements: Iterable[str]) -> set[str]:
    environment = default_environment()
    environment["extra"] = ""
    pending = list(_requirement_names(requirements))
    result: set[str] = set()
    missing: list[str] = []

    while pending:
        name = pending.pop()
        if name in result:
            continue
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        result.add(name)
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            pending.append(_normalize(requirement.name))

    if missing:
        raise RuntimeError(
            "exact release environment is missing required distributions: "
            + ", ".join(sorted(set(missing)))
            + ". Run uv sync --frozen --extra transcription --extra scheduler "
            "--extra image-gen before generating/checking manifests."
        )
    return result


def _top_level_imports(distributions: Iterable[str]) -> dict[str, list[str]]:
    package_map = metadata.packages_distributions()
    result: dict[str, list[str]] = {}
    for distribution in sorted(distributions):
        imports = sorted(
            package
            for package, owners in package_map.items()
            if any(_normalize(owner) == distribution for owner in owners)
        )
        if not imports:
            candidate = distribution.replace("-", "_")
            try:
                __import__(candidate)
            except Exception as exc:
                raise RuntimeError(
                    f"cannot map distribution {distribution!r} to a top-level import"
                ) from exc
            imports = [candidate]
        result[distribution] = imports
    return result


def _lock_payload() -> tuple[str, dict[str, str]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = []
    versions: dict[str, str] = {}
    for package in lock.get("package", ()):  # uv.lock uses [[package]]
        canonical = dict(package)
        if canonical.get("name") == "matrx-local":
            # Release version bumps must not invalidate the dependency contract.
            canonical.pop("version", None)
        else:
            name = canonical.get("name")
            version = canonical.get("version")
            if isinstance(name, str) and isinstance(version, str):
                versions[_normalize(name)] = version
        packages.append(canonical)
    payload = json.dumps(packages, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest(), versions


def _contract_payload() -> dict:
    core_requirements, project_image_requirements = _read_project_contract()
    runtime_requirements = _read_runtime_input()
    project_names = _requirement_names(project_image_requirements)
    runtime_names = _requirement_names(runtime_requirements)
    if runtime_names != project_names:
        raise RuntimeError(
            "image-gen.in and pyproject.toml [image-gen] differ: "
            f"only_runtime={sorted(runtime_names - project_names)}, "
            f"only_project={sorted(project_names - runtime_names)}"
        )

    core_closure = _distribution_closure(core_requirements)
    managed_closure = _distribution_closure(runtime_requirements)
    shared_distributions = sorted(
        (core_closure & managed_closure) - FROZEN_EXCLUDED_DISTRIBUTIONS
    )
    shared_import_map = _top_level_imports(shared_distributions)
    shared_imports = sorted(
        {package for packages in shared_import_map.values() for package in packages}
    )
    lock_sha256, _locked_versions = _lock_payload()
    direct_versions = {
        _normalize(Requirement(raw).name): str(Requirement(raw).specifier).removeprefix("==")
        for raw in runtime_requirements
    }

    source_contract = {
        "core_requirements": sorted(core_requirements),
        "project_image_requirements": sorted(project_image_requirements),
        "runtime_requirements": runtime_requirements,
        "lock_graph_sha256": lock_sha256,
        "critical_frozen_modules": list(CRITICAL_FROZEN_MODULES),
        "runtime_imports": list(RUNTIME_IMPORTS),
        "runtime_attributes": {
            module: list(attributes)
            for module, attributes in RUNTIME_ATTRIBUTES.items()
        },
    }
    contract_sha256 = hashlib.sha256(
        json.dumps(source_contract, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "python_minor": "3.13",
        "contract_sha256": contract_sha256,
        "lock_graph_sha256": lock_sha256,
        "lock_source": "uv.lock",
        "managed_requirements": runtime_requirements,
        "managed_direct_versions": direct_versions,
        "shared_distributions": shared_distributions,
        "shared_import_map": shared_import_map,
        "shared_import_packages": shared_imports,
        "critical_frozen_modules": list(CRITICAL_FROZEN_MODULES),
        "runtime_imports": list(RUNTIME_IMPORTS),
        "runtime_attributes": {
            module: list(attributes)
            for module, attributes in RUNTIME_ATTRIBUTES.items()
        },
    }


def _serialized(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    contract = _contract_payload()
    expected = {"image-gen-contract.json": contract}

    stale: list[str] = []
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    for name, value in expected.items():
        path = MANIFEST_DIR / name
        content = _serialized(value)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(name)
        else:
            path.write_text(content, encoding="utf-8")

    if stale:
        print(
            "runtime manifest contract is missing/stale: " + ", ".join(stale),
            file=sys.stderr,
        )
        print(
            "run: .venv/bin/python scripts/generate-runtime-manifests.py",
            file=sys.stderr,
        )
        return 1
    verb = "validated" if args.check else "generated"
    print(
        f"{verb} image runtime contract {contract['contract_sha256'][:12]} "
        f"({len(contract['shared_distributions'])} shared distributions, "
        f"{len(contract['shared_import_packages'])} import roots)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
