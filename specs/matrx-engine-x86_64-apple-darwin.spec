# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
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
_matrx_ai_mods = collect_submodules('matrx_ai')


a = Analysis(
    [os.path.join(_ROOT, 'run.py')],
    pathex=[_ROOT],
    binaries=_espeakng_libs + _soundfile_libs,
    datas=[
        (os.path.join(_ROOT, 'app'), 'app'),
        (os.path.join(_ROOT, 'scraper-service/app'), 'scraper-service/app'),
        (os.path.join(_ROOT, 'pyproject.toml'), '.'),
    ] + _espeakng_data + _soundfile_data + _kokoro_data + _lang_tags_data,
    hiddenimports=_matrx_ai_mods + [
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'httptools', 'python_multipart', 'multipart',
        'pydantic', 'fastapi', 'websockets', 'httpx',
        'curl_cffi', 'bs4', 'lxml', 'selectolax', 'asyncpg', 'cachetools',
        'tldextract', 'markdownify', 'tabulate', 'fitz', 'pytesseract',
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

# See matrx-engine-aarch64-apple-darwin.spec for the full rationale behind
# the EXE name + BUNDLE block. The two macOS specs are intentionally identical
# in structure — only the architecture-specific build inputs differ (which
# PyInstaller picks up automatically from the Python interpreter being used
# by build-sidecar.sh under each arch's runner).
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
        'LSUIElement': True,
        'LSBackgroundOnly': True,
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
