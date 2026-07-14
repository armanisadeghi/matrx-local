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
