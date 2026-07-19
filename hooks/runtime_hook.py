"""PyInstaller runtime hook — set environment variables for bundled tools.

This runs before any application code when the frozen binary starts.
It injects compile-time Supabase bootstrap config and then
points Playwright, Tesseract, and ffmpeg to their bundled/user locations
inside sys._MEIPASS (the PyInstaller extraction directory) or the user's home.
"""

import os
import sys
import base64
import csv
from pathlib import Path

# ── Windows UTF-8 fix — MUST be first, before any other import ───────────────
#
# Windows uses CP1252 (charmap) as the default stdout/stderr encoding for
# console applications. Our log messages contain Unicode characters (✓, →, ←,
# ─, ⚠, etc.) that are not representable in CP1252. When Python tries to write
# them it raises UnicodeEncodeError, which gets swallowed by Starlette's
# middleware logger and floods stderr with hundreds of "--- Logging error ---"
# tracebacks per second — completely obscuring real errors.
#
# Fix strategy (defence-in-depth):
#   1. PYTHONUTF8=1 — tells the Python interpreter itself to use UTF-8 for
#      ALL text I/O, file opens, etc.  Effective for subprocesses we spawn.
#   2. PYTHONIOENCODING=utf-8:replace — used by Python < 3.7 and as a
#      secondary signal for libraries that read it directly.
#   3. Reconfigure sys.stdout / sys.stderr with UTF-8 + errors='replace' so
#      that any character that still can't be encoded becomes '?' instead of
#      raising. This is the decisive fix for the running process.
#
# This hook runs before any application code so the streams are correct for
# the very first log line emitted during import of app.common.platform_ctx.
#
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        else:
            import io

            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        else:
            import io

            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )
    except Exception:
        pass  # If reconfigure fails (e.g. no buffer attr), continue — better than crashing.

# Inject the Supabase bootstrap values baked in at build time by CI. Runtime
# service URLs come from remote app config (with its last-good disk cache).
# Must run before any other module import so dotenv / config.py see the values.
try:
    from app.bundled_config import apply as _apply_bundled_config

    _apply_bundled_config()
except Exception:
    pass  # Dev mode or partial build — values come from .env instead.

if hasattr(sys, "_MEIPASS"):
    base = sys._MEIPASS

    # Playwright: browsers are NOT bundled inside the binary (bundling causes
    # codesign failures on macOS with Chrome's nested framework structure).
    # Point to a persistent user-writable directory instead; the engine will
    # auto-install browsers there on first startup if they are missing.
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(Path.home() / ".matrx" / "playwright-browsers"),
    )

    # Tesseract: point to the bundled tessdata language files.
    tessdata = os.path.join(base, "tessdata")
    if os.path.isdir(tessdata):
        os.environ.setdefault("TESSDATA_PREFIX", tessdata)

    # imageio-ffmpeg: it auto-discovers its binary, but set PATH so
    # yt-dlp and any subprocess calls can also find ffmpeg.
    ffmpeg_bin_dir = os.path.join(base, "imageio_ffmpeg", "binaries")
    if os.path.isdir(ffmpeg_bin_dir):
        os.environ["PATH"] = ffmpeg_bin_dir + os.pathsep + os.environ.get("PATH", "")

# ── HF Hub: disable Xet in the frozen binary ──────────────────────────────────
#
# hf_xet (native Rust extension shipped with user-installed huggingface_hub)
# spawns helper processes during Xet-backed downloads.  In a frozen PyInstaller
# binary a process spawn re-executes THIS binary, which booted a complete rogue
# second engine (observed live 2026-07-09: first model download → duplicate
# engine on 22141 stole ~/.matrx/local.json and killed the UI's connection).
# run.py's freeze_support() intercepts multiprocessing spawns, but Xet is not
# needed at all — plain HTTP downloads work at full speed — so disable it
# outright in frozen builds.  Respect an explicit user override if already set.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# Transformers 5.3's auto-docstring helper assumes inspect.getsourcefile()
# contains at least three path components and indexes ``[-3]`` to detect
# ``transformers/models/<family>``. PyInstaller preserves relative code-object
# filenames (for example ``transformers/modeling_layers.py``), which makes that
# valid import crash with IndexError before any model can load. Normalize only
# relative Transformers source paths to their real frozen extraction path.
if hasattr(sys, "_MEIPASS"):
    import inspect as _matrx_inspect

    _matrx_original_getsourcefile = _matrx_inspect.getsourcefile

    def _matrx_frozen_getsourcefile(obj):
        _matrx_source = _matrx_original_getsourcefile(obj)
        _matrx_module = getattr(obj, "__module__", "")
        if (
            _matrx_source
            and isinstance(_matrx_module, str)
            and _matrx_module.startswith("transformers.")
            and not os.path.isabs(_matrx_source)
        ):
            return os.path.join(sys._MEIPASS, _matrx_source)
        return _matrx_source

    _matrx_inspect.getsourcefile = _matrx_frozen_getsourcefile

# A new managed media runtime is an immutable, versioned slot. Activate it only
# when the durable state and slot manifest match the exact contract embedded in
# this application. This executes before app imports, so it intentionally stays
# self-contained instead of importing installer.py.
try:
    if os.getenv("MATRX_FROZEN_RUNTIME_VERIFY") == "1":
        raise RuntimeError("managed runtime activation is disabled during frozen verification")
    import hashlib as _runtime_hashlib
    import json as _runtime_json
    import platform as _runtime_platform

    if os.getenv("MATRX_HOME_DIR"):
        _runtime_home = Path(os.environ["MATRX_HOME_DIR"]).expanduser().resolve(
            strict=False
        )
    elif sys.platform == "win32":
        _runtime_local_app = os.getenv(
            "LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
        )
        _runtime_home = Path(_runtime_local_app) / "AI Matrx"
    else:
        _runtime_home = Path.home() / ".matrx"

    _runtime_control = _runtime_home / "image-gen-runtime"
    _runtime_state_path = _runtime_control / "state.json"
    if _runtime_state_path.is_file():
        _runtime_state = _runtime_json.loads(
            _runtime_state_path.read_text(encoding="utf-8")
        )
        _runtime_base = Path(
            getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
        )
        _runtime_contract = _runtime_json.loads(
            (
                _runtime_base
                / "config"
                / "runtime-manifests"
                / "image-gen-contract.json"
            ).read_text(encoding="utf-8")
        )
        _runtime_required = _runtime_contract["contract_sha256"]
        _runtime_machine = _runtime_platform.machine().lower()
        if sys.platform == "darwin" and _runtime_machine in {"arm64", "aarch64"}:
            _runtime_target = "aarch64-apple-darwin"
        elif sys.platform == "darwin" and _runtime_machine in {"x86_64", "amd64"}:
            _runtime_target = "x86_64-apple-darwin"
        elif sys.platform == "win32" and _runtime_machine in {"x86_64", "amd64"}:
            _runtime_target = "x86_64-pc-windows-msvc"
        elif sys.platform.startswith("linux") and _runtime_machine in {"x86_64", "amd64"}:
            _runtime_target = "x86_64-unknown-linux-gnu"
        else:
            raise RuntimeError(
                f"no managed runtime target for {sys.platform}/{_runtime_machine}"
            )
        _runtime_target_path = (
            _runtime_base
            / "config"
            / "runtime-manifests"
            / f"image-gen-{_runtime_target}.json"
        )
        _runtime_target_manifest = _runtime_json.loads(
            _runtime_target_path.read_text(encoding="utf-8")
        )
        if _runtime_target_manifest.get("target") != _runtime_target:
            raise RuntimeError("embedded managed runtime target manifest is mismatched")
        if _runtime_target_manifest.get("supported") is not True:
            raise RuntimeError(
                _runtime_target_manifest.get("unsupported_reason")
                or f"managed runtime is unsupported for {_runtime_target}"
            )
        _runtime_minimum_macos = _runtime_target_manifest.get("minimum_macos")
        if _runtime_minimum_macos and sys.platform == "darwin":
            _runtime_macos = _runtime_platform.mac_ver()[0]
            if tuple(map(int, _runtime_macos.split(".")[:2])) < tuple(
                map(int, str(_runtime_minimum_macos).split(".")[:2])
            ):
                raise RuntimeError(
                    f"managed runtime requires macOS {_runtime_minimum_macos}; "
                    f"found {_runtime_macos}"
                )
        _runtime_minimum_glibc = _runtime_target_manifest.get("minimum_glibc")
        if _runtime_minimum_glibc and sys.platform.startswith("linux"):
            _runtime_libc_name, _runtime_libc_version = _runtime_platform.libc_ver()
            if _runtime_libc_name.lower() != "glibc" or tuple(
                map(int, _runtime_libc_version.split(".")[:2])
            ) < tuple(map(int, str(_runtime_minimum_glibc).split(".")[:2])):
                raise RuntimeError(
                    f"managed runtime requires glibc {_runtime_minimum_glibc}; "
                    f"found {_runtime_libc_name or 'unknown'} "
                    f"{_runtime_libc_version or 'unknown'}"
                )
        if _runtime_target_manifest.get("contract_sha256") != _runtime_required:
            raise RuntimeError("embedded target manifest is stale for this app contract")
        _runtime_lock_name = _runtime_target_manifest.get("lock_file")
        if (
            not isinstance(_runtime_lock_name, str)
            or Path(_runtime_lock_name).name != _runtime_lock_name
        ):
            raise RuntimeError("embedded target manifest has an invalid lock filename")
        _runtime_lock_path = _runtime_target_path.parent / _runtime_lock_name
        _runtime_lock_digest = _runtime_hashlib.sha256(
            _runtime_lock_path.read_bytes()
        ).hexdigest()
        if _runtime_lock_digest != _runtime_target_manifest.get("lock_sha256"):
            raise RuntimeError("embedded managed runtime lock digest does not match")
        _runtime_packages = {
            _entry["name"]: _entry["version"]
            for _entry in _runtime_target_manifest.get("packages", ())
        }
        if not _runtime_packages or len(_runtime_packages) != len(
            _runtime_target_manifest.get("packages", ())
        ):
            raise RuntimeError("embedded target package inventory is invalid")
        _runtime_slot_name = _runtime_state.get("active_slot")
        if (
            _runtime_state.get("state") != "ready"
            or _runtime_state.get("runtime_revision") != _runtime_required
            or not isinstance(_runtime_slot_name, str)
            or Path(_runtime_slot_name).name != _runtime_slot_name
        ):
            raise RuntimeError(
                "state is not READY for this app contract "
                f"({_runtime_state.get('state')!r}, "
                f"{_runtime_state.get('runtime_revision')!r})"
            )

        _runtime_slots = (_runtime_control / "slots").resolve(strict=False)
        _runtime_slot = (_runtime_slots / _runtime_slot_name).resolve(strict=False)
        if _runtime_slot.parent != _runtime_slots:
            raise RuntimeError("active slot escapes the managed runtime root")
        _runtime_slot_manifest = _runtime_json.loads(
            (_runtime_slot / ".runtime-manifest.json").read_text(encoding="utf-8")
        )
        _runtime_expected = {
            "runtime_revision": _runtime_required,
            "python_abi": sys.implementation.cache_tag or "unknown",
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "platform": sys.platform,
            "machine": _runtime_machine,
            "target": _runtime_target,
            "packages": _runtime_packages,
        }
        _runtime_mismatches = [
            f"{_key}={_runtime_slot_manifest.get(_key)!r} (expected {_value!r})"
            for _key, _value in _runtime_expected.items()
            if _runtime_slot_manifest.get(_key) != _value
        ]
        if _runtime_mismatches:
            raise RuntimeError("; ".join(_runtime_mismatches))
        if not (_runtime_slot / ".install-complete").is_file():
            raise RuntimeError("active runtime slot has no install evidence")
        _runtime_record_anchors = _runtime_slot_manifest.get("record_hashes")
        if not isinstance(_runtime_record_anchors, dict) or not _runtime_record_anchors:
            raise RuntimeError("active runtime manifest has no RECORD anchors")
        for _runtime_relative, _runtime_claimed_hash in _runtime_record_anchors.items():
            _runtime_anchor_lexical = Path(
                os.path.abspath(os.path.normpath(_runtime_slot / _runtime_relative))
            )
            if not _runtime_anchor_lexical.is_relative_to(_runtime_slot.resolve(strict=False)):
                raise RuntimeError("runtime RECORD anchor escapes active slot")
            _runtime_anchor = _runtime_anchor_lexical.resolve(strict=False)
            if (
                not _runtime_anchor.is_relative_to(_runtime_slot.resolve(strict=False))
                or not _runtime_anchor.is_file()
            ):
                raise RuntimeError("runtime RECORD anchor is missing or resolves outside slot")
            _runtime_anchor_digest = _runtime_hashlib.sha256(
                _runtime_anchor.read_bytes()
            ).hexdigest()
            if _runtime_anchor_digest != str(_runtime_claimed_hash).removeprefix("sha256:"):
                raise RuntimeError(f"runtime RECORD anchor mismatch: {_runtime_relative}")
        _runtime_actual_packages = {}
        for _runtime_dist_info in _runtime_slot.glob("*.dist-info"):
            _runtime_dist_stem = _runtime_dist_info.name[: -len(".dist-info")]
            _runtime_dist_name, _, _runtime_dist_version = _runtime_dist_stem.rpartition("-")
            if _runtime_dist_name and _runtime_dist_version:
                _runtime_actual_packages[
                    _runtime_dist_name.replace("_", "-").lower()
                ] = _runtime_dist_version
        if _runtime_actual_packages != _runtime_packages:
            raise RuntimeError(
                "active runtime package inventory differs from the release contract"
            )
        _runtime_slot_root = _runtime_slot.resolve(strict=False)
        for _runtime_dist_info in _runtime_slot.glob("*.dist-info"):
            _runtime_record = _runtime_dist_info / "RECORD"
            if not _runtime_record.is_file():
                raise RuntimeError(
                    f"active runtime distribution has no RECORD: {_runtime_dist_info.name}"
                )
            with _runtime_record.open(newline="", encoding="utf-8") as _runtime_handle:
                _runtime_rows = list(csv.reader(_runtime_handle))
            if not _runtime_rows:
                raise RuntimeError(
                    f"active runtime distribution has empty RECORD: {_runtime_dist_info.name}"
                )
            for _runtime_row in _runtime_rows:
                if not _runtime_row or not _runtime_row[0]:
                    raise RuntimeError(
                        f"active runtime has malformed RECORD: {_runtime_dist_info.name}"
                    )
                _runtime_file_lexical = Path(
                    os.path.abspath(os.path.normpath(_runtime_slot / _runtime_row[0]))
                )
                if not _runtime_file_lexical.is_relative_to(_runtime_slot_root):
                    continue
                _runtime_file = _runtime_file_lexical.resolve(strict=False)
                if not _runtime_file.is_relative_to(_runtime_slot_root):
                    raise RuntimeError(
                        f"active runtime symlink escapes slot: {_runtime_row[0]}"
                    )
                if not _runtime_file.is_file():
                    raise RuntimeError(
                        f"active runtime file is missing: {_runtime_row[0]}"
                    )
                if len(_runtime_row) >= 3 and _runtime_row[2]:
                    if _runtime_file.stat().st_size != int(_runtime_row[2]):
                        raise RuntimeError(
                            f"active runtime file size mismatch: {_runtime_row[0]}"
                        )
                if len(_runtime_row) >= 2 and _runtime_row[1]:
                    _runtime_algorithm, _runtime_expected_digest = _runtime_row[1].split(
                        "=", 1
                    )
                    if _runtime_algorithm != "sha256":
                        raise RuntimeError(
                            f"unsupported runtime RECORD digest: {_runtime_algorithm}"
                        )
                    _runtime_digest = _runtime_hashlib.sha256()
                    with _runtime_file.open("rb") as _runtime_file_handle:
                        while True:
                            _runtime_chunk = _runtime_file_handle.read(1024 * 1024)
                            if not _runtime_chunk:
                                break
                            _runtime_digest.update(_runtime_chunk)
                    _runtime_actual_digest = base64.urlsafe_b64encode(
                        _runtime_digest.digest()
                    ).rstrip(b"=").decode()
                    if _runtime_actual_digest != _runtime_expected_digest.rstrip("="):
                        raise RuntimeError(
                            f"active runtime file digest mismatch: {_runtime_row[0]}"
                        )
        _runtime_slot_text = str(_runtime_slot)
        while _runtime_slot_text in sys.path:
            sys.path.remove(_runtime_slot_text)
        # Production precedence: frozen/core packages win; managed heavy
        # packages deliberately absent from the bundle resolve from this slot.
        sys.path.append(_runtime_slot_text)
        print(
            f"[runtime_hook] Activated verified media runtime "
            f"{_runtime_slot_name} ({_runtime_required[:12]})",
            file=sys.stderr,
        )
except Exception as _runtime_exc:
    print(
        f"[runtime_hook] Managed media runtime withheld: {_runtime_exc!r}",
        file=sys.stderr,
    )

# ── Transcription / capability packages (user-installed on demand) ────────────
#
# openai-whisper + torch are NOT bundled. When the user installs Speech
# Transcription from Settings → Capabilities, packages land in
# ~/.matrx/transcription-packages/ (or Windows equivalent). Inject here so
# `import whisper` works without a restart after the next engine boot.
try:
    _cap_dir_candidates = []
    if os.getenv("MATRX_FROZEN_RUNTIME_VERIFY") == "1":
        pass
    elif sys.platform == "win32":
        _local_app = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        _cap_dir_candidates.append(
            Path(_local_app) / "AI Matrx" / "transcription-packages"
        )
    else:
        _cap_dir_candidates.append(Path.home() / ".matrx" / "transcription-packages")

    for _cap_dir in _cap_dir_candidates:
        if (_cap_dir / ".install-complete").exists():
            _cap_str = str(_cap_dir)
            if _cap_str not in sys.path:
                # Same isolation contract as image generation: optional heavy
                # packages fill gaps without shadowing the frozen core runtime.
                sys.path.append(_cap_str)
            break
except Exception:
    pass  # Never crash on path injection failure
