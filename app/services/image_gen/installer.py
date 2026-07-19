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
import importlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from app.common.system_logger import get_logger
from app.services.optional_packages.core import (
    InstallCancelledError,
    TORCH_CPU_INDEX_URL as _TORCH_CPU_INDEX_URL,
    InstallProgress,
    find_python as _find_python,
    packages_dir,
    run_pip_streaming as _run_pip_streaming,
    run_subprocess_cancellable as _run_subprocess_cancellable,
)
from app.services.image_gen.runtime_state import (
    INSTALL_EVIDENCE,
    RUNTIME_MANIFEST_REVISION,
    RUNTIME_REVISION,
    RuntimeFileLock,
    RuntimePhase,
    RuntimeSnapshot,
    active_slot_path,
    authoritative_snapshot,
    create_staging_slot,
    current_runtime_contract,
    finalize_staging_slot,
    read_snapshot,
    remove_slot,
    slot_path,
    validate_slot,
    write_slot_manifest,
    write_snapshot,
)

logger = get_logger()

# ── Package list ──────────────────────────────────────────────────────────────

# All packages to install (order matters — torch first so its deps land before
# the diffusers wheel asks for them).
# diffusers 0.39.0 is REQUIRED by the current model catalogs (Flux2Klein /
# ZImage / QwenImage / Wan / LTX pipelines) and fixes valid Civitai Z-Image
# LoRAs which omit per-layer alpha tensors. Keep these pins in sync with
# pyproject.toml [image-gen] and service.py MIN_DIFFUSERS_VERSION.
IMAGE_GEN_PACKAGES = [
    "torch>=2.6",
    "torchvision",
    "diffusers==0.39.0",
    # 5.3 adds the Qwen3 GGUF metadata path used by selectable FLUX.2 Klein
    # encoders. Older managed runtimes must migrate before those assets load.
    "transformers>=5.3.0",
    "accelerate>=1.0",
    "peft>=0.13.1",
    # Required by diffusers' load_lora_weights / set_adapters / unload_lora_weights
    # (USE_PEFT_BACKEND gate — without peft every LoRA apply fails with
    # "PEFT backend is required for this method").
    "sentencepiece>=0.2.0",
    # Transformers' GGUF loader dequantizes alternative Klein text encoders
    # into PyTorch tensors. Required only by that selectable encoder format,
    # but installed with the managed runtime so choosing it never asks users
    # to open a terminal.
    "gguf>=0.10.0",
    # protobuf intentionally NOT installed here. The engine bundles its own
    # (core dep via matrx-ai → xai-sdk, which hard-rejects protobuf 7). This
    # Older releases PREPENDED this dir to sys.path, so a protobuf copy here
    # shadowed the engine's and killed matrx-ai init on every packaged boot ("Unsupported
    # protobuf version: 7.34.1", 2026-07-12). transformers' slow-tokenizer
    # paths import the engine's bundled protobuf just fine.
    # inject_image_gen_path() purges any copy left by older installs.
    "huggingface_hub>=0.22.0",
]

_TORCH_PACKAGES = {"torch", "torchvision", "torchaudio"}
_COMPATIBILITY_MIGRATION_MARKER = ".compatibility-upgrade-pending"
_TORCH_REQUIREMENT_RE = re.compile(
    r"^torch\s*(?:\(\s*)?==\s*([^);\s]+)", re.IGNORECASE
)

CRITICAL_RUNTIME_IMPORTS = (
    "torch",
    "torchvision",
    "diffusers",
    "transformers",
    "accelerate",
    "peft",
    "sentencepiece",
    "gguf",
    "huggingface_hub.dataclasses",
    "jinja2.meta",
    "tqdm.contrib.logging",
    "diffusers.loaders.single_file_model",
    "diffusers.models.autoencoders.autoencoder_kl",
    "diffusers.models.autoencoders.autoencoder_kl_wan",
    "diffusers.pipelines.pipeline_utils",
)

CRITICAL_PIPELINE_CLASSES = (
    "DiffusionPipeline",
    "FluxPipeline",
    "Flux2KleinPipeline",
    "StableDiffusionPipeline",
    "StableDiffusionXLPipeline",
    "ZImagePipeline",
    "QwenImagePipeline",
    "WanPipeline",
    "WanImageToVideoPipeline",
    "LTXPipeline",
    "LTXImageToVideoPipeline",
)


@dataclass(frozen=True, slots=True)
class RuntimeVerification:
    valid: bool
    packages: dict[str, str]
    failure_code: str | None = None
    failure_detail: str | None = None


# ── Install directory ─────────────────────────────────────────────────────────


def get_image_gen_packages_dir() -> Path:
    """Authoritative active slot, or the legacy location before first repair."""
    active = active_slot_path()
    if active is not None:
        return active
    snapshot = authoritative_snapshot()
    if snapshot.state is RuntimePhase.RESTART_REQUIRED and snapshot.candidate_slot:
        valid, _, _ = validate_slot(snapshot.candidate_slot)
        if valid:
            return slot_path(snapshot.candidate_slot)
    return packages_dir("image-gen-packages")


def is_image_gen_installed() -> bool:
    """True only for an authoritative, exact-manifest, activated runtime."""
    return authoritative_snapshot().ready


def _compatibility_migration_pending() -> bool:
    """Whether an interrupted mandatory runtime migration must be resumed."""
    snapshot = authoritative_snapshot()
    if snapshot.state in {
        RuntimePhase.UPDATING,
        RuntimePhase.REPAIRING,
        RuntimePhase.VALIDATING,
        RuntimePhase.ACTIVATING,
        RuntimePhase.RESTART_REQUIRED,
        RuntimePhase.FAILED,
        RuntimePhase.ROLLED_BACK,
    }:
        return True
    return (
        packages_dir("image-gen-packages") / _COMPATIBILITY_MIGRATION_MARKER
    ).exists()


def _use_torch_cpu_index() -> bool:
    """Match the full installer's Torch wheel policy on every migration."""
    arch = platform.machine().lower()
    return not (sys.platform == "darwin" and arch in ("arm64", "aarch64"))


def _package_versions_at(pkg_dir: Path) -> dict[str, str]:
    """Versions of the managed packages, read from *.dist-info dir names.

    Works without importing the packages — safe to call at any time.
    Returns e.g. {"diffusers": "0.39.0", "torch": "2.6.0", ...}.
    """
    versions: dict[str, str] = {}
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


def get_installed_package_versions() -> dict[str, str]:
    return _package_versions_at(get_image_gen_packages_dir())


def _get_torchvision_torch_requirement() -> str | None:
    """Return Torchvision's exact managed-Torch requirement without importing it.

    Importing a mismatched Torchvision is precisely what raises the opaque
    ``operator torchvision::nms does not exist`` startup error. Wheel metadata
    is safe to inspect and Torchvision publishes an exact Torch requirement.
    """
    try:
        distributions = importlib.metadata.distributions(
            path=[str(get_image_gen_packages_dir())]
        )
        for distribution in distributions:
            if distribution.metadata.get("Name", "").lower() != "torchvision":
                continue
            for requirement in distribution.requires or ():
                match = _TORCH_REQUIREMENT_RE.match(requirement)
                if match:
                    return match.group(1)
    except (OSError, ValueError):
        logger.debug(
            "[image_gen_installer] Could not inspect Torchvision metadata",
            exc_info=True,
        )
    return None


def needs_upgrade() -> bool:
    """True when the install marker exists but diffusers is older than the
    catalog's minimum (service.py MIN_DIFFUSERS_VERSION). POST /image-gen/install
    re-runs pip with the upgraded pins in that case instead of short-circuiting.
    """
    if _compatibility_migration_pending():
        return True
    if not is_image_gen_installed():
        # A legacy completion marker is evidence that user opted into the
        # runtime, but is never authority. Convert it through a full staged
        # reinstall instead of trusting or mutating it in place.
        return (packages_dir("image-gen-packages") / INSTALL_EVIDENCE).exists()
    from app.services.image_gen.service import (  # noqa: PLC0415 — avoid cycle at import time
        MIN_DIFFUSERS_VERSION,
        _parse_version,
    )

    versions = get_installed_package_versions()
    installed = versions.get("diffusers")
    if installed is None:
        return True  # marker without diffusers on disk — reinstall
    if _parse_version(installed) < MIN_DIFFUSERS_VERSION:
        return True
    transformers_version = versions.get("transformers")
    if transformers_version is None or _parse_version(transformers_version) < (5, 3, 0):
        return True
    if versions.get("peft") is None:
        return True  # LoRA apply needs peft — older installs predate this dep
    if versions.get("gguf") is None:
        return True  # selectable GGUF text encoders need Transformers' parser
    torch_version = versions.get("torch")
    torchvision_version = versions.get("torchvision")
    if torch_version is None or torchvision_version is None:
        return True
    required_torch = _get_torchvision_torch_requirement()
    if required_torch is not None and torch_version.split("+", 1)[0] != required_torch:
        return True
    return False


def critical_runtime_import_check(
    *,
    importer: Callable[[str], Any] = importlib.import_module,
    expected_root: Path | None = None,
) -> dict[str, str]:
    """Exercise the lazy imports that have caused packaged-only outages.

    ``importer`` is an intentional test seam. When ``expected_root`` is given,
    heavy managed packages must originate from that immutable slot; this stops
    a system/user-site install from falsely satisfying verification.
    """
    modules: dict[str, Any] = {}
    for name in CRITICAL_RUNTIME_IMPORTS:
        modules[name] = importer(name)
    diffusers = modules["diffusers"]
    missing_classes = [
        name for name in CRITICAL_PIPELINE_CLASSES if not hasattr(diffusers, name)
    ]
    if missing_classes:
        raise RuntimeError(
            "Diffusers is missing required pipeline classes: "
            + ", ".join(missing_classes)
        )

    if expected_root is not None:
        expected = expected_root.resolve(strict=False)
        for name in ("torch", "torchvision", "diffusers", "transformers", "accelerate", "peft"):
            module_file = getattr(modules[name], "__file__", None)
            if not module_file:
                raise RuntimeError(f"{name} has no import origin")
            try:
                inside = Path(module_file).resolve(strict=False).is_relative_to(expected)
            except (OSError, ValueError):
                inside = False
            if not inside:
                raise RuntimeError(
                    f"{name} resolved outside candidate runtime: {module_file}"
                )

    versions: dict[str, str] = {}
    for name in ("torch", "torchvision", "diffusers", "transformers", "accelerate", "peft"):
        version = getattr(modules[name], "__version__", None)
        if version is not None:
            versions[name] = str(version)
    if versions.get("diffusers") != "0.39.0":
        raise RuntimeError(
            f"Diffusers 0.39.0 is required, found {versions.get('diffusers')!r}"
        )
    transformers = versions.get("transformers", "0.0")
    if tuple(int(part) for part in transformers.split(".")[:2]) < (5, 3):
        raise RuntimeError(f"Transformers >=5.3 is required, found {transformers!r}")
    return versions


def _validate_selected_python(
    python: str,
    *,
    cancel_event: threading.Event | None,
) -> None:
    check = _run_subprocess_cancellable(
        [
            python,
            "-c",
            (
                "import json,platform,sys; print(json.dumps({"
                "'python_abi':sys.implementation.cache_tag or 'unknown',"
                "'python_version':f'{sys.version_info.major}.{sys.version_info.minor}',"
                "'platform':sys.platform,'machine':platform.machine().lower()}))"
            ),
        ],
        cancel_event=cancel_event,
        timeout=30,
    )
    if check.returncode != 0:
        raise RuntimeError(f"Could not inspect installer Python: {check.stderr[-2000:]}")
    try:
        actual = json.loads(check.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("Installer Python returned no valid runtime contract") from exc
    expected = current_runtime_contract()
    mismatches = [
        f"{key}={actual.get(key)!r} (engine requires {expected[key]!r})"
        for key in ("python_abi", "python_version", "platform", "machine")
        if actual.get(key) != expected[key]
    ]
    if mismatches:
        raise RuntimeError(
            "Installer Python does not match the production engine ABI: "
            + "; ".join(mismatches)
        )


def _verify_runtime_subprocess(
    pkg_dir: Path,
    *,
    cancel_event: threading.Event | None,
) -> dict[str, str]:
    python = _find_python()
    _validate_selected_python(python, cancel_event=cancel_event)
    imports = json.dumps(CRITICAL_RUNTIME_IMPORTS)
    classes = json.dumps(CRITICAL_PIPELINE_CLASSES)
    script = (
        "import importlib,json,pathlib; "
        f"root=pathlib.Path({str(pkg_dir)!r}).resolve(); "
        f"mods={{n:importlib.import_module(n) for n in {imports}}}; "
        f"missing=[n for n in {classes} if not hasattr(mods['diffusers'],n)]; "
        "assert not missing, missing; "
        "managed=('torch','torchvision','diffusers','transformers','accelerate','peft'); "
        "bad={n:str(pathlib.Path(mods[n].__file__).resolve()) for n in managed "
        "if root not in pathlib.Path(mods[n].__file__).resolve().parents}; "
        "assert not bad, bad; "
        "assert mods['diffusers'].__version__=='0.39.0'; "
        "assert tuple(map(int,mods['transformers'].__version__.split('.')[:2])) >= (5,3); "
        "print(json.dumps({n:str(getattr(mods[n],'__version__','unknown')) for n in managed}))"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(pkg_dir)
    env["PYTHONNOUSERSITE"] = "1"
    check = _run_subprocess_cancellable(
        [python, "-I", "-c", script],
        cancel_event=cancel_event,
        env=env,
        timeout=180,
    )
    # -I intentionally ignores PYTHONPATH. Pass the slot in the script instead.
    if check.returncode != 0:
        # Retry is never appropriate: a verifier failure means the candidate is
        # not publishable. Include the captured traceback for diagnostics.
        raise RuntimeError(f"Critical runtime verification failed: {check.stderr[-4000:]}")
    try:
        return {str(k): str(v) for k, v in json.loads(check.stdout.splitlines()[-1]).items()}
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("Critical runtime verifier returned no manifest") from exc


def migrate_incompatible_runtime(
    progress: InstallProgress | None = None,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Synchronously upgrade a previously installed incompatible runtime.

    This is deliberately a startup migration, not UI advice: shipped app
    updates must repair every existing image-gen install before importing its
    optional packages. Only diffusers and its resolved lightweight Python
    dependencies are touched; models, encoders, LoRAs, and user data stay put.
    Torch and Torchvision are resolved together because pip target installs do
    not consider packages already present in the target as satisfying new
    dependencies. Leaving Torchvision behind can produce an ABI-incompatible
    pair even when both distributions are individually valid.
    The pending marker makes a power/network interruption retry on the next
    engine start rather than allowing the known-broken loader to run.
    """
    if not needs_upgrade():
        return False

    pkg_dir = get_image_gen_packages_dir()
    marker = pkg_dir / ".install-complete"
    pending = pkg_dir / _COMPATIBILITY_MIGRATION_MARKER
    # A missing marker with no pending migration means image generation was
    # never installed; optional capabilities must never auto-install themselves.
    if not marker.exists() and not pending.exists():
        return False

    pkg_dir.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        marker.replace(pending)

    progress = progress or InstallProgress(log_prefix="image_gen_runtime_migration")
    progress.status = "running"
    try:
        progress.update("upgrading", 10.0, "Updating required AI runtime…")
        # Do NOT invoke the full installer: this migration only repairs the
        # managed runtime and does not alter models or other user assets. Include
        # Torchvision explicitly so pip resolves it against the Torch version
        # pulled by PEFT instead of leaving a stale native extension behind.
        _run_pip_streaming(
            [
                "torchvision",
                "diffusers==0.39.0",
                "transformers>=5.3.0",
                "peft>=0.13.1",
                "gguf>=0.10.0",
            ],
            pkg_dir,
            progress,
            extra_index=_TORCH_CPU_INDEX_URL if _use_torch_cpu_index() else None,
            cancel_event=cancel_event,
        )
        progress.update("verifying", 80.0, "Verifying image runtime support…")
        python = _find_python()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(pkg_dir) + os.pathsep + env.get("PYTHONPATH", "")
        check = _run_subprocess_cancellable(
            [
                python,
                "-c",
                "import diffusers, gguf, peft, transformers; "
                "from diffusers import ZImagePipeline; "
                "assert diffusers.__version__ == '0.39.0'; "
                "assert tuple(map(int, transformers.__version__.split('.')[:2])) >= (5, 3); "
                "print('ok')",
            ],
            cancel_event=cancel_event,
            env=env,
            timeout=60,
        )
        if check.returncode != 0 or "ok" not in check.stdout:
            raise RuntimeError(
                f"Runtime migration verification failed: {check.stderr[-2000:]}"
            )
        # inject_image_gen_path() intentionally requires the complete marker.
        # Publish it provisionally, then remove it again if in-process/frozen
        # activation fails. The durable pending marker remains until every
        # runtime check below succeeds.
        marker.write_text(
            json.dumps(
                {
                    "packages": IMAGE_GEN_PACKAGES,
                    "versions": get_installed_package_versions(),
                    "migration": "diffusers-0.39.0-peft-gguf-text-encoders",
                }
            ),
            encoding="utf-8",
        )
        # The old runtime was deliberately withheld by the frozen runtime hook,
        # so this is the first point this process may import image packages.
        # Capture process state so a frozen-only activation failure can be
        # rolled back instead of leaving a rejected path/modules load-bearing.
        modules_before_activation = set(sys.modules)
        path_was_present = str(pkg_dir) in sys.path
        try:
            if not inject_image_gen_path():
                raise RuntimeError("managed package directory could not be activated")
            from app.services.image_gen import service as _svc_mod  # noqa: PLC0415
            from app.services.video_gen import service as _vid_mod  # noqa: PLC0415

            _svc_mod.DEPS_AVAILABLE, _svc_mod.DEPS_REASON = _svc_mod._check_deps()
            _vid_mod.DEPS_AVAILABLE, _vid_mod.DEPS_REASON = _vid_mod._check_deps()
            if not _svc_mod.DEPS_AVAILABLE or not _vid_mod.DEPS_AVAILABLE:
                reasons = "; ".join(
                    reason
                    for available, reason in (
                        (_svc_mod.DEPS_AVAILABLE, _svc_mod.DEPS_REASON),
                        (_vid_mod.DEPS_AVAILABLE, _vid_mod.DEPS_REASON),
                    )
                    if not available
                )
                raise RuntimeError(
                    "managed packages installed but failed in-process activation: "
                    f"{reasons or 'dependency check returned unavailable'}"
                )
        except Exception as exc:
            _rollback_runtime_activation(
                pkg_dir,
                modules_before_activation,
                path_was_present,
            )
            raise RuntimeError(
                f"Runtime migration installed but could not activate packages: {exc}"
            ) from exc
        pending.unlink(missing_ok=True)
        progress.finish("Required AI runtime update complete")
        logger.info("[image_gen_installer] Required image runtime migration complete")
        return True
    except InstallCancelledError as exc:
        marker.unlink(missing_ok=True)
        progress.fail(f"Required AI runtime update interrupted: {exc}")
        logger.info(
            "[image_gen_installer] Required image runtime migration interrupted; "
            "the pending marker will resume it on next startup"
        )
        raise
    except Exception as exc:
        # Keep the pending marker as durable retry state. The service gates
        # generation while it remains, so a partial pip target cannot load.
        marker.unlink(missing_ok=True)
        progress.fail(f"Required AI runtime update failed: {exc}")
        logger.exception(
            "[image_gen_installer] Required image runtime migration failed"
        )
        raise


def _purge_shadowing_protobuf(pkg_dir: Path) -> bool:
    """Delete any protobuf copy from the managed image-gen dir — LOUDLY.
    Returns True when the dir is clean (nothing found, or everything removed).

    Older installs pip-installed protobuf into this dir (it was in
    IMAGE_GEN_PACKAGES until 2026-07-13). Older app releases prepended the dir
    to sys.path, so that copy shadowed the engine's own
    protobuf: xai-sdk aborted with "Unsupported protobuf version: 7.34.1" and
    matrx-ai init failed on every packaged boot. The engine's protobuf serves
    every consumer (xai-sdk AND transformers), so a copy here is never
    correct. A False return means the caller must NOT inject the dir.

    Scream-and-fix per platform doctrine: every removal is logged at WARNING.
    """
    victims: list[Path] = []
    victims += list(pkg_dir.glob("protobuf-*.dist-info"))
    for sub in ("protobuf", "_upb"):
        p = pkg_dir / "google" / sub
        if p.exists():
            victims.append(p)
    if not victims:
        return True
    import shutil  # noqa: PLC0415 — cold path, only when a stale copy exists

    clean = True
    for v in victims:
        try:
            shutil.rmtree(v) if v.is_dir() else v.unlink()
            logger.warning(
                "[image_gen_installer] PURGED stale protobuf artifact %s from the "
                "managed image-gen dir — it shadowed the engine's bundled protobuf "
                "and broke matrx-ai init (see IMAGE_GEN_PACKAGES comment)",
                v,
            )
        except OSError as exc:
            clean = False
            logger.error(
                "[image_gen_installer] Could not purge shadowing protobuf artifact "
                "%s: %s — matrx-ai init would fail with 'Unsupported protobuf "
                "version'; the image-gen dir will NOT be injected this session",
                v,
                exc,
            )
    # If google/ was only a protobuf namespace shell, drop the empty husk.
    google_dir = pkg_dir / "google"
    try:
        if google_dir.is_dir() and not any(google_dir.iterdir()):
            google_dir.rmdir()
    except OSError:
        pass
    return clean


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
        purged_clean = _purge_shadowing_protobuf(pkg_dir_path)
    except Exception:
        purged_clean = False
        logger.exception("[image_gen_installer] protobuf purge failed")
    if not purged_clean:
        # Keep the managed runtime internally coherent even though it now sits
        # behind the engine's bundled dependencies in import precedence.
        logger.error(
            "[image_gen_installer] NOT injecting %s into sys.path — a protobuf "
            "copy survived the purge and would break matrx-ai init. Image/video "
            "generation is unavailable this session; restart the engine to retry.",
            pkg_dir_path,
        )
        return False
    pkg_dir = str(pkg_dir_path)
    if pkg_dir not in sys.path:
        # Optional runtimes are fallbacks, never replacements for the frozen
        # engine's FastAPI/Starlette/anyio/httpx stack. A pip --target install
        # includes transitive copies of those core packages; prepending this dir
        # made them load-bearing and produced shutdown/runtime incompatibilities.
        # Heavy packages absent from the bundle (torch/diffusers/transformers)
        # still resolve normally from the appended directory.
        sys.path.append(pkg_dir)
        logger.debug(
            "[image_gen_installer] Appended optional runtime %s to sys.path", pkg_dir
        )
    # Apply compatibility patch every startup — idempotent, fast (skips if already done)
    try:
        _patch_transformers_filecmp(pkg_dir_path)
    except Exception as patch_err:
        logger.warning(
            "[image_gen_installer] filecmp patch attempt failed: %s", patch_err
        )
    return True


def _rollback_runtime_activation(
    pkg_dir: Path,
    modules_before: set[str],
    path_was_present: bool,
) -> None:
    """Undo a failed managed-runtime activation within the current process.

    Removing newly imported modules does not unload native libraries already
    mapped by the OS, but it prevents a rejected/partial runtime from remaining
    load-bearing or shadowing the engine's bundled dependencies. A clean engine
    restart performs the durable retry recorded by the pending marker.
    """
    pkg_root = pkg_dir.resolve(strict=False)
    if not path_was_present:
        pkg_text = str(pkg_dir)
        while pkg_text in sys.path:
            sys.path.remove(pkg_text)

    for name, module in list(sys.modules.items()):
        if name in modules_before or module is None:
            continue
        locations: list[str] = []
        module_file = getattr(module, "__file__", None)
        if module_file:
            locations.append(str(module_file))
        module_path = getattr(module, "__path__", None)
        if module_path:
            locations.extend(str(entry) for entry in module_path)
        try:
            belongs_to_managed_runtime = any(
                Path(location).resolve(strict=False).is_relative_to(pkg_root)
                for location in locations
            )
        except (OSError, ValueError):
            belongs_to_managed_runtime = False
        if belongs_to_managed_runtime:
            sys.modules.pop(name, None)

    logger.warning(
        "[image_gen_installer] Rolled back failed managed-runtime activation; "
        "the pending migration will retry after restart"
    )


# ── Global singleton ──────────────────────────────────────────────────────────

_active_progress: InstallProgress | None = None
_background_futures: set[asyncio.Future[object]] = set()
_installer_cancel = threading.Event()


def get_active_progress() -> InstallProgress | None:
    return _active_progress


def _submit_background(function, *args) -> None:
    """Run installer work while retaining and observing its executor future."""
    future = asyncio.get_running_loop().run_in_executor(None, function, *args)
    _background_futures.add(future)

    def _finished(done: asyncio.Future[object]) -> None:
        _background_futures.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            logger.warning("[image_gen_installer] Background package task cancelled")
        except Exception:
            # The worker owns the detailed log and progress failure. Retrieving
            # the exception here prevents asyncio's misleading orphan warning.
            pass

    future.add_done_callback(_finished)


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


def _do_install(
    progress: InstallProgress,
    cancel_event: threading.Event | None = None,
) -> None:
    """Blocking installer — called from a thread pool executor."""
    pkg_dir = get_image_gen_packages_dir()
    pkg_dir.mkdir(parents=True, exist_ok=True)

    marker = pkg_dir / ".install-complete"
    marker.unlink(missing_ok=True)

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
            extra_index=_TORCH_CPU_INDEX_URL if _use_torch_cpu_index() else None,
            cancel_event=cancel_event,
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
        _run_pip_streaming(
            rest_packages,
            pkg_dir,
            progress,
            cancel_event=cancel_event,
        )
        progress.update("installing", 90.0, "All packages downloaded and installed ✓")

        # ── Step 3: verify imports in a clean subprocess ──────────────────────
        progress.update("verifying", 92.0, "Verifying installation…")
        python = _find_python()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(pkg_dir) + os.pathsep + env.get("PYTHONPATH", "")
        check = _run_subprocess_cancellable(
            [
                python,
                "-c",
                "import torch, diffusers, transformers, accelerate, peft; print('ok')",
            ],
            cancel_event=cancel_event,
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
        (pkg_dir / _COMPATIBILITY_MIGRATION_MARKER).unlink(missing_ok=True)
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
    _installer_cancel.clear()

    _submit_background(_do_install, progress, _installer_cancel)
    return progress


async def start_compatibility_migration() -> InstallProgress | None:
    """Start the mandatory old-runtime migration without blocking app startup.

    The runtime hook has already withheld incompatible optional packages, so
    this background task cannot expose the old loader. The shared progress
    singleton lets the existing installer status/SSE/UI show its automatic
    progress and makes duplicate startup calls harmless.
    """
    global _active_progress
    if not needs_upgrade():
        return None
    if _active_progress is not None and _active_progress.status == "running":
        return _active_progress

    progress = InstallProgress(log_prefix="image_gen_runtime_migration")
    progress.status = "running"
    progress._loop = asyncio.get_running_loop()
    _active_progress = progress
    _installer_cancel.clear()
    _submit_background(migrate_incompatible_runtime, progress, _installer_cancel)
    return progress


async def shutdown_background_installers(timeout: float = 10.0) -> bool:
    """Cancel and await every package subprocess owned by this engine."""
    _installer_cancel.set()
    futures = set(_background_futures)
    if not futures:
        return True
    done, pending = await asyncio.wait(futures, timeout=timeout)
    for future in done:
        try:
            future.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    if pending:
        logger.error(
            "[image_gen_installer] %d background installer task(s) did not stop "
            "within %.1fs",
            len(pending),
            timeout,
        )
        return False
    logger.info("[image_gen_installer] Background package tasks stopped")
    return True
