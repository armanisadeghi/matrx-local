"""The engine's sync-daemon client (FS-C5, SPEC-CUSTODY §6).

These tests speak to a **real Unix socket** running a real HTTP/1.1 conversation, so the transport
selection, the httpx UDS transport, the header contract and the response parsing are all exercised
for real. Only the daemon's own decision is scripted. A test that replaced the socket with a mocked
`_request` would prove nothing about the seam that actually broke in production.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

from app.services.sync_client.client import (
    STATE_DAEMON_NOT_RUNNING,
    STATE_OFFLINE,
    STATE_SIGN_IN_NEEDED,
    STATE_SIGNED_IN,
    SyncDaemonClient,
    _remaining_seconds,
)


class FakeDaemon:
    """A real HTTP/1.1 server on a real Unix socket, scripted per path.

    It is a stand-in for `matrx-syncd` and is never cited as product evidence — the daemon's own
    proof is a live run recorded in `crates/matrx-syncd/README.md`.
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.socket_path = home / "run" / "syncd.sock"
        self.responses: dict[str, tuple[int, dict]] = {}
        self.seen_headers: list[dict[str, str]] = []
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        (self.home / "run").mkdir(parents=True, exist_ok=True)
        (self.home / "syncd.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "world": "dev",
                    "pid": 1,
                    "socket_path": str(self.socket_path),
                    "tcp_port": None,
                    "daemon_version": "0.1.0",
                    "started_at": "2026-09-15T00:00:00Z",
                }
            )
        )
        (self.home / "syncd.token").write_text("control-token-line-1\nread-token-line-2\n")
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(self.socket_path)
        )

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        head = request.decode("latin-1").split("\r\n")
        path = head[0].split(" ")[1]
        headers = {}
        for line in head[1:]:
            if ": " in line:
                name, value = line.split(": ", 1)
                headers[name.lower()] = value
        self.seen_headers.append(headers)

        status, body = self.responses.get(path, (404, {"error": {"code": "not_found"}}))
        payload = json.dumps(body).encode()
        writer.write(
            b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
            b"Connection: close\r\n\r\n" % (status, len(payload))
            + payload
        )
        await writer.drain()
        writer.close()


@pytest.fixture
def short_home():
    """A SHORT temporary home.

    `sun_path` is 104 bytes on macOS and 108 on Linux, and pytest's `tmp_path` is already deep
    enough to blow it — the real daemon lives at `~/.matrx/run/syncd.sock`, which is short. The
    limit is the reason the daemon puts its socket under the home rather than beside the journal
    in a nested directory.
    """
    import shutil
    import tempfile

    path = Path(tempfile.mkdtemp(prefix="mxs", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
async def daemon(short_home: Path):
    fake = FakeDaemon(short_home)
    await fake.start()
    yield fake
    await fake.stop()


def _jwt(exp: int) -> str:
    import base64

    def seg(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'ES256'})}.{seg({'sub': 'u-1', 'exp': exp})}.sig"


async def test_a_token_is_fetched_over_the_real_unix_socket(daemon: FakeDaemon, short_home: Path):
    daemon.responses["/v1/token"] = (
        200,
        {
            "access_token": _jwt(4102444800),
            "expires_at": "2100-01-01T00:00:00Z",
            "user_id": "u-1",
            "token_type": "Bearer",
        },
    )
    client = SyncDaemonClient(home=short_home)
    assert await client.access_token() == daemon.responses["/v1/token"][1]["access_token"]

    # The contract SPEC-ENGINE §3 requires on every route, sent for real.
    headers = daemon.seen_headers[-1]
    assert headers["authorization"] == "Bearer control-token-line-1"
    assert headers["x-matrx-client"] == "engine"


async def test_the_token_is_cached_and_not_refetched(daemon: FakeDaemon, short_home: Path):
    daemon.responses["/v1/token"] = (
        200,
        {
            "access_token": _jwt(4102444800),
            "expires_at": "2100-01-01T00:00:00Z",
            "user_id": "u-1",
            "token_type": "Bearer",
        },
    )
    client = SyncDaemonClient(home=short_home)
    await client.access_token()
    calls = len(daemon.seen_headers)
    await client.access_token()
    await client.access_token()
    assert len(daemon.seen_headers) == calls, "S11: a live token is served from memory"


async def test_a_409_is_a_state_with_its_remedy_never_an_exception(
    daemon: FakeDaemon, short_home: Path
):
    daemon.responses["/v1/token"] = (
        409,
        {
            "state": STATE_SIGN_IN_NEEDED,
            "state_reason": "Sign back in as admin@admin.com on this computer to resume syncing.",
            "since": "2026-09-15T00:00:00Z",
            "email": "admin@admin.com",
        },
    )
    client = SyncDaemonClient(home=short_home)
    assert await client.access_token() is None  # a state, not a raise
    state = client.last_state
    assert state is not None
    assert state.state == STATE_SIGN_IN_NEEDED
    assert "admin@admin.com" in state.state_reason
    assert state.needs_sign_in is True


async def test_offline_is_a_state_that_does_not_ask_the_user_to_do_anything(
    daemon: FakeDaemon, short_home: Path
):
    daemon.responses["/v1/token"] = (
        409,
        {"state": STATE_OFFLINE, "state_reason": "Not connected — retrying.", "since": "t"},
    )
    client = SyncDaemonClient(home=short_home)
    assert await client.access_token() is None
    assert client.last_state.state == STATE_OFFLINE
    assert client.last_state.needs_sign_in is False


async def test_no_daemon_is_a_named_state_not_a_crash(short_home: Path):
    # Nothing was ever started: no discovery file, no token file, no socket. The engine must keep
    # running — local models, local tools and the file browser are not gated on a token (S13).
    client = SyncDaemonClient(home=short_home)
    assert await client.access_token() is None
    snapshot = await client.session()
    assert snapshot.state == STATE_DAEMON_NOT_RUNNING
    assert "Start sync" in snapshot.state_reason
    assert snapshot.signed_in is False


async def test_a_stale_discovery_file_whose_socket_is_gone_is_the_same_state(short_home: Path):
    (short_home / "run").mkdir(parents=True)
    (short_home / "syncd.json").write_text(
        json.dumps({"version": 1, "socket_path": str(short_home / "run" / "gone.sock")})
    )
    (short_home / "syncd.token").write_text("c\nr\n")
    client = SyncDaemonClient(home=short_home)
    assert await client.access_token() is None
    assert client.last_state.state == STATE_DAEMON_NOT_RUNNING


async def test_the_session_snapshot_round_trips(daemon: FakeDaemon, short_home: Path):
    daemon.responses["/v1/session"] = (
        200,
        {
            "signed_in": True,
            "user_id": "u-1",
            "email": "admin@admin.com",
            "state": STATE_SIGNED_IN,
            "state_reason": "Signed in and syncing.",
            "since": "2026-09-15T00:00:00Z",
            "next_attempt_at": None,
            "cloud_state_write_pending": False,
        },
    )
    client = SyncDaemonClient(home=short_home)
    snapshot = await client.session()
    assert snapshot.signed_in is True
    assert snapshot.email == "admin@admin.com"
    assert snapshot.state == STATE_SIGNED_IN
    assert await client.user_id() == "u-1"


@pytest.mark.anyio
async def test_an_unreadable_expiry_means_do_not_cache_never_cache_forever():
    # MXL-D-046 was the opposite mistake: an expiry that could not be trusted was trusted anyway.
    assert _remaining_seconds(None) == 0.0
    assert _remaining_seconds("") == 0.0
    assert _remaining_seconds("not a date") == 0.0
    assert _remaining_seconds("2100-01-01T00:00:00Z") > 0.0
