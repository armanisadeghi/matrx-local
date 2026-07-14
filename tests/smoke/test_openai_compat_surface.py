"""OpenAI-compatible /v1 surface tests.

The critical contract is SDK compatibility without letting the endpoint become
public: clients use ``Authorization: Bearer <supabase_jwt>`` exactly like an
OpenAI api_key, while the engine proxies only to the Rust-owned llama-server.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import app.common  # noqa: F401  (win the app.config <-> app.common import cycle)


@pytest.fixture()
def local_llm_registered(monkeypatch: pytest.MonkeyPatch):
    from app.services.ai import local_llm_registry

    monkeypatch.setattr(local_llm_registry, "_local_llm_port", 8765)
    monkeypatch.setattr(local_llm_registry, "_local_llm_model", "qwen3-8b")
    yield


@pytest.fixture()
def v1_app() -> FastAPI:
    from app.api.openai_compat_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://v1.test",
        headers={"Authorization": "Bearer test-token"},
        timeout=30.0,
    )


def _patch_upstream(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> list[dict[str, Any]]:
    from app.api import openai_compat_routes

    calls: list[dict[str, Any]] = []
    transport = httpx.MockTransport(handler)

    def _factory():
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(openai_compat_routes, "_new_upstream_client", _factory)
    monkeypatch.setattr(openai_compat_routes, "_new_non_stream_upstream_client", _factory)
    return calls


def test_models_lists_active_local_model(v1_app: FastAPI, local_llm_registered):
    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.get("/v1/models")
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        assert body["data"][0]["id"] == "local/qwen3-8b"
        assert body["data"][0]["root"] == "qwen3-8b"

    asyncio.run(_run())


def test_chat_completion_proxies_and_preserves_params(
    v1_app: FastAPI,
    local_llm_registered,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["accept_encoding"] = request.headers.get("accept-encoding")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-local",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            },
        )

    _patch_upstream(monkeypatch, _handler)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "local/qwen3-8b",
                    "messages": [{"role": "user", "content": "hi"}],
                    "temperature": 0.12,
                    "top_p": 0.9,
                    "stream": False,
                    "extra_body": {"llama": "kept"},
                },
            )
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "ok"

    asyncio.run(_run())
    assert captured["url"] == "http://127.0.0.1:8765/v1/chat/completions"
    assert captured["authorization"] is None
    assert captured["accept_encoding"] == "identity"
    assert captured["body"]["model"] == "qwen3-8b"
    assert captured["body"]["temperature"] == 0.12
    assert captured["body"]["top_p"] == 0.9
    assert captured["body"]["extra_body"] == {"llama": "kept"}


def test_chat_completion_streams_upstream_sse_bytes(
    v1_app: FastAPI,
    local_llm_registered,
    monkeypatch: pytest.MonkeyPatch,
):
    sse = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

    class _SseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["stream"] is True
        assert body["model"] == "qwen3-8b"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_SseStream(),
        )

    _patch_upstream(monkeypatch, _handler)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen3-8b",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
            )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.content == sse

    asyncio.run(_run())


def test_unknown_model_returns_openai_error(v1_app: FastAPI, local_llm_registered):
    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/chat/completions",
                json={"model": "local/not-running", "messages": []},
            )
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "model_not_found"
        assert body["error"]["param"] == "model"

    asyncio.run(_run())


def test_v1_surface_mounted_and_auth_gated_on_engine(
    http: httpx.Client,
    http_public: httpx.Client,
) -> None:
    r = http_public.get("/v1/models")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authorization_required"
    assert r.json()["error"]["type"] == "authentication_error"

    r = http.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["object"] == "list"


def test_non_stream_proxy_uses_finite_read_timeout() -> None:
    from app.api.openai_compat_routes import _NON_STREAM_TIMEOUT, _STREAM_TIMEOUT

    assert _STREAM_TIMEOUT.read is None
    assert _NON_STREAM_TIMEOUT.read == 300.0


@dataclass
class _FakeTtsResult:
    success: bool
    audio_bytes: bytes | None = None
    voice_id: str = "af_heart"
    duration_seconds: float = 1.25
    sample_rate: int = 24000
    error: str | None = None
    error_code: str | None = None


class _FakeTtsService:
    def __init__(self, *, downloaded: bool = True, result: _FakeTtsResult | None = None):
        self.model_downloaded = downloaded
        self.result = result or _FakeTtsResult(success=True, audio_bytes=b"RIFFfake-wav")
        self.calls: list[dict[str, Any]] = []
        self.background_download_checked = False

    def maybe_start_background_download(self) -> None:
        self.background_download_checked = True

    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def test_audio_speech_returns_wav(v1_app: FastAPI, monkeypatch: pytest.MonkeyPatch):
    from app.api import openai_compat_routes

    svc = _FakeTtsService()
    monkeypatch.setattr(openai_compat_routes, "_get_tts_service", lambda: svc)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/speech",
                json={
                    "model": "tts-1",
                    "input": "hello",
                    "voice": "af_heart",
                    "response_format": "wav",
                    "speed": 1.1,
                },
            )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/wav")
        assert r.content == b"RIFFfake-wav"

    asyncio.run(_run())
    assert svc.calls == [
        {"text": "hello", "voice_id": "af_heart", "speed": 1.1, "lang": None}
    ]


def test_audio_speech_requires_downloaded_model(
    v1_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import openai_compat_routes

    svc = _FakeTtsService(downloaded=False)
    monkeypatch.setattr(openai_compat_routes, "_get_tts_service", lambda: svc)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/speech",
                json={"input": "hello", "voice": "af_heart"},
            )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "model_not_downloaded"

    asyncio.run(_run())
    assert svc.background_download_checked is True


def test_audio_speech_rejects_unsupported_format(v1_app: FastAPI):
    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/speech",
                json={"input": "hello", "voice": "af_heart", "response_format": "mp3"},
            )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "unsupported_response_format"

    asyncio.run(_run())


def test_audio_speech_rejects_oversized_input(v1_app: FastAPI):
    from app.api.openai_compat_routes import _MAX_SPEECH_INPUT_CHARS

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/speech",
                json={"input": "x" * (_MAX_SPEECH_INPUT_CHARS + 1)},
            )
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "input_too_large"

    asyncio.run(_run())


def test_audio_transcription_returns_json(
    v1_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import openai_compat_routes
    from app.tools.types import ToolResult

    captured: dict[str, Any] = {}

    async def _fake_transcribe(file_path: str, *, model: str, language: str | None):
        captured["exists_during_call"] = __import__("os").path.exists(file_path)
        captured["model"] = model
        captured["language"] = language
        return ToolResult(
            output="Transcription (en):\n\nhello there",
            metadata={
                "text": "hello there",
                "language": "en",
                "segments": [{"start": 0.0, "end": 1.0, "text": "hello there"}],
            },
        )

    monkeypatch.setattr(openai_compat_routes, "_transcribe_audio_file", _fake_transcribe)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/transcriptions",
                data={
                    "model": "whisper-1",
                    "language": "en",
                    "response_format": "verbose_json",
                },
                files={"file": ("sample.wav", b"fake wav", "audio/wav")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["text"] == "hello there"
        assert body["language"] == "en"
        assert body["segments"][0]["text"] == "hello there"

    asyncio.run(_run())
    assert captured == {"exists_during_call": True, "model": "base", "language": "en"}


def test_audio_transcription_text_format(
    v1_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import openai_compat_routes
    from app.tools.types import ToolResult

    async def _fake_transcribe(file_path: str, *, model: str, language: str | None):
        return ToolResult(output="", metadata={"text": "plain text"})

    monkeypatch.setattr(openai_compat_routes, "_transcribe_audio_file", _fake_transcribe)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/transcriptions",
                data={"model": "base", "response_format": "text"},
                files={"file": ("sample.wav", b"fake wav", "audio/wav")},
            )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert r.text == "plain text"

    asyncio.run(_run())


def test_audio_transcription_missing_dependency_error(
    v1_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import openai_compat_routes
    from app.tools.types import ToolResult, ToolResultType

    async def _fake_transcribe(file_path: str, *, model: str, language: str | None):
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Whisper is not installed",
            metadata={"fix_capability_id": "transcription"},
        )

    monkeypatch.setattr(openai_compat_routes, "_transcribe_audio_file", _fake_transcribe)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/transcriptions",
                data={"model": "base"},
                files={"file": ("sample.wav", b"fake wav", "audio/wav")},
            )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "missing_dependency"

    asyncio.run(_run())


def test_audio_transcription_rejects_oversized_upload(v1_app: FastAPI):
    from app.api.openai_compat_routes import _MAX_TRANSCRIPTION_BYTES

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/audio/transcriptions",
                data={"model": "base"},
                files={
                    "file": (
                        "huge.wav",
                        b"x" * (_MAX_TRANSCRIPTION_BYTES + 1),
                        "audio/wav",
                    )
                },
            )
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "file_too_large"

    asyncio.run(_run())


def test_embeddings_returns_openai_shape(
    v1_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import openai_compat_routes

    captured: dict[str, Any] = {}

    async def _fake_embed(texts: tuple[str, ...], model_name: str):
        captured["texts"] = texts
        captured["model_name"] = model_name
        return [[0.1, 0.2], [0.3, 0.4]]

    monkeypatch.setattr(openai_compat_routes, "_embed_texts", _fake_embed)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/embeddings",
                json={
                    "model": "local/BAAI/bge-small-en-v1.5",
                    "input": ["hello world", "again"],
                },
            )
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        assert body["model"] == "BAAI/bge-small-en-v1.5"
        assert body["data"][0] == {
            "object": "embedding",
            "index": 0,
            "embedding": [0.1, 0.2],
        }
        assert body["usage"]["total_tokens"] == 3

    asyncio.run(_run())
    assert captured == {
        "texts": ("hello world", "again"),
        "model_name": "BAAI/bge-small-en-v1.5",
    }


def test_embeddings_missing_dependency_returns_openai_error(
    v1_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api import openai_compat_routes

    async def _missing(texts: tuple[str, ...], model_name: str):
        raise ImportError("fastembed")

    monkeypatch.setattr(openai_compat_routes, "_embed_texts", _missing)

    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/embeddings",
                json={"input": "hello"},
            )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "missing_dependency"

    asyncio.run(_run())


def test_embeddings_rejects_base64_encoding(v1_app: FastAPI):
    async def _run() -> None:
        async with _client(v1_app) as client:
            r = await client.post(
                "/v1/embeddings",
                json={"input": "hello", "encoding_format": "base64"},
            )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "unsupported_encoding_format"

    asyncio.run(_run())


def test_v1_request_body_log_redaction() -> None:
    from app.main import _request_body_for_log

    body = {
        "model": "local/qwen",
        "messages": [{"role": "user", "content": "private prompt"}],
        "input": "private embedding text",
    }
    redacted = _request_body_for_log("/v1/chat/completions", body)

    assert redacted == {
        "_redacted": "openai-compatible request body",
        "keys": ["input", "messages", "model"],
    }
    assert "private prompt" not in str(redacted)
    assert "private embedding text" not in str(redacted)
