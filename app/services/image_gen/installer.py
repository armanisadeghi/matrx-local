"""Image generation package installer.

Handles on-demand installation of torch, diffusers, transformers, accelerate
into a dedicated user-writable directory alongside the frozen binary.  This
keeps the sidecar binary small (no PyTorch bundled) while letting consumers
install image generation with a single in-app click — no terminal, no uv,
no developer knowledge required.

The packages are installed into:
  macOS / Linux  →  ~/.matrx/image-gen-packages/
  Windows        →  %LOCALAPPDATA%\\AI Matrx\\image-gen-packages\\

The runtime_hook.py adds this directory to sys.path on every engine start
once the install is complete, so the frozen binary can import them.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from app.common.system_logger import get_logger
from app.services.optional_packages.core import (
    TORCH_CPU_INDEX_URL as _TORCH_CPU_INDEX_URL,
    InstallProgress,
    find_python as _find_python,
    packages_dir,
    run_pip_streaming as _run_pip_streaming,
)

logger = get_logger()

# ── Package list ──────────────────────────────────────────────────────────────

# All packages to install (order matters — torch first so its deps land before
# the diffusers wheel asks for them).
# diffusers >= 0.39 is REQUIRED by the current model catalogs (Flux2Klein /
# ZImage / QwenImage / Wan / LTX pipelines). Keep these pins in sync with
# pyproject.toml [image-gen] and service.py MIN_DIFFUSERS_VERSION.
IMAGE_GEN_PACKAGES = [
    "torch>=2.6",
    "torchvision",
    "diffusers>=0.39.0",
    "transformers>=4.51",
    "accelerate>=1.0",
    "sentencepiece>=0.2.0",
    # protobuf intentionally NOT installed here. The engine bundles its own
    # (core dep via matrx-ai → xai-sdk, which hard-rejects protobuf 7). This
    # dir is PREPENDED to sys.path, so a protobuf copy here shadowed the
    # engine's and killed matrx-ai init on every packaged boot ("Unsupported
    # protobuf version: 7.34.1", 2026-07-12). transformers' slow-tokenizer
    # paths import the engine's bundled protobuf just fine.
    # inject_image_gen_path() purges any copy left by older installs.
    "huggingface_hub>=0.22.0",
]

_TORCH_PACKAGES = {"torch", "torchvision", "torchaudio"}


# ── Install directory ─────────────────────────────────────────────────────────


def get_image_gen_packages_dir() -> Path:
    """Platform-appropriate directory for image-gen packages."""
    return packages_dir("image-gen-packages")


def is_image_gen_installed() -> bool:
    """True if the managed image-gen packages directory is complete."""
    return (get_image_gen_packages_dir() / ".install-complete").exists()


def get_installed_package_versions() -> dict[str, str]:
    """Versions of the managed packages, read from *.dist-info dir names.

    Works without importing the packages — safe to call at any time.
    Returns e.g. {"diffusers": "0.39.0", "torch": "2.6.0", ...}.
    """
    versions: dict[str, str] = {}
    pkg_dir = get_image_gen_packages_dir()
    if not pkg_dir.exists():
        return versions
    try:
        for entry in pkg_dir.glob("*.dist-info"):
            stem = entry.name[: -len(".dist-info")]
            name, _, version = stem.rpartition("-")
            if name and version:
                versions[name.replace("_", "-").lower()] = version
    except OSError:
        pass
    return versions


def needs_upgrade() -> bool:
    """True when the install marker exists but diffusers is older than the
    catalog's minimum (service.py MIN_DIFFUSERS_VERSION). POST /image-gen/install
    re-runs pip with the upgraded pins in that case instead of short-circuiting.
    """
    if not is_image_gen_installed():
        return False
    from app.services.image_gen.service import (  # noqa: PLC0415 — avoid cycle at import time
        MIN_DIFFUSERS_VERSION,
        _parse_version,
    )

    installed = get_installed_package_versions().get("diffusers")
    if installed is None:
        return True  # marker without diffusers on disk — reinstall
    return _parse_version(installed) < MIN_DIFFUSERS_VERSION


def _purge_shadowing_protobuf(pkg_dir: Path) -> None:
    """Delete any protobuf copy from the managed image-gen dir — LOUDLY.

    Older installs pip-installed protobuf into this dir (it was in
    IMAGE_GEN_PACKAGES until 2026-07-13). Because the dir is PREPENDED to
    sys.path, that copy shadowed the engine's own protobuf for importlib
    metadata lookups — and, in frozen builds that failed to bundle
    google.protobuf, for the module itself: xai-sdk then aborted with
    "Unsupported protobuf version: 7.34.1" and matrx-ai init failed on every
    packaged boot. The engine's bundled protobuf serves every consumer
    (xai-sdk AND transformers), so a copy here is never correct.

    Scream-and-fix per platform doctrine: every removal is logged at WARNING.
    """
    victims: list[Path] = []
    victims += list(pkg_dir.glob("protobuf-*.dist-info"))
    for sub in ("protobuf", "_upb"):
        p = pkg_dir / "google" / sub
        if p.exists():
            victims.append(p)
    if not victims:
        return
    import shutil  # noqa: PLC0415 — cold path, only when a stale copy exists

    for v in victims:
        try:
            shutil.rmtree(v) if v.is_dir() else v.unlink()
            logger.warning(
                "[image_gen_installer] PURGED stale protobuf artifact %s from the "
                "managed image-gen dir — it shadowed the engine's bundled protobuf "
                "and broke matrx-ai init (see IMAGE_GEN_PACKAGES comment)", v,
            )
        except OSError as exc:
            logger.error(
                "[image_gen_installer] Could not purge shadowing protobuf artifact "
                "%s: %s — matrx-ai init may fail with 'Unsupported protobuf version'",
                v, exc,
            )
    # If google/ was only a protobuf namespace shell, drop the empty husk.
    google_dir = pkg_dir / "google"
    try:
        if google_dir.is_dir() and not any(google_dir.iterdir()):
            google_dir.rmdir()
    except OSError:
        pass


def inject_image_gen_path() -> bool:
    """Add the managed packages dir to sys.path if the install is complete.

    Called from runtime_hook.py and at engine startup.
    Returns True if path was injected, False if packages not yet installed.
    Also applies the filecmp compatibility patch to transformers on first call
    so that users who installed before the patch was introduced are fixed
    automatically without needing to reinstall.
    """
    pkg_dir_path = get_image_gen_packages_dir()
    if not is_image_gen_installed():
        return False
    try:
        _purge_shadowing_protobuf(pkg_dir_path)
    except Exception:
        logger.exception("[image_gen_installer] protobuf purge failed — continuing")
    pkg_dir = str(pkg_dir_path)
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
        logger.debug("[image_gen_installer] Injected %s into sys.path", pkg_dir)
    # Apply compatibility patch every startup — idempotent, fast (skips if already done)
    try:
        _patch_transformers_filecmp(pkg_dir_path)
    except Exception as patch_err:
        logger.warning(
            "[image_gen_installer] filecmp patch attempt failed: %s", patch_err
        )
    return True


# ── Global singleton ──────────────────────────────────────────────────────────

_active_progress: InstallProgress | None = None


def get_active_progress() -> InstallProgress | None:
    return _active_progress


# ── Compatibility patches ─────────────────────────────────────────────────────


def _patch_transformers_filecmp(pkg_dir: Path) -> None:
    """Patch transformers/dynamic_module_utils.py to handle missing `filecmp`.

    `filecmp` is a Python stdlib module that PyInstaller may not bundle when it
    never appears in the engine's own import graph.  `transformers` imports it
    unconditionally at the top of dynamic_module_utils.py, which causes an
    ImportError inside a frozen binary even though the transformers package
    itself was successfully installed.

    The patch replaces the bare `import filecmp` with a try/except that falls
    back to an always-copy stub.  The always-copy behaviour is safe and correct
    — it's slightly redundant (copies a file even when it hasn't changed) but
    produces identical results.

    This function is idempotent — running it on an already-patched file is safe.
    """
    target = pkg_dir / "transformers" / "dynamic_module_utils.py"
    if not target.exists():
        logger.warning(
            "[image_gen_installer] Could not find dynamic_module_utils.py to patch"
        )
        return

    src = target.read_text(encoding="utf-8")

    # Already patched?
    if "_files_equal" in src:
        logger.debug(
            "[image_gen_installer] dynamic_module_utils.py already patched — skipping"
        )
        return

    old_import = "import filecmp"
    new_import = (
        "try:\n"
        "    import filecmp as _filecmp_mod\n"
        "    def _files_equal(a: str, b: str) -> bool:\n"
        "        return _filecmp_mod.cmp(a, b)\n"
        "except ModuleNotFoundError:\n"
        "    # filecmp is excluded from some frozen binaries (e.g. PyInstaller).\n"
        "    # Always-copy fallback is safe: slightly redundant but functionally correct.\n"
        "    def _files_equal(a: str, b: str) -> bool:  # type: ignore[misc]\n"
        "        return False"
    )

    if old_import not in src:
        logger.warning(
            "[image_gen_installer] 'import filecmp' not found in dynamic_module_utils.py — "
            "transformers version may have changed; skipping patch"
        )
        return

    patched = src.replace(old_import, new_import, 1)
    patched = patched.replace("filecmp.cmp(", "_files_equal(", 100)
    target.write_text(patched, encoding="utf-8")

    # Remove stale .pyc so Python uses our patched source
    pyc_dir = target.parent / "__pycache__"
    if pyc_dir.exists():
        for pyc in pyc_dir.glob("dynamic_module_utils*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass

    logger.info("[image_gen_installer] Patched transformers/dynamic_module_utils.py ✓")


# ── Main installer (runs in a thread) ────────────────────────────────────────


def _do_install(progress: InstallProgress) -> None:
    """Blocking installer — called from a thread pool executor."""
    import platform as _platform

    pkg_dir = get_image_gen_packages_dir()
    pkg_dir.mkdir(parents=True, exist_ok=True)

    marker = pkg_dir / ".install-complete"
    marker.unlink(missing_ok=True)

    arch = _platform.machine().lower()
    use_torch_cpu_index = not (
        sys.platform == "darwin" and arch in ("arm64", "aarch64")
    )

    try:
        progress.update("preparing", 2.0, "Preparing installation directory…")

        # ── Step 1: PyTorch ───────────────────────────────────────────────────
        torch_packages = [
            p
            for p in IMAGE_GEN_PACKAGES
            if any(p.lower().startswith(t) for t in _TORCH_PACKAGES)
        ]
        progress.update(
            "downloading",
            5.0,
            "Downloading PyTorch… this is the big one (~400–800 MB). "
            "You'll see download lines appear below as it progresses.",
        )
        _run_pip_streaming(
            torch_packages,
            pkg_dir,
            progress,
            extra_index=_TORCH_CPU_INDEX_URL if use_torch_cpu_index else None,
        )
        progress.update("downloading", 45.0, "PyTorch installed ✓")

        # ── Step 2: diffusers + supporting packages ───────────────────────────
        rest_packages = [
            p
            for p in IMAGE_GEN_PACKAGES
            if not any(p.lower().startswith(t) for t in _TORCH_PACKAGES)
        ]
        progress.update(
            "downloading", 47.0, "Downloading diffusers, transformers, accelerate…"
        )
        _run_pip_streaming(rest_packages, pkg_dir, progress)
        progress.update("installing", 90.0, "All packages downloaded and installed ✓")

        # ── Step 3: verify imports in a clean subprocess ──────────────────────
        progress.update("verifying", 92.0, "Verifying installation…")
        python = _find_python()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(pkg_dir) + os.pathsep + env.get("PYTHONPATH", "")
        check = subprocess.run(
            [
                python,
                "-c",
                "import torch, diffusers, transformers, accelerate; print('ok')",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if check.returncode != 0 or "ok" not in check.stdout:
            raise RuntimeError(
                f"Post-install import check failed:\n{check.stderr[-2000:]}"
            )
        progress.update("verifying", 95.0, "All imports verified ✓")

        # ── Step 3.5: patch transformers for frozen-binary compatibility ─────────
        # transformers/dynamic_module_utils.py imports `filecmp` at the top level.
        # filecmp is a stdlib module that PyInstaller may not auto-collect when it
        # doesn't appear in the engine's own import graph.  We patch the installed
        # copy to guard the import so it degrades gracefully inside the frozen binary
        # (always-copy fallback is safe and correct).
        progress.update("verifying", 96.0, "Applying compatibility patches…")
        _patch_transformers_filecmp(pkg_dir)
        progress.update("verifying", 97.0, "Compatibility patches applied ✓")

        # ── Step 4: write versioned marker + inject path ─────────────────────
        # The marker records exactly what was installed so the upgrade path
        # (needs_upgrade()) and diagnostics can reason about the install
        # without importing the packages.
        marker.write_text(
            json.dumps(
                {
                    "packages": IMAGE_GEN_PACKAGES,
                    "versions": get_installed_package_versions(),
                }
            )
        )
        inject_image_gen_path()

        # Reload availability in the running service
        try:
            from app.services.image_gen import service as _svc_mod

            _svc_mod.DEPS_AVAILABLE, _svc_mod.DEPS_REASON = _svc_mod._check_deps()
            # video_gen shares this install — refresh its snapshot too.
            from app.services.video_gen import service as _vid_mod

            _vid_mod.DEPS_AVAILABLE, _vid_mod.DEPS_REASON = _vid_mod._check_deps()
            logger.info(
                "[image_gen_installer] Service deps reloaded: image=%s video=%s",
                _svc_mod.DEPS_AVAILABLE,
                _vid_mod.DEPS_AVAILABLE,
            )
        except Exception as reload_err:
            logger.warning(
                "[image_gen_installer] Could not reload service deps: %s", reload_err
            )

        progress.finish("Installation complete — Image generation is ready!")

    except Exception as exc:
        progress.fail(str(exc))


# ── Public API ────────────────────────────────────────────────────────────────


async def start_install() -> InstallProgress:
    """Start a background install.  Returns immediately with a progress object.

    Raises RuntimeError if an install is already running.
    """
    global _active_progress
    if _active_progress is not None and _active_progress.status == "running":
        raise RuntimeError("Installation already in progress")

    progress = InstallProgress(log_prefix="image_gen_installer")
    progress.status = "running"
    progress._loop = asyncio.get_running_loop()
    _active_progress = progress

    asyncio.get_running_loop().run_in_executor(None, _do_install, progress)
    return progress
