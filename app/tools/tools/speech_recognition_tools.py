"""Speech Recognition tools — on-device transcription via SFSpeechRecognizer (macOS only).

Uses Apple's built-in speech recognition framework (the same engine Siri uses).
This is an alternative/complement to the Whisper-based transcription.

Requires:
  - TCC grant: Speech Recognition (System Settings → Privacy & Security → Speech Recognition)
  - NSSpeechRecognitionUsageDescription in Info.plist
  - pyobjc-framework-Speech
  - Microphone access (for live recording path)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.services.action_needed import os_permission_needed
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_PERMISSION_HINT = (
    "Speech Recognition access is required. "
    "Grant it in System Settings → Privacy & Security → Speech Recognition, then restart the app."
)

# SFSpeechRecognizerAuthorizationStatus constants
_SF_NOT_DETERMINED = 0
_SF_DENIED = 1
_SF_RESTRICTED = 2
_SF_AUTHORIZED = 3


def _ensure_speech_access() -> None:
    import Speech  # type: ignore[import]

    status = Speech.SFSpeechRecognizer.authorizationStatus()
    if status == _SF_AUTHORIZED:
        return
    if status == _SF_NOT_DETERMINED:
        raise PermissionError(
            f"Speech Recognition access has not been requested. {_PERMISSION_HINT}"
        )
    raise PermissionError(
        f"Speech Recognition authorization status={status}. {_PERMISSION_HINT}"
    )


def _transcribe_file_sync(audio_path: str, locale: str, timeout: float) -> str:
    """Blocking transcription of an audio file via SFSpeechRecognizer."""
    import Speech  # type: ignore[import]
    import Foundation  # type: ignore[import]

    _ensure_speech_access()

    locale_obj = Foundation.NSLocale.localeWithLocaleIdentifier_(locale)
    recognizer = Speech.SFSpeechRecognizer.alloc().initWithLocale_(locale_obj)

    if recognizer is None or not recognizer.isAvailable():
        raise RuntimeError(
            f"SFSpeechRecognizer unavailable for locale '{locale}'. "
            "Ensure the device is connected to the internet for first-time use, "
            "or use a locale with an installed offline language model."
        )

    url = Foundation.NSURL.fileURLWithPath_(audio_path)
    request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(url)
    request.setShouldReportPartialResults_(False)

    result_text: list[str] = [""]
    error_msg: list[str | None] = [None]
    done = threading.Event()

    def handler(result: Any, error: Any) -> None:
        if error is not None:
            error_msg[0] = str(error.localizedDescription())
        elif result is not None:
            result_text[0] = str(result.bestTranscription().formattedString())
        done.set()

    task = recognizer.recognitionTaskWithRequest_resultHandler_(request, handler)
    if not done.wait(timeout=timeout):
        task.cancel()
        raise TimeoutError(f"Speech recognition timed out after {timeout}s.")

    if error_msg[0]:
        raise RuntimeError(f"Speech recognition error: {error_msg[0]}")

    return result_text[0]


async def tool_transcribe_with_speech(
    session: ToolSession,
    audio_path: str,
    locale: str = "en-US",
    timeout: float = 60.0,
) -> ToolResult:
    """Transcribe an audio file using Apple's on-device SFSpeechRecognizer.

    Uses Apple's built-in speech recognition (same engine as Siri/Dictation).
    Supports many languages without downloading model files. Requires internet
    connectivity on first use for model initialization; subsequent uses work offline.

    Args:
        audio_path: Absolute path to the audio file (WAV, M4A, MP3, AIFF, etc.).
        locale: BCP-47 locale code for the language (e.g. "en-US", "es-ES", "fr-FR").
                Defaults to "en-US".
        timeout: Maximum seconds to wait for transcription (default 60, max 300).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(
            output="Apple Speech Recognition is only available on macOS.",
            type=ToolResultType.ERROR,
        )

    timeout = max(5.0, min(timeout, 300.0))
    path = Path(audio_path)
    if not path.exists():
        return ToolResult(
            output=f"Audio file not found: {audio_path}",
            type=ToolResultType.ERROR,
        )

    try:
        text = await asyncio.get_event_loop().run_in_executor(
            None, _transcribe_file_sync, str(path), locale, timeout
        )
    except PermissionError as exc:
        return ToolResult(
            output=str(exc),
            metadata={"available": False, "hint": _PERMISSION_HINT},
            type=ToolResultType.ERROR,
            action_needed=os_permission_needed(feature="Apple Speech Recognition", permission_key="speech_recognition", source="tool.speech_recognition"),
        )
    except TimeoutError as exc:
        return ToolResult(output=str(exc), type=ToolResultType.ERROR)
    except Exception as exc:
        logger.exception("tool_transcribe_with_speech failed")
        return ToolResult(output=f"Transcription failed: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=text or "(no speech detected)",
        metadata={
            "transcript": text,
            "locale": locale,
            "audio_path": str(path),
            "engine": "SFSpeechRecognizer",
        },
        type=ToolResultType.SUCCESS,
    )


async def tool_list_speech_locales(session: ToolSession) -> ToolResult:
    """List all locales supported by SFSpeechRecognizer on this device."""
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Apple Speech Recognition is only available on macOS.", type=ToolResultType.ERROR)

    def _list_locales_sync() -> list[str]:
        import Speech  # type: ignore[import]
        locales = Speech.SFSpeechRecognizer.supportedLocales()
        return sorted(str(locale.localeIdentifier()) for locale in locales)

    try:
        locales = await asyncio.get_event_loop().run_in_executor(None, _list_locales_sync)
    except Exception as exc:
        return ToolResult(output=f"Failed to list locales: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"SFSpeechRecognizer supports {len(locales)} locale(s).",
        metadata={"locales": locales, "count": len(locales)},
        type=ToolResultType.SUCCESS,
    )
