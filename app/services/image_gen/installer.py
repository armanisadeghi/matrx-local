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
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from app.common.system_logger import get_logger
from app.services.optional_packages.core import (
    InstallProgress,
    find_python as _find_python,
    packages_dir,
    run_pip_streaming as _run_pip_streaming,
    run_subprocess_cancellable as _run_subprocess_cancellable,
)
from app.services.image_gen.runtime_state import (
    INSTALL_EVIDENCE,
    RUNTIME_MANIFEST_REVISION,
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

# Package selection is release-owned by a target-specific, hash-locked contract.
# This module intentionally contains no floating fallback requirement list.
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
    "filecmp",
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
    "LTX2Pipeline",
    "LTXImageToVideoPipeline",
)


@dataclass(frozen=True, slots=True)
class RuntimeVerification:
    valid: bool
    packages: dict[str, str]
    failure_code: str | None = None
    failure_detail: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeInstallContract:
    contract_sha256: str
    target: str
    requirements_file: Path
    packages: dict[str, str]
    record_hashes: dict[str, str]

    @property
    def runtime_revision(self) -> str:
        return self.contract_sha256


class UnsupportedRuntimeError(RuntimeError):
    pass


def runtime_target_id() -> str:
    contract = current_runtime_contract()
    platform_id = str(contract["platform"])
    machine = str(contract["machine"])
    if platform_id == "darwin" and machine in {"arm64", "aarch64"}:
        return "aarch64-apple-darwin"
    if platform_id == "darwin" and machine in {"x86_64", "amd64"}:
        return "x86_64-apple-darwin"
    if platform_id == "win32" and machine in {"x86_64", "amd64"}:
        return "x86_64-pc-windows-msvc"
    if platform_id.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "x86_64-unknown-linux-gnu"
    raise RuntimeError(
        f"No managed media-runtime target for platform={platform_id!r} machine={machine!r}"
    )


def _contract_manifest_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.getenv("MATRX_MEDIA_RUNTIME_MANIFEST")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    filename = f"image-gen-{runtime_target_id()}.json"
    candidates.append(bundle_root / "runtime-manifests" / filename)
    candidates.append(bundle_root / "config" / "runtime-manifests" / filename)
    candidates.append(
        Path(__file__).resolve().parents[3] / "config" / "runtime-manifests" / filename
    )
    return candidates


def load_runtime_install_contract() -> RuntimeInstallContract:
    """Load and authenticate the release-owned target lock manifest.

    There is intentionally no floating-package fallback. A release without a
    current hash-locked artifact cannot mutate a user's runtime.
    """
    manifest_path = next((path for path in _contract_manifest_candidates() if path.is_file()), None)
    if manifest_path is None:
        raise RuntimeError(
            f"No locked media-runtime manifest is available for {runtime_target_id()}"
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Runtime contract cannot be read: {exc}") from exc
    if raw.get("schema_version") != RUNTIME_MANIFEST_REVISION:
        raise RuntimeError("Runtime contract manifest revision is unsupported")
    if raw.get("target") != runtime_target_id():
        raise RuntimeError(
            f"Runtime contract target {raw.get('target')!r} does not match "
            f"{runtime_target_id()!r}"
        )
    if raw.get("python_minor") != str(current_runtime_contract()["python_version"]):
        raise RuntimeError("Target runtime Python minor does not match this engine")
    supported = raw.get("supported", raw.get("support", True))
    if supported is False:
        raise UnsupportedRuntimeError(
            str(raw.get("unsupported_reason") or raw.get("reason") or "Platform is unsupported")
        )
    minimum_macos = raw.get("minimum_macos")
    if minimum_macos and sys.platform == "darwin":
        current_text = platform.mac_ver()[0]
        try:
            current = tuple(int(part) for part in current_text.split(".")[:2])
            required = tuple(int(part) for part in str(minimum_macos).split(".")[:2])
        except ValueError as exc:
            raise RuntimeError("Runtime contract has an invalid minimum_macos") from exc
        if current < required:
            raise UnsupportedRuntimeError(
                f"Local media generation requires macOS {minimum_macos} or newer; "
                f"this machine runs {current_text}."
            )
    requirements_value = raw.get("lock_file")
    if not isinstance(requirements_value, str) or Path(requirements_value).name != requirements_value:
        raise RuntimeError("Runtime contract requirements_file must be a basename")
    requirements = manifest_path.parent / requirements_value
    if not requirements.is_file():
        raise RuntimeError(f"Locked requirements artifact is missing: {requirements}")
    requirements_bytes = requirements.read_bytes()
    requirements_text = requirements_bytes.decode("utf-8")
    pinned_lines = [
        line.strip()
        for line in requirements_text.splitlines()
        if line.strip()
        and not line.lstrip().startswith(("#", "--"))
        and not line.strip().startswith("\\")
    ]
    if not pinned_lines or any("==" not in line for line in pinned_lines):
        raise RuntimeError(
            "Runtime lock must contain only exact package versions"
        )
    if "--hash=sha256:" not in requirements_text:
        raise RuntimeError("Runtime lock contains no sha256 wheel hashes")
    lock_digest = hashlib.sha256(requirements_bytes).hexdigest()
    if raw.get("lock_sha256") != lock_digest:
        raise RuntimeError(
            f"Runtime lock digest mismatch: claimed {raw.get('lock_sha256')!r}, "
            f"calculated {lock_digest}"
        )
    canonical_path = manifest_path.parent / "image-gen-contract.json"
    try:
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Canonical runtime contract cannot be read: {exc}") from exc
    claimed_contract = raw.get("contract_sha256")
    if canonical.get("contract_sha256") != claimed_contract:
        raise RuntimeError("Target lock does not match the canonical runtime contract")
    if canonical.get("python_minor") != str(current_runtime_contract()["python_version"]):
        raise RuntimeError("Canonical runtime Python minor does not match this engine")
    package_entries = raw.get("packages")
    if not isinstance(package_entries, list) or not package_entries:
        raise RuntimeError("Runtime contract has no exact package-version map")
    packages: dict[str, str] = {}
    for entry in package_entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Runtime contract package entry is malformed")
        name, version, wheels = entry.get("name"), entry.get("version"), entry.get("wheels")
        if not isinstance(name, str) or not isinstance(version, str) or not isinstance(wheels, list):
            raise RuntimeError("Runtime contract package entry is incomplete")
        if name in packages:
            raise RuntimeError(f"Duplicate runtime package entry: {name}")
        packages[name] = version
    return RuntimeInstallContract(
        contract_sha256=str(claimed_contract),
        target=runtime_target_id(),
        requirements_file=requirements,
        packages=packages,
        record_hashes={},
    )


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
    if not getattr(sys, "frozen", False):
        return _source_runtime_verification().valid
    snapshot = authoritative_snapshot()
    if not snapshot.ready or snapshot.active_slot is None:
        return False
    try:
        contract = load_runtime_install_contract()
    except Exception:
        return False
    return (
        snapshot.runtime_revision == contract.runtime_revision
        and validate_slot(
            snapshot.active_slot,
            expected_revision=contract.runtime_revision,
            expected_packages=contract.packages,
            expected_target=contract.target,
        )[0]
    )


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
    if not getattr(sys, "frozen", False):
        return False
    if _compatibility_migration_pending():
        return True
    if not is_image_gen_installed():
        # A legacy completion marker is evidence that user opted into the
        # runtime, but is never authority. Convert it through a full staged
        # reinstall instead of trusting or mutating it in place.
        return (packages_dir("image-gen-packages") / INSTALL_EVIDENCE).exists()
    snapshot = authoritative_snapshot()
    try:
        contract = load_runtime_install_contract()
    except Exception:
        return True
    if (
        snapshot.runtime_revision != contract.runtime_revision
        or snapshot.active_slot is None
        or not validate_slot(
            snapshot.active_slot,
            expected_revision=contract.runtime_revision,
            expected_packages=contract.packages,
            expected_target=contract.target,
        )[0]
    ):
        return True
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
    transformers_module = modules["transformers"]
    missing_transformers = [
        name
        for name in ("AutoImageProcessor", "AutoTokenizer")
        if not hasattr(transformers_module, name)
    ]
    if missing_transformers:
        raise RuntimeError(
            "Transformers is missing required runtime attributes: "
            + ", ".join(missing_transformers)
        )
    if importer is importlib.import_module:
        torch = modules["torch"]
        torchvision = modules["torchvision"]
        boxes = torch.empty((0, 4), dtype=torch.float32)
        scores = torch.empty((0,), dtype=torch.float32)
        torchvision.ops.nms(boxes, scores, 0.5)

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
    if getattr(sys, "frozen", False):
        contract = load_runtime_install_contract()
        env = os.environ.copy()
        env["MATRX_FROZEN_RUNTIME_VERIFY"] = "1"
        env["MATRX_FROZEN_RUNTIME_PATH"] = str(pkg_dir)
        env["MATRX_FROZEN_RUNTIME_TARGET"] = contract.target
        check = _run_subprocess_cancellable(
            [sys.executable],
            cancel_event=cancel_event,
            env=env,
            timeout=180,
        )
        sentinel = "MATRX_FROZEN_RUNTIME_VERIFY="
        line = next(
            (item for item in check.stdout.splitlines() if item.startswith(sentinel)),
            None,
        )
        if line is None:
            raise RuntimeError(
                "Frozen runtime verifier emitted no sentinel: "
                f"rc={check.returncode} stderr={check.stderr[-4000:]}"
            )
        try:
            result = json.loads(line.removeprefix(sentinel))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Frozen runtime verifier emitted invalid JSON") from exc
        if (
            check.returncode != 0
            or result.get("ok") is not True
            or result.get("contract") != contract.runtime_revision
        ):
            raise RuntimeError(
                "Frozen runtime verification failed: "
                + str(result.get("error") or result)
            )
        return dict(contract.packages)

    python = _find_python()
    _validate_selected_python(python, cancel_event=cancel_event)
    imports = json.dumps(CRITICAL_RUNTIME_IMPORTS)
    classes = json.dumps(CRITICAL_PIPELINE_CLASSES)
    script = (
        "import importlib,json,pathlib,sys; "
        f"root=pathlib.Path({str(pkg_dir)!r}).resolve(); "
        "sys.path.append(str(root)); "
        f"mods={{n:importlib.import_module(n) for n in {imports}}}; "
        f"missing=[n for n in {classes} if not hasattr(mods['diffusers'],n)]; "
        "assert not missing, missing; "
        "missing_t=[n for n in ('AutoImageProcessor','AutoTokenizer') "
        "if not hasattr(mods['transformers'],n)]; assert not missing_t, missing_t; "
        "managed=('torch','torchvision','diffusers','transformers','accelerate','peft'); "
        "bad={n:str(pathlib.Path(mods[n].__file__).resolve()) for n in managed "
        "if root not in pathlib.Path(mods[n].__file__).resolve().parents}; "
        "assert not bad, bad; "
        "assert mods['diffusers'].__version__=='0.39.0'; "
        "assert tuple(map(int,mods['transformers'].__version__.split('.')[:2])) >= (5,3); "
        "t=mods['torch']; tv=mods['torchvision']; "
        "tv.ops.nms(t.empty((0,4),dtype=t.float32),t.empty((0,),dtype=t.float32),0.5); "
        "print(json.dumps({n:str(getattr(mods[n],'__version__','unknown')) for n in managed}))"
    )
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    check = _run_subprocess_cancellable(
        [python, "-I", "-c", script],
        cancel_event=cancel_event,
        env=env,
        timeout=180,
    )
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
    """Compatibility facade for a synchronous clean, staged runtime update.

    It never mutates the legacy tree. Models, encoders, LoRAs, generated media,
    and queues live outside runtime slots and remain untouched.
    """
    if not needs_upgrade():
        return False

    progress = progress or InstallProgress(log_prefix="image_gen_runtime_migration")
    progress.status = "running"
    _install_runtime_sync(
        progress,
        cancel_event,
        "update",
        uuid.uuid4().hex,
    )
    if progress.status == "error":
        raise RuntimeError(progress.error or "Managed runtime migration failed")
    return True



def inject_image_gen_path(pkg_dir_path: Path | None = None) -> bool:
    """Add the managed packages dir to sys.path if the install is complete.

    Called from runtime_hook.py and at engine startup.
    Returns True if path was injected, False if packages not yet installed.
    """
    pending_activation = False
    if pkg_dir_path is None:
        snapshot = authoritative_snapshot()
        if snapshot.ready and snapshot.active_slot:
            pkg_dir_path = slot_path(snapshot.active_slot)
        elif snapshot.state is RuntimePhase.RESTART_REQUIRED and snapshot.candidate_slot:
            valid, _, _ = validate_slot(snapshot.candidate_slot)
            if not valid:
                return False
            pkg_dir_path = slot_path(snapshot.candidate_slot)
            pending_activation = True
        elif not getattr(sys, "frozen", False):
            # Source development uses its uv environment directly. It never
            # writes or borrows the installed app's managed-runtime state.
            try:
                critical_runtime_import_check()
                return True
            except Exception:
                return False
        else:
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
    if pending_activation:
        verification = verify_runtime_path(pkg_dir_path)
        if not verification.valid:
            return False
        with RuntimeFileLock():
            snapshot = read_snapshot()
            if (
                snapshot.state is RuntimePhase.RESTART_REQUIRED
                and snapshot.candidate_slot == pkg_dir_path.name
            ):
                write_snapshot(
                    RuntimeSnapshot(
                        state=RuntimePhase.READY,
                        runtime_revision=snapshot.runtime_revision,
                        operation=snapshot.operation,
                        attempt_id=snapshot.attempt_id,
                        stage="ready",
                        percent=100.0,
                        message="Managed media runtime activated after restart.",
                        active_slot=snapshot.candidate_slot,
                        last_known_good_slot=snapshot.active_slot,
                        packages=verification.packages,
                    )
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


def verify_runtime_path(path: Path) -> RuntimeVerification:
    """Validate one immutable candidate using production import precedence."""
    path = path.resolve(strict=False)
    try:
        contract = load_runtime_install_contract()
    except Exception as exc:
        return RuntimeVerification(False, {}, "contract_unavailable", str(exc))
    valid, reason, manifest = validate_slot(
        path.name,
        expected_revision=contract.runtime_revision,
        expected_packages=contract.packages,
        expected_target=contract.target,
    )
    if not valid or slot_path(path.name) != path:
        return RuntimeVerification(False, {}, "manifest_invalid", reason or "invalid slot path")

    modules_before = set(sys.modules)
    path_was_present = str(path) in sys.path
    if not path_was_present:
        # Production precedence: frozen/core packages remain ahead of the
        # optional runtime. This is the only ordering the verifier may certify.
        sys.path.append(str(path))
    try:
        packages = critical_runtime_import_check(expected_root=path)
    except Exception as exc:
        _rollback_runtime_activation(path, modules_before, path_was_present)
        return RuntimeVerification(False, {}, "critical_import_failed", str(exc))
    expected = contract.packages
    mismatches = {
        name: (packages.get(name), version)
        for name, version in expected.items()
        if name in packages and packages.get(name) != version
    }
    if mismatches:
        _rollback_runtime_activation(path, modules_before, path_was_present)
        return RuntimeVerification(
            False,
            packages,
            "package_version_mismatch",
            repr(mismatches),
        )
    return RuntimeVerification(True, packages or dict(manifest["packages"]))


def validate_active_runtime() -> RuntimeVerification:
    snapshot = authoritative_snapshot()
    if not snapshot.ready or snapshot.active_slot is None:
        return RuntimeVerification(
            False,
            {},
            "runtime_not_ready",
            snapshot.failure_detail or snapshot.message,
        )
    return verify_runtime_path(slot_path(snapshot.active_slot))


def record_runtime_integrity_failure(detail: str) -> None:
    """Persist a verified-runtime import/native failure as repairable state."""
    if not getattr(sys, "frozen", False):
        return
    with RuntimeFileLock():
        snapshot = authoritative_snapshot()
        if not snapshot.ready:
            return
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase.FAILED,
                runtime_revision=snapshot.runtime_revision,
                operation=None,
                attempt_id=snapshot.attempt_id,
                stage="runtime_integrity",
                percent=100.0,
                message="The active media runtime failed an integrity check and requires repair.",
                failure_code="runtime_integrity_failed",
                failure_detail=detail,
                active_slot=snapshot.active_slot,
                last_known_good_slot=snapshot.last_known_good_slot,
                packages=snapshot.packages,
            )
        )


def is_runtime_integrity_failure(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ImportError):
            return True
        text = str(current).lower()
        if any(
            marker in text
            for marker in (
                "failed to import",
                "dlopen",
                "undefined symbol",
                "symbol not found",
                "dll load failed",
                "cannot open shared object",
                "mach-o",
                "wrong architecture",
                "incompatible architecture",
                "library not loaded",
                "dynamic module does not define module export function",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


# ── Global singleton ──────────────────────────────────────────────────────────

_active_progress: InstallProgress | None = None
_background_futures: set[asyncio.Future[object]] = set()
_installer_cancel = threading.Event()


def get_active_progress() -> InstallProgress | None:
    return _active_progress


_source_verification_cache: RuntimeVerification | None = None


def _source_runtime_verification() -> RuntimeVerification:
    global _source_verification_cache
    if _source_verification_cache is not None:
        return _source_verification_cache
    try:
        packages = critical_runtime_import_check()
        _source_verification_cache = RuntimeVerification(True, packages)
    except Exception as exc:
        _source_verification_cache = RuntimeVerification(
            False, {}, "source_runtime_invalid", str(exc)
        )
    return _source_verification_cache


def get_runtime_status() -> dict[str, Any]:
    """Canonical backend/UI snapshot for the managed image/video runtime."""
    progress = get_active_progress()
    if not getattr(sys, "frozen", False):
        source = _source_runtime_verification()
        if source.valid:
            return {
                "state": "ready",
                "operation": None,
                "attempt_id": None,
                "runtime_revision": "source-development",
                "required_revision": "source-development",
                "stage": "ready",
                "percent": 100.0,
                "message": "Source-development media runtime verified.",
                "failure_code": None,
                "failure_detail": None,
                "repairable": False,
                "image_available": True,
                "video_packages_available": True,
                "package_checks": [
                    {"name": name, "version": version, "ok": True}
                    for name, version in sorted(source.packages.items())
                ],
                "log_lines": list(progress.log_lines) if progress else [],
                "active_slot": None,
                "last_known_good_slot": None,
                "candidate_slot": None,
                "packages": source.packages,
                "manifest_revision": RUNTIME_MANIFEST_REVISION,
            }

    snapshot = authoritative_snapshot()
    try:
        contract = load_runtime_install_contract()
        required_revision: str | None = contract.runtime_revision
        contract_error: str | None = None
        unsupported = False
    except UnsupportedRuntimeError as exc:
        contract = None
        required_revision = "unsupported"
        contract_error = str(exc)
        unsupported = True
    except Exception as exc:
        contract = None
        required_revision = "unavailable"
        contract_error = str(exc)
        unsupported = False

    state = snapshot.state.value
    message = snapshot.message
    failure_code = snapshot.failure_code
    failure_detail = snapshot.failure_detail
    ready = snapshot.ready
    if unsupported:
        state = RuntimePhase.FAILED.value
        ready = False
        failure_code = "unsupported_platform"
        failure_detail = contract_error
    elif contract is None:
        state = RuntimePhase.FAILED.value
        ready = False
        failure_code = "contract_unavailable"
        failure_detail = contract_error
        message = (
            "This app build does not contain a valid media-runtime contract. "
            "Update AI Matrx to a corrected release."
        )
    elif ready and (
        contract is None or snapshot.runtime_revision != contract.runtime_revision
    ):
        state = RuntimePhase.FAILED.value
        ready = False
        failure_code = "contract_unavailable" if contract is None else "revision_mismatch"
        failure_detail = contract_error or (
            f"active={snapshot.runtime_revision!r} required={required_revision!r}"
        )

    logs: list[str] = []
    if progress is not None:
        with progress._lock:
            logs = list(progress.log_lines)
    return {
        "state": state,
        "operation": snapshot.operation,
        "attempt_id": snapshot.attempt_id,
        "runtime_revision": snapshot.runtime_revision,
        "required_revision": required_revision,
        "stage": snapshot.stage,
        "percent": snapshot.percent,
        "message": message,
        "failure_code": failure_code,
        "failure_detail": failure_detail,
        "repairable": (
            state in {"failed", "rolled_back"}
            and not unsupported
            and failure_code != "contract_unavailable"
        ),
        "image_available": ready,
        "video_packages_available": ready,
        "package_checks": [
            {"name": name, "version": version, "ok": ready}
            for name, version in sorted(snapshot.packages.items())
        ],
        "log_lines": logs,
        "active_slot": snapshot.active_slot,
        "last_known_good_slot": snapshot.last_known_good_slot,
        "candidate_slot": snapshot.candidate_slot,
        "packages": snapshot.packages,
        "manifest_revision": snapshot.manifest_revision,
    }


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



def _persist_progress(
    *,
    phase: RuntimePhase,
    operation: str,
    attempt_id: str,
    progress: InstallProgress,
    active_slot: str | None,
    last_known_good_slot: str | None,
    candidate_slot: str | None = None,
    runtime_revision: str | None = None,
    failure_code: str | None = None,
    failure_detail: str | None = None,
    packages: dict[str, str] | None = None,
) -> None:
    write_snapshot(
        RuntimeSnapshot(
            state=phase,
            runtime_revision=runtime_revision,
            operation=operation,
            attempt_id=attempt_id,
            stage=progress.stage,
            percent=progress.percent,
            message=progress.message,
            failure_code=failure_code,
            failure_detail=failure_detail,
            active_slot=active_slot,
            last_known_good_slot=last_known_good_slot,
            candidate_slot=candidate_slot,
            packages=packages or {},
        )
    )


def _install_runtime_sync(
    progress: InstallProgress,
    cancel_event: threading.Event | None,
    operation: Literal["install", "update", "repair"],
    attempt_id: str,
) -> None:
    """Build, validate, and atomically publish one immutable runtime slot."""
    staging: Path | None = None
    candidate: Path | None = None
    previous = authoritative_snapshot()
    previous_active = previous.active_slot if previous.ready else None
    last_known_good = previous_active or previous.last_known_good_slot
    phase = {
        "install": RuntimePhase.INSTALLING,
        "update": RuntimePhase.UPDATING,
        "repair": RuntimePhase.REPAIRING,
    }[operation]
    try:
        with RuntimeFileLock(timeout=60.0):
            contract = load_runtime_install_contract()
            progress.update("preparing", 2.0, "Preparing immutable media runtime…")
            _persist_progress(
                phase=phase,
                operation=operation,
                attempt_id=attempt_id,
                progress=progress,
                active_slot=previous_active,
                last_known_good_slot=last_known_good,
                runtime_revision=contract.runtime_revision,
            )
            _, staging = create_staging_slot()

            if not getattr(sys, "frozen", False):
                python = _find_python()
                _validate_selected_python(python, cancel_event=cancel_event)
            progress.update("installing", 10.0, "Installing locked media runtime…")
            _run_pip_streaming(
                [],
                staging,
                progress,
                cancel_event=cancel_event,
                requirements_file=contract.requirements_file,
                require_hashes=True,
            )

            progress.update("validating", 72.0, "Validating every critical runtime import…")
            _persist_progress(
                phase=RuntimePhase.VALIDATING,
                operation=operation,
                attempt_id=attempt_id,
                progress=progress,
                active_slot=previous_active,
                last_known_good_slot=last_known_good,
                runtime_revision=contract.runtime_revision,
            )
            installed_versions = _package_versions_at(staging)
            if installed_versions != contract.packages:
                raise RuntimeError(
                    "Installed package versions do not exactly match the release contract: "
                    f"expected={contract.packages!r} actual={installed_versions!r}"
                )
            _verify_runtime_subprocess(staging, cancel_event=cancel_event)
            (staging / INSTALL_EVIDENCE).write_text(
                contract.runtime_revision, encoding="utf-8"
            )
            write_slot_manifest(
                staging,
                runtime_revision=contract.runtime_revision,
                packages=installed_versions,
                target=contract.target,
                record_hashes=contract.record_hashes,
            )
            candidate_name, candidate = finalize_staging_slot(
                staging, contract.runtime_revision
            )
            staging = None

            progress.update("activating", 90.0, "Activating verified media runtime…")
            _persist_progress(
                phase=RuntimePhase.ACTIVATING,
                operation=operation,
                attempt_id=attempt_id,
                progress=progress,
                active_slot=previous_active,
                last_known_good_slot=last_known_good,
                candidate_slot=candidate_name,
                runtime_revision=contract.runtime_revision,
                packages=installed_versions,
            )
            verification = verify_runtime_path(candidate)
            if not verification.valid:
                if (
                    verification.failure_code == "critical_import_failed"
                    and verification.failure_detail
                    and "outside candidate runtime" in verification.failure_detail
                ):
                    progress.finish("Runtime verified; engine restart required for activation.")
                    _persist_progress(
                        phase=RuntimePhase.RESTART_REQUIRED,
                        operation=operation,
                        attempt_id=attempt_id,
                        progress=progress,
                        active_slot=previous_active,
                        last_known_good_slot=last_known_good,
                        candidate_slot=candidate_name,
                        runtime_revision=contract.runtime_revision,
                        packages=installed_versions,
                    )
                    return
                raise RuntimeError(
                    verification.failure_detail or "Production runtime activation failed"
                )

            from app.services.image_gen import service as image_service
            from app.services.video_gen import service as video_service

            image_service.DEPS_AVAILABLE, image_service.DEPS_REASON = image_service._check_deps()
            video_service.DEPS_AVAILABLE, video_service.DEPS_REASON = video_service._check_deps()
            if not image_service.DEPS_AVAILABLE or not video_service.DEPS_AVAILABLE:
                raise RuntimeError(
                    "Runtime imports passed but service activation failed: "
                    f"image={image_service.DEPS_REASON!r}; video={video_service.DEPS_REASON!r}"
                )

            progress.finish("Managed media runtime is verified and ready.")
            _persist_progress(
                phase=RuntimePhase.READY,
                operation=operation,
                attempt_id=attempt_id,
                progress=progress,
                active_slot=candidate_name,
                last_known_good_slot=previous_active,
                runtime_revision=contract.runtime_revision,
                packages=installed_versions,
            )
    except Exception as exc:
        if staging is not None:
            remove_slot(staging)
        if candidate is not None:
            remove_slot(candidate)
        progress.fail(str(exc))
        rolled_back = False
        if previous_active:
            valid, _, _ = validate_slot(previous_active)
            rolled_back = valid
        _persist_progress(
            phase=RuntimePhase.ROLLED_BACK if rolled_back else RuntimePhase.FAILED,
            operation=operation,
            attempt_id=attempt_id,
            progress=progress,
            active_slot=previous_active if rolled_back else None,
            last_known_good_slot=last_known_good,
            runtime_revision=previous.runtime_revision if rolled_back else None,
            failure_code=("activation_rolled_back" if rolled_back else "install_failed"),
            failure_detail=str(exc),
            packages=previous.packages if rolled_back else {},
        )


# ── Public API ────────────────────────────────────────────────────────────────


async def start_install() -> InstallProgress:
    """Start a background install.  Returns immediately with a progress object.

    Raises RuntimeError if an install is already running.
    """
    return await ensure_runtime("install")


async def ensure_runtime(
    operation: Literal["install", "update"] = "install",
) -> InstallProgress:
    """Ensure the exact release runtime exists; never accept a floating install."""
    global _active_progress
    if _active_progress is not None and _active_progress.status == "running":
        return _active_progress

    # Source runs intentionally consume `uv sync --extra image-gen` without
    # touching the installed application's durable state.
    if not getattr(sys, "frozen", False):
        try:
            critical_runtime_import_check()
        except Exception:
            pass
        else:
            progress = InstallProgress(log_prefix="image_gen_source_runtime")
            progress._loop = asyncio.get_running_loop()
            progress.finish(
                "Source-development media runtime verified from the active uv environment."
            )
            _active_progress = progress
            return progress

    snapshot = authoritative_snapshot()
    try:
        contract = load_runtime_install_contract()
    except UnsupportedRuntimeError as exc:
        progress = InstallProgress(log_prefix="image_gen_installer")
        progress._loop = asyncio.get_running_loop()
        progress.fail(str(exc))
        _active_progress = progress
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase.FAILED,
                operation=operation,
                attempt_id=uuid.uuid4().hex,
                stage="unsupported",
                message=str(exc),
                failure_code="unsupported_platform",
                failure_detail=str(exc),
                active_slot=snapshot.active_slot if snapshot.ready else None,
                last_known_good_slot=snapshot.last_known_good_slot,
            )
        )
        return progress
    except Exception:
        contract = None
    if contract is None:
        progress = InstallProgress(log_prefix="image_gen_installer")
        progress._loop = asyncio.get_running_loop()
        detail = "No valid locked media-runtime contract is embedded in this app build."
        progress.fail(detail)
        _active_progress = progress
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase.FAILED,
                operation=operation,
                attempt_id=uuid.uuid4().hex,
                stage="contract",
                message=(
                    "This app build cannot install media generation safely. "
                    "Update AI Matrx to a corrected release."
                ),
                failure_code="contract_unavailable",
                failure_detail=detail,
                active_slot=snapshot.active_slot if snapshot.ready else None,
                last_known_good_slot=snapshot.last_known_good_slot,
            )
        )
        return progress
    if (
        snapshot.ready
        and contract is not None
        and snapshot.runtime_revision == contract.runtime_revision
        and snapshot.active_slot is not None
        and validate_slot(
            snapshot.active_slot,
            expected_revision=contract.runtime_revision,
            expected_packages=contract.packages,
            expected_target=contract.target,
        )[0]
    ):
        progress = InstallProgress(log_prefix="image_gen_installer")
        progress._loop = asyncio.get_running_loop()
        progress.finish("Managed media runtime already verified.")
        _active_progress = progress
        return progress

    progress = InstallProgress(log_prefix="image_gen_installer")
    progress.status = "running"
    progress._loop = asyncio.get_running_loop()
    _active_progress = progress
    _installer_cancel.clear()
    attempt_id = uuid.uuid4().hex
    effective: Literal["install", "update", "repair"] = operation
    progress.update("preparing", 1.0, f"Preparing media-runtime {operation}…")
    phase = RuntimePhase.INSTALLING if operation == "install" else RuntimePhase.UPDATING
    _persist_progress(
        phase=phase,
        operation=operation,
        attempt_id=attempt_id,
        progress=progress,
        active_slot=snapshot.active_slot if snapshot.ready else None,
        last_known_good_slot=(
            snapshot.active_slot if snapshot.ready else snapshot.last_known_good_slot
        ),
        runtime_revision=contract.runtime_revision if contract is not None else None,
    )
    _submit_background(
        _install_runtime_sync,
        progress,
        _installer_cancel,
        effective,
        attempt_id,
    )
    return progress


async def repair_runtime() -> InstallProgress:
    """Always build a clean slot; never repair a package tree in place."""
    global _active_progress
    if _active_progress is not None and _active_progress.status == "running":
        return _active_progress
    try:
        load_runtime_install_contract()
    except UnsupportedRuntimeError as exc:
        progress = InstallProgress(log_prefix="image_gen_runtime_repair")
        progress._loop = asyncio.get_running_loop()
        progress.fail(str(exc))
        _active_progress = progress
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase.FAILED,
                operation="repair",
                attempt_id=uuid.uuid4().hex,
                stage="unsupported",
                message=str(exc),
                failure_code="unsupported_platform",
                failure_detail=str(exc),
            )
        )
        return progress
    except Exception as exc:
        progress = InstallProgress(log_prefix="image_gen_runtime_repair")
        progress._loop = asyncio.get_running_loop()
        progress.fail(str(exc))
        _active_progress = progress
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase.FAILED,
                operation="repair",
                stage="contract",
                message="Update AI Matrx to restore the media-runtime contract.",
                failure_code="contract_unavailable",
                failure_detail=str(exc),
            )
        )
        return progress
    progress = InstallProgress(log_prefix="image_gen_runtime_repair")
    progress.status = "running"
    progress._loop = asyncio.get_running_loop()
    _active_progress = progress
    _installer_cancel.clear()
    attempt_id = uuid.uuid4().hex
    previous = authoritative_snapshot()
    progress.update("preparing", 1.0, "Preparing clean media-runtime repair…")
    try:
        required_revision = load_runtime_install_contract().runtime_revision
    except Exception:
        required_revision = None
    _persist_progress(
        phase=RuntimePhase.REPAIRING,
        operation="repair",
        attempt_id=attempt_id,
        progress=progress,
        active_slot=previous.active_slot if previous.ready else None,
        last_known_good_slot=(
            previous.active_slot if previous.ready else previous.last_known_good_slot
        ),
        runtime_revision=required_revision,
    )
    _submit_background(
        _install_runtime_sync,
        progress,
        _installer_cancel,
        "repair",
        attempt_id,
    )
    return progress


async def start_compatibility_migration() -> InstallProgress | None:
    """Start the mandatory old-runtime migration without blocking app startup.

    The runtime hook has already withheld incompatible optional packages, so
    this background task cannot expose the old loader. The shared progress
    singleton lets the existing installer status/SSE/UI show its automatic
    progress and makes duplicate startup calls harmless.
    """
    global _active_progress
    if authoritative_snapshot().state is RuntimePhase.RESTART_REQUIRED:
        return None
    if not needs_upgrade():
        return None
    try:
        load_runtime_install_contract()
    except UnsupportedRuntimeError as exc:
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase.FAILED,
                operation="update",
                stage="unsupported",
                message=str(exc),
                failure_code="unsupported_platform",
                failure_detail=str(exc),
            )
        )
        return None
    except Exception as exc:
        write_snapshot(
            RuntimeSnapshot(
                state=RuntimePhase.FAILED,
                operation="update",
                stage="contract",
                message="Update AI Matrx to restore the media-runtime contract.",
                failure_code="contract_unavailable",
                failure_detail=str(exc),
            )
        )
        return None
    if _active_progress is not None and _active_progress.status == "running":
        return _active_progress

    progress = InstallProgress(log_prefix="image_gen_runtime_migration")
    progress.status = "running"
    progress._loop = asyncio.get_running_loop()
    _active_progress = progress
    _installer_cancel.clear()
    attempt_id = uuid.uuid4().hex
    previous = authoritative_snapshot()
    try:
        required_revision = load_runtime_install_contract().runtime_revision
    except Exception:
        required_revision = None
    progress.update("preparing", 1.0, "Preparing required media-runtime update…")
    _persist_progress(
        phase=RuntimePhase.UPDATING,
        operation="update",
        attempt_id=attempt_id,
        progress=progress,
        active_slot=previous.active_slot if previous.ready else None,
        last_known_good_slot=(
            previous.active_slot if previous.ready else previous.last_known_good_slot
        ),
        runtime_revision=required_revision,
    )
    _submit_background(
        _install_runtime_sync,
        progress,
        _installer_cancel,
        "update",
        attempt_id,
    )
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
