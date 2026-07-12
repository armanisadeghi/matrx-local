"""On-demand installer for Settings → Capabilities (Whisper, etc.).

Mirrors the image-gen consumer installer: packages land in a user-writable
directory, progress streams over SSE, and the frozen sidecar imports them via
sys.path injection. End users never touch a terminal.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from app.common.system_logger import get_logger
from app.services.optional_packages.core import (
    TORCH_CPU_INDEX_URL,
    InstallProgress,
    find_python,
    packages_dir,
    run_pip_streaming,
)

logger = get_logger()

# capability_id → install recipe
CAPABILITY_INSTALL: dict[str, dict] = {
    "transcription": {
        "dir_name": "transcription-packages",
        # torch first; openai-whisper pulls tiktoken/numba/etc.
        "packages": ["torch>=2.6", "openai-whisper"],
        "needs_torch_index": True,
        "verify_imports": ["torch", "whisper"],
        "display_name": "Speech Transcription (Whisper)",
    },
}

_active: dict[str, InstallProgress] = {}


def get_capability_packages_dir(capability_id: str) -> Path:
    recipe = CAPABILITY_INSTALL[capability_id]
    return packages_dir(recipe["dir_name"])


def is_capability_installed(capability_id: str) -> bool:
    if capability_id not in CAPABILITY_INSTALL:
        return False
    return (get_capability_packages_dir(capability_id) / ".install-complete").exists()


def inject_capability_path(capability_id: str) -> bool:
    """Add managed packages dir to sys.path when install marker exists."""
    if not is_capability_installed(capability_id):
        return False
    pkg_dir = str(get_capability_packages_dir(capability_id))
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
        logger.debug(
            "[capabilities_installer] Injected %s into sys.path (%s)",
            pkg_dir,
            capability_id,
        )
    return True


def inject_all_capability_paths() -> list[str]:
    """Inject every completed capability package dir. Returns injected ids."""
    injected: list[str] = []
    for cap_id in CAPABILITY_INSTALL:
        if inject_capability_path(cap_id):
            injected.append(cap_id)
    return injected


def probe_module_available(module_name: str) -> bool:
    """True if module is importable (after path injection)."""
    import importlib.util

    if module_name == "fitz":
        try:
            import importlib.metadata

            importlib.metadata.version("PyMuPDF")
            return True
        except importlib.metadata.PackageNotFoundError:
            return False
    return importlib.util.find_spec(module_name) is not None


def get_active_progress(capability_id: str | None = None) -> InstallProgress | None:
    if capability_id is not None:
        return _active.get(capability_id)
    # Any running install (UI typically has one at a time)
    for progress in _active.values():
        if progress.status == "running":
            return progress
    # Fall back to most recent
    if not _active:
        return None
    return next(reversed(_active.values()))


def _do_install(capability_id: str, progress: InstallProgress) -> None:
    recipe = CAPABILITY_INSTALL[capability_id]
    pkg_dir = get_capability_packages_dir(capability_id)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    marker = pkg_dir / ".install-complete"
    marker.unlink(missing_ok=True)

    packages: list[str] = list(recipe["packages"])
    arch = platform.machine().lower()
    use_torch_cpu_index = recipe.get("needs_torch_index") and not (
        sys.platform == "darwin" and arch in ("arm64", "aarch64")
    )

    # Reuse torch from image-gen packages when present — skip re-downloading.
    from app.services.image_gen.installer import (
        get_image_gen_packages_dir,
        inject_image_gen_path,
        is_image_gen_installed,
    )

    skip_torch = False
    if recipe.get("needs_torch_index") and is_image_gen_installed():
        inject_image_gen_path()
        # Drop torch pins; whisper still needs its own non-torch deps.
        packages = [p for p in packages if not p.lower().startswith("torch")]
        skip_torch = True
        # Ensure image-gen dir is on PYTHONPATH for the verify subprocess
        # and that whisper's target install can see torch via engine path.
        logger.info("[capabilities_installer] Reusing torch from image-gen-packages")

    try:
        progress.update("preparing", 2.0, "Preparing installation directory…")

        if not packages:
            # Only torch was listed and image-gen already has it — still need whisper
            packages = ["openai-whisper"]

        if skip_torch:
            progress.update(
                "downloading",
                10.0,
                "PyTorch already installed (image generation). Downloading Whisper…",
            )
            run_pip_streaming(packages, pkg_dir, progress)
        elif recipe.get("needs_torch_index"):
            torch_pkgs = [p for p in packages if p.lower().startswith("torch")]
            rest = [p for p in packages if not p.lower().startswith("torch")]
            progress.update(
                "downloading",
                5.0,
                "Downloading PyTorch… this is the large download. "
                "Progress lines appear below as it runs.",
            )
            run_pip_streaming(
                torch_pkgs,
                pkg_dir,
                progress,
                extra_index=TORCH_CPU_INDEX_URL if use_torch_cpu_index else None,
            )
            progress.update("downloading", 55.0, "PyTorch installed ✓")
            if rest:
                progress.update(
                    "downloading",
                    58.0,
                    f"Downloading {', '.join(rest)}…",
                )
                run_pip_streaming(rest, pkg_dir, progress)
        else:
            progress.update(
                "downloading",
                10.0,
                f"Downloading {', '.join(packages)}…",
            )
            run_pip_streaming(packages, pkg_dir, progress)

        progress.update("installing", 90.0, "Packages installed ✓")

        progress.update("verifying", 92.0, "Verifying installation…")
        python = find_python()
        env = os.environ.copy()
        path_parts = [str(pkg_dir)]
        if is_image_gen_installed():
            path_parts.append(str(get_image_gen_packages_dir()))
        env["PYTHONPATH"] = (
            os.pathsep.join(path_parts) + os.pathsep + env.get("PYTHONPATH", "")
        )
        imports = ", ".join(recipe["verify_imports"])
        check = subprocess.run(
            [python, "-c", f"import {imports}; print('ok')"],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        if check.returncode != 0 or "ok" not in check.stdout:
            raise RuntimeError(
                f"Post-install import check failed:\n{check.stderr[-2000:]}"
            )
        progress.update("verifying", 97.0, "Imports verified ✓")

        marker.write_text(
            json.dumps(
                {
                    "capability_id": capability_id,
                    "packages": recipe["packages"],
                    "reused_image_gen_torch": skip_torch,
                }
            )
        )
        inject_capability_path(capability_id)
        if is_image_gen_installed():
            inject_image_gen_path()

        name = recipe.get("display_name", capability_id)
        progress.finish(f"Installation complete — {name} is ready!")

    except Exception as exc:
        progress.fail(str(exc))


async def start_capability_install(capability_id: str) -> InstallProgress:
    """Start a background install. Returns immediately.

    Raises KeyError for unknown capability_id, RuntimeError if already running.
    """
    if capability_id not in CAPABILITY_INSTALL:
        raise KeyError(capability_id)

    existing = _active.get(capability_id)
    if existing is not None and existing.status == "running":
        raise RuntimeError("Installation already in progress")

    # Also block if a different capability install is running (shared pip/python)
    for other_id, other in _active.items():
        if other_id != capability_id and other.status == "running":
            raise RuntimeError(
                f"Another capability install is in progress ({other_id})"
            )

    progress = InstallProgress(log_prefix=f"capabilities:{capability_id}")
    progress.status = "running"
    progress._loop = asyncio.get_running_loop()
    _active[capability_id] = progress

    asyncio.get_running_loop().run_in_executor(
        None, _do_install, capability_id, progress
    )
    return progress


def uses_managed_installer(capability_id: str) -> bool:
    return capability_id in CAPABILITY_INSTALL
