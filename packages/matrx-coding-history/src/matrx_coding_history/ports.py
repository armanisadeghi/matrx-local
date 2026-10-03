"""What the importer and the reconciler need from the app that runs them.

Each app injects its own: Matrx Local wires its SQLite repos, its durable hook delivery queue and
its AIDreamClient; Matrx 2 wires the values its core hands the service per call, a direct
delivery to the server, and in-memory working state. Nothing in this package reads a database,
a keychain or an app setting on its own.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, Protocol

from matrx_coding_history.bridge import BridgeRequest

EnqueueOrigin = Literal["explicit_history", "capture_recovery", "local_runtime"]


class CloudOffline(Exception):
    """The server could not be reached (no answer at all)."""


class CloudError(Exception):
    """The server answered with an error. ``str(error)`` is the caller-facing message."""

    def __init__(self, status: int, message: str, *, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class CloudHttp(Protocol):
    """Authenticated JSON calls to the AI Matrx server (the app holds the token)."""

    async def get_json(self, path: str) -> Any: ...

    async def post_json(self, path: str, body: Any, *, timeout: float) -> Any: ...


class AccessTokens(Protocol):
    """The signed-in AI Matrx person on this computer."""

    async def user_id(self) -> str | None:
        """The person's user id, or None when nobody is signed in."""
        ...

    async def access_token(self) -> str | None:
        """A usable (unexpired) access token, or None."""
        ...


class BridgeSink(Protocol):
    """Where import envelopes go: Matrx Local's durable outbox, or a direct delivery."""

    async def enqueue_many(
        self,
        requests: Sequence[BridgeRequest],
        *,
        enqueue_origin: str,
    ) -> dict[str, Any]:
        """Returns at least ``duplicates_by_index`` (one bool per request) and ``pending``."""
        ...


class SyncMeta(Protocol):
    async def set_last_sync(
        self, key: str, *, status: str = ..., last_hash: str | None = ...
    ) -> Any: ...


class BackfillLedger(Protocol):
    """The capture reconciler's per-session attempt record."""

    async def attempts(self) -> dict[str, dict[str, Any]]: ...

    async def record_attempt(
        self, session_key: str, source_state: str | None, error: str | None
    ) -> None: ...

    async def recent(self, limit: int) -> list[dict[str, Any]]: ...

    async def exhausted(self, max_attempts: int) -> list[dict[str, Any]]: ...


class Logger(Protocol):
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...

    def info(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...

    def error(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...


#: Called for a session the cloud already holds that changed recently (Matrx Local: the opt-in
#: raw transcript backup). ``(provider, raw_session_id, raw_session_id, project_key)``.
KnownSessionChanged = Callable[[str, str, str, str], Any]

#: The complete owner-scoped identity list, or CaptureReconcileBlocked.
IdentitySource = Callable[[], Awaitable[list[dict[str, Any]]]]

__all__ = [
    "AccessTokens",
    "BackfillLedger",
    "BridgeSink",
    "CloudError",
    "CloudHttp",
    "CloudOffline",
    "EnqueueOrigin",
    "IdentitySource",
    "KnownSessionChanged",
    "Logger",
    "SyncMeta",
]
