"""Opt-in PyInstaller runtime hook for release artifact verification.

This hook is inert for ordinary application launches. Release/smoke tooling sets
``MATRX_FROZEN_RUNTIME_VERIFY=1`` and points
``MATRX_FROZEN_RUNTIME_PATH`` at the exact locked image-generation environment.
The directory is appended, matching production precedence, then critical lazy
imports execute inside the real frozen CPython process.
"""

from __future__ import annotations

import importlib
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

        imported: dict[str, str] = {}
        for module_name in contract["runtime_imports"]:
            module = importlib.import_module(module_name)
            imported[module_name] = str(getattr(module, "__file__", "<built-in>"))
        for module_name, attributes in contract["runtime_attributes"].items():
            module = importlib.import_module(module_name)
            for attribute in attributes:
                getattr(module, attribute)

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
