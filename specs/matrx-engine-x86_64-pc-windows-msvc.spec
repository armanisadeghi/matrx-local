# -*- mode: python ; coding: utf-8 -*-
#
# Windows PyInstaller spec for matrx-engine (the Matrx Engine sidecar).
#
# Key Windows-specific differences from the macOS/Linux specs:
#
#   runtime_tmpdir: set to a FIXED path under %LOCALAPPDATA% instead of None.
#
#   Why: PyInstaller --onefile on Windows extracts all bundled files into a
#   temp directory at launch (_MEIxxxxxx in %TEMP% when runtime_tmpdir=None).
#   Windows Restart Manager tracks which installer-registered files are in use.
#   Even after matrx-engine.exe exits, the extraction dir and its handles may
#   linger in the Restart Manager registry, causing NSIS to report:
#     "Error opening file for writing: matrx-engine.exe"
#   even when no process is running.
#
#   By using a fixed, known path (AI Matrx\engine-runtime), we can:
#     1. Delete that directory explicitly in the NSIS pre-install hook before
#        copying any files, clearing all stale Restart Manager registrations.
#     2. Reuse the extracted files across app restarts (faster cold start).
#
#   upx=True: UPX compression is safe on Windows (unlike macOS where it
#   corrupts dylibs before code signing). Reduces binary size significantly.

import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

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


a = Analysis(
    [os.path.join(_ROOT, 'run.py')],
    pathex=[_ROOT],
    binaries=_espeakng_libs + _soundfile_libs,
    datas=[
        (os.path.join(_ROOT, 'app'), 'app'),
        (os.path.join(_ROOT, 'scraper-service/app'), 'scraper-service/app'),
        (os.path.join(_ROOT, 'pyproject.toml'), '.'),
    ] + _espeakng_data + _soundfile_data + _kokoro_data + _lang_tags_data,
    hiddenimports=[
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
        'keyring.backends.Windows',
        'cryptography', 'cryptography.fernet',
        # torch/torchvision/transformers lazily import stdlib modules the engine's
        # own import graph never references, so PyInstaller omits them and the
        # user-installed packages raise ModuleNotFoundError at load time. Verified
        # by booting the frozen engine and loading FLUX.2-klein-4B (see LESSONS.md).
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
    excludes=[
        'torch', 'torchvision', 'torchaudio', 'tensorflow', 'tensorboard',
        'triton', 'scipy', 'nipype', 'nibabel', 'pyxnat', 'openai_whisper',
        'whisper', 'matplotlib', 'sklearn', 'skimage',
        'IPython', 'ipykernel', 'jupyter', 'ipywidgets',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='matrx-engine-x86_64-pc-windows-msvc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    # Fixed extraction directory under %LOCALAPPDATA%\AI Matrx\engine-runtime.
    # This replaces the random _MEIxxxxxx temp folder that Windows Restart Manager
    # holds open even after the process exits, blocking installer file writes.
    # The NSIS pre-install hook deletes this directory before copying new files.
    runtime_tmpdir='%LOCALAPPDATA%\\AI Matrx\\engine-runtime',
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
