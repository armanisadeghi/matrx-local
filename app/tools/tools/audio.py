"""Audio tools — device listing, recording, playback, and transcription."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import uuid
from pathlib import Path

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.services.action_needed import os_permission_needed
from app.config import TEMP_DIR
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

AUDIO_DIR = TEMP_DIR / "audio"


def _ensure_audio_dir() -> Path:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIO_DIR


async def tool_list_audio_devices(
    session: ToolSession,
) -> ToolResult:
    """List available audio input (microphones) and output (speakers) devices.

    On macOS, uses system_profiler (CoreAudio) as the primary source so all
    devices are visible — not just the PortAudio default aggregate.  sounddevice
    indices are attached where they match, since sd.rec() needs them.
    """
    if PLATFORM["is_mac"]:
        return await _list_devices_macos()
    return await _list_devices_sounddevice()


async def _list_devices_macos() -> ToolResult:
    """macOS: enumerate all CoreAudio devices via system_profiler, then overlay
    sounddevice indices so the recording path can still use sd.rec()."""
    import json as _json
    import asyncio as _asyncio

    inputs: list[dict] = []
    outputs: list[dict] = []

    # --- CoreAudio via system_profiler ---
    try:
        proc = await _asyncio.create_subprocess_exec(
            "system_profiler", "SPAudioDataType", "-json",
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=10)
        data = _json.loads(stdout)
        for item in data.get("SPAudioDataType", []):
            name = item.get("_name", "Unknown")
            # Each item in SPAudioDataType is a device; check input/output fields
            # The keys differ between macOS versions — try both common layouts
            input_ch = item.get("coreaudio_input_source", item.get("coreaudio_device_input", ""))
            output_ch = item.get("coreaudio_output_source", item.get("coreaudio_device_output", ""))
            default_rate = 44100  # CoreAudio commonly resamples to the host rate
            try:
                rate_str = item.get("coreaudio_default_audio_input_device",
                           item.get("coreaudio_device_srate", ""))
                if rate_str and str(rate_str).replace(".", "").isdigit():
                    default_rate = int(float(str(rate_str)))
            except Exception:
                pass

            # Any device that can record (input_ch present or not "0 ch")
            if input_ch not in ("", None, "0 ch"):
                inputs.append({
                    "name": name,
                    "sample_rate": default_rate,
                    "channels": 1,  # Will be overridden by sounddevice if available
                    "index": None,  # Filled in below
                    "is_default": bool(item.get("coreaudio_default_audio_input_device")),
                })
            if output_ch not in ("", None, "0 ch"):
                outputs.append({
                    "name": name,
                    "sample_rate": default_rate,
                    "channels": 2,
                    "index": None,
                    "is_default": bool(item.get("coreaudio_default_audio_output_device")),
                })
    except Exception as exc:
        logger.debug("system_profiler parse failed (%s); falling back to sounddevice only", exc)

    # --- sounddevice for indices + richer metadata ---
    try:
        import sounddevice as sd
        sd_devices = sd.query_devices()
        sd_inputs: list[dict] = []
        sd_outputs: list[dict] = []
        for i, dev in enumerate(sd_devices):
            entry = {
                "index": i,
                "name": dev["name"],
                "sample_rate": dev["default_samplerate"],
            }
            if dev["max_input_channels"] > 0:
                e = dict(entry)
                e["channels"] = dev["max_input_channels"]
                sd_inputs.append(e)
            if dev["max_output_channels"] > 0:
                e = dict(entry)
                e["channels"] = dev["max_output_channels"]
                sd_outputs.append(e)

        def _match_name(name: str, sd_list: list[dict]) -> dict | None:
            """Fuzzy match a CoreAudio name to a sounddevice entry."""
            name_lower = name.lower()
            for sd_dev in sd_list:
                if sd_dev["name"].lower() == name_lower:
                    return sd_dev
            for sd_dev in sd_list:
                if name_lower in sd_dev["name"].lower() or sd_dev["name"].lower() in name_lower:
                    return sd_dev
            return None

        if inputs:
            for entry in inputs:
                match = _match_name(entry["name"], sd_inputs)
                if match:
                    entry["index"] = match["index"]
                    entry["sample_rate"] = match["sample_rate"]
                    entry["channels"] = match["channels"]
        else:
            # system_profiler gave us nothing usable — fall back to sounddevice list
            inputs = sd_inputs

        if outputs:
            for entry in outputs:
                match = _match_name(entry["name"], sd_outputs)
                if match:
                    entry["index"] = match["index"]
                    entry["sample_rate"] = match["sample_rate"]
                    entry["channels"] = match["channels"]
        else:
            outputs = sd_outputs

    except ImportError:
        pass  # No sounddevice — CoreAudio list is still returned without indices

    # Ensure at least one entry if everything failed
    if not inputs and not outputs:
        return _list_devices_fallback()

    lines = ["Input Devices (Microphones):"]
    for d in inputs:
        idx = f"[{d['index']}] " if d.get("index") is not None else ""
        lines.append(f"  {idx}{d['name']} ({d.get('channels', '?')}ch, {d.get('sample_rate', '?')}Hz)")
    lines.append("")
    lines.append("Output Devices (Speakers):")
    for d in outputs:
        idx = f"[{d['index']}] " if d.get("index") is not None else ""
        lines.append(f"  {idx}{d['name']} ({d.get('channels', '?')}ch, {d.get('sample_rate', '?')}Hz)")

    return ToolResult(
        output="\n".join(lines),
        metadata={"inputs": inputs, "outputs": outputs},
    )


async def _list_devices_sounddevice() -> ToolResult:
    """Non-macOS: use sounddevice directly."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = []
        outputs = []
        for i, dev in enumerate(devices):
            entry = {
                "index": i,
                "name": dev["name"],
                "sample_rate": dev["default_samplerate"],
            }
            if dev["max_input_channels"] > 0:
                entry["channels"] = dev["max_input_channels"]
                inputs.append(dict(entry))
            if dev["max_output_channels"] > 0:
                e = dict(entry)
                e["channels"] = dev["max_output_channels"]
                outputs.append(e)

        lines = ["Input Devices (Microphones):"]
        for d in inputs:
            lines.append(f"  [{d['index']}] {d['name']} ({d['channels']}ch, {d['sample_rate']:.0f}Hz)")
        lines.append("")
        lines.append("Output Devices (Speakers):")
        for d in outputs:
            lines.append(f"  [{d['index']}] {d['name']} ({d['channels']}ch, {d['sample_rate']:.0f}Hz)")

        return ToolResult(
            output="\n".join(lines),
            metadata={"inputs": inputs, "outputs": outputs},
        )
    except ImportError:
        return _list_devices_fallback()


def _list_devices_fallback() -> ToolResult:
    """Fallback using system commands."""
    try:
        if PLATFORM["is_mac"]:
            result = subprocess.run(
                ["system_profiler", "SPAudioDataType", "-json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return ToolResult(
                output=f"Audio devices (raw):\n{result.stdout[:3000]}",
                metadata={"raw": True},
            )
        elif PLATFORM["is_windows"]:
            result = subprocess.run(
                [
                    CAPABILITIES["powershell_path"],
                    "-Command",
                    "Get-WmiObject Win32_SoundDevice | Select-Object Name, Status | Format-List",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return ToolResult(output=f"Audio devices:\n{result.stdout}")
        else:
            result = subprocess.run(
                ["arecord", "-l"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            result2 = subprocess.run(
                ["aplay", "-l"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return ToolResult(
                output=f"Input devices:\n{result.stdout}\n\nOutput devices:\n{result2.stdout}",
            )
    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "Audio Recording is not installed. "
                "Go to Settings → Capabilities to install it, or open the Devices & Permissions page.\n"
                f"Developer info: pip install sounddevice. Error: {e}"
            ),
            metadata={"fix_capability_id": "audio_recording"},
        )


async def tool_record_audio(
    session: ToolSession,
    duration_seconds: int = 5,
    device_index: int | None = None,
    sample_rate: int | None = None,
    channels: int = 1,
    format: str = "wav",
) -> ToolResult:
    """Record audio from microphone for specified duration. Returns path to audio file.

    sample_rate defaults to None, which causes us to use the device's native rate
    rather than a hardcoded 44100 that many devices don't support.
    """
    if duration_seconds < 1 or duration_seconds > 300:
        return ToolResult(
            type=ToolResultType.ERROR, output="Duration must be 1-300 seconds."
        )

    if PLATFORM["is_mac"]:
        from app.services.permissions.checker import PermissionStatus, check_microphone

        permission = await check_microphone()
        if permission.status != PermissionStatus.GRANTED:
            return ToolResult(
                type=ToolResultType.ERROR,
                output=permission.user_details or permission.details,
                action_needed=os_permission_needed(
                    feature="Audio recording",
                    permission_key="microphone",
                    source="tool.audio",
                ),
            )

    _ensure_audio_dir()
    filename = f"recording_{uuid.uuid4().hex[:8]}.{format}"
    filepath = AUDIO_DIR / filename

    try:
        import sounddevice as sd

        # Use the device's native sample rate if not specified — avoids
        # "Invalid sample rate" errors when the caller passes 44100 but
        # the device's default is 48000.
        if sample_rate is None:
            dev_info = sd.query_devices(device_index, "input") if device_index is not None \
                       else sd.query_devices(kind="input")
            sample_rate = int(dev_info["default_samplerate"])

        logger.info(
            "Recording %ds of audio (device=%s, rate=%d, ch=%d)",
            duration_seconds,
            device_index,
            sample_rate,
            channels,
        )

        recording = sd.rec(
            int(duration_seconds * sample_rate),
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
            device=device_index,
        )
        # Run in thread to not block event loop
        await asyncio.get_event_loop().run_in_executor(None, lambda: sd.wait())

        # Save as WAV
        import wave

        with wave.open(str(filepath), "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # int16
            wf.setframerate(sample_rate)
            wf.writeframes(recording.tobytes())

        file_size = filepath.stat().st_size
        return ToolResult(
            output=f"Recorded {duration_seconds}s of audio to {filepath} ({file_size} bytes)",
            metadata={
                "path": str(filepath),
                "duration_seconds": duration_seconds,
                "sample_rate": sample_rate,
                "channels": channels,
                "size_bytes": file_size,
            },
        )

    except ImportError:
        # Fallback to system tools
        effective_rate = sample_rate or 44100
        return await _record_fallback(filepath, duration_seconds, effective_rate, channels)
    except Exception as e:
        err_str = str(e)
        permission_failure = PLATFORM["is_mac"] and any(
            marker in err_str.lower()
            for marker in ("permission", "not permitted", "access denied")
        )
        if (
            "No Default Input Device" in err_str
            or "Invalid device" in err_str
            or "PortAudio" in err_str
        ):
            return ToolResult(
                type=ToolResultType.ERROR,
                output=(
                    f"No audio input device found: {e}\n\n"
                    "Troubleshooting:\n"
                    "  • macOS: Grant Microphone access in System Settings → Privacy & Security\n"
                    "  • Linux/WSL: Connect a microphone or enable PulseAudio/PipeWire\n"
                    "  • Check available devices with ListAudioDevices tool first"
                ),
                action_needed=(
                    os_permission_needed(
                        feature="Audio recording",
                        permission_key="microphone",
                        source="tool.audio",
                    )
                    if permission_failure
                    else None
                ),
            )
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"Recording failed: {e}",
            action_needed=(
                os_permission_needed(
                    feature="Audio recording",
                    permission_key="microphone",
                    source="tool.audio",
                )
                if permission_failure
                else None
            ),
        )


async def _record_fallback(
    filepath: Path, duration: int, rate: int, channels: int
) -> ToolResult:
    """Record using system tools when sounddevice is not available."""
    try:
        if PLATFORM["is_mac"]:
            # Use sox/rec if available
            proc = await asyncio.create_subprocess_exec(
                "rec",
                "-r",
                str(rate),
                "-c",
                str(channels),
                str(filepath),
                "trim",
                "0",
                str(duration),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=duration + 10)
        elif PLATFORM["is_windows"]:
            # Use PowerShell with built-in audio
            ps_script = f"""
Add-Type -AssemblyName System.Speech
$audio = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$audio.SetInputToDefaultAudioDevice()
$stream = [System.IO.File]::Create('{filepath}')
$audio.SetInputToAudioStream($stream, (New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo({rate}, 16, {channels})))
Start-Sleep -Seconds {duration}
$stream.Close()
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=duration + 10)
        else:
            proc = await asyncio.create_subprocess_exec(
                "arecord",
                "-d",
                str(duration),
                "-r",
                str(rate),
                "-c",
                str(channels),
                "-f",
                "S16_LE",
                str(filepath),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=duration + 10)

        if filepath.exists():
            return ToolResult(
                output=f"Recorded {duration}s to {filepath} (system fallback)",
                metadata={"path": str(filepath), "duration_seconds": duration},
            )
        return ToolResult(
            type=ToolResultType.ERROR, output="Recording produced no file."
        )

    except FileNotFoundError:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "Audio Recording is not installed. "
                "Go to Settings → Capabilities to install it, or open the Devices & Permissions page.\n"
                "Developer info: pip install sounddevice numpy"
            ),
            metadata={"fix_capability_id": "audio_recording"},
        )
    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Fallback recording failed: {e}"
        )


async def tool_play_audio(
    session: ToolSession,
    file_path: str,
    device_index: int | None = None,
) -> ToolResult:
    """Play an audio file through speakers."""
    resolved = session.resolve_path(file_path)

    if not os.path.isfile(resolved):
        return ToolResult(
            type=ToolResultType.ERROR, output=f"File not found: {resolved}"
        )

    try:
        import sounddevice as sd
        import wave

        with wave.open(resolved, "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())

        import numpy as np

        audio_data = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            audio_data = audio_data.reshape(-1, channels)

        sd.play(audio_data, samplerate=rate, device=device_index)
        await asyncio.get_event_loop().run_in_executor(None, sd.wait)

        duration = len(frames) / (rate * channels * 2)
        return ToolResult(output=f"Played {resolved} ({duration:.1f}s)")

    except ImportError:
        # Fallback
        try:
            if PLATFORM["is_mac"]:
                proc = await asyncio.create_subprocess_exec(
                    "afplay",
                    resolved,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            elif PLATFORM["is_windows"]:
                proc = await asyncio.create_subprocess_exec(
                    CAPABILITIES["powershell_path"],
                    "-Command",
                    f"(New-Object Media.SoundPlayer '{resolved}').PlaySync()",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "aplay",
                    resolved,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            await asyncio.wait_for(proc.communicate(), timeout=300)
            return ToolResult(output=f"Played {resolved}")
        except Exception as e:
            return ToolResult(type=ToolResultType.ERROR, output=f"Playback failed: {e}")

    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Playback failed: {e}")


async def tool_transcribe_audio(
    session: ToolSession,
    file_path: str,
    model: str = "base",
    language: str | None = None,
) -> ToolResult:
    """Transcribe audio file to text using OpenAI Whisper (local model).

    Models: tiny, base, small, medium, large
    Smaller models are faster but less accurate.
    """
    resolved = session.resolve_path(file_path)

    if not os.path.isfile(resolved):
        return ToolResult(
            type=ToolResultType.ERROR, output=f"File not found: {resolved}"
        )

    try:
        import whisper

        logger.info("Loading whisper model '%s' for transcription", model)

        # Run in executor to not block event loop
        loop = asyncio.get_event_loop()

        def _transcribe():
            m = whisper.load_model(model)
            opts = {}
            if language:
                opts["language"] = language
            result = m.transcribe(resolved, **opts)
            return result

        result = await loop.run_in_executor(None, _transcribe)

        text = result.get("text", "").strip()
        lang = result.get("language", "unknown")
        segments = result.get("segments", [])

        segment_data = []
        for seg in segments:
            segment_data.append(
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip(),
                }
            )

        return ToolResult(
            output=f"Transcription ({lang}):\n\n{text}",
            metadata={
                "text": text,
                "language": lang,
                "segments": segment_data,
                "model": model,
                "source_file": resolved,
            },
        )

    except ImportError:
        # Try whisper CLI
        try:
            cmd = ["whisper", resolved, "--model", model, "--output_format", "txt"]
            if language:
                cmd.extend(["--language", language])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            if proc.returncode != 0:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=(
                        "Speech Transcription (Whisper) is not installed. "
                        "Go to Settings → Capabilities to install it, or open the Devices & Permissions page.\n"
                        f"Developer info: pip install openai-whisper\nError: {stderr.decode()}"
                    ),
                    metadata={"fix_capability_id": "transcription"},
                )

            text = stdout.decode().strip()
            return ToolResult(
                output=f"Transcription:\n\n{text}",
                metadata={"text": text, "model": model},
            )
        except FileNotFoundError:
            return ToolResult(
                type=ToolResultType.ERROR,
                output=(
                    "Speech Transcription (Whisper) is not installed. "
                    "Go to Settings → Capabilities to install it, or open the Devices & Permissions page.\n"
                    "Developer info: pip install openai-whisper"
                ),
                metadata={"fix_capability_id": "transcription"},
            )

    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Transcription failed: {e}"
        )
