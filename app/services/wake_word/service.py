"""openWakeWord detection service.

Architecture
────────────
A single background asyncio task streams 16kHz mono audio from the system
microphone via sounddevice, feeds 80ms frames to the openWakeWord ONNX model,
and pushes detection events onto an asyncio.Queue that the SSE route drains.

This mirrors the Rust whisper-tiny wake word engine's event contract exactly:
  wake-word-detected  { keyword: str, score: float }
  wake-word-rms       float  (0–1, emitted ~5 Hz)
  wake-word-mode      "listening" | "muted" | "dismissed"
  wake-word-error     str    (non-fatal)

That identical contract means the frontend hook can subscribe to either engine
without any changes to the event-handling logic.

Cross-platform
──────────────
Uses sounddevice (PortAudio) for microphone input — the same library already
used throughout the app.  openWakeWord uses ONNX Runtime for inference, which
ships pre-built wheels for macOS (arm64 + x86_64), Windows (x64), and Linux
(x86_64).  No native build step is required.

Cooldown / dismiss state machine
─────────────────────────────────
  LISTENING   — actively detecting
  MUTED       — audio ignored, task kept alive (fast resume)
  DISMISSED   — false-trigger cooldown; auto-reverts after DISMISS_PAUSE_S
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any

import numpy as np

from app.common.system_logger import get_logger
from .models import (
    oww_models_dir,
    model_exists,
    _onnx_filename,
    get_bundled_models,
    download_model,
)

logger = get_logger()

# ── Constants ─────────────────────────────────────────────────────────────────

SAMPLE_RATE = 16_000          # Hz — openWakeWord requires 16kHz mono
FRAME_MS = 80                  # ms per inference chunk (openWakeWord default)
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 1280 samples per chunk

DEFAULT_THRESHOLD = 0.5        # confidence score to fire a detection
COOLDOWN_S = 3.0               # seconds before re-arming after a trigger
DISMISS_PAUSE_S = 10.0         # seconds of suppress after user dismisses

RMS_EMIT_EVERY_N = 6           # emit RMS once every N frames (~5 Hz)


class _Mode(str, Enum):
    LISTENING = "listening"
    MUTED = "muted"
    DISMISSED = "dismissed"


# ── Singleton ─────────────────────────────────────────────────────────────────

_service_instance: "WakeWordService | None" = None


def get_wake_word_service() -> "WakeWordService":
    global _service_instance
    if _service_instance is None:
        _service_instance = WakeWordService()
    return _service_instance


# ── Service ───────────────────────────────────────────────────────────────────

class WakeWordService:
    """Manages the openWakeWord background detection loop."""

    def __init__(self) -> None:
        self._mode = _Mode.LISTENING
        self._running = False
        self._task: asyncio.Task | None = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._model_name: str = "hey_jarvis"
        self._threshold: float = DEFAULT_THRESHOLD
        self._device_name: str | None = None
        self._dismiss_until: float = 0.0

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def mode(self) -> str:
        return self._mode.value

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def threshold(self) -> float:
        return self._threshold

    # ── Control API ───────────────────────────────────────────────────────

    async def start(
        self,
        model_name: str | None = None,
        threshold: float | None = None,
        device_name: str | None = None,
    ) -> None:
        """Start the detection loop. Idempotent if already running."""
        if model_name:
            self._model_name = model_name
        if threshold is not None:
            self._threshold = threshold
        if device_name is not None:
            self._device_name = device_name

        if self._running:
            # Already running — just un-mute and update config
            self._mode = _Mode.LISTENING
            await self._emit("wake-word-mode", _Mode.LISTENING.value)
            return

        # Ensure model is present; auto-download BUNDLED_MODELS if missing
        if not model_exists(self._model_name):
            if self._model_name in get_bundled_models():
                await self._emit("wake-word-error", f"Downloading {self._model_name}…")
                try:
                    await download_model(self._model_name)
                except Exception as exc:
                    await self._emit("wake-word-error", f"Download failed: {exc}")
                    raise
            else:
                raise ValueError(
                    f"OWW model not found: {self._model_name}. "
                    "Download it from the Wake Word settings tab first."
                )

        self._running = True
        self._mode = _Mode.LISTENING
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._detection_loop(), name="oww-detection")
        await self._emit("wake-word-mode", _Mode.LISTENING.value)
        logger.info(f"OWW wake word service started (model={self._model_name})")

    async def stop(self) -> None:
        """Stop the detection loop entirely."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._mode = _Mode.MUTED
        await self._emit("wake-word-mode", _Mode.MUTED.value)
        logger.info("OWW wake word service stopped")

    async def mute(self) -> None:
        """Ignore audio without stopping the task (fast resume)."""
        self._mode = _Mode.MUTED
        await self._emit("wake-word-mode", _Mode.MUTED.value)

    async def unmute(self) -> None:
        """Resume after mute."""
        self._mode = _Mode.LISTENING
        await self._emit("wake-word-mode", _Mode.LISTENING.value)

    async def dismiss(self) -> None:
        """Mark as dismissed — suppress re-trigger for DISMISS_PAUSE_S seconds."""
        self._mode = _Mode.DISMISSED
        self._dismiss_until = time.monotonic() + DISMISS_PAUSE_S
        await self._emit("wake-word-mode", _Mode.DISMISSED.value)

    async def configure(
        self,
        model_name: str | None = None,
        threshold: float | None = None,
    ) -> None:
        """Update config at runtime. Takes effect on the next detection frame."""
        if model_name and model_name != self._model_name:
            self._model_name = model_name
            if self._running:
                # Restart to reload the new model
                await self.stop()
                await self.start()
        if threshold is not None:
            self._threshold = max(0.0, min(1.0, threshold))

    # ── Event queue (drained by SSE route) ───────────────────────────────

    async def _emit(self, event: str, data: Any) -> None:
        try:
            self._event_queue.put_nowait({"event": event, "data": data})
        except asyncio.QueueFull:
            pass  # drop if no SSE consumer is connected

    async def next_event(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Pop and return the next queued event, or None on timeout."""
        try:
            return await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ── Detection loop ────────────────────────────────────────────────────

    async def _detection_loop(self) -> None:
        """Background asyncio task: stream mic → OWW inference → emit events."""
        try:
            await self._run_loop()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception(f"OWW detection loop crashed: {exc}")
            await self._emit("wake-word-error", str(exc))
        finally:
            self._running = False
            # Signal the real state — leaving _mode on "listening" showed a
            # live UI over a dead loop. MUTED is the existing "not listening"
            # state stop() also uses.
            self._mode = _Mode.MUTED
            try:
                await self._emit("wake-word-mode", _Mode.MUTED.value)
            except Exception:
                pass
            logger.info("OWW detection loop exited")

    async def _run_loop(self) -> None:
        import sounddevice as sd
        import openwakeword
        from openwakeword.model import Model as OWWModel

        loop = asyncio.get_event_loop()

        # Load the ONNX model in a thread pool so we don't block the event loop
        model_path = str(oww_models_dir() / _onnx_filename(self._model_name))

        def _load() -> OWWModel:
            return OWWModel(
                wakeword_models=[model_path],
                inference_framework="onnx",
                enable_speex_noise_suppression=False,
            )

        oww_model: OWWModel = await loop.run_in_executor(None, _load)
        logger.info(f"OWW model loaded: {model_path}")

        # Resolve device index for sounddevice
        device_index = self._resolve_device(self._device_name)

        # Audio ring buffer — filled by the sounddevice callback
        # All appends are marshalled onto the event-loop thread via
        # call_soon_threadsafe, and the drain below runs on the same thread —
        # no lock needed.
        audio_buffer: list[np.ndarray] = []

        def _audio_callback(
            indata: np.ndarray, frames: int, _time, status
        ) -> None:
            if status:
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self._emit("wake-word-error", str(status))
                    )
                )
            # indata shape: (frames, channels) — flatten to mono.
            # openWakeWord expects int16-scale PCM: its melspectrogram does
            # np.astype(int16) on the raw values, so feeding float32 in
            # [-1, 1] truncates to -1/0/1 (digital silence) and detection
            # NEVER fires. Keep the float copy only for the RMS gate.
            mono = (indata[:, 0] * 32767.0).astype(np.int16)
            loop.call_soon_threadsafe(audio_buffer.append, mono)

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            device=device_index,
            callback=_audio_callback,
        )

        last_trigger = 0.0
        frame_count = 0

        with stream:
            while self._running:
                # Yield control briefly so the event loop can breathe
                await asyncio.sleep(0.02)

                # Drain everything the callback has accumulated
                frames = audio_buffer.copy()
                audio_buffer.clear()

                for chunk in frames:
                    frame_count += 1

                    # Emit RMS at ~5 Hz regardless of mode (keeps the UI meter alive)
                    if frame_count % RMS_EMIT_EVERY_N == 0:
                        rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32767.0) ** 2)))
                        await self._emit("wake-word-rms", min(rms * 10, 1.0))

                    # Auto-expire dismiss cooldown
                    if self._mode == _Mode.DISMISSED:
                        if time.monotonic() >= self._dismiss_until:
                            self._mode = _Mode.LISTENING
                            await self._emit("wake-word-mode", _Mode.LISTENING.value)

                    if self._mode != _Mode.LISTENING:
                        continue

                    # Check post-trigger cooldown
                    if time.monotonic() - last_trigger < COOLDOWN_S:
                        continue

                    # Energy gate — skip near-silence frames (float scale)
                    rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32767.0) ** 2)))
                    if rms < 0.0005:
                        continue

                    # Run inference (blocking — offload to thread pool)
                    # OWW predict() is fast (~1ms on CPU) but not async-native
                    def _predict(audio: np.ndarray) -> dict:
                        return oww_model.predict(audio)

                    scores: dict = await loop.run_in_executor(None, _predict, chunk)

                    # scores is { model_name: float } — check all loaded models
                    for model_key, score in scores.items():
                        if score >= self._threshold:
                            last_trigger = time.monotonic()
                            logger.info(
                                f"OWW wake word detected: {model_key} score={score:.3f}"
                            )
                            await self._emit(
                                "wake-word-detected",
                                {"keyword": self._model_name, "score": round(score, 4)},
                            )
                            break  # one detection per frame is enough

    @staticmethod
    def _resolve_device(device_name: str | None) -> int | None:
        """Resolve a device name string to a sounddevice index, or None for default."""
        if not device_name:
            return None
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            for idx, dev in enumerate(devices):
                if dev["name"] == device_name and dev["max_input_channels"] > 0:
                    return idx
        except Exception as exc:
            logger.warning(f"Could not resolve audio device {device_name!r}: {exc}")
        return None
