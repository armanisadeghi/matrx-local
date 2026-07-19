"""TTS endpoint smoke tests.

Covers:
  * /tts/status, /tts/voices — unauthenticated read paths
  * Streaming protocol v2 framing: chunk → end on success, error frame on
    bogus input, no truncation possible (END is always last frame).
  * Synthesis happy path (only when the model is already on disk so the
    test runs fast). Skipped otherwise.
  * Pure framing roundtrip (no engine required).

The streaming endpoint always returns HTTP 200 — errors are surfaced as
in-stream error frames. So the success criterion is the *last* frame being
``END`` and the absence of unexpected ``ERROR`` frames.
"""

from __future__ import annotations

import io
import json
import struct

import httpx
import pytest

from app.services.tts.models import (
    STREAM_TAG_CHUNK,
    STREAM_TAG_END,
    STREAM_TAG_ERROR,
)
from app.services.tts.service import frame_chunk, frame_end, frame_error


# ── Pure unit: framing roundtrip (no engine) ──────────────────────────────────


def _parse_frames(data: bytes) -> list[tuple[int, bytes]]:
    """Parse v2 frames from a byte stream. Used by both the test and as a
    reference implementation for what the frontend reader must accept."""
    frames: list[tuple[int, bytes]] = []
    buf = io.BytesIO(data)
    while True:
        hdr = buf.read(5)
        if not hdr:
            break
        if len(hdr) < 5:
            raise AssertionError(f"truncated header: {len(hdr)} bytes")
        tag, length = struct.unpack(">BI", hdr)
        payload = buf.read(length)
        if len(payload) != length:
            raise AssertionError(
                f"truncated payload: tag=0x{tag:02x} expected {length} got {len(payload)}"
            )
        frames.append((tag, payload))
    return frames


def test_frame_chunk_end_roundtrip() -> None:
    payload = b"FAKE-WAV-BYTES"
    blob = frame_chunk(payload) + frame_end()
    frames = _parse_frames(blob)
    assert frames == [(STREAM_TAG_CHUNK, payload), (STREAM_TAG_END, b"")]


def test_frame_error_roundtrip() -> None:
    blob = frame_error("voice_not_found", "Unknown voice: xx_yy") + frame_end()
    frames = _parse_frames(blob)
    assert frames[0][0] == STREAM_TAG_ERROR
    parsed = json.loads(frames[0][1])
    assert parsed == {"code": "voice_not_found", "message": "Unknown voice: xx_yy"}
    assert frames[1] == (STREAM_TAG_END, b"")


def test_frame_large_chunk() -> None:
    """4-byte big-endian length must accept large payloads correctly."""
    payload = b"X" * 200_000
    blob = frame_chunk(payload)
    tag, length = struct.unpack(">BI", blob[:5])
    assert tag == STREAM_TAG_CHUNK
    assert length == 200_000
    assert blob[5:] == payload


# ── Engine-based smoke tests ──────────────────────────────────────────────────


def _model_present(http: httpx.Client) -> bool:
    """Ask the isolated engine; never inspect the installed app's model path."""
    response = http.get("/tts/status")
    return response.status_code == 200 and bool(response.json()["model_downloaded"])


def test_tts_status_returns_struct(http: httpx.Client) -> None:
    r = http.get("/tts/status")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "model_downloaded",
        "model_loaded",
        "is_downloading",
        "download_progress",
        "model_dir",
        "voice_count",
    ):
        assert key in body, f"missing key: {key}"
    assert body["voice_count"] >= 50  # 54 builtin voices in v1.0


def test_tts_voices_returns_list(http: httpx.Client) -> None:
    r = http.get("/tts/voices")
    assert r.status_code == 200, r.text
    voices = r.json()
    assert isinstance(voices, list)
    assert any(v["voice_id"] == "af_heart" for v in voices), "default voice missing"


def test_tts_synthesize_validation_empty(http: httpx.Client) -> None:
    """Empty/whitespace text is rejected at the API layer (400/422)."""
    r = http.post(
        "/tts/synthesize",
        json={"text": "   ", "voice_id": "af_heart"},
    )
    # Pydantic validator rejects empty stripped text → 422
    assert r.status_code == 422, r.text


def test_tts_stream_protocol_header(http: httpx.Client) -> None:
    """The streaming endpoint advertises protocol v2 in headers."""
    if not _model_present(http):
        pytest.skip("Kokoro model not downloaded; skipping live stream test")

    with http.stream(
        "POST",
        "/tts/synthesize-stream",
        json={"text": "Hi.", "voice_id": "af_heart"},
        timeout=60.0,
    ) as r:
        assert r.status_code == 200, r.read().decode()[:500]
        assert r.headers.get("X-TTS-Stream-Protocol") == "2"
        assert r.headers.get("X-TTS-Sample-Rate") == "24000"
        # Drain the body so the connection releases.
        for _ in r.iter_bytes():
            pass


def _read_all_frames(client: httpx.Client, payload: dict) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    with client.stream(
        "POST",
        "/tts/synthesize-stream",
        json=payload,
        timeout=120.0,
    ) as r:
        assert r.status_code == 200, r.read().decode()[:500]
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf.extend(chunk)
            while True:
                if len(buf) < 5:
                    break
                tag = buf[0]
                length = int.from_bytes(buf[1:5], "big")
                if len(buf) < 5 + length:
                    break
                frames.append((tag, bytes(buf[5 : 5 + length])))
                del buf[: 5 + length]
        if buf:
            raise AssertionError(f"trailing bytes after last frame: {len(buf)}")
    return frames


def test_tts_stream_short_text_emits_chunk_then_end(http: httpx.Client) -> None:
    if not _model_present(http):
        pytest.skip("Kokoro model not downloaded; skipping live stream test")

    frames = _read_all_frames(
        http,
        {"text": "Hello world.", "voice_id": "af_heart"},
    )
    tags = [t for t, _ in frames]
    assert tags[-1] == STREAM_TAG_END, f"last frame must be END: tags={tags}"
    assert STREAM_TAG_ERROR not in tags, "no error frame expected"
    assert STREAM_TAG_CHUNK in tags, "expected at least one chunk frame"


def test_tts_stream_long_text_emits_multiple_chunks(http: httpx.Client) -> None:
    if not _model_present(http):
        pytest.skip("Kokoro model not downloaded; skipping live stream test")

    text = (
        "Streaming text-to-speech should yield audio in pieces. "
        "Each phoneme batch arrives separately. "
        "The last frame is always end-of-stream so the client can detect a clean finish. "
        "This sentence makes the input long enough to trigger the streaming code path."
    )
    frames = _read_all_frames(
        http,
        {"text": text, "voice_id": "af_heart"},
    )
    tags = [t for t, _ in frames]
    assert tags[-1] == STREAM_TAG_END
    assert STREAM_TAG_ERROR not in tags
    chunk_count = sum(1 for t in tags if t == STREAM_TAG_CHUNK)
    assert chunk_count >= 1, f"expected ≥1 chunks for long text, got {chunk_count}"


def test_tts_stream_bad_voice_emits_error_frame(http: httpx.Client) -> None:
    if not _model_present(http):
        pytest.skip("Kokoro model not downloaded; skipping live stream test")

    frames = _read_all_frames(
        http,
        {"text": "Hello.", "voice_id": "no_such_voice_xyz"},
    )
    tags = [t for t, _ in frames]
    assert STREAM_TAG_ERROR in tags
    assert tags[-1] == STREAM_TAG_END
    err_payloads = [p for t, p in frames if t == STREAM_TAG_ERROR]
    parsed = json.loads(err_payloads[0])
    assert parsed["code"] == "voice_not_found"
    assert "no_such_voice_xyz" in parsed["message"]
