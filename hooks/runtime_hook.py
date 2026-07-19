"""PyInstaller runtime hook — set environment variables for bundled tools.

This runs before any application code when the frozen binary starts.
It injects compile-time Supabase bootstrap config and then
points Playwright, Tesseract, and ffmpeg to their bundled/user locations
inside sys._MEIPASS (the PyInstaller extraction directory) or the user's home.
"""

import os
import sys
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

# A new managed media runtime is an immutable, versioned slot. Activate it only
# when the durable state and slot manifest match the exact contract embedded in
# this application. This executes before app imports, so it intentionally stays
# self-contained instead of importing installer.py.
try:
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
            "machine": _runtime_platform.machine().lower(),
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

# ── Image generation packages (user-installed on demand) ──────────────────────
#
# torch + diffusers are NOT bundled in the frozen binary (they are ~1 GB+).
# When the user installs image generation from inside the app, the packages
# land in a dedicated user-writable directory.  Inject that directory into
# sys.path here so subsequent `import torch` / `import diffusers` work
# without any restart.
#
# This runs before application imports. Only inject the exact runtime this app
# release supports: an older Diffusers directory would otherwise be imported by
# ``app.main`` before its mandatory migration can repair it. In particular,
# 0.37.x crashes loading valid Civitai/AI-Toolkit Z-Image LoRAs without
# per-layer alpha tensors. The main startup migration upgrades a withheld old
# runtime, verifies it, then injects it in the same process.
try:
    _ig_dir_candidates = []
    if sys.platform == "win32":
        _local_app = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        _ig_dir_candidates.append(Path(_local_app) / "AI Matrx" / "image-gen-packages")
    else:
        _ig_dir_candidates.append(Path.home() / ".matrx" / "image-gen-packages")

    for _ig_dir in _ig_dir_candidates:
        _complete = (_ig_dir / ".install-complete").exists()
        _migration_pending = (_ig_dir / ".compatibility-upgrade-pending").exists()
        _required_diffusers = (_ig_dir / "diffusers-0.39.0.dist-info").exists()
        if False and _complete and not _migration_pending and _required_diffusers:
            # Purge any protobuf copy older installs left here BEFORE the dir
            # enters sys.path. Older releases prepended this directory, so its
            # protobuf shadowed the engine's copy (xai-sdk rejects protobuf 7)
            # and killed matrx-ai init on every packaged boot
            # ("Unsupported protobuf version: 7.34.1", 2026-07-12). Mirrors
            # installer.py _purge_shadowing_protobuf — kept inline here
            # because this hook must not import app code.
            #
            # Keep purging during the transition so previously corrupted
            # runtimes are repaired on disk as well as isolated by precedence.
            _purge_failed = False
            try:
                import shutil as _shutil

                _victims = list(_ig_dir.glob("protobuf-*.dist-info"))
                _victims += [
                    _p for _p in (_ig_dir / "google" / "protobuf", _ig_dir / "google" / "_upb")
                    if _p.exists()
                ]
                for _v in _victims:
                    try:
                        _shutil.rmtree(_v) if _v.is_dir() else _v.unlink()
                        print(
                            f"[runtime_hook] PURGED stale protobuf artifact {_v} "
                            "(it shadows the engine's protobuf and breaks matrx-ai init)",
                            file=sys.stderr,
                        )
                    except Exception as _purge_exc:
                        _purge_failed = True
                        print(
                            f"[runtime_hook] FAILED to purge protobuf artifact {_v}: "
                            f"{_purge_exc!r}",
                            file=sys.stderr,
                        )
                _g = _ig_dir / "google"
                if _g.is_dir() and not any(_g.iterdir()):
                    _g.rmdir()
            except Exception as _scan_exc:
                _purge_failed = True
                print(
                    f"[runtime_hook] protobuf purge scan failed: {_scan_exc!r}",
                    file=sys.stderr,
                )

            if _purge_failed:
                print(
                    f"[runtime_hook] NOT injecting {_ig_dir} into sys.path — a "
                    "protobuf copy survived the purge and would shadow the "
                    "engine's own (matrx-ai init would fail with 'Unsupported "
                    "protobuf version'). Image/video generation is unavailable "
                    "this session; restart the app (with no old engine running) "
                    "to retry the purge.",
                    file=sys.stderr,
                )
                break

            _ig_str = str(_ig_dir)
            if _ig_str not in sys.path:
                # Managed packages are fallbacks. pip --target also installs
                # transitive copies of core packages such as anyio/httpx; those
                # must never replace the versions frozen with FastAPI/Starlette.
                sys.path.append(_ig_str)

            # Patch transformers/dynamic_module_utils.py to guard against `import filecmp`.
            # filecmp is a stdlib module that PyInstaller may not bundle when it doesn't
            # appear in the engine's own import graph.  transformers imports it unconditionally
            # at the top level, causing an ImportError during _check_deps() in service.py
            # even though the packages installed correctly.  The patch is idempotent.
            try:
                _dmu = _ig_dir / "transformers" / "dynamic_module_utils.py"
                if _dmu.exists():
                    _src = _dmu.read_text(encoding="utf-8")
                    if "import filecmp" in _src and "_files_equal" not in _src:
                        _old = "import filecmp"
                        _new = (
                            "try:\n"
                            "    import filecmp as _filecmp_mod\n"
                            "    def _files_equal(a, b):\n"
                            "        return _filecmp_mod.cmp(a, b)\n"
                            "except ModuleNotFoundError:\n"
                            "    def _files_equal(a, b):\n"
                            "        return False"
                        )
                        _patched = _src.replace(_old, _new, 1)
                        _patched = _patched.replace(
                            "filecmp.cmp(", "_files_equal(", 100
                        )
                        _dmu.write_text(_patched, encoding="utf-8")
                        # Remove stale .pyc
                        _pyc_dir = _dmu.parent / "__pycache__"
                        if _pyc_dir.exists():
                            for _pyc in _pyc_dir.glob("dynamic_module_utils*.pyc"):
                                try:
                                    _pyc.unlink()
                                except OSError:
                                    pass
            except Exception:
                pass  # patch failure is non-fatal; import will fail naturally if filecmp is missing

            break
        elif _complete or _migration_pending:
            print(
                "[runtime_hook] Withholding incompatible image-gen runtime; "
                "startup will run the mandatory compatibility migration",
                file=sys.stderr,
            )
            break
except Exception:
    pass  # Never crash on path injection failure

# ── Transcription / capability packages (user-installed on demand) ────────────
#
# openai-whisper + torch are NOT bundled. When the user installs Speech
# Transcription from Settings → Capabilities, packages land in
# ~/.matrx/transcription-packages/ (or Windows equivalent). Inject here so
# `import whisper` works without a restart after the next engine boot.
try:
    _cap_dir_candidates = []
    if sys.platform == "win32":
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
