# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# SPECPATH is injected by PyInstaller and equals the directory containing this
# spec file (specs/). All project-relative paths must be resolved from the
# project root, which is one level up.
_ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

# espeak-ng: bundled native library + language dictionaries (required by kokoro-onnx TTS)
_espeakng_data = collect_data_files('espeakng_loader')
_espeakng_libs = collect_dynamic_libs('espeakng_loader')
# soundfile: bundled libsndfile native library
_soundfile_data = collect_data_files('_soundfile_data')
_soundfile_libs = collect_dynamic_libs('_soundfile_data')
# kokoro-onnx: config.json (vocab) must be collected
_kokoro_data = collect_data_files('kokoro_onnx')
# language_tags: JSON registry files required by phonemizer → segments → csvw → language-tags
_lang_tags_data = collect_data_files('language_tags')

# matrx-ai resolves matrx_ai.db.* lazily (PEP 562 __getattr__) and its
# configure() historically loaded matrx_ai/db/_registry.py by file path, so
# PyInstaller's static analysis sees NONE of those submodules. Missing
# matrx_ai.db._registry killed the AI stack in every packaged build; collect
# the whole package so no lazily-imported submodule can go missing again.
# Packages that read their OWN installed metadata at import time, e.g.
# replicate/__about__.py -> importlib.metadata.version("replicate"). PyInstaller
# does not bundle .dist-info metadata unless told to, so these raise
# PackageNotFoundError and kill the engine in the COMPILED sidecar only — dev
# runs are fine, which is exactly what makes it easy to ship.
# (v1.3.105 shipped with `replicate` missing: ai_engine -> state=failed on boot.)
# If you add a dependency whose __init__ calls importlib.metadata.version(),
# add it here. Missing packages are skipped, so an optional extra that is not
# installed on this build machine will not break the build.
# matrx-ai/matrx-utils metadata: app/services/ai/engine.py prints a startup
# banner via importlib.metadata.version(); without .dist-info bundled every
# packaged build lies 'matrx-ai = NOT INSTALLED' while the library works.
_METADATA_PKGS = ['replicate', 'protobuf', 'matrx-ai', 'matrx-utils',
                  'matrx-orm', 'matrx-connect', 'matrx-graph', 'matrx-runtime']
_pkg_metadata = []
for _pkg in _METADATA_PKGS:
    try:
        _pkg_metadata += copy_metadata(_pkg)
    except Exception:
        pass  # optional/absent on this build host — nothing to copy

_matrx_ai_mods = collect_submodules('matrx_ai')

# google.protobuf is a namespace-package member PyInstaller does NOT collect
# from the static import graph (xai-sdk pulls it lazily). When it is missing
# from the frozen bundle, `import google.protobuf` falls through to sys.path —
# where the runtime hook has PREPENDED ~/.matrx/image-gen-packages, whose own
# protobuf (7.x) xai-sdk hard-rejects: "Unsupported protobuf version" killed
# matrx-ai init on every packaged boot of v1.3.107 (2026-07-12).
#
# IMPORTANT — what this bundling does and does NOT do: PyInstaller 6 has no
# meta_path FrozenImporter; frozen imports resolve through a sys.path hook IN
# PATH ORDER, and 'google' is a PEP 420 namespace package, so a protobuf copy
# in the PREPENDED image-gen dir would STILL win over this bundle. The actual
# shadowing defense is the purge in hooks/runtime_hook.py + installer.py
# (the hook refuses to inject the dir if a protobuf copy survives). This
# bundling exists so the frozen app HAS a protobuf at all once the dir is
# clean; copy_metadata keeps importlib.metadata.version('protobuf') truthful.
# google._upb._message is protobuf's C-extension backend (also invisible to
# static analysis; missing-module hiddenimports are warnings, not errors).
_protobuf_mods = collect_submodules('google.protobuf') + ['google._upb._message']

# ── Office (docx/pptx/xlsx) codec — matrx_files.specific_handlers.office ──────
# The canonical Office codec and its renderers (python-docx/pptx/openpyxl/
# xlsxwriter) are ALL lazily imported inside functions (Read of an Office file
# and the OfficeGenerate tool), so PyInstaller's static analysis misses every
# one of them — without this the frozen sidecar raises ModuleNotFoundError the
# moment an Office document is read or generated. collect_data_files also ships
# the python-docx / python-pptx default template files (default.docx /
# default.pptx) that Document()/Presentation() load with no args.
_office_datas = []
_office_hidden = collect_submodules('matrx_files')
for _office_pkg in ('docx', 'pptx', 'openpyxl', 'xlsxwriter', 'et_xmlfile'):
    try:
        _office_datas += collect_data_files(_office_pkg)
        _office_hidden += collect_submodules(_office_pkg)
    except Exception:
        pass



a = Analysis(
    [os.path.join(_ROOT, 'run.py')],
    pathex=[_ROOT],
    binaries=_espeakng_libs + _soundfile_libs,
    datas=[
        (os.path.join(_ROOT, 'app'), 'app'),
        (os.path.join(_ROOT, 'scraper-service/app'), 'scraper-service/app'),
        (os.path.join(_ROOT, 'pyproject.toml'), '.'),
    ] + _espeakng_data + _soundfile_data + _kokoro_data + _lang_tags_data + _pkg_metadata + _office_datas,
    hiddenimports=_matrx_ai_mods + _protobuf_mods + _office_hidden + [
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'httptools', 'python_multipart', 'multipart',
        'pydantic', 'fastapi', 'websockets', 'httpx',
        'curl_cffi', 'bs4', 'lxml', 'selectolax', 'asyncpg', 'cachetools',
        'tldextract', 'markdownify', 'tabulate', 'fitz', 'pytesseract',
        # send2trash picks its platform backend at import time; the
        # unused-platform submodules are conditional imports PyInstaller
        # can miss. Trash-first Delete tool depends on these.
        'send2trash', 'send2trash.mac', 'send2trash.win',
        'send2trash.plat_gio', 'send2trash.plat_other',
        'playwright', 'playwright.async_api', 'playwright.sync_api',
        'playwright._impl._driver',
        'yt_dlp', 'yt_dlp.extractor', 'yt_dlp.downloader',
        'yt_dlp.postprocessor', 'yt_dlp.utils',
        'imageio_ffmpeg', 'psutil', 'pydantic_settings', 'zeroconf',
        'watchfiles', 'sounddevice', 'soundfile', 'pynput',
        'kokoro_onnx', 'kokoro_onnx.tokenizer', 'kokoro_onnx.config', 'kokoro_onnx.trim',
        'phonemizer', 'phonemizer.backend', 'phonemizer.backend.espeak',
        'phonemizer.backend.espeak.wrapper',
        'espeakng_loader', '_soundfile_data',
        'language_tags', 'language_tags.tags', 'language_tags.Tag', 'language_tags.Subtag',
        'app.tools.tools.system', 'app.tools.tools.file_ops',
        'app.tools.tools.clipboard', 'app.tools.tools.execution',
        'app.tools.tools.network', 'app.tools.tools.notify',
        'app.tools.tools.transfer', 'app.tools.tools.process_manager',
        'app.tools.tools.window_manager', 'app.tools.tools.input_automation',
        'app.tools.tools.audio', 'app.tools.tools.browser_automation',
        'app.tools.tools.network_discovery', 'app.tools.tools.system_monitor',
        'app.tools.tools.file_watch', 'app.tools.tools.app_integration',
        'app.tools.tools.scheduler', 'app.tools.tools.media',
        'app.tools.tools.ner',
        'app.tools.tools.wifi_bluetooth',
        # stdlib modules not auto-discovered by PyInstaller but required by
        # user-installed image-gen packages (transformers uses filecmp directly)
        # Dynamically/lazily imported — PyInstaller's static analysis misses these:
        'supabase', 'openwakeword', 'onnxruntime',
        'matrx_scheduler',
        # OS keychain (credential encryption at rest) + crypto backend.
        'keyring', 'keyring.backends', 'keyring.backends.chainer',
        'keyring.backends.fail', 'keyring.backends.null',
        'keyring.backends.macOS',
        'cryptography', 'cryptography.fernet',
        # torch/torchvision/transformers lazily import stdlib modules the engine's
        # own import graph never references, so PyInstaller omits them and the
        # user-installed packages raise ModuleNotFoundError at load time. Verified
        # by booting the frozen engine and loading FLUX.2-klein-4B (see docs/official/build-lessons.md).
        #   filecmp,doctest  <- transformers    modulefinder <- torchvision/__init__
        #   timeit           <- torch _strobelight (torch>=2.11 imports it at import)
        'filecmp', 'doctest', 'modulefinder', 'timeit',
        # torch/torchvision profiling+loader stdlib that can be pulled during import
        # or pipeline load; harmless if unused, fatal if missing:
        'runpy', 'pdb', 'cProfile', 'pstats', 'pickletools',
        'pkgutil', 'linecache', 'selectors', 'faulthandler',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(_ROOT, 'hooks/runtime_hook.py')],
    excludes=['torch', 'torchvision', 'torchaudio', 'tensorflow', 'tensorboard', 'triton', 'scipy', 'nipype', 'nibabel', 'pyxnat', 'openai_whisper', 'whisper', 'matplotlib', 'sklearn', 'skimage', 'IPython', 'ipykernel', 'jupyter', 'ipywidgets'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ── EXE: the inner Mach-O executable.
#
# `name` MUST equal the Helper app bundle's CFBundleExecutable so macOS can
# locate it inside Matrx Engine.app/Contents/MacOS/. We use 'Matrx Engine'
# (with a space) because that is the string Activity Monitor displays.
#
# The flat binary that lands in dist/ at this stage (named 'Matrx Engine')
# is also the file Tauri's externalBin would copy on platforms where we use
# the flat binary instead of the Helper app — but on macOS we wrap it via
# BUNDLE() below and ship that instead.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Matrx Engine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is disabled on macOS: UPX cannot correctly process .dylib shared
    # libraries on macOS, corrupting them before code signing runs. This would
    # cause signed dylibs to be invalid at runtime. On Linux/Windows it is fine.
    upx=sys.platform != 'darwin',
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=os.environ.get('APPLE_SIGNING_IDENTITY', None),
    entitlements_file=os.environ.get('SIDECAR_ENTITLEMENTS', None),
)


# ── BUNDLE: wrap the EXE into Matrx Engine.app (Helper app bundle).
#
# This is the single most important change for fixing macOS Activity Monitor:
# by giving the engine its own .app sub-bundle with its own Info.plist,
# CFBundleName, CFBundleIdentifier, and icon, macOS will display the engine
# process as "Matrx Engine" with its own icon — instead of "AI Matrx" with
# the parent app's icon (which is what happened when the engine binary lived
# directly inside the parent app's Contents/MacOS/).
#
# Bundle layout produced:
#   dist/Matrx Engine.app/
#     Contents/
#       Info.plist                         ← driven by `info_plist` below
#       MacOS/
#         Matrx Engine                     ← the EXE above
#       Resources/
#         engine-icon.icns                 ← from `icon` below
#
# build-sidecar.sh copies this directory into desktop/src-tauri/sidecar/ so
# that tauri-bundler can pick it up via `bundle.macOS.files` and place it
# inside the parent app at Contents/Frameworks/Matrx Engine.app/. Tauri's
# auto-codesign-nested-code feature (PR #8259) then signs the helper as part
# of the normal bundle/sign/notarize flow — no post-build restructure needed.
#
# Bundle identifier choice (com.aimatrx.desktop.engine):
#   - Sub-domain of the parent (com.aimatrx.desktop) so it reads as a
#     component of AI Matrx rather than a separate app.
#   - Distinct enough that macOS gives it its own TCC bucket — meaning users
#     upgrading from a flat-binary build will see permission prompts the
#     first time the engine accesses microphone/screen-capture/full-disk.
#     This is the standard pattern (Chrome Helper, Slack Helper, etc.) and is
#     a one-time event per upgrade.
#
# LSUIElement / LSBackgroundOnly:
#   - The helper must NEVER appear in the Dock or app switcher; it is a
#     pure background process that the parent UI controls.
#
# NS*UsageDescription keys:
#   - These must be present on the helper (not just the parent) because
#     macOS shows the dialog using the binary that triggered the API call.
#     If the helper triggers a microphone request and has no usage string,
#     the request is silently denied. Strings mirror the parent's so the
#     user sees a consistent explanation.
app = BUNDLE(
    exe,
    name='Matrx Engine.app',
    icon=os.path.join(_ROOT, 'desktop/src-tauri/icons/engine-icon.icns'),
    bundle_identifier='com.aimatrx.desktop.engine',
    info_plist={
        'CFBundleName': 'Matrx Engine',
        'CFBundleDisplayName': 'Matrx Engine',
        'CFBundleExecutable': 'Matrx Engine',
        'CFBundleIdentifier': 'com.aimatrx.desktop.engine',
        'CFBundleIconFile': 'engine-icon.icns',
        'CFBundleShortVersionString': os.environ.get('MATRX_ENGINE_VERSION', '1.0.0'),
        'CFBundleVersion': os.environ.get('MATRX_ENGINE_VERSION', '1.0.0'),
        'CFBundlePackageType': 'APPL',
        'CFBundleSignature': '????',
        'LSMinimumSystemVersion': '10.15',
        # Keep the helper out of the Dock / app switcher — it is a background
        # process. Without these the user would see two icons in the Dock.
        'LSUIElement': True,
        'LSBackgroundOnly': True,
        # TCC usage descriptions — see the comment above for why these must
        # live on the helper, not just on the parent.
        'NSMicrophoneUsageDescription':
            'AI Matrx needs microphone access for transcription, voice recording, '
            'and wake-word detection.',
        'NSCameraUsageDescription':
            'AI Matrx needs camera access for image-capture tools and AI workflows.',
        'NSScreenCaptureUsageDescription':
            'AI Matrx needs screen-recording access for the screenshot tool and '
            'AI automation workflows.',
        'NSAppleEventsUsageDescription':
            'AI Matrx needs Apple Events access for system automation tools '
            '(window management, keyboard automation).',
        'NSContactsUsageDescription':
            'AI Matrx needs Contacts access for the address-book integration tool.',
        'NSCalendarsUsageDescription':
            'AI Matrx needs Calendar access for the calendar integration tool.',
        'NSRemindersUsageDescription':
            'AI Matrx needs Reminders access for the reminders integration tool.',
        'NSPhotoLibraryUsageDescription':
            'AI Matrx needs Photos access for image-based AI workflows.',
        'NSBluetoothAlwaysUsageDescription':
            'AI Matrx needs Bluetooth access to discover and interact with nearby '
            'devices.',
    },
)
