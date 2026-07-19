"""Optional capability detection and installation management.

Heavy capabilities (Whisper / torch) install via the same frozen-safe
``--target`` + SSE progress path as image-gen. Lightweight probes remain
sync for status display.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.common.platform_ctx import refresh_package_capabilities
from app.common.system_logger import get_logger
from app.services.capabilities.installer import (
    get_active_progress,
    get_capability_packages_dir,
    get_lightweight_capability_packages_dir,
    inject_capability_path,
    inject_lightweight_capability_path,
    is_capability_installed,
    probe_module_available,
    start_capability_install,
    uses_managed_installer,
)
from app.services.optional_packages.core import find_python

logger = get_logger()
router = APIRouter(prefix="/capabilities", tags=["capabilities"])


# ---------------------------------------------------------------------------
# Capability definitions
# ---------------------------------------------------------------------------

CapabilityStatus = Literal["installed", "not_installed", "checking"]


class Capability(BaseModel):
    id: str
    name: str
    description: str
    status: CapabilityStatus
    packages: list[str]
    install_extra: str | None = None
    size_warning: str | None = None
    docs_url: str | None = None


class CapabilitiesResponse(BaseModel):
    capabilities: list[Capability]


class InstallRequest(BaseModel):
    capability_id: str


class InstallStartResponse(BaseModel):
    """Returned immediately from POST /capabilities/install.

    Heavy installs continue in the background — connect to
    GET /capabilities/install/stream for progress.
    """

    status: Literal["idle", "running", "complete", "error"]
    capability_id: str
    stage: str = ""
    percent: float = 0.0
    message: str = ""
    error: str | None = None
    already_installed: bool = False
    install_dir: str | None = None
    log_lines: list[str] | None = None
    # True when this capability uses the managed SSE installer (vs sync pip).
    async_install: bool = False


CAPABILITY_SPECS: dict[str, dict] = {
    "browser_automation": {
        "name": "Browser Automation",
        "description": "Control a real browser (Chromium, Firefox, or WebKit) to navigate websites, click elements, fill forms, and take screenshots. Powers BrowserNavigate, BrowserClick, BrowserExtract, BrowserScreenshot, and BrowserEval tools.",
        "probe_module": "playwright",
        "packages": ["playwright"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://playwright.dev/python/",
    },
    "audio_recording": {
        "name": "Audio Recording & Playback",
        "description": "Record from microphone and play audio files. Powers the RecordAudio, PlayAudio, and ListAudioDevices tools.",
        "probe_module": "sounddevice",
        "packages": ["sounddevice", "numpy"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://python-sounddevice.readthedocs.io/",
    },
    "transcription": {
        "name": "Speech Transcription (Whisper)",
        "description": "Transcribe audio files to text using OpenAI Whisper running locally. Powers the TranscribeAudio tool. Requires PyTorch — large download.",
        "probe_module": "whisper",
        "packages": ["openai-whisper"],
        "install_extra": "transcription",
        "size_warning": "~400–800 MB download (includes PyTorch)",
        "docs_url": "https://github.com/openai/whisper",
    },
    "ner": {
        "name": "Entity Extraction (GLiNER)",
        "description": "Extract zero-shot named entities and common PII locally using GLiNER / GLiNER2. Powers local_extract_entities and local_extract_pii. Requires PyTorch — large download.",
        "probe_module": "gliner2",
        "packages": ["gliner2[local]", "gliner", "huggingface_hub"],
        "install_extra": "ner",
        "size_warning": "~1–2 GB download before model weights (includes PyTorch)",
        "docs_url": "https://github.com/fastino-ai/GLiNER2",
    },
    "ocr": {
        "name": "OCR (Image Text Extraction)",
        "description": "Extract text from images using Tesseract. Powers the ImageOCR tool. Tesseract data files are bundled in the sidecar — no separate system install needed.",
        "probe_module": "pytesseract",
        "packages": ["pytesseract"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://github.com/madmaze/pytesseract",
    },
    "pdf_extraction": {
        "name": "PDF Extraction",
        "description": "Extract text, images, and metadata from PDF files. Powers the PdfExtract tool.",
        "probe_module": "fitz",
        "packages": ["PyMuPDF"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://pymupdf.readthedocs.io/",
    },
    "system_monitoring": {
        "name": "System Monitoring",
        "description": "Monitor CPU, RAM, disk usage, battery, and top processes. Powers the SystemResources, BatteryStatus, DiskUsage, and TopProcesses tools.",
        "probe_module": "psutil",
        "packages": ["psutil"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://psutil.readthedocs.io/",
    },
    "network_discovery": {
        "name": "Network Discovery (mDNS)",
        "description": "Discover devices and services on the local network via mDNS/Bonjour. Powers the MDNSDiscover tool.",
        "probe_module": "zeroconf",
        "packages": ["zeroconf"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://python-zeroconf.readthedocs.io/",
    },
    "media_download": {
        "name": "Media Downloading (yt-dlp)",
        "description": "Download videos/audio from YouTube, Twitter, Instagram, and 1000+ other sites. Powers the DownloadMedia tool. Bundled in the sidecar by default.",
        "probe_module": "yt_dlp",
        "packages": ["yt-dlp"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://github.com/yt-dlp/yt-dlp",
    },
    "video_processing": {
        "name": "Video Processing (ffmpeg)",
        "description": "Convert, trim, merge, and process audio/video files. Powers audio conversion and video extraction tools. Uses imageio-ffmpeg which bundles ffmpeg — no system install needed.",
        "probe_module": "imageio_ffmpeg",
        "packages": ["imageio-ffmpeg"],
        "install_extra": None,
        "size_warning": None,
        "docs_url": "https://github.com/imageio/imageio-ffmpeg",
    },
}


def _capability_status(cap_id: str, spec: dict) -> CapabilityStatus:
    if uses_managed_installer(cap_id):
        inject_capability_path(cap_id)
        if cap_id == "transcription":
            try:
                from app.services.image_gen.installer import inject_image_gen_path

                inject_image_gen_path()
            except Exception:
                pass
        if is_capability_installed(cap_id) or probe_module_available(
            spec["probe_module"]
        ):
            return "installed"
        return "not_installed"
    inject_lightweight_capability_path(cap_id)
    return (
        "installed" if probe_module_available(spec["probe_module"]) else "not_installed"
    )


@router.get("", response_model=CapabilitiesResponse)
async def get_capabilities() -> CapabilitiesResponse:
    """Return the status of every optional capability."""
    caps: list[Capability] = []
    for cap_id, spec in CAPABILITY_SPECS.items():
        caps.append(
            Capability(
                id=cap_id,
                name=spec["name"],
                description=spec["description"],
                status=_capability_status(cap_id, spec),
                packages=spec["packages"],
                install_extra=spec.get("install_extra"),
                size_warning=spec.get("size_warning"),
                docs_url=spec.get("docs_url"),
            )
        )
    return CapabilitiesResponse(capabilities=caps)


def _status_payload(
    *,
    capability_id: str,
    status: str,
    stage: str = "",
    percent: float = 0.0,
    message: str = "",
    error: str | None = None,
    already_installed: bool = False,
    progress: object | None = None,
    async_install: bool = False,
) -> InstallStartResponse:
    log_lines = None
    install_dir = None
    if uses_managed_installer(capability_id):
        install_dir = str(get_capability_packages_dir(capability_id))
        async_install = True
    if progress is not None:
        log_lines = list(getattr(progress, "log_lines", []) or [])
    return InstallStartResponse(
        status=status,  # type: ignore[arg-type]
        capability_id=capability_id,
        stage=stage,
        percent=percent,
        message=message,
        error=error,
        already_installed=already_installed,
        install_dir=install_dir,
        log_lines=log_lines,
        async_install=async_install,
    )


async def _run_isolated(
    cmd: list[str], timeout: int, *, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    kwargs: dict = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if env is not None:
        kwargs["env"] = env
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        import subprocess as _sp

        kwargs["creationflags"] = _sp.CREATE_NEW_PROCESS_GROUP

    proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


@router.post("/install", response_model=InstallStartResponse)
async def install_capability(req: InstallRequest) -> InstallStartResponse:
    """Start installing a capability.

    Heavy capabilities (Whisper) start a background job and return immediately —
    subscribe to ``GET /capabilities/install/stream?capability_id=…`` for
    progress. Lightweight packages install via frozen-safe ``pip --target``.
    """
    spec = CAPABILITY_SPECS.get(req.capability_id)
    if not spec:
        raise HTTPException(
            status_code=404, detail=f"Unknown capability: {req.capability_id}"
        )

    cap_id = req.capability_id

    if uses_managed_installer(cap_id):
        if is_capability_installed(cap_id) and probe_module_available(
            spec["probe_module"]
        ):
            inject_capability_path(cap_id)
            return _status_payload(
                capability_id=cap_id,
                status="complete",
                stage="done",
                percent=100.0,
                message=f"{spec['name']} is already installed.",
                already_installed=True,
                async_install=True,
            )

        existing = get_active_progress(cap_id)
        if existing and existing.status == "running":
            return _status_payload(
                capability_id=cap_id,
                status="running",
                stage=existing.stage,
                percent=existing.percent,
                message=existing.message,
                progress=existing,
                async_install=True,
            )

        try:
            progress = await start_capability_install(cap_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return _status_payload(
            capability_id=cap_id,
            status=progress.status,
            stage=progress.stage,
            percent=progress.percent,
            message="Installation started.",
            progress=progress,
            async_install=True,
        )

    packages = spec["packages"]
    logger.info("Installing capability '%s': %s", cap_id, packages)

    try:
        python = find_python()
    except RuntimeError as exc:
        return _status_payload(
            capability_id=cap_id,
            status="error",
            message=str(exc),
            error=str(exc),
        )

    target = get_lightweight_capability_packages_dir(cap_id)
    target.mkdir(parents=True, exist_ok=True)
    marker = target / ".install-complete"
    marker.unlink(missing_ok=True)

    try:
        returncode, _stdout, stderr = await _run_isolated(
            [
                python,
                "-m",
                "pip",
                "install",
                "--target",
                str(target),
                "--upgrade",
                "--disable-pip-version-check",
                *packages,
            ],
            timeout=300,
        )
        if returncode != 0:
            logger.error(
                "[capabilities] pip install failed (rc=%d): %s",
                returncode,
                stderr[:2000],
            )
            return _status_payload(
                capability_id=cap_id,
                status="error",
                message=stderr.strip() or "Installation failed.",
                error=stderr.strip() or "Installation failed.",
            )

        if cap_id == "browser_automation":
            browser_env = os.environ.copy()
            browser_env["PYTHONPATH"] = os.pathsep.join(
                part
                for part in (str(target), browser_env.get("PYTHONPATH", ""))
                if part
            )
            rc2, _, err2 = await _run_isolated(
                [
                    python,
                    "-m",
                    "playwright",
                    "install",
                    "chromium",
                    "firefox",
                    "webkit",
                ],
                timeout=600,
                env=browser_env,
            )
            if rc2 != 0:
                return _status_payload(
                    capability_id=cap_id,
                    status="error",
                    message=(
                        "Playwright installed but browser download failed: "
                        f"{err2.strip()}"
                    ),
                    error=err2.strip(),
                )

        marker.write_text(json.dumps({"capability_id": cap_id, "packages": packages}))
        inject_lightweight_capability_path(cap_id)
        refresh_package_capabilities()

        logger.info("Capability '%s' installed successfully", cap_id)
        return _status_payload(
            capability_id=cap_id,
            status="complete",
            stage="done",
            percent=100.0,
            message=f"Installed: {', '.join(packages)}",
        )

    except asyncio.TimeoutError:
        return _status_payload(
            capability_id=cap_id,
            status="error",
            message="Installation timed out.",
            error="Installation timed out.",
        )
    except Exception as exc:
        logger.exception("Unexpected error installing '%s'", cap_id)
        return _status_payload(
            capability_id=cap_id,
            status="error",
            message=str(exc),
            error=str(exc),
        )


@router.get("/install/status", response_model=InstallStartResponse)
async def get_install_status(capability_id: str) -> InstallStartResponse:
    """Poll current installation status for a capability."""
    if capability_id not in CAPABILITY_SPECS:
        raise HTTPException(
            status_code=404, detail=f"Unknown capability: {capability_id}"
        )

    if uses_managed_installer(capability_id) and is_capability_installed(capability_id):
        active = get_active_progress(capability_id)
        if not (active and active.status == "running"):
            return _status_payload(
                capability_id=capability_id,
                status="complete",
                stage="done",
                percent=100.0,
                message="Already installed.",
                already_installed=True,
                async_install=True,
            )

    progress = get_active_progress(capability_id)
    if progress is None:
        return _status_payload(
            capability_id=capability_id,
            status="idle",
            message="No installation in progress.",
            async_install=uses_managed_installer(capability_id),
        )

    return _status_payload(
        capability_id=capability_id,
        status=progress.status,
        stage=progress.stage,
        percent=progress.percent,
        message=progress.message,
        error=progress.error,
        progress=progress,
        async_install=True,
    )


@router.get("/install/stream")
async def stream_install_progress(capability_id: str) -> StreamingResponse:
    """SSE stream of installation progress for a managed capability."""

    if not uses_managed_installer(capability_id):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Capability '{capability_id}' does not use async install streaming"
            ),
        )

    async def event_stream():
        loop = asyncio.get_running_loop()
        yield (
            f"data: {json.dumps({'status': 'connected', 'percent': 0, 'capability_id': capability_id})}\n\n"
        )

        active = get_active_progress(capability_id)
        if is_capability_installed(capability_id) and not (
            active and active.status == "running"
        ):
            yield (
                f"data: {json.dumps({'status': 'complete', 'percent': 100, 'message': 'Already installed', 'capability_id': capability_id})}\n\n"
            )
            return

        deadline = loop.time() + 15.0
        while get_active_progress(capability_id) is None:
            if loop.time() > deadline:
                yield (
                    f"data: {json.dumps({'status': 'error', 'message': 'No install started. Call POST /capabilities/install first.', 'capability_id': capability_id})}\n\n"
                )
                return
            await asyncio.sleep(0.3)

        progress = get_active_progress(capability_id)
        assert progress is not None

        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        async def _pump() -> None:
            try:
                async for event in progress.events():
                    await queue.put(event)
            finally:
                await queue.put(_SENTINEL)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is _SENTINEL:
                    break
                event = dict(item)
                event.setdefault("capability_id", capability_id)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("status") in ("complete", "error"):
                    break
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
