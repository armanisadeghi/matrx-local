"""The control-API client (SPEC-CUSTODY §6, CONTRACT-RULINGS C5/C6/C7).

Transport: the per-user Unix socket on macOS and Linux; the loopback TCP listener on Windows,
which C7 added precisely so a client that cannot open a named pipe still has one (it supersedes
SPEC-ENGINE E7's "health+events only" listener). Both serve the identical ``/v1`` API with the
identical bearer auth, so nothing above this module knows which one was used.

Custody: the daemon is the sole session cache and refresh owner. Each consumer operation reads its
current token-owner grant from the daemon; concurrent engine callers share that local request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Final

import httpx

logger = logging.getLogger(__name__)

#: The daemon answers in milliseconds over a local socket; anything longer is a fault, not slowness.
_REQUEST_TIMEOUT_SECONDS: Final[float] = 6.0

#: The five session values of the ONE honest-state enum (C3). A consumer's switch over these is
#: exhaustive; nothing here invents a sixth.
STATE_SIGNED_IN: Final[str] = "signed_in"
STATE_SIGN_IN_NEEDED: Final[str] = "sign_in_needed"
STATE_SIGNED_OUT: Final[str] = "signed_out"
STATE_CREDENTIAL_STORE_UNAVAILABLE: Final[str] = "credential_store_unavailable"
STATE_OFFLINE: Final[str] = "offline"

#: Not a session state — the daemon itself is not reachable. SPEC-ENGINE's device state.
STATE_DAEMON_NOT_RUNNING: Final[str] = "daemon_not_running"


@dataclass(frozen=True)
class SessionSnapshot:
    """What the device's session is right now, as a state — never as an exception."""

    state: str
    state_reason: str
    signed_in: bool = False
    user_id: str | None = None
    email: str | None = None
    since: str | None = None
    next_attempt_at: str | None = None
    cloud_state_write_pending: bool = False

    @property
    def needs_sign_in(self) -> bool:
        """Whether a person must act before cloud work can resume."""
        return self.state in (STATE_SIGN_IN_NEEDED, STATE_SIGNED_OUT)


def _matrx_home() -> Path:
    """This world's home, resolved exactly as the engine already resolves it (Hard Rule 9)."""
    # `run.py` forces MATRX_HOME_DIR to ~/.matrx-dev for every source run, so reading the same
    # variable is what keeps the engine and the daemon in the SAME world. Never a second guess.
    from app.config import MATRX_HOME_DIR

    return Path(MATRX_HOME_DIR)


class SyncDaemonClient:
    """One client per process; the daemon remains the sole token cache owner."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = home or _matrx_home()
        self._lock = asyncio.Lock()
        self._last_state: SessionSnapshot | None = None
        self._grant_task: asyncio.Task[tuple[str, str] | None] | None = None

    # ---------------------------------------------------------------- discovery

    @property
    def _discovery_path(self) -> Path:
        return self._home / "syncd.json"

    @property
    def _token_path(self) -> Path:
        return self._home / "syncd.token"

    def _discovery(self) -> dict[str, Any] | None:
        """The daemon's own discovery file (C5). Never the engine's ``local.json``."""
        try:
            return json.loads(self._discovery_path.read_text())
        except (OSError, ValueError):
            return None

    def _control_token(self) -> str | None:
        """Line 1 of ``syncd.token`` — the control scope (S17)."""
        try:
            lines = self._token_path.read_text().splitlines()
        except OSError:
            return None
        return lines[0].strip() if lines and lines[0].strip() else None

    def _client_for(
        self, discovery: dict[str, Any]
    ) -> tuple[httpx.AsyncClient, str] | None:
        """Build a client for whichever transport this OS uses, and the base URL to use with it."""
        if sys.platform == "win32":
            # C7: the loopback listener exists so a client that cannot open a named pipe still has
            # a transport. Python takes it rather than growing a pipe transport of its own.
            port = discovery.get("tcp_port")
            if not port:
                return None
            return (
                httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS),
                f"http://127.0.0.1:{port}",
            )
        socket_path = discovery.get("socket_path")
        if not socket_path or not Path(socket_path).exists():
            return None
        transport = httpx.AsyncHTTPTransport(uds=socket_path)
        # The host name is arbitrary over a Unix socket — the daemon's Host allow-list applies to
        # the TCP listener only — but it must be a valid one for httpx to build a request.
        return (
            httpx.AsyncClient(transport=transport, timeout=_REQUEST_TIMEOUT_SECONDS),
            "http://localhost",
        )

    def _daemon_down(self) -> SessionSnapshot:
        return SessionSnapshot(
            state=STATE_DAEMON_NOT_RUNNING,
            state_reason=(
                "AI Matrx Sync is not running on this computer, so there is no signed-in session "
                "to use. Open AI Matrx and choose Start sync."
            ),
        )

    async def _request(
        self, method: str, path: str
    ) -> tuple[int, dict[str, Any]] | None:
        discovery = self._discovery()
        if not discovery:
            return None
        token = self._control_token()
        if not token:
            return None
        built = self._client_for(discovery)
        if not built:
            return None
        client, base = built
        try:
            async with client:
                response = await client.request(
                    method,
                    f"{base}{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        # SPEC-ENGINE §3 requires it on every route, and it is not a CORS-simple
                        # header, which is why the daemon's allow-list names it.
                        "X-Matrx-Client": "engine",
                    },
                )
        except httpx.HTTPError as exc:
            logger.debug(
                "[sync_client] %s %s did not reach the daemon: %s", method, path, exc
            )
            return None
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    # ------------------------------------------------------------------ session

    async def session(self) -> SessionSnapshot:
        """``GET /v1/session`` — what every surface renders, including when there is no session."""
        result = await self._request("GET", "/v1/session")
        if result is None:
            snapshot = self._daemon_down()
            self._last_state = snapshot
            return snapshot
        status, body = result
        if status != 200:
            snapshot = self._daemon_down()
            self._last_state = snapshot
            return snapshot
        snapshot = SessionSnapshot(
            state=str(body.get("state") or STATE_SIGNED_OUT),
            state_reason=str(body.get("state_reason") or ""),
            signed_in=bool(body.get("signed_in")),
            user_id=body.get("user_id"),
            email=body.get("email"),
            since=body.get("since"),
            next_attempt_at=body.get("next_attempt_at"),
            cloud_state_write_pending=bool(body.get("cloud_state_write_pending")),
        )
        self._last_state = snapshot
        return snapshot

    async def session_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield custody ``session.changed`` SSE payloads; callers reconnect."""
        discovery = self._discovery()
        token = self._control_token()
        built = self._client_for(discovery) if discovery and token else None
        if not built or not token:
            return
        client, base = built
        try:
            async with client.stream(
                "GET",
                f"{base}/v1/events",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Matrx-Client": "engine",
                },
            ) as response:
                if response.status_code != 200:
                    return
                # Subscribe first, then read current state: a transition between
                # the pre-connect snapshot and subscription must not be missed.
                yield {"connected": True}
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            payload = json.loads(line[5:].strip())
                        except ValueError:
                            continue
                        if isinstance(payload, dict):
                            yield payload
        except httpx.HTTPError:
            return
        finally:
            await client.aclose()

    # -------------------------------------------------------------------- token

    async def access_token(self, *, force: bool = False) -> str | None:
        """The daemon's current token, or ``None`` when no session is usable.

        ``force`` remains source-compatible for 401 retry callers. It does
        not request rotation: only the daemon owns refresh and rotation.
        """
        _ = force
        grant = await self.access_grant()
        return grant[0] if grant is not None else None

    async def user_id(self) -> str | None:
        """The daemon's current owner, without exposing a durable credential."""
        grant = await self.access_grant()
        return grant[1] if grant is not None else None

    async def access_grant(self) -> tuple[str, str] | None:
        """Read the daemon's current token-owner pair once for concurrent callers.

        This deliberately bypasses the consumer cache without asking the
        daemon to refresh. An account switch therefore becomes visible at an
        operation boundary, while simultaneous local callers share one GET.
        """
        async with self._lock:
            task = self._grant_task
            if task is None or task.done():
                task = asyncio.create_task(self._read_current_grant())
                self._grant_task = task
        return await asyncio.shield(task)

    async def _read_current_grant(self) -> tuple[str, str] | None:
        result = await self._request("GET", "/v1/token")
        if result is None:
            self._last_state = self._daemon_down()
            return None

        status, body = result
        token = body.get("access_token") if status == 200 else None
        user_id = body.get("user_id") if status == 200 else None
        if (
            isinstance(token, str)
            and token
            and isinstance(user_id, str)
            and user_id
            and _jwt_subject(token) == user_id
        ):
            self._last_state = SessionSnapshot(
                state=STATE_SIGNED_IN,
                state_reason="Signed in and syncing.",
                signed_in=True,
                user_id=user_id,
            )
            return token, user_id

        if status == 409:
            self._last_state = SessionSnapshot(
                state=str(body.get("state") or STATE_SIGNED_OUT),
                state_reason=str(body.get("state_reason") or ""),
                email=body.get("email"),
                since=body.get("since"),
            )
        else:
            self._last_state = self._daemon_down()
        return None

    @property
    def last_state(self) -> SessionSnapshot | None:
        """The last state observed, for a surface that must not make a call to render."""
        return self._last_state


def _jwt_subject(token: str) -> str | None:
    """Read the daemon-returned JWT subject to bind it to its owner field."""
    import base64

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (IndexError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    subject = claims.get("sub") if isinstance(claims, dict) else None
    return subject if isinstance(subject, str) and subject else None


_CLIENT: SyncDaemonClient | None = None


def get_sync_client() -> SyncDaemonClient:
    """The process-wide client."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SyncDaemonClient()
    return _CLIENT


def reset_sync_client() -> None:
    """Drop the process-wide client. For tests, and for a world change during a test run."""
    global _CLIENT
    _CLIENT = None
