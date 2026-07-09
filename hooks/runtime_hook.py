"""PyInstaller runtime hook — set environment variables for bundled tools.

This runs before any application code when the frozen binary starts.
It injects compile-time config (API URLs, publishable keys) and then
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
                sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        else:
            import io
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
    except Exception:
        pass  # If reconfigure fails (e.g. no buffer attr), continue — better than crashing.

# Inject API URLs and publishable keys baked in at build time by CI.
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

# ── Image generation packages (user-installed on demand) ──────────────────────
#
# torch + diffusers are NOT bundled in the frozen binary (they are ~1 GB+).
# When the user installs image generation from inside the app, the packages
# land in a dedicated user-writable directory.  Inject that directory into
# sys.path here so subsequent `import torch` / `import diffusers` work
# without any restart.
#
# This runs on every engine start.  If the directory doesn't exist yet (user
# hasn't installed) it's a no-op — the image-gen service gracefully reports
# "not available" instead.
try:
    _ig_dir_candidates = []
    if sys.platform == "win32":
        _local_app = os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        _ig_dir_candidates.append(Path(_local_app) / "AI Matrx" / "image-gen-packages")
    else:
        _ig_dir_candidates.append(Path.home() / ".matrx" / "image-gen-packages")

    for _ig_dir in _ig_dir_candidates:
        if (_ig_dir / ".install-complete").exists():
            _ig_str = str(_ig_dir)
            if _ig_str not in sys.path:
                sys.path.insert(0, _ig_str)

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
                        _patched = _patched.replace("filecmp.cmp(", "_files_equal(", 100)
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
except Exception:
    pass  # Never crash on path injection failure
