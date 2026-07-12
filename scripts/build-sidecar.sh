#!/usr/bin/env bash
#
# Build the Python/FastAPI engine as a standalone sidecar binary.
#
# This uses PyInstaller to create a single-file executable that Tauri
# can spawn as a managed child process. The binary is placed in
# desktop/src-tauri/sidecar/ with platform-specific naming.
#
# Usage:
#   ./scripts/build-sidecar.sh
#   ./scripts/build-sidecar.sh --target x86_64-apple-darwin
#
# Prerequisites:
#   pip install pyinstaller playwright
#   playwright install  (or run: playwright install chromium firefox webkit)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SIDECAR_DIR="$PROJECT_ROOT/desktop/src-tauri/sidecar"

# ── Preflight checks ─────────────────────────────────────────────────────────
PREFLIGHT_OK=true

if ! command -v uv &>/dev/null; then
    echo "ERROR: uv is not installed." >&2
    echo "       Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    PREFLIGHT_OK=false
fi

if [[ ! -f "$PROJECT_ROOT/pyproject.toml" ]]; then
    echo "ERROR: pyproject.toml not found at $PROJECT_ROOT" >&2
    echo "       Run this script from the project root." >&2
    PREFLIGHT_OK=false
fi

if [[ ! -f "$PROJECT_ROOT/run.py" ]]; then
    echo "ERROR: run.py not found at $PROJECT_ROOT" >&2
    echo "       Run this script from the project root." >&2
    PREFLIGHT_OK=false
fi

if ! command -v curl &>/dev/null; then
    echo "ERROR: curl is not installed — needed for downloading dependencies." >&2
    PREFLIGHT_OK=false
fi

if ! $PREFLIGHT_OK; then
    echo ""
    echo "Fix the issues above and re-run: bash scripts/build-sidecar.sh"
    exit 1
fi

# Detect platform triple
detect_target() {
    local os arch
    os="$(uname -s)"
    arch="$(uname -m)"

    case "$os" in
        Linux)
            case "$arch" in
                x86_64)  echo "x86_64-unknown-linux-gnu" ;;
                aarch64) echo "aarch64-unknown-linux-gnu" ;;
                *)       echo "unknown-linux" ;;
            esac
            ;;
        Darwin)
            case "$arch" in
                x86_64)  echo "x86_64-apple-darwin" ;;
                arm64)   echo "aarch64-apple-darwin" ;;
                *)       echo "unknown-darwin" ;;
            esac
            ;;
        MINGW*|MSYS*|CYGWIN*)
            echo "x86_64-pc-windows-msvc"
            ;;
        *)
            echo "unknown-platform"
            ;;
    esac
}

# Parse arguments — allow CI to override the auto-detected target triple
OVERRIDE_TARGET=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) OVERRIDE_TARGET="$2"; shift 2 ;;
        *)        shift ;;
    esac
done

TARGET="${OVERRIDE_TARGET:-$(detect_target)}"
BINARY_NAME="matrx-engine-$TARGET"

# On Windows, add .exe extension
case "$TARGET" in
    *windows*) BINARY_NAME="$BINARY_NAME.exe" ;;
esac

# On macOS, the spec files produce a Helper app bundle (Matrx Engine.app)
# rather than a flat binary, so it can show in Activity Monitor with its own
# name and icon. The flat binary `Matrx Engine` lives inside the bundle at
# Contents/MacOS/Matrx Engine; tauri-bundler picks the whole .app up via
# bundle.macOS.files and embeds it under Contents/Frameworks/.
IS_MACOS=false
case "$TARGET" in
    *apple-darwin*) IS_MACOS=true ;;
esac
HELPER_APP_NAME="Matrx Engine.app"

echo "=== Building AI Matrx Engine Sidecar ==="
echo "Target: $TARGET"
if $IS_MACOS; then
    echo "Output: $SIDECAR_DIR/$HELPER_APP_NAME (Helper app bundle)"
else
    echo "Output: $SIDECAR_DIR/$BINARY_NAME"
fi
echo ""

# Create output directory
mkdir -p "$SIDECAR_DIR"

# Build with PyInstaller
cd "$PROJECT_ROOT"

# Resolve the venv Python — always use it directly so we control the env.
# (uv run would re-sync before running, which reinstalls the bogus `fitz`
# package pulled in by matrx-utils, which drags in pathlib — incompatible
# with PyInstaller on Python 3.13+.)
# Detect venv Python — differs between Unix (.venv/bin/python) and
# Windows/MSYS (.venv/Scripts/python.exe).
detect_venv_python() {
    if [[ -f "$PROJECT_ROOT/.venv/bin/python" ]]; then
        echo "$PROJECT_ROOT/.venv/bin/python"
    elif [[ -f "$PROJECT_ROOT/.venv/Scripts/python.exe" ]]; then
        echo "$PROJECT_ROOT/.venv/Scripts/python.exe"
    else
        echo ""
    fi
}

PYTHON="$(detect_venv_python)"
if [[ -z "$PYTHON" ]]; then
    if command -v uv &>/dev/null; then
        echo "  → .venv not found — running 'uv sync --extra transcription --no-cache' first..."
        uv sync --extra transcription --no-cache || {
            echo "  ⚠ uv sync failed — retrying in 30s (PyPI CDN propagation delay)..."
            sleep 30
            uv sync --extra transcription --no-cache
        }
    else
        echo "ERROR: .venv not found. Run 'uv sync' first."
        exit 1
    fi
    PYTHON="$(detect_venv_python)"
    if [[ -z "$PYTHON" ]]; then
        echo "ERROR: .venv Python not found after uv sync."
        exit 1
    fi
fi

# Remove the obsolete 'pathlib' backport that matrx-utils pulls in via fitz →
# nipype → pyxnat. Python 3.13 ships pathlib as stdlib; the backport breaks
# PyInstaller. Safe to remove: nothing in this project actually imports it.
if "$PYTHON" -c "import importlib.metadata; importlib.metadata.version('pathlib')" &>/dev/null 2>&1; then
    echo "  → Removing incompatible 'pathlib' backport (replaced by stdlib)..."
    "$PYTHON" -m pip uninstall pathlib -y --quiet
fi

PYTHON_CMD="$PYTHON"

# ---------------------------------------------------------------------------
# Step 2: Gather tessdata (Tesseract language data files)
# ---------------------------------------------------------------------------
echo ""
echo "=== Gathering Tesseract Data ==="
TESSDATA_ARG=""
TESSDATA_PATH="$("$PYTHON_CMD" -c "
import subprocess, sys
try:
    import pytesseract
    prefix = pytesseract.get_tessdata_prefix()
    print(prefix.rstrip('/'))
except Exception:
    # Try common system paths
    import os, platform
    candidates = []
    if platform.system() == 'Linux':
        candidates = ['/usr/share/tesseract-ocr/5/tessdata', '/usr/share/tesseract-ocr/4.00/tessdata', '/usr/share/tessdata']
    elif platform.system() == 'Darwin':
        candidates = ['/usr/local/share/tessdata', '/opt/homebrew/share/tessdata']
    for c in candidates:
        if os.path.isdir(c):
            print(c)
            break
" 2>/dev/null || echo "")"

if [[ -n "$TESSDATA_PATH" && -d "$TESSDATA_PATH" ]]; then
    echo "  → Bundling tessdata from: $TESSDATA_PATH"
    TESSDATA_ARG="--add-data \"$TESSDATA_PATH:tessdata\""
else
    echo "  → Tesseract data not found locally — OCR will require system Tesseract"
fi

# ---------------------------------------------------------------------------
# Step 3: Locate imageio-ffmpeg binary for PATH injection
# ---------------------------------------------------------------------------
FFMPEG_DIR="$("$PYTHON_CMD" -c "
import imageio_ffmpeg, os
ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
print(os.path.dirname(ffmpeg_exe))
" 2>/dev/null || echo "")"

if [[ -n "$FFMPEG_DIR" ]]; then
    echo "=== imageio-ffmpeg binary found: $FFMPEG_DIR ==="
fi

# ---------------------------------------------------------------------------
# Step 4: Run PyInstaller
# ---------------------------------------------------------------------------
echo ""
echo "Running PyInstaller (using $PYTHON_CMD)..."
echo "  → Playwright browsers will be auto-installed at runtime (not bundled)"

# ── Choose build method: spec file (macOS) or inline flags (Linux/Windows) ──
#
# The spec file (matrx-engine-aarch64-apple-darwin.spec or *x86_64*) is the
# authoritative build config for macOS. It contains codesign_identity (for
# signing all collected dylibs) and upx=False (UPX corrupts dylibs on macOS).
# We MUST use the spec file on macOS — CLI flags alone can't express these.
#
SPEC_FILE="$PROJECT_ROOT/specs/matrx-engine-$TARGET.spec"

build_with_spec() {
    echo "  → Using spec file: $SPEC_FILE"
    "$PYTHON_CMD" -m PyInstaller \
        --clean \
        --noconfirm \
        "$SPEC_FILE"
}

build_with_flags() {
    # Write the PyInstaller command to a temp file to avoid arg-quoting issues.
    # Note: this fallback path is only used on Windows / Linux (the macOS spec
    # files are required because they contain the BUNDLE() block that produces
    # Matrx Engine.app). The fallback therefore always produces the flat
    # `matrx-engine-<triple>[.exe]` binary that Tauri's externalBin expects.
    local CMD_FILE
    CMD_FILE="$(mktemp)"
cat > "$CMD_FILE" << 'PYINSTALLER_EOF'
import subprocess, sys, os

args = [
    sys.executable, "-m", "PyInstaller",
    "--name", os.environ["BINARY_NAME"],
    "--onefile",
    "--clean",
    "--noconfirm",
    "--runtime-hook", "hooks/runtime_hook.py",
    "--exclude-module", "torch",
    "--exclude-module", "torchvision",
    "--exclude-module", "torchaudio",
    "--exclude-module", "tensorflow",
    "--exclude-module", "tensorboard",
    "--exclude-module", "triton",
    "--exclude-module", "scipy",
    "--exclude-module", "nipype",
    "--exclude-module", "nibabel",
    "--exclude-module", "pyxnat",
    "--exclude-module", "openai_whisper",
    "--exclude-module", "whisper",
    "--exclude-module", "matplotlib",
    "--exclude-module", "sklearn",
    "--exclude-module", "skimage",
    "--exclude-module", "IPython",
    "--exclude-module", "ipykernel",
    "--exclude-module", "jupyter",
    "--exclude-module", "ipywidgets",
    "--hidden-import", "uvicorn",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols",
    "--hidden-import", "uvicorn.protocols.http",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan",
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "httptools",
    "--hidden-import", "python_multipart",
    "--hidden-import", "multipart",
    "--hidden-import", "pydantic",
    "--hidden-import", "fastapi",
    "--hidden-import", "websockets",
    "--hidden-import", "httpx",
    "--hidden-import", "curl_cffi",
    "--hidden-import", "bs4",
    "--hidden-import", "lxml",
    "--hidden-import", "selectolax",
    "--hidden-import", "asyncpg",
    "--hidden-import", "cachetools",
    "--hidden-import", "tldextract",
    "--hidden-import", "markdownify",
    "--hidden-import", "tabulate",
    "--hidden-import", "fitz",
    "--hidden-import", "pytesseract",
    "--hidden-import", "playwright",
    "--hidden-import", "playwright.async_api",
    "--hidden-import", "playwright.sync_api",
    "--hidden-import", "playwright._impl._driver",
    "--hidden-import", "yt_dlp",
    "--hidden-import", "yt_dlp.extractor",
    "--hidden-import", "yt_dlp.downloader",
    "--hidden-import", "yt_dlp.postprocessor",
    "--hidden-import", "yt_dlp.utils",
    "--hidden-import", "imageio_ffmpeg",
    "--hidden-import", "psutil",
    "--hidden-import", "pydantic_settings",
    "--hidden-import", "zeroconf",
    "--hidden-import", "watchfiles",
    "--hidden-import", "sounddevice",
    "--hidden-import", "soundfile",
    "--hidden-import", "pynput",
    "--hidden-import", "kokoro_onnx",
    "--hidden-import", "kokoro_onnx.tokenizer",
    "--hidden-import", "kokoro_onnx.config",
    "--hidden-import", "kokoro_onnx.trim",
    "--hidden-import", "phonemizer",
    "--hidden-import", "phonemizer.backend",
    "--hidden-import", "phonemizer.backend.espeak",
    "--hidden-import", "phonemizer.backend.espeak.wrapper",
    "--hidden-import", "espeakng_loader",
    "--hidden-import", "_soundfile_data",
    "--hidden-import", "language_tags",
    "--hidden-import", "language_tags.tags",
    "--hidden-import", "language_tags.Tag",
    "--hidden-import", "language_tags.Subtag",
    "--collect-data", "espeakng_loader",
    "--collect-data", "_soundfile_data",
    "--collect-data", "kokoro_onnx",
    "--collect-data", "language_tags",
    "--collect-binaries", "espeakng_loader",
    "--collect-binaries", "_soundfile_data",
    "--hidden-import", "app.tools.tools.system",
    "--hidden-import", "app.tools.tools.file_ops",
    "--hidden-import", "app.tools.tools.clipboard",
    "--hidden-import", "app.tools.tools.execution",
    "--hidden-import", "app.tools.tools.network",
    "--hidden-import", "app.tools.tools.notify",
    "--hidden-import", "app.tools.tools.transfer",
    "--hidden-import", "app.tools.tools.process_manager",
    "--hidden-import", "app.tools.tools.window_manager",
    "--hidden-import", "app.tools.tools.input_automation",
    "--hidden-import", "app.tools.tools.audio",
    "--hidden-import", "app.tools.tools.browser_automation",
    "--hidden-import", "app.tools.tools.network_discovery",
    "--hidden-import", "app.tools.tools.system_monitor",
    "--hidden-import", "app.tools.tools.file_watch",
    "--hidden-import", "app.tools.tools.app_integration",
    "--hidden-import", "app.tools.tools.scheduler",
    "--hidden-import", "app.tools.tools.media",
    "--hidden-import", "app.tools.tools.wifi_bluetooth",
    "--hidden-import", "pydantic_settings",
    # Lazily/dynamically imported — PyInstaller's static analysis misses these
    # (supabase + onnxruntime are imported inside functions; openwakeword loads
    # its ONNX backend dynamically). Omitting them causes ModuleNotFoundError
    # in the frozen sidecar only (works fine from source).
    "--hidden-import", "supabase",
    "--hidden-import", "openwakeword",
    "--hidden-import", "onnxruntime",
    # matrx-ai resolves matrx_ai.db.* lazily (PEP 562 __getattr__), so static
    # analysis sees none of it. A missing matrx_ai.db._registry killed the AI
    # stack in every packaged build — collect the whole package.
    "--collect-submodules", "matrx_ai",
    # Cross-component scheduler host (optional extra, lazily imported, gated off
    # by default) — bundle it so flipping MATRX_LOCAL_SCHEDULER_ENABLED works.
    "--hidden-import", "matrx_scheduler",
    # OS keychain (credential encryption at rest) — backends load dynamically.
    "--hidden-import", "keyring",
    "--hidden-import", "keyring.backends",
    "--hidden-import", "keyring.backends.chainer",
    "--hidden-import", "keyring.backends.fail",
    "--hidden-import", "cryptography",
    "--hidden-import", "cryptography.fernet",
    # stdlib modules missed by PyInstaller auto-analysis; required by
    # user-installed image-gen packages. transformers imports filecmp/doctest;
    # torchvision/__init__ does "from modulefinder import Module"; torch>=2.11
    # _strobelight does "from timeit import default_timer" at import. These fail
    # with ModuleNotFoundError at model-load time — verified by loading
    # FLUX.2-klein-4B in the frozen engine (see docs/official/build-lessons.md).
    "--hidden-import", "filecmp",
    "--hidden-import", "doctest",
    "--hidden-import", "modulefinder",
    "--hidden-import", "timeit",
    "--hidden-import", "runpy",
    "--hidden-import", "pdb",
    "--hidden-import", "cProfile",
    "--hidden-import", "pstats",
    "--hidden-import", "pickletools",
    "--hidden-import", "pkgutil",
    "--hidden-import", "linecache",
    "--hidden-import", "selectors",
    "--hidden-import", "faulthandler",
    "--add-data", "app:app",
    "--add-data", "scraper-service/app:scraper-service/app",
]

tessdata = os.environ.get("TESSDATA_PATH_ARG", "")
if tessdata:
    args += ["--add-data", tessdata]

args.append("run.py")

print("Running:", " ".join(args))
result = subprocess.run(args)
sys.exit(result.returncode)
PYINSTALLER_EOF
    "$PYTHON_CMD" "$CMD_FILE"
    local rc=$?
    rm -f "$CMD_FILE"
    return $rc
}

# Set env vars for the Python script
export BINARY_NAME="matrx-engine-$TARGET"
if [[ -n "${TESSDATA_PATH:-}" && -d "$TESSDATA_PATH" ]]; then
    export TESSDATA_PATH_ARG="$TESSDATA_PATH:tessdata"
fi

# Bake the engine version (for Matrx Engine.app's Info.plist on macOS). The
# spec file reads MATRX_ENGINE_VERSION; we resolve it from pyproject.toml so
# the helper bundle's CFBundleShortVersionString tracks the parent app
# automatically. The fallback "1.0.0" is a defensive default — should never
# be hit because pyproject.toml is committed to the repo.
if [[ -f "$PROJECT_ROOT/pyproject.toml" ]]; then
    MATRX_ENGINE_VERSION=$(
        "$PYTHON_CMD" -c "
import re, sys, pathlib
text = pathlib.Path('$PROJECT_ROOT/pyproject.toml').read_text()
m = re.search(r'^version\s*=\s*\"([^\"]+)\"', text, re.MULTILINE)
print(m.group(1) if m else '1.0.0')
" 2>/dev/null || echo "1.0.0"
    )
    export MATRX_ENGINE_VERSION
    echo "  → Engine version (for Helper app Info.plist): $MATRX_ENGINE_VERSION"
fi

# ---------------------------------------------------------------------------
# Step 3b: Bake API URLs and publishable keys into app/bundled_config.py
# ---------------------------------------------------------------------------
# Required env vars (must be set in CI or local environment):
#   AIDREAM_SERVER_URL_LIVE, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY
echo ""
echo "=== Writing bundled config ==="
"$PYTHON_CMD" "$PROJECT_ROOT/scripts/write_bundled_config.py" || {
    echo "WARNING: write_bundled_config.py failed — API URLs may not be available in the binary."
    echo "         Set AIDREAM_SERVER_URL_LIVE, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY in your environment."
}

# ── macOS code signing: re-sign Python dylibs AT SOURCE before PyInstaller ──
#
# ROOT CAUSE FIX: codesign --deep on a PyInstaller --onefile binary cannot
# reach inside the compressed archive to re-sign embedded dylibs. The dylibs
# are compressed data inside the EXE, not separate files. When they are
# extracted to /tmp at runtime, they still carry Python.org's original team ID.
#
# The ONLY correct fix: re-sign the dylibs in the Python installation BEFORE
# PyInstaller collects and packs them. PyInstaller then bakes in the already-
# correctly-signed copy, so macOS sees our Team ID when it's extracted.
#
ENTITLEMENTS_FILE="$PROJECT_ROOT/desktop/src-tauri/sidecar/sidecar.entitlements.plist"
if [[ -n "${APPLE_SIGNING_IDENTITY:-}" && "$(uname -s)" == "Darwin" ]]; then
    echo ""
    echo "=== Pre-Build: Re-signing Python dylibs at source ==="
    echo "  Identity: $APPLE_SIGNING_IDENTITY"

    # Find the Python prefix (base install dir, not the venv)
    PYTHON_PREFIX="$("$PYTHON_CMD" -c 'import sys; print(sys.base_prefix)')"
    echo "  Python prefix: $PYTHON_PREFIX"

    # Build codesign base args — plain identity signing, no --timestamp or
    # --options runtime (those are for executables, not shared libraries).
    # The Team ID from --sign is the only thing macOS checks for dlopen().
    SIGN_BASE=(codesign --force --sign "$APPLE_SIGNING_IDENTITY")
    # No entitlements on dylibs — entitlements are only meaningful on process
    # executables. The outer EXE gets entitlements via SIDECAR_ENTITLEMENTS
    # (consumed by codesign_identity in the spec file).

    # Re-sign every .dylib and .so in the Python installation.
    # This ensures libpython3.13.dylib and any extension modules packed by
    # PyInstaller carry our Team ID before they are added to the archive.
    SIGNED_COUNT=0
    while IFS= read -r -d '' dylib; do
        "${SIGN_BASE[@]}" "$dylib" 2>/dev/null && (( SIGNED_COUNT++ )) || true
    done < <(find "$PYTHON_PREFIX" \( -name "*.dylib" -o -name "*.so" \) -print0 2>/dev/null)

    # Also sign dylibs in the venv itself (compiled extensions installed by uv/pip)
    VENV_DIR="$PROJECT_ROOT/.venv"
    if [[ -d "$VENV_DIR" ]]; then
        while IFS= read -r -d '' dylib; do
            "${SIGN_BASE[@]}" "$dylib" 2>/dev/null && (( SIGNED_COUNT++ )) || true
        done < <(find "$VENV_DIR" \( -name "*.dylib" -o -name "*.so" \) -print0 2>/dev/null)
    fi

    echo "  ✅ Re-signed $SIGNED_COUNT dylib/so files"
    echo ""

    # Export so the .spec file can also pass codesign_identity to PyInstaller
    # (this signs the outer EXE, which is correct)
    export APPLE_SIGNING_IDENTITY
    [[ -f "$ENTITLEMENTS_FILE" ]] && export SIDECAR_ENTITLEMENTS="$ENTITLEMENTS_FILE"
else
    echo "  → Pre-build signing: skipped (not macOS or APPLE_SIGNING_IDENTITY not set)"
fi

# Run PyInstaller — prefer spec file (contains codesign_identity + upx=False),
# fall back to inline flags for platforms without a spec file.
if [[ -f "$SPEC_FILE" ]]; then
    build_with_spec
else
    echo "  → No spec file found at $SPEC_FILE — using inline flags"
    build_with_flags
fi

# ── Post-build: verify signatures (macOS only) ─────────────────────────────
#
# On macOS, PyInstaller's BUNDLE() produces dist/Matrx Engine.app, with the
# inner Mach-O at Contents/MacOS/Matrx Engine signed by PyInstaller's
# codesign_identity step. We verify the inner binary here; the outer .app
# bundle's CodeResources will be created by tauri-bundler when it copies the
# helper into the parent app via bundle.macOS.files (Tauri's nested-code
# auto-codesign feature, PR #8259).
#
# On Windows/Linux, codesign is a no-op — we rely on the OS-native signing
# performed by tauri-bundler / signtool / Authenticode when the parent app is
# bundled.
if [[ "$(uname -s)" == "Darwin" ]]; then
    HELPER_APP_PATH="dist/$HELPER_APP_NAME"
    HELPER_INNER_BIN="$HELPER_APP_PATH/Contents/MacOS/Matrx Engine"
    if [[ -n "${APPLE_SIGNING_IDENTITY:-}" && -f "$HELPER_INNER_BIN" ]]; then
        echo ""
        echo "=== Post-Build: Verifying Helper app inner binary signature ==="
        codesign --verify --verbose "$HELPER_INNER_BIN"
        echo "  ✅ Inner binary signature valid"
    fi
fi

# ── Copy build output into the Tauri sidecar/ directory ─────────────────────
#
# macOS  : copy the whole Matrx Engine.app — tauri-bundler picks it up via
#          bundle.macOS.files and embeds it at Contents/Frameworks/.
# Win/Lin: copy the flat matrx-engine-<triple>[.exe] — tauri-bundler picks
#          it up via bundle.externalBin and embeds it at Contents/MacOS/
#          (or the platform equivalent).
echo "Copying build output to sidecar directory..."
if $IS_MACOS; then
    SRC_APP="dist/$HELPER_APP_NAME"
    DEST_APP="$SIDECAR_DIR/$HELPER_APP_NAME"
    if [[ ! -d "$SRC_APP" ]]; then
        echo "ERROR: PyInstaller did not produce $SRC_APP — check the spec file."
        exit 1
    fi
    rm -rf "$DEST_APP"
    # ditto preserves Mach-O metadata, code-signing attributes, symlinks, and
    # extended attributes (xattr). cp -R loses xattrs which can corrupt the
    # signature on already-signed files inside the bundle.
    /usr/bin/ditto "$SRC_APP" "$DEST_APP"
    echo ""
    echo "=== Build Complete ==="
    echo "Helper app: $DEST_APP"
    du -sh "$DEST_APP"
else
    cp "dist/matrx-engine-$TARGET"* "$SIDECAR_DIR/"
    echo ""
    echo "=== Build Complete ==="
    echo "Binary: $SIDECAR_DIR/$BINARY_NAME"
    ls -lh "$SIDECAR_DIR/$BINARY_NAME"
fi

DESKTOP_DIR="$(dirname "$SIDECAR_DIR")"
TAURI_DEV_CMD="cd $PROJECT_ROOT/desktop && npm run tauri:dev"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Next Steps"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Option 1 — Test the sidecar binary standalone:"
echo ""
if $IS_MACOS; then
    echo "    \"$SIDECAR_DIR/$HELPER_APP_NAME/Contents/MacOS/Matrx Engine\""
else
    echo "    $SIDECAR_DIR/$BINARY_NAME"
fi
echo ""
echo "    Then verify it's running:"
echo "    curl http://localhost:22140/tools/list"
echo ""
echo "  Option 2 — Launch the full Tauri desktop app:"
echo "    (Tauri will spawn the sidecar automatically)"
echo ""
echo "    $TAURI_DEV_CMD"
echo ""
echo "  Option 3 — Run the Python engine directly (dev mode, no binary):"
echo ""
echo "    cd $PROJECT_ROOT && uv run python run.py"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
