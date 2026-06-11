"""Kokoro TTS service — production-grade.

Architecture
------------

* **Singleton** — one ``TtsService`` per engine. Created lazily under a module
  lock; thread-safe.
* **Lazy load** — model + voices files are downloaded once into ``~/.matrx/tts``
  and loaded into a kept-alive ``Kokoro`` instance on first synth request. All
  blocking work (download, load, synth) runs in the asyncio default executor.
* **Hybrid chunker** — short text goes through ``kokoro.create()`` (single shot,
  one chunk frame); long text goes through ``kokoro.create_stream()`` wrapped
  with a per-chunk watchdog and explicit exception capture. Both paths emit the
  same v2 framed wire protocol so the client never branches.
* **Wire format v2** — each frame is ``1B tag · 4B BE uint32 len · N payload``.
  Tags: 0x01 CHUNK (WAV), 0x02 END (empty), 0xFF ERROR (UTF-8 JSON). See
  ``models.py`` constants.
* **Voice blending** — kokoro voices are ``(510, 1, 256) float32`` embeddings
  in the ``voices-v1.0.bin`` NpzFile. Any weighted linear combination is itself
  a valid embedding; we cache builtin embeddings on first lookup and custom
  ``.npy`` voices keyed by mtime to avoid re-reading from disk per synth.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import struct
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.common.system_logger import get_logger
from app.config import MATRX_HOME_DIR
from app.services.tts.models import (
    BUILTIN_VOICES,
    DEFAULT_VOICE_ID,
    EMBEDDING_SHAPE,
    LANGUAGE_MAP,
    ONNX_MODEL_FILENAME,
    ONNX_MODEL_SHA256,
    ONNX_MODEL_SIZE_BYTES,
    ONNX_MODEL_URL,
    SAMPLE_RATE,
    STREAM_CHUNK_TIMEOUT_SECONDS,
    STREAM_TAG_CHUNK,
    STREAM_TAG_END,
    STREAM_TAG_ERROR,
    STREAM_THRESHOLD_CHARS,
    VOICE_MAP,
    VOICES_BIN_FILENAME,
    VOICES_BIN_SHA256,
    VOICES_BIN_SIZE_BYTES,
    VOICES_BIN_URL,
)

logger = get_logger()

TTS_DIR = MATRX_HOME_DIR / "tts"
TTS_OUTPUT_DIR = TTS_DIR / "output"
CUSTOM_VOICES_DIR = TTS_DIR / "custom-voices"

PREVIEW_TEXT = "Hello! This is a preview of my voice. I hope you enjoy how I sound."

_SAFE_ID_RE = re.compile(r"[^a-z0-9_-]")


def _sanitize_voice_id(raw: str) -> str:
    """Convert arbitrary text to a filesystem-safe voice identifier."""
    cleaned = raw.lower().strip().replace(" ", "_")
    cleaned = _SAFE_ID_RE.sub("", cleaned)
    return cleaned[:64]


# ── Result / error types ──────────────────────────────────────────────────────


class TtsError(Exception):
    """Domain error with a stable code the API layer maps to HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class SynthesisResult:
    success: bool
    audio_bytes: bytes | None = None
    sample_rate: int = SAMPLE_RATE
    duration_seconds: float = 0.0
    voice_id: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None
    error_code: str | None = None


# ── WAV encoding ──────────────────────────────────────────────────────────────


def _wav_bytes(samples, sample_rate: int) -> bytes:
    """Encode a numpy float32 array as a 16-bit PCM WAV in-memory.

    Float samples in roughly [-1, 1] occasionally exceed range; clip before
    int16 cast to prevent overflow wrap-around (which produces audible clicks).
    """
    import numpy as np

    pcm = np.clip(samples * 32767.0, -32768.0, 32767.0).astype(np.int16)
    buf = io.BytesIO()
    num_samples = pcm.shape[0]
    data_size = num_samples * 2  # 16-bit = 2 bytes per sample
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm.tobytes())
    return buf.getvalue()


# ── Stream framing (v2) ───────────────────────────────────────────────────────


def _frame(tag: int, payload: bytes) -> bytes:
    return struct.pack(">BI", tag, len(payload)) + payload


def frame_chunk(wav_bytes: bytes) -> bytes:
    return _frame(STREAM_TAG_CHUNK, wav_bytes)


def frame_end() -> bytes:
    return _frame(STREAM_TAG_END, b"")


def frame_error(code: str, message: str) -> bytes:
    payload = json.dumps({"code": code, "message": message}).encode("utf-8")
    return _frame(STREAM_TAG_ERROR, payload)


# ── Service ───────────────────────────────────────────────────────────────────


class TtsService:
    """Singleton service wrapping the Kokoro ONNX TTS model."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kokoro: Any = None
        self._model_loaded = False
        self._is_downloading = False
        self._download_progress: float = 0.0
        self._event_loop: asyncio.AbstractEventLoop | None = None

        # Embedding caches
        self._voices_npz: Any = None  # numpy NpzFile, opened lazily
        self._builtin_emb_cache: dict[str, Any] = {}
        self._custom_emb_cache: dict[str, tuple[float, Any]] = {}  # vid → (mtime, ndarray)

    # ── Model presence / status ───────────────────────────────────────────

    def _file_ok(self, path, expected_size: int) -> bool:
        return path.is_file() and path.stat().st_size == expected_size

    @property
    def model_downloaded(self) -> bool:
        return (
            self._file_ok(TTS_DIR / ONNX_MODEL_FILENAME, ONNX_MODEL_SIZE_BYTES)
            and self._file_ok(TTS_DIR / VOICES_BIN_FILENAME, VOICES_BIN_SIZE_BYTES)
        )

    @property
    def model_loaded(self) -> bool:
        return self._model_loaded

    def get_status(self) -> dict[str, Any]:
        return {
            "model_downloaded": self.model_downloaded,
            "model_loaded": self._model_loaded,
            "is_downloading": self._is_downloading,
            "download_progress": self._download_progress,
            "model_dir": str(TTS_DIR),
            "voice_count": len(BUILTIN_VOICES),
        }

    def list_voices(self) -> list[dict[str, Any]]:
        voices: list[dict[str, Any]] = []
        for v in BUILTIN_VOICES:
            voices.append({
                "voice_id": v.voice_id,
                "name": v.name,
                "gender": v.gender,
                "language": v.language,
                "lang_code": v.lang_code,
                "quality_grade": v.quality_grade,
                "traits": v.traits,
                "is_custom": v.is_custom,
                "is_default": v.is_default,
            })
        voices.extend(self._load_custom_voices())
        return voices

    def _load_custom_voices(self) -> list[dict[str, Any]]:
        """Scan CUSTOM_VOICES_DIR for .npy and legacy .bin voices."""
        if not CUSTOM_VOICES_DIR.is_dir():
            return []
        result: list[dict[str, Any]] = []
        seen: set[str] = set()

        for f in sorted(CUSTOM_VOICES_DIR.glob("*.npy")):
            vid = f.stem
            seen.add(vid)
            meta = self._read_voice_meta(vid)
            result.append({
                "voice_id": vid,
                "name": meta.get("name", vid.replace("_", " ").title()),
                "gender": meta.get("gender", "female"),
                "language": meta.get("language", "Custom"),
                "lang_code": meta.get("lang_code", "a"),
                "quality_grade": "Custom",
                "traits": meta.get("traits", ["blended"]),
                "is_custom": True,
                "is_default": False,
                "blend_recipe": meta.get("blend_recipe", []),
            })

        for f in sorted(CUSTOM_VOICES_DIR.glob("*.bin")):
            vid = f.stem
            if vid in seen:
                continue
            result.append({
                "voice_id": vid,
                "name": vid.replace("_", " ").title(),
                "gender": "female",
                "language": "Custom",
                "lang_code": "a",
                "quality_grade": "Custom",
                "traits": ["custom"],
                "is_custom": True,
                "is_default": False,
                "blend_recipe": [],
            })
        return result

    def _read_voice_meta(self, voice_id: str) -> dict[str, Any]:
        meta_path = CUSTOM_VOICES_DIR / f"{voice_id}.json"
        if meta_path.is_file():
            try:
                return json.loads(meta_path.read_text())
            except Exception as exc:
                logger.warning("[tts] meta read failed for %s: %s", voice_id, exc)
        return {}

    def _write_voice_meta(self, voice_id: str, meta: dict[str, Any]) -> None:
        CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
        (CUSTOM_VOICES_DIR / f"{voice_id}.json").write_text(json.dumps(meta, indent=2))

    # ── Download ──────────────────────────────────────────────────────────

    async def download_model(self) -> dict[str, Any]:
        """Download both files. Idempotent and race-safe."""
        if self.model_downloaded:
            return {"success": True, "already_downloaded": True}

        # Acquire the in-progress flag synchronously *before* scheduling the
        # executor task, otherwise two concurrent callers can both pass the
        # guard and both spawn a download.
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._is_downloading:
                waiter = getattr(self, "_download_future", None)
            else:
                self._is_downloading = True
                self._download_progress = 0.0
                waiter = None
                self._download_future = loop.create_future()

        if waiter is not None:
            # A concurrent cold-start: wait for the in-flight download instead
            # of surfacing a spurious "in_progress" error to the second caller
            # (synthesize() treated that as a failure).
            try:
                return dict(await asyncio.shield(waiter))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return {"success": False, "error": str(exc),
                        "error_code": "download_failed"}

        self._event_loop = loop  # captured for thread-pool → loop dispatch
        try:
            result = await loop.run_in_executor(None, self._download_model_sync)
        except BaseException as exc:
            if not self._download_future.done():
                self._download_future.set_result(
                    {"success": False, "error": str(exc),
                     "error_code": "download_failed"}
                )
            with self._lock:
                self._is_downloading = False
            raise
        else:
            if not self._download_future.done():
                self._download_future.set_result(result)
            with self._lock:
                self._is_downloading = False
            return result

    def _emit_dm_progress(
        self,
        dl_id: str,
        display_name: str,
        status: str,
        bytes_done: int,
        total_bytes: int,
        speed_bps: float = 0.0,
    ) -> None:
        """Emit a progress event to the universal download manager (best-effort)."""
        from datetime import datetime, timezone

        try:
            from app.services.downloads.manager import get_download_manager, ProgressEvent
            dm = get_download_manager()
            updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            async def _push() -> None:
                percent = (bytes_done / total_bytes * 100) if total_bytes > 0 else 0.0
                remaining = max(0, total_bytes - bytes_done)
                eta: float | None = (remaining / speed_bps) if (speed_bps > 0 and remaining > 0) else None
                evt = ProgressEvent(
                    id=dl_id,
                    category="tts",
                    filename="kokoro-82m.onnx",
                    display_name=display_name,
                    status=status,
                    bytes_done=bytes_done,
                    total_bytes=total_bytes,
                    percent=percent,
                    part_current=1,
                    part_total=1,
                    speed_bps=speed_bps,
                    eta_seconds=eta,
                    updated_at=updated_at,
                    bandwidth_bps=speed_bps,
                )
                await dm._broadcast(evt)

            loop = self._event_loop
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(_push(), loop)
        except Exception as exc:
            logger.debug("[tts] download progress emit failed: %s", exc)

    def _sha256_of_file(self, path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()

    def _verify_file(self, path, expected_size: int, expected_sha: str) -> None:
        size = path.stat().st_size
        if size != expected_size:
            raise TtsError(
                "size_mismatch",
                f"{path.name}: expected {expected_size} bytes, got {size}",
            )
        actual = self._sha256_of_file(path)
        if actual.lower() != expected_sha.lower():
            raise TtsError(
                "sha_mismatch",
                f"{path.name}: SHA-256 mismatch (expected {expected_sha[:16]}…, got {actual[:16]}…)",
            )

    def _download_model_sync(self) -> dict[str, Any]:
        import httpx
        import time as _time

        TTS_DIR.mkdir(parents=True, exist_ok=True)

        # Sweep stale .tmp files left over from a crashed previous attempt.
        for stale in TTS_DIR.glob("*.tmp"):
            try:
                stale.unlink()
                logger.info("[tts] swept stale tmp: %s", stale.name)
            except Exception:
                pass

        total_bytes = ONNX_MODEL_SIZE_BYTES + VOICES_BIN_SIZE_BYTES
        downloaded = 0
        dl_id = "tts-kokoro-model"
        self._emit_dm_progress(dl_id, "Kokoro TTS Model", "active", 0, total_bytes)

        try:
            for url, filename, expected_size, expected_sha in [
                (ONNX_MODEL_URL, ONNX_MODEL_FILENAME, ONNX_MODEL_SIZE_BYTES, ONNX_MODEL_SHA256),
                (VOICES_BIN_URL, VOICES_BIN_FILENAME, VOICES_BIN_SIZE_BYTES, VOICES_BIN_SHA256),
            ]:
                dest = TTS_DIR / filename
                if dest.is_file() and dest.stat().st_size == expected_size:
                    # Also verify SHA — silent corruption check.
                    try:
                        self._verify_file(dest, expected_size, expected_sha)
                        downloaded += expected_size
                        self._download_progress = (downloaded / total_bytes) * 100
                        self._emit_dm_progress(dl_id, "Kokoro TTS Model", "active", downloaded, total_bytes)
                        logger.info("[tts] %s already verified, skipping", filename)
                        continue
                    except TtsError as exc:
                        logger.warning("[tts] %s present but corrupt (%s); re-downloading",
                                       filename, exc.message)
                        dest.unlink()

                logger.info("[tts] Downloading %s from %s", filename, url)
                tmp = dest.with_suffix(".tmp")

                _speed_samples: list[tuple[float, int]] = []
                _SPEED_WINDOW = 10

                def _calc_speed() -> float:
                    if len(_speed_samples) < 2:
                        return 0.0
                    dt = _speed_samples[-1][0] - _speed_samples[0][0]
                    if dt <= 0:
                        return 0.0
                    db = _speed_samples[-1][1] - _speed_samples[0][1]
                    return max(0.0, db / dt)

                with httpx.stream("GET", url, follow_redirects=True, timeout=300) as resp:
                    resp.raise_for_status()
                    last_emit = 0
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1_048_576):
                            f.write(chunk)
                            downloaded += len(chunk)
                            self._download_progress = (downloaded / total_bytes) * 100

                            _speed_samples.append((_time.monotonic(), downloaded))
                            if len(_speed_samples) > _SPEED_WINDOW:
                                _speed_samples.pop(0)

                            if downloaded - last_emit >= 2 * 1024 * 1024:
                                last_emit = downloaded
                                self._emit_dm_progress(
                                    dl_id, "Kokoro TTS Model", "active",
                                    downloaded, total_bytes,
                                    speed_bps=_calc_speed(),
                                )

                shutil.move(str(tmp), str(dest))
                logger.info("[tts] Downloaded %s (%.1f MB)", filename, dest.stat().st_size / 1_048_576)

                # Verify exactly here. If hash fails the next call will re-download.
                try:
                    self._verify_file(dest, expected_size, expected_sha)
                except TtsError as exc:
                    dest.unlink(missing_ok=True)
                    raise

            self._download_progress = 100.0
            self._emit_dm_progress(dl_id, "Kokoro TTS Model", "completed", total_bytes, total_bytes)
            return {"success": True}

        except TtsError as exc:
            logger.error("[tts] verification failed: %s", exc.message)
            self._emit_dm_progress(dl_id, "Kokoro TTS Model", "failed", downloaded, total_bytes)
            return {"success": False, "error": exc.message, "error_code": exc.code}
        except Exception as exc:
            logger.error("[tts] Model download failed: %s", exc, exc_info=True)
            self._emit_dm_progress(dl_id, "Kokoro TTS Model", "failed", downloaded, total_bytes)
            return {"success": False, "error": str(exc), "error_code": "download_failed"}

    # ── Load / unload ─────────────────────────────────────────────────────

    async def ensure_loaded(self) -> dict[str, Any]:
        """Ensure model is downloaded and loaded.

        On load failure (e.g. corrupt model on disk that passed size check),
        delete both files and trigger one re-download retry.
        """
        if self._model_loaded and self._kokoro is not None:
            return {"success": True, "already_loaded": True}

        if not self.model_downloaded:
            dl = await self.download_model()
            if not dl.get("success"):
                return dl

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._load_model_sync)
        if result.get("success"):
            return result

        # Self-heal: delete files and retry once — but ONLY for failures that
        # re-downloading can plausibly fix. Environmental errors (ImportError
        # in a frozen build, OOM) previously triggered a delete + 330 MB
        # download + fail cycle on every synth call.
        if not result.get("retriable_by_redownload", True):
            logger.warning(
                "[tts] load failed (%s) — not a file problem; skipping re-download",
                result.get("error"),
            )
            return result

        logger.warning("[tts] load failed (%s); deleting files and re-downloading",
                       result.get("error"))
        for fn in (ONNX_MODEL_FILENAME, VOICES_BIN_FILENAME):
            (TTS_DIR / fn).unlink(missing_ok=True)
        dl = await self.download_model()
        if not dl.get("success"):
            return dl
        return await loop.run_in_executor(None, self._load_model_sync)

    def _load_model_sync(self) -> dict[str, Any]:
        with self._lock:
            if self._model_loaded and self._kokoro is not None:
                return {"success": True, "already_loaded": True}
            try:
                from kokoro_onnx import Kokoro

                model_path = str(TTS_DIR / ONNX_MODEL_FILENAME)
                voices_path = str(TTS_DIR / VOICES_BIN_FILENAME)

                logger.info("[tts] Loading Kokoro model from %s", model_path)
                t0 = time.monotonic()
                self._kokoro = Kokoro(model_path, voices_path)
                elapsed = time.monotonic() - t0
                self._model_loaded = True
                logger.info("[tts] Model loaded in %.1fs", elapsed)
                return {"success": True, "load_time_seconds": elapsed}

            except Exception as exc:
                logger.error("[tts] Failed to load model: %s", exc, exc_info=True)
                self._kokoro = None
                self._model_loaded = False
                return {
                    "success": False,
                    "error": str(exc),
                    "error_code": "load_failed",
                    "error_type": type(exc).__name__,
                    # Environmental failures (missing package, OOM, provider
                    # init) won't be fixed by re-downloading the files.
                    "retriable_by_redownload": not isinstance(
                        exc, (ImportError, ModuleNotFoundError, MemoryError)
                    ),
                }

    async def unload(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._unload_sync)
        return {"success": True}

    def _unload_sync(self) -> None:
        with self._lock:
            self._kokoro = None
            self._model_loaded = False
            self._voices_npz = None
            self._builtin_emb_cache.clear()
            self._custom_emb_cache.clear()
            logger.info("[tts] Model unloaded and caches cleared")

    # ── Voice resolution ──────────────────────────────────────────────────

    def _resolve_voice_arg(self, voice_id: str):
        """Return ``(voice_arg, lang_code)``.

        ``voice_arg`` is a string ID for builtin voices (kokoro looks it up in
        its loaded NpzFile) or a numpy array for custom .npy / legacy .bin
        voices. Returns ``(None, "en-us")`` when the voice cannot be resolved.
        """
        # Builtin
        voice_info = VOICE_MAP.get(voice_id)
        if voice_info:
            lmeta = LANGUAGE_MAP.get(voice_info.lang_code)
            return voice_id, (lmeta.espeak_fallback if lmeta else "en-us")

        # Custom .npy
        npy_path = CUSTOM_VOICES_DIR / f"{voice_id}.npy"
        if npy_path.is_file():
            emb = self._load_custom_embedding(voice_id, npy_path)
            meta = self._read_voice_meta(voice_id)
            lang_code = meta.get("lang_code", "a")
            lmeta = LANGUAGE_MAP.get(lang_code)
            return emb, (lmeta.espeak_fallback if lmeta else "en-us")

        # Legacy .bin — load the bytes directly into a numpy array (the prior
        # implementation returned the string ID, which kokoro then failed to
        # resolve from its NpzFile).
        bin_path = CUSTOM_VOICES_DIR / f"{voice_id}.bin"
        if bin_path.is_file():
            try:
                emb = self._load_custom_embedding(voice_id, bin_path)
            except Exception as exc:
                logger.error("[tts] legacy .bin load failed for %s: %s", voice_id, exc)
                return None, "en-us"
            return emb, "en-us"

        return None, "en-us"

    def _load_custom_embedding(self, voice_id: str, path):
        """Load a custom voice embedding with mtime-based caching."""
        import numpy as np

        mtime = path.stat().st_mtime
        cached = self._custom_emb_cache.get(voice_id)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        if path.suffix == ".bin":
            # Some .bin files are NpzFile, others are raw .npy
            try:
                emb = np.load(str(path), allow_pickle=False)
            except Exception:
                npz = np.load(str(path))
                keys = list(npz.files)
                emb = npz[keys[0]] if keys else None
                if emb is None:
                    raise TtsError("voice_load_failed", f"Empty .bin: {voice_id}")
        else:
            emb = np.load(str(path), allow_pickle=False)

        self._custom_emb_cache[voice_id] = (mtime, emb)
        return emb

    def _builtin_embedding(self, voice_id: str):
        """Return the raw numpy embedding for a builtin voice with caching."""
        import numpy as np

        if voice_id in self._builtin_emb_cache:
            return self._builtin_emb_cache[voice_id]

        with self._lock:
            if self._voices_npz is None:
                voices_path = TTS_DIR / VOICES_BIN_FILENAME
                if not voices_path.is_file():
                    raise TtsError("model_missing", "voices-v1.0.bin not found — download the model first")
                self._voices_npz = np.load(str(voices_path))

        if voice_id not in self._voices_npz:
            raise TtsError("voice_not_found", f"Voice '{voice_id}' not in builtin pack")
        emb = self._voices_npz[voice_id].copy()
        self._builtin_emb_cache[voice_id] = emb
        return emb

    def _resolve_embedding(self, voice_id: str):
        """Get a voice embedding by ID — tries builtin then custom."""
        if voice_id in VOICE_MAP:
            return self._builtin_embedding(voice_id)
        npy_path = CUSTOM_VOICES_DIR / f"{voice_id}.npy"
        if npy_path.is_file():
            return self._load_custom_embedding(voice_id, npy_path)
        bin_path = CUSTOM_VOICES_DIR / f"{voice_id}.bin"
        if bin_path.is_file():
            return self._load_custom_embedding(voice_id, bin_path)
        raise TtsError("voice_not_found", f"Voice '{voice_id}' not found")

    # ── Synthesis (single-shot) ───────────────────────────────────────────

    def _validate_text(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            raise TtsError("empty_text", "Text is empty after stripping whitespace")
        return stripped

    async def synthesize(
        self,
        text: str,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: float = 1.0,
        lang: str | None = None,
    ) -> SynthesisResult:
        """Synthesize a single complete WAV. Used for short text and previews."""
        try:
            text = self._validate_text(text)
        except TtsError as exc:
            return SynthesisResult(success=False, error=exc.message, error_code=exc.code)

        load_result = await self.ensure_loaded()
        if not load_result.get("success"):
            return SynthesisResult(
                success=False,
                error=load_result.get("error", "Failed to load model"),
                error_code=load_result.get("error_code", "load_failed"),
            )

        voice_arg, resolved_lang = self._resolve_voice_arg(voice_id)
        if voice_arg is None:
            return SynthesisResult(success=False,
                                   error=f"Unknown voice: {voice_id}",
                                   error_code="voice_not_found")
        if lang is not None:
            resolved_lang = lang

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._synthesize_sync, text, voice_arg, speed, resolved_lang, voice_id,
        )

    def _synthesize_sync(
        self,
        text: str,
        voice_arg: Any,
        speed: float,
        lang: str,
        voice_id: str = "",
    ) -> SynthesisResult:
        with self._lock:
            kokoro = self._kokoro
        if kokoro is None:
            return SynthesisResult(success=False, error="Model not loaded",
                                   error_code="not_loaded")

        try:
            t0 = time.monotonic()
            samples, sample_rate = kokoro.create(text, voice=voice_arg, speed=speed, lang=lang)
            if samples is None or len(samples) == 0:
                return SynthesisResult(
                    success=False,
                    error="Model returned empty audio",
                    error_code="empty_audio",
                )
            audio_bytes = _wav_bytes(samples, sample_rate)
            duration = len(samples) / sample_rate
            elapsed = time.monotonic() - t0
            logger.info(
                "[tts] Synthesized %.1fs audio in %.2fs (%.1fx) voice=%s",
                duration, elapsed, duration / max(elapsed, 0.001), voice_id,
            )
            return SynthesisResult(
                success=True, audio_bytes=audio_bytes, sample_rate=sample_rate,
                duration_seconds=duration, voice_id=voice_id, elapsed_seconds=elapsed,
            )
        except Exception as exc:
            logger.error("[tts] Synthesis failed: %s", exc, exc_info=True)
            return SynthesisResult(success=False, error=str(exc), error_code="synthesis_failed")

    # ── Synthesis (streaming, framed v2) ──────────────────────────────────

    async def synthesize_stream(
        self,
        text: str,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: float = 1.0,
        lang: str | None = None,
        is_disconnected=None,
    ) -> AsyncIterator[bytes]:
        """Yield framed v2 bytes for the streaming endpoint.

        Hybrid strategy:
          - Text shorter than ``STREAM_THRESHOLD_CHARS`` → one ``create()`` call,
            one CHUNK frame, one END frame.
          - Longer text → ``kokoro.create_stream()`` wrapped with per-chunk
            watchdog and explicit error capture.

        Always emits exactly one terminating frame: END on success, ERROR on
        failure. The client can rely on this to detect truncation.

        ``is_disconnected``: optional async callable; if it returns ``True``
        between chunks we abort the stream (no frames yielded after that).
        """
        # Validate text first — we want a clean error frame, not a hang.
        try:
            text = self._validate_text(text)
        except TtsError as exc:
            yield frame_error(exc.code, exc.message)
            yield frame_end()
            return

        load_result = await self.ensure_loaded()
        if not load_result.get("success"):
            yield frame_error(
                load_result.get("error_code", "load_failed"),
                load_result.get("error", "Failed to load model"),
            )
            yield frame_end()
            return

        voice_arg, resolved_lang = self._resolve_voice_arg(voice_id)
        if voice_arg is None:
            yield frame_error("voice_not_found", f"Unknown voice: {voice_id}")
            yield frame_end()
            return
        if lang is not None:
            resolved_lang = lang

        with self._lock:
            kokoro = self._kokoro
        if kokoro is None:
            yield frame_error("not_loaded", "Model not loaded")
            yield frame_end()
            return

        async def _check_disconnect() -> bool:
            if is_disconnected is None:
                return False
            try:
                return bool(await is_disconnected())
            except Exception:
                return False

        # ── Short path: single create() call ──────────────────────────────
        if len(text) < STREAM_THRESHOLD_CHARS:
            loop = asyncio.get_running_loop()
            try:
                samples, sr = await loop.run_in_executor(
                    None,
                    lambda: kokoro.create(text, voice=voice_arg, speed=speed, lang=resolved_lang),
                )
            except Exception as exc:
                logger.error("[tts] create() failed: %s", exc, exc_info=True)
                yield frame_error("synthesis_failed", str(exc))
                yield frame_end()
                return

            if samples is None or len(samples) == 0:
                yield frame_error("empty_audio", "Model returned empty audio")
                yield frame_end()
                return

            yield frame_chunk(_wav_bytes(samples, sr))
            yield frame_end()
            return

        # ── Long path: native phoneme-batch streaming with watchdog ───────
        chunk_index = 0
        try:
            stream_iter = kokoro.create_stream(
                text=text, voice=voice_arg, speed=speed, lang=resolved_lang,
            ).__aiter__()
            while True:
                if await _check_disconnect():
                    logger.info("[tts] client disconnected after %d chunks", chunk_index)
                    return  # client gone — frames go nowhere; just stop
                try:
                    audio_chunk, sample_rate = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=STREAM_CHUNK_TIMEOUT_SECONDS,
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.error("[tts] stream chunk %d timed out after %.0fs",
                                 chunk_index, STREAM_CHUNK_TIMEOUT_SECONDS)
                    yield frame_error("chunk_timeout",
                                      f"Synthesis stalled after {STREAM_CHUNK_TIMEOUT_SECONDS}s")
                    yield frame_end()
                    return

                if audio_chunk is None or len(audio_chunk) == 0:
                    continue
                wav = _wav_bytes(audio_chunk, sample_rate)
                duration = len(audio_chunk) / sample_rate
                logger.debug("[tts] stream chunk %d: %.2fs audio, %d bytes WAV",
                             chunk_index, duration, len(wav))
                chunk_index += 1
                yield frame_chunk(wav)

            yield frame_end()
            logger.info("[tts] stream completed cleanly: %d chunks", chunk_index)

        except Exception as exc:
            logger.error("[tts] stream synthesis error at chunk %d: %s",
                         chunk_index, exc, exc_info=True)
            yield frame_error("stream_failed", str(exc))
            yield frame_end()

    # ── Voice blending & custom voices ────────────────────────────────────

    def blend_voices_sync(
        self,
        components: list[dict[str, Any]],
    ):
        """Compute a weighted blend of voice embeddings (weights normalised)."""
        import numpy as np

        if not components:
            raise TtsError("invalid_blend", "Need at least one component voice")

        embeddings = []
        weights = []
        for c in components:
            vid = c["voice_id"]
            w = float(c.get("weight", 1.0))
            if w <= 0:
                continue
            embeddings.append(self._resolve_embedding(vid))
            weights.append(w)

        if not embeddings:
            raise TtsError("invalid_blend", "All component weights are zero")

        total = sum(weights)
        weights = [w / total for w in weights]
        blended = sum(e * w for e, w in zip(embeddings, weights))
        return blended.astype(np.float32)

    async def blend_and_preview(
        self,
        components: list[dict[str, Any]],
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> SynthesisResult:
        load_result = await self.ensure_loaded()
        if not load_result.get("success"):
            return SynthesisResult(success=False,
                                   error=load_result.get("error", "Model load failed"),
                                   error_code=load_result.get("error_code", "load_failed"))
        loop = asyncio.get_running_loop()
        try:
            blended_emb = await loop.run_in_executor(None, self.blend_voices_sync, components)
        except TtsError as exc:
            return SynthesisResult(success=False, error=exc.message, error_code=exc.code)
        except Exception as exc:
            return SynthesisResult(success=False, error=str(exc), error_code="blend_failed")

        with self._lock:
            kokoro = self._kokoro
        if kokoro is None:
            return SynthesisResult(success=False, error="Model not loaded",
                                   error_code="not_loaded")

        try:
            samples, sr = await loop.run_in_executor(
                None,
                lambda: kokoro.create(PREVIEW_TEXT, voice=blended_emb, speed=speed, lang=lang),
            )
            if samples is None or len(samples) == 0:
                return SynthesisResult(success=False, error="Empty audio returned",
                                       error_code="empty_audio")
            return SynthesisResult(
                success=True, audio_bytes=_wav_bytes(samples, sr), sample_rate=sr,
                duration_seconds=len(samples) / sr, voice_id="blend-preview",
            )
        except Exception as exc:
            return SynthesisResult(success=False, error=str(exc), error_code="synthesis_failed")

    async def save_blended_voice(
        self,
        voice_id: str,
        name: str,
        components: list[dict[str, Any]],
        gender: str = "female",
        lang_code: str = "a",
    ) -> dict[str, Any]:
        voice_id = _sanitize_voice_id(voice_id)
        if not voice_id:
            return {"success": False, "error": "Invalid voice ID", "error_code": "invalid_id"}

        loop = asyncio.get_running_loop()
        try:
            blended_emb = await loop.run_in_executor(None, self.blend_voices_sync, components)
        except TtsError as exc:
            return {"success": False, "error": exc.message, "error_code": exc.code}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_code": "blend_failed"}

        CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
        import numpy as np
        npy_path = CUSTOM_VOICES_DIR / f"{voice_id}.npy"
        np.save(str(npy_path), blended_emb)
        # Invalidate cache so the new file is picked up on next resolve
        self._custom_emb_cache.pop(voice_id, None)

        meta = {
            "name": name, "gender": gender, "language": "Custom",
            "lang_code": lang_code, "traits": ["blended"], "blend_recipe": components,
        }
        self._write_voice_meta(voice_id, meta)
        logger.info("[tts] Saved blended voice '%s' (%d components)", voice_id, len(components))
        return {"success": True, "voice_id": voice_id}

    async def import_voice_file(
        self,
        voice_id: str,
        name: str,
        data: bytes,
        file_ext: str,
        gender: str = "female",
        lang_code: str = "a",
    ) -> dict[str, Any]:
        """Import a voice embedding from uploaded bytes (.npy or .bin format).

        Validates the embedding has the exact shape Kokoro expects (510, 1, 256)
        before saving so a bad file fails on upload, not on first synthesis.
        """
        import numpy as np

        voice_id = _sanitize_voice_id(voice_id)
        if not voice_id:
            return {"success": False, "error": "Invalid voice ID", "error_code": "invalid_id"}

        CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)

        try:
            buf = io.BytesIO(data)
            if file_ext == ".npy":
                emb = np.load(buf, allow_pickle=False)
            elif file_ext in (".bin", ".npz"):
                npz = np.load(buf)
                keys = list(npz.files)
                if len(keys) == 1:
                    emb = npz[keys[0]]
                elif voice_id in npz:
                    emb = npz[voice_id]
                else:
                    emb = npz[keys[0]]
            else:
                return {"success": False,
                        "error": f"Unsupported file format: {file_ext}",
                        "error_code": "bad_format"}

            if tuple(emb.shape) != EMBEDDING_SHAPE:
                return {
                    "success": False,
                    "error": f"Embedding shape {tuple(emb.shape)}; expected {EMBEDDING_SHAPE}",
                    "error_code": "bad_shape",
                }

            npy_path = CUSTOM_VOICES_DIR / f"{voice_id}.npy"
            np.save(str(npy_path), emb.astype(np.float32))
            self._custom_emb_cache.pop(voice_id, None)

            meta = {
                "name": name, "gender": gender, "language": "Custom",
                "lang_code": lang_code, "traits": ["imported"], "blend_recipe": [],
            }
            self._write_voice_meta(voice_id, meta)
            logger.info("[tts] Imported voice '%s' from %d bytes", voice_id, len(data))
            return {"success": True, "voice_id": voice_id}

        except Exception as exc:
            logger.error("[tts] Voice import failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc), "error_code": "import_failed"}

    async def rename_custom_voice(self, voice_id: str, new_name: str) -> dict[str, Any]:
        npy_path = CUSTOM_VOICES_DIR / f"{voice_id}.npy"
        bin_path = CUSTOM_VOICES_DIR / f"{voice_id}.bin"
        if not npy_path.is_file() and not bin_path.is_file():
            return {"success": False,
                    "error": f"Custom voice '{voice_id}' not found",
                    "error_code": "voice_not_found"}
        meta = self._read_voice_meta(voice_id)
        meta["name"] = new_name
        self._write_voice_meta(voice_id, meta)
        return {"success": True}

    async def delete_custom_voice(self, voice_id: str) -> dict[str, Any]:
        deleted = False
        for ext in (".npy", ".bin", ".json"):
            p = CUSTOM_VOICES_DIR / f"{voice_id}{ext}"
            if p.is_file():
                p.unlink()
                deleted = True
        if not deleted:
            return {"success": False,
                    "error": f"Custom voice '{voice_id}' not found",
                    "error_code": "voice_not_found"}
        self._custom_emb_cache.pop(voice_id, None)
        logger.info("[tts] Deleted custom voice '%s'", voice_id)
        return {"success": True}

    async def synthesize_with_blend(
        self,
        text: str,
        components: list[dict[str, Any]],
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> SynthesisResult:
        try:
            text = self._validate_text(text)
        except TtsError as exc:
            return SynthesisResult(success=False, error=exc.message, error_code=exc.code)

        load_result = await self.ensure_loaded()
        if not load_result.get("success"):
            return SynthesisResult(success=False,
                                   error=load_result.get("error", "Model load failed"),
                                   error_code=load_result.get("error_code", "load_failed"))
        loop = asyncio.get_running_loop()
        try:
            blended_emb = await loop.run_in_executor(None, self.blend_voices_sync, components)
        except TtsError as exc:
            return SynthesisResult(success=False, error=exc.message, error_code=exc.code)
        except Exception as exc:
            return SynthesisResult(success=False, error=str(exc), error_code="blend_failed")

        with self._lock:
            kokoro = self._kokoro
        if kokoro is None:
            return SynthesisResult(success=False, error="Model not loaded",
                                   error_code="not_loaded")

        t0 = time.monotonic()
        try:
            samples, sr = await loop.run_in_executor(
                None, lambda: kokoro.create(text, voice=blended_emb, speed=speed, lang=lang),
            )
            if samples is None or len(samples) == 0:
                return SynthesisResult(success=False, error="Empty audio returned",
                                       error_code="empty_audio")
            return SynthesisResult(
                success=True, audio_bytes=_wav_bytes(samples, sr), sample_rate=sr,
                duration_seconds=len(samples) / sr, voice_id="blend",
                elapsed_seconds=time.monotonic() - t0,
            )
        except Exception as exc:
            return SynthesisResult(success=False, error=str(exc), error_code="synthesis_failed")

    async def preview_voice(self, voice_id: str) -> SynthesisResult:
        return await self.synthesize(text=PREVIEW_TEXT, voice_id=voice_id, speed=1.0)


# ── Singleton ────────────────────────────────────────────────────────────────

_service: TtsService | None = None
_service_lock = threading.Lock()


def get_tts_service() -> TtsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = TtsService()
    return _service
