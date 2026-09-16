from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HTTP_HELPER = REPO_ROOT / "scripts" / "smoke-http.sh"


class _Handler(BaseHTTPRequestHandler):
    status = 200
    seen_authorization = ""

    def do_GET(self) -> None:  # noqa: N802
        type(self).seen_authorization = self.headers.get("Authorization", "")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "complete"}).encode())

    def log_message(self, *_args: object) -> None:
        pass


def _server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/status"


def _run_helper(url: str, output: Path, diagnostic: Path) -> subprocess.CompletedProcess[str]:
    command = f'''source "{HTTP_HELPER}"
smoke_http_get "{url}" "{output}" "{diagnostic}" 2 smoke-local
'''
    return subprocess.run(
        ["bash", "-c", command], text=True, capture_output=True, env=_hostile_proxy_env()
    )


def _hostile_proxy_env() -> dict[str, str]:
    return {
        **os.environ,
        "http_proxy": "http://127.0.0.1:1",
        "HTTP_PROXY": "http://127.0.0.1:1",
        "NO_PROXY": "",
        "no_proxy": "",
    }


def test_helper_bypasses_hostile_proxy_and_keeps_bearer_private(tmp_path: Path) -> None:
    server, url = _server()
    try:
        # This is the pre-repair probe shape: it honors ambient proxy settings
        # and cannot reach the local engine once NO_PROXY is cleared.
        old_probe = subprocess.run(
            ["curl", "-sf", "--max-time", "2", url],
            text=True,
            capture_output=True,
            env=_hostile_proxy_env(),
        )
        output = tmp_path / "status.json"
        diagnostic = tmp_path / "status.curl.log"
        result = _run_helper(url, output, diagnostic)
    finally:
        server.shutdown()

    assert old_probe.returncode != 0
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == {"status": "complete"}
    assert _Handler.seen_authorization == "Bearer smoke-local"
    text = diagnostic.read_text()
    assert "curl_exit=0" in text
    assert "http_status=200" in text
    assert "smoke-local" not in text


@pytest.mark.parametrize("status", [401, 500])
def test_helper_rejects_http_error_and_preserves_last_good_json(
    tmp_path: Path, status: int
) -> None:
    server, url = _server()
    _Handler.status = status
    try:
        output = tmp_path / "status.json"
        output.write_text('{"status":"complete"}')
        diagnostic = tmp_path / "status.curl.log"
        result = _run_helper(url, output, diagnostic)
    finally:
        server.shutdown()
        _Handler.status = 200

    assert result.returncode != 0
    assert json.loads(output.read_text()) == {"status": "complete"}
    assert "curl_exit=0" in diagnostic.read_text()
    assert f"http_status={status}" in diagnostic.read_text()


def test_helper_records_connection_failure_and_preserves_last_good_json(tmp_path: Path) -> None:
    output = tmp_path / "status.json"
    output.write_text('{"status":"complete"}')
    diagnostic = tmp_path / "status.curl.log"
    result = _run_helper("http://127.0.0.1:1/status", output, diagnostic)

    assert result.returncode != 0
    assert json.loads(output.read_text()) == {"status": "complete"}
    text = diagnostic.read_text()
    assert "curl_exit=" in text
    assert "http_status=000" in text or "http_status=unavailable" in text
