"""Real-loopback coverage for the documents PostgREST transport boundary."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType
from typing import Any, Iterator

import httpx
import pytest


class _TransportHandler(BaseHTTPRequestHandler):
    status = 200
    body: object = []

    def do_GET(self) -> None:  # noqa: N802
        encoded = json.dumps(type(self).body).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if type(self).status != 204:
            self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def documents_transport() -> Iterator[Any]:
    """Load only the transport against an inert temporary config module."""
    config_name = "app.config"
    transport_name = "app.services.documents.supabase_client"
    prior_config = sys.modules.get(config_name)
    prior_transport = sys.modules.get(transport_name)
    isolated_config = ModuleType(config_name)
    isolated_config.SUPABASE_URL = ""
    isolated_config.SUPABASE_PUBLISHABLE_KEY = "isolated-publishable-key"
    sys.modules[config_name] = isolated_config
    sys.modules.pop(transport_name, None)
    try:
        yield importlib.import_module(transport_name)
    finally:
        sys.modules.pop(transport_name, None)
        if prior_transport is not None:
            sys.modules[transport_name] = prior_transport
        if prior_config is None:
            sys.modules.pop(config_name, None)
        else:
            sys.modules[config_name] = prior_config


@pytest.fixture
def isolated_postgrest(documents_transport: Any) -> Iterator[Any]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TransportHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    documents_transport._REST_BASE = f"http://127.0.0.1:{server.server_port}"
    try:
        yield documents_transport
    finally:
        server.shutdown()
        thread.join()
        _TransportHandler.status = 200
        _TransportHandler.body = []


def _client(transport: Any) -> Any:
    client = transport.SupabaseDocClient()
    client.set_jwt("isolated-test-token")
    return client


def test_get_404_is_not_an_empty_result(isolated_postgrest: Any) -> None:
    _TransportHandler.status = 404
    _TransportHandler.body = {"code": "PGRST205", "message": "missing relation"}

    with pytest.raises(httpx.HTTPStatusError) as caught:
        asyncio.run(_client(isolated_postgrest)._request("GET", "notes", schema="workbench"))

    assert caught.value.response.status_code == 404
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_client(isolated_postgrest).get_note("not-logged-by-transport"))


def test_successful_empty_get_and_no_content_remain_empty(isolated_postgrest: Any) -> None:
    _TransportHandler.status = 200
    _TransportHandler.body = []
    assert asyncio.run(_client(isolated_postgrest)._request("GET", "notes", schema="workbench")) == []

    _TransportHandler.status = 204
    assert asyncio.run(_client(isolated_postgrest)._request("GET", "notes", schema="workbench")) == []


def test_403_preserves_exception_and_logs_only_safe_transport_fields(
    isolated_postgrest: Any, caplog: pytest.LogCaptureFixture
) -> None:
    _TransportHandler.status = 403
    secret_message = "adversarial payload must never reach logs"
    _TransportHandler.body = {
        "code": "42501",
        "message": secret_message,
        "details": "private detail",
        "hint": "private hint",
    }

    with caplog.at_level(logging.WARNING, logger=isolated_postgrest.__name__):
        with pytest.raises(httpx.HTTPStatusError) as caught:
            asyncio.run(_client(isolated_postgrest)._request("GET", "notes", schema="workbench"))

    assert caught.value.response.status_code == 403
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "Supabase PostgREST request rejected method=GET schema=workbench "
        "table=notes status=403 code=42501"
    ]
    assert secret_message not in caplog.text
    assert "private detail" not in caplog.text
    assert "private hint" not in caplog.text


@pytest.mark.parametrize("unsafe_code", ["PGRST1234", "PGRST1; private"])
def test_malformed_or_unbounded_error_code_is_not_logged(
    isolated_postgrest: Any, caplog: pytest.LogCaptureFixture, unsafe_code: str
) -> None:
    _TransportHandler.status = 403
    _TransportHandler.body = {"code": unsafe_code, "message": "do not log me"}

    with caplog.at_level(logging.WARNING, logger=isolated_postgrest.__name__):
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(_client(isolated_postgrest)._request("GET", "notes", schema="workbench"))

    assert caplog.messages[-1].endswith("status=403 code=unknown")
    assert unsafe_code not in caplog.text
