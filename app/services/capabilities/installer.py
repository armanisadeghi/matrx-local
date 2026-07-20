"""On-demand installer for Settings → Capabilities (Whisper, etc.).

Mirrors the image-gen consumer installer: packages land in a user-writable
directory, progress streams over SSE, and the frozen sidecar imports them via
sys.path injection. End users never touch a terminal.

Single-ML-stack doctrine (app/services/optional_packages/FEATURE.md): the
managed media runtime slot is the ONLY provider of the torch/transformers/
numpy family. Capabilities that need that stack declare
``requires_ml_runtime`` — the runtime is installed first when absent, the
capability's own packages resolve against the slot's exact pins, and any
slot-owned distribution pip drags in transitively is removed before the
install is marked complete. Recipes must never name a slot-owned
distribution; ``screen_install_packages`` and the guardrail unit tests both
refuse it.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from app.common.system_logger import get_logger
from app.services.optional_packages.core import (
    TORCH_CPU_INDEX_URL,
    InstallProgress,
    find_python,
    packages_dir,
    run_pip_streaming,
    run_subprocess_cancellable,
)
from app.services.optional_packages.guardrails import (
    SLOT_OWNED_DISTRIBUTIONS,
    canonical_distribution_name,
    find_shadowing_distributions,
    sanitize_target_dir,
    screen_install_packages,
    write_slot_constraints_file,
)

logger = get_logger()

# capability_id → install recipe. NEVER add a slot-owned ML distribution
# (torch/transformers/numpy family) to a recipe — declare
# ``requires_ml_runtime`` and the managed media runtime provides the stack.
CAPABILITY_INSTALL: dict[str, dict] = {
    "transcription": {
        "dir_name": "transcription-packages",
        "packages": ["openai-whisper>=20250625"],
        "requires_ml_runtime": True,
        "verify_imports": ["torch", "whisper"],
        "display_name": "Speech Transcription (Whisper)",
    },
    "ner": {
        "dir_name": "ner-packages",
        # gliner < 0.2.27 is broken on transformers 5.x (uniform ~0.05 scores);
        # the slot ships transformers 5.3, so the floor is a correctness pin.
        "packages": ["gliner2[local]>=1.3.2", "gliner>=0.2.27"],
        "requires_ml_runtime": True,
        "verify_imports": ["torch", "gliner2", "gliner", "huggingface_hub"],
        "display_name": "Entity Extraction (GLiNER)",
    },
}

# Lightweight capabilities use the same persistent ``pip --target`` contract
# through app.api.capabilities_routes. Keep their directory names here so the
# startup injector can restore completed installs before tools begin loading.
LIGHTWEIGHT_CAPABILITY_IDS: tuple[str, ...] = (
    "browser_automation",
    "audio_recording",
    "ocr",
    "pdf_extraction",
    "system_monitoring",
    "network_discovery",
    "media_download",
    "video_processing",
)

_active: dict[str, InstallProgress] = {}


def get_capability_packages_dir(capability_id: str) -> Path:
    recipe = CAPABILITY_INSTALL[capability_id]
    return packages_dir(recipe["dir_name"])


def is_capability_installed(capability_id: str) -> bool:
    if capability_id not in CAPABILITY_INSTALL:
        return False
    return (get_capability_packages_dir(capability_id) / ".install-complete").exists()


def get_lightweight_capability_packages_dir(capability_id: str) -> Path:
    if capability_id not in LIGHTWEIGHT_CAPABILITY_IDS:
        raise KeyError(capability_id)
    return packages_dir(f"capability-{capability_id}")


def is_lightweight_capability_installed(capability_id: str) -> bool:
    if capability_id not in LIGHTWEIGHT_CAPABILITY_IDS:
        return False
    return (
        get_lightweight_capability_packages_dir(capability_id)
        / ".install-complete"
    ).exists()


def _append_optional_package_path(pkg_dir: Path, capability_id: str) -> None:
    """Expose optional packages as fallbacks behind the frozen engine.

    ``pip --target`` installs transitive dependencies alongside the requested
    package. Prepending that directory can replace the FastAPI/Starlette/anyio
    versions bundled and tested with the engine, so optional targets must never
    take precedence over existing import locations.
    """
    pkg_dir_str = str(pkg_dir)
    if pkg_dir_str not in sys.path:
        sys.path.append(pkg_dir_str)
        logger.debug(
            "[capabilities_installer] Appended %s to sys.path (%s)",
            pkg_dir_str,
            capability_id,
        )


def inject_capability_path(capability_id: str) -> bool:
    """Append a managed packages dir when its install marker exists."""
    if not is_capability_installed(capability_id):
        return False
    _append_optional_package_path(
        get_capability_packages_dir(capability_id), capability_id
    )
    return True


def inject_lightweight_capability_path(capability_id: str) -> bool:
    """Append a completed lightweight capability target as a fallback."""
    if not is_lightweight_capability_installed(capability_id):
        return False
    _append_optional_package_path(
        get_lightweight_capability_packages_dir(capability_id), capability_id
    )
    return True


def sanitize_ml_shadowing_at_startup() -> list[str]:
    """One-time migration: strip stale ML stacks out of capability dirs.

    Legacy installs (and pip's transitive resolution) left full torch/
    transformers copies inside ``ner-packages``/``transcription-packages``.
    Once the managed media runtime is authoritative, those copies are dead
    weight and the exact hazard behind MXL-D-070, so remove them loudly.
    Frozen-only: source runs use the uv venv, not capability dirs, for ML.
    Never blocks startup — a failed sweep logs and moves on.
    """
    if not getattr(sys, "frozen", False):
        return []
    try:
        from app.services.image_gen.installer import (  # noqa: PLC0415
            is_image_gen_installed,
        )

        if not is_image_gen_installed():
            return []
    except Exception as exc:
        logger.error(
            "[capabilities_installer] Startup ML-shadowing sweep skipped — "
            "runtime state unreadable: %s",
            exc,
        )
        return []

    sanitized: list[str] = []
    targets = [
        (cap_id, get_capability_packages_dir(cap_id))
        for cap_id in CAPABILITY_INSTALL
        if is_capability_installed(cap_id)
    ] + [
        (cap_id, get_lightweight_capability_packages_dir(cap_id))
        for cap_id in LIGHTWEIGHT_CAPABILITY_IDS
        if is_lightweight_capability_installed(cap_id)
    ]
    for cap_id, pkg_dir in targets:
        try:
            if find_shadowing_distributions(pkg_dir):
                sanitize_target_dir(
                    pkg_dir, log_prefix=f"capabilities:{cap_id}:startup-migration"
                )
                sanitized.append(cap_id)
        except Exception as exc:
            logger.error(
                "[capabilities_installer] Startup ML-shadowing sweep failed for "
                "%s (%s): %s — capability left as-is; slot precedence still "
                "protects the engine",
                cap_id,
                pkg_dir,
                exc,
            )
    return sanitized


def inject_all_capability_paths() -> list[str]:
    """Inject every completed capability package dir. Returns injected ids."""
    sanitize_ml_shadowing_at_startup()
    injected: list[str] = []
    for cap_id in CAPABILITY_INSTALL:
        if inject_capability_path(cap_id):
            injected.append(cap_id)
    for cap_id in LIGHTWEIGHT_CAPABILITY_IDS:
        if inject_lightweight_capability_path(cap_id):
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


class _ScaledProgress:
    """Progress facade mapping a phase's 0–100% into a slice of the real bar."""

    def __init__(self, inner: InstallProgress, base: float) -> None:
        self._inner = inner
        self._base = base

    def update(self, stage: str, percent: float, message: str) -> None:
        span = 100.0 - self._base
        self._inner.update(stage, self._base + percent * span / 100.0, message)

    def log(self, line: str) -> None:
        self._inner.log(line)

    def finish(self, message: str = "Installation complete") -> None:
        self._inner.finish(message)

    def fail(self, error: str) -> None:
        self._inner.fail(error)


def _resolve_thin_install_plan(
    packages: list[str],
    constraints_file: Path,
    extra_index: str | None,
) -> list[str] | None:
    """Resolve the full dependency tree, then drop slot-provided distributions.

    Uses ``pip install --dry-run --report`` so the resolver picks versions
    compatible with the slot's exact pins (e.g. a numba release that accepts
    the slot's numpy) without downloading multi-hundred-MB wheels we would
    delete anyway. Returns exact ``name==version`` pins for everything the
    capability dir must actually carry, or None when the selected installer
    cannot produce a report (caller falls back to a full install + sanitize).
    """
    try:
        python = find_python()
        with tempfile.TemporaryDirectory(prefix="matrx-resolve-") as tmp:
            report_path = Path(tmp) / "resolve-report.json"
            cmd = [
                python,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--ignore-installed",
                "--quiet",
                "--disable-pip-version-check",
                "--report",
                str(report_path),
                "--constraint",
                str(constraints_file),
            ]
            if extra_index:
                cmd += ["--extra-index-url", extra_index]
            cmd += packages
            result = run_subprocess_cancellable(cmd, cancel_event=None, timeout=600)
            if result.returncode != 0:
                logger.warning(
                    "[capabilities_installer] Dependency resolution report failed "
                    "(rc=%s): %s",
                    result.returncode,
                    result.stderr[-2000:],
                )
                return None
            report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "[capabilities_installer] Dependency resolution report unavailable "
            "(%s); falling back to full install + sanitize",
            exc,
        )
        return None

    plan: list[str] = []
    skipped: list[str] = []
    for item in report.get("install", []):
        metadata = item.get("metadata", {})
        name = canonical_distribution_name(str(metadata.get("name", "")))
        version = str(metadata.get("version", ""))
        if not name or not version:
            return None
        if name in SLOT_OWNED_DISTRIBUTIONS:
            skipped.append(f"{name}=={version}")
            continue
        plan.append(f"{name}=={version}")
    logger.info(
        "[capabilities_installer] Resolved thin install plan: %d packages to "
        "install, %d provided by the managed ML runtime (%s)",
        len(plan),
        len(skipped),
        ", ".join(skipped) or "none",
    )
    return plan


def _do_install(
    capability_id: str, progress: InstallProgress | _ScaledProgress
) -> None:
    recipe = CAPABILITY_INSTALL[capability_id]
    pkg_dir = get_capability_packages_dir(capability_id)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    marker = pkg_dir / ".install-complete"
    marker.unlink(missing_ok=True)

    packages: list[str] = list(recipe["packages"])
    requires_ml_runtime = bool(recipe.get("requires_ml_runtime"))
    log_prefix = f"capabilities:{capability_id}"

    try:
        screen_install_packages(packages, context=f"capability {capability_id}")

        progress.update("preparing", 2.0, "Preparing installation directory…")

        slot_dir: Path | None = None
        constraints_path: Path | None = None
        extra_index: str | None = None
        if requires_ml_runtime:
            from app.services.image_gen.installer import (  # noqa: PLC0415
                get_image_gen_packages_dir,
                is_image_gen_installed,
            )

            if not is_image_gen_installed():
                raise RuntimeError(
                    "The managed media runtime is not installed — this "
                    "capability consumes its ML stack (torch/transformers) and "
                    "cannot install without it."
                )
            slot_dir = get_image_gen_packages_dir()
            constraints_path = write_slot_constraints_file(
                pkg_dir / ".slot-constraints.txt"
            )
            arch = platform.machine().lower()
            if not (sys.platform == "darwin" and arch in ("arm64", "aarch64")):
                extra_index = TORCH_CPU_INDEX_URL

            progress.update(
                "resolving",
                6.0,
                "Resolving packages against the managed ML runtime…",
            )
            thin_plan = _resolve_thin_install_plan(
                packages, constraints_path, extra_index
            )
        else:
            thin_plan = None

        if thin_plan is not None:
            progress.update(
                "downloading",
                12.0,
                f"Downloading {len(thin_plan)} packages "
                "(PyTorch stack shared with media generation)…",
            )
            run_pip_streaming(
                thin_plan,
                pkg_dir,
                progress,
                extra_index=extra_index,
                constraints_file=constraints_path,
                no_deps=True,
            )
        else:
            progress.update(
                "downloading",
                12.0,
                f"Downloading {', '.join(packages)}…",
            )
            run_pip_streaming(
                packages,
                pkg_dir,
                progress,
                extra_index=extra_index,
                constraints_file=constraints_path,
            )

        progress.update("installing", 78.0, "Packages installed ✓")

        # The slot is the only ML-stack provider: remove any slot-owned
        # distribution pip pulled transitively, then prove none remain.
        removed = sanitize_target_dir(pkg_dir, log_prefix=log_prefix)
        leftover = find_shadowing_distributions(pkg_dir)
        if leftover:
            raise RuntimeError(
                f"Slot-owned ML distributions survived sanitization: {leftover}"
            )

        progress.update("verifying", 85.0, "Verifying installation…")
        python = find_python()
        env = os.environ.copy()
        # Production sys.path precedence: managed runtime slot BEFORE the
        # capability dir. Verifying in any other order certifies a stack the
        # engine will never run (MXL-D-070).
        path_parts: list[str] = []
        if slot_dir is not None and slot_dir.is_dir():
            path_parts.append(str(slot_dir))
        path_parts.append(str(pkg_dir))
        env["PYTHONPATH"] = (
            os.pathsep.join(path_parts) + os.pathsep + env.get("PYTHONPATH", "")
        )
        imports = ", ".join(recipe["verify_imports"])
        check_lines = [f"import {imports}"]
        if requires_ml_runtime and slot_dir is not None and getattr(sys, "frozen", False):
            check_lines += [
                "import pathlib, torch",
                "origin = pathlib.Path(torch.__file__).resolve()",
                f"slot = pathlib.Path({str(slot_dir)!r}).resolve()",
                "assert origin.is_relative_to(slot), "
                "f'torch resolved outside the managed runtime slot: {origin}'",
            ]
        check_lines.append("print('ok')")
        check = subprocess.run(
            [python, "-c", "\n".join(check_lines)],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
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
                    "requires_ml_runtime": requires_ml_runtime,
                    "ml_runtime_slot": str(slot_dir) if slot_dir else None,
                    "thin_resolution": thin_plan is not None,
                    "sanitized_distributions": removed,
                }
            )
        )
        inject_capability_path(capability_id)

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

    if CAPABILITY_INSTALL[capability_id].get("requires_ml_runtime"):
        asyncio.get_running_loop().create_task(
            _install_with_ml_runtime(capability_id, progress)
        )
    else:
        asyncio.get_running_loop().run_in_executor(
            None, _do_install, capability_id, progress
        )
    return progress


async def _install_with_ml_runtime(
    capability_id: str, progress: InstallProgress
) -> None:
    """Ensure the managed media runtime exists, then run the capability install.

    Capabilities no longer carry their own torch stack; when the runtime is
    absent, its install runs first (0–55% of the combined progress bar) and the
    thin capability install takes the remainder. A runtime failure fails the
    capability install loudly — there is no fallback stack.
    """
    from app.services.image_gen.installer import (  # noqa: PLC0415
        ensure_runtime,
        is_image_gen_installed,
    )

    try:
        loop = asyncio.get_running_loop()
        runtime_present = await loop.run_in_executor(None, is_image_gen_installed)
        install_progress: InstallProgress | _ScaledProgress = progress
        if not runtime_present:
            progress.update(
                "ml-runtime",
                1.0,
                "Installing the managed media runtime first — it provides the "
                "PyTorch stack this capability shares with media generation…",
            )
            runtime_progress = await ensure_runtime("install")
            last_message = ""
            while runtime_progress.status == "running":
                await asyncio.sleep(1.0)
                message = runtime_progress.message
                if message and message != last_message:
                    last_message = message
                    progress.update(
                        "ml-runtime",
                        min(55.0, 1.0 + runtime_progress.percent * 0.54),
                        f"Media runtime: {message}",
                    )
            runtime_ready = await loop.run_in_executor(None, is_image_gen_installed)
            if runtime_progress.status != "complete" or not runtime_ready:
                progress.fail(
                    "The managed media runtime install failed, so this "
                    "capability cannot be installed: "
                    + (runtime_progress.error or runtime_progress.message)
                )
                return
            install_progress = _ScaledProgress(progress, base=55.0)
        await loop.run_in_executor(None, _do_install, capability_id, install_progress)
    except Exception as exc:
        logger.error(
            "[capabilities_installer] ML-runtime orchestration failed for %s: %s",
            capability_id,
            exc,
        )
        progress.fail(str(exc))


def uses_managed_installer(capability_id: str) -> bool:
    return capability_id in CAPABILITY_INSTALL
