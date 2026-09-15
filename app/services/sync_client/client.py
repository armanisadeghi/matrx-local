"""The control-API client (SPEC-CUSTODY §6, CONTRACT-RULINGS C5/C6/C7).

Transport: the per-user Unix socket on macOS and Linux; the loopback TCP listener on Windows,
which C7 added precisely so a client that cannot open a named pipe still has one (it supersedes
SPEC-ENGINE E7's "health+events only" listener). Both serve the identical ``/v1`` API with the
identical bearer auth, so nothing above this module knows which one was used.

Caching: S11/S12 — hold the access token in memory until 30 seconds before it expires, never
persist it, and never refresh. A consumer that receives 401 asks again (which may trigger the
daemon's one forced rotation per minute) and retries once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

#: S11 — a consumer caches until the remaining life minus this, and never persists.
_CACHE_MARGIN_SECONDS: Final[int] = 30

#: The daemon answers in milliseconds over a local socket; anything longer is a fault, not slowness.
_REQUEST_TIMEOUT_SECONDS: Final[float] = 6.0

#: S10 — a consumer's 401 may force ONE rotation, and the daemon rate-limits it to one a minute.
_FORCED_RETRY_INTERVAL_SECONDS: Final[int] = 60

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
    """One client per process. Cheap to construct; holds only an in-memory token."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = home or _matrx_home()
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._last_forced: float = 0.0
        self._last_state: SessionSnapshot | None = None

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

    def _client_for(self, discovery: dict[str, Any]) -> tuple[httpx.AsyncClient, str] | None:
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

    async def _request(self, method: str, path: str) -> tuple[int, dict[str, Any]] | None:
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
            logger.debug("[sync_client] %s %s did not reach the daemon: %s", method, path, exc)
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

    # -------------------------------------------------------------------- token

    async def access_token(self, *, force: bool = False) -> str | None:
        """A short-lived JWT, or ``None`` when there is no usable session.

        ``force=True`` is a consumer answering its own 401 (S10). The daemon rate-limits the
        rotation it triggers; this client additionally refuses to ask more than once a minute so a
        looping consumer cannot turn one 401 into a storm.
        """
        async with self._lock:
            now = time.monotonic()
            if force:
                if now - self._last_forced < _FORCED_RETRY_INTERVAL_SECONDS:
                    force = False
                else:
                    self._last_forced = now
                    self._token = None
            if not force and self._token and now < self._token_expires_at:
                return self._token

            result = await self._request("GET", "/v1/token")
            if result is None:
                self._token = None
                self._last_state = self._daemon_down()
                return None

            status, body = result
            if status == 200 and body.get("access_token"):
                self._token = str(body["access_token"])
                self._token_expires_at = now + max(
                    0.0, _remaining_seconds(body.get("expires_at")) - _CACHE_MARGIN_SECONDS
                )
                self._last_state = SessionSnapshot(
                    state=STATE_SIGNED_IN,
                    state_reason="Signed in and syncing.",
                    signed_in=True,
                    user_id=body.get("user_id"),
                )
                return self._token

            # 409 is the documented refusal shape and carries the state verbatim. It is a STATE,
            # never an exception: a local model, the local tools and the file browser must keep
            # working with no session at all (S13).
            self._token = None
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

    async def user_id(self) -> str | None:
        """The signed-in user's id, without handing anybody a token to get it."""
        snapshot = self._last_state or await self.session()
        return snapshot.user_id if snapshot.signed_in else None

    @property
    def last_state(self) -> SessionSnapshot | None:
        """The last state observed, for a surface that must not make a call to render."""
        return self._last_state


def _remaining_seconds(expires_at: Any) -> float:
    """Seconds of life left in an RFC3339 expiry, or 0 when it cannot be read.

    Zero is the safe answer: it means "do not cache", never "cache forever". MXL-D-046 was the
    opposite mistake.
    """
    if not isinstance(expires_at, str) or not expires_at:
        return 0.0
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


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

