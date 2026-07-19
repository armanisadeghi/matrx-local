"""Opt-in PyInstaller runtime hook for release artifact verification.

This hook is inert for ordinary application launches. Release/smoke tooling sets
``MATRX_FROZEN_RUNTIME_VERIFY=1`` and points
``MATRX_FROZEN_RUNTIME_PATH`` at the exact locked image-generation environment.
The directory is appended, matching production precedence, then critical lazy
imports execute inside the real frozen CPython process.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path


if os.environ.get("MATRX_FROZEN_RUNTIME_VERIFY") == "1":
    result: dict[str, object] = {
        "contract": "unknown",
        "ok": False,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    try:
        runtime_path = Path(os.environ["MATRX_FROZEN_RUNTIME_PATH"]).resolve(strict=True)
        if not runtime_path.is_dir():
            raise RuntimeError(f"managed runtime is not a directory: {runtime_path}")
        runtime_text = str(runtime_path)
        while runtime_text in sys.path:
            sys.path.remove(runtime_text)
        # Production contract: frozen/core dependencies win. Never prepend here.
        sys.path.append(runtime_text)

        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        contract_path = (
            base / "config" / "runtime-manifests" / "image-gen-contract.json"
        )
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        result["contract"] = contract["contract_sha256"]
        if result["python"] != contract["python_minor"]:
            raise RuntimeError(
                f"frozen Python {result['python']} != contract {contract['python_minor']}"
            )

        target = os.environ["MATRX_FROZEN_RUNTIME_TARGET"]
        target_path = (
            base / "config" / "runtime-manifests" / f"image-gen-{target}.json"
        )
        target_manifest = json.loads(target_path.read_text(encoding="utf-8"))
        if target_manifest.get("supported") is not True:
            raise RuntimeError(f"verification invoked for unsupported target {target}")
        if target_manifest.get("target") != target:
            raise RuntimeError("embedded target manifest identifies the wrong platform")
        if target_manifest.get("contract_sha256") != contract["contract_sha256"]:
            raise RuntimeError("embedded target manifest is stale")
        lock_path = target_path.parent / target_manifest["lock_file"]
        if hashlib.sha256(lock_path.read_bytes()).hexdigest() != target_manifest.get(
            "lock_sha256"
        ):
            raise RuntimeError("embedded target requirements digest is invalid")
        expected_versions = {
            item["name"].lower().replace("_", "-"): item["version"]
            for item in target_manifest["packages"]
        }
        actual_versions = {
            distribution.metadata["Name"].lower().replace("_", "-"): distribution.version
            for distribution in importlib.metadata.distributions(path=[runtime_text])
        }
        if actual_versions != expected_versions:
            raise RuntimeError(
                "isolated runtime package inventory differs from target manifest: "
                f"expected={expected_versions!r}, actual={actual_versions!r}"
            )

        imported: dict[str, str] = {}
        for module_name in contract["runtime_imports"]:
            module = importlib.import_module(module_name)
            imported[module_name] = str(getattr(module, "__file__", "<built-in>"))
        for module_name, attributes in contract["runtime_attributes"].items():
            module = importlib.import_module(module_name)
            for attribute in attributes:
                getattr(module, attribute)

        for module_name in (
            "accelerate",
            "diffusers",
            "gguf",
            "peft",
            "sentencepiece",
            "torch",
            "torchvision",
            "transformers",
        ):
            origin = Path(imported[module_name]).resolve(strict=False)
            if not origin.is_relative_to(runtime_path):
                raise RuntimeError(
                    f"managed module {module_name} resolved outside isolated runtime: {origin}"
                )

        import torch
        import torchvision

        tensor = torch.tensor([1.0, 2.0]) + 1
        if tensor.tolist() != [2.0, 3.0]:
            raise RuntimeError("PyTorch CPU operation returned an unexpected result")
        # Torchvision loads its `_C` library through torch.ops rather than as a
        # normal PyInit extension. `_has_ops()` proves that native activation.
        if not torchvision.extension._has_ops():
            raise RuntimeError("Torchvision native operators failed to load")

        result["imports"] = imported
        result["target"] = target
        result["torch"] = str(torch.__version__)
        result["torchvision"] = str(torchvision.__version__)
        result["ok"] = True
    except BaseException as exc:  # release gate must serialize every failure
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()

    print("MATRX_FROZEN_RUNTIME_VERIFY=" + json.dumps(result, sort_keys=True), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result["ok"] else 86)
