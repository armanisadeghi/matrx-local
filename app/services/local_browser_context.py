"""Serialized daemon-owned context fence for the local browser transport."""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.api.routes import _ENGINE_BOOT_ID

Grant = tuple[str, str] | None
GrantReader = Callable[[], Awaitable[Grant]]
ContextChangeListener = Callable[["BrowserContext"], None]


def grant_claims(token: str) -> tuple[str, str]:
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode((part + "=" * (-len(part) % 4)).encode()))
        user_id, session_id = data["sub"], data["session_id"]
        if not isinstance(user_id, str) or str(uuid.UUID(session_id)) != session_id:
            raise ValueError
        return user_id, session_id
    except Exception as exc:
        raise ValueError("malformed daemon grant") from exc


@dataclass(frozen=True)
class BrowserContext:
    engine_boot_id: str
    revision: int
    organization_id: str | None


@dataclass(frozen=True)
class FreshContext:
    context: BrowserContext
    owner: tuple[str, str]
    daemon_jwt: str


class LocalBrowserContext:
    def __init__(self, boot_id: str = _ENGINE_BOOT_ID, grant_reader: GrantReader | None = None) -> None:
        self._boot_id, self._grant_reader = boot_id, grant_reader
        self._revision = 0
        self._organization_id: str | None = None
        self._owner: tuple[str, str] | None = None
        self._lock = asyncio.Lock()
        self._listeners: set[ContextChangeListener] = set()

    def subscribe(self, listener: ContextChangeListener) -> Callable[[], None]:
        """Register a synchronous invalidation listener.

        Listeners run while the context lock is held, so they must only fence
        local state.  Network sends belong in work scheduled *after* that
        synchronous fence.
        """
        self._listeners.add(listener)

        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    def _changed(self) -> None:
        snapshot = self._snapshot()
        for listener in tuple(self._listeners):
            try:
                listener(snapshot)
            except Exception:
                # A notification consumer is never authority over this fence.
                continue

    async def _grant(self) -> Grant:
        if self._grant_reader:
            return await self._grant_reader()
        from app.services.sync_client import get_sync_client
        reader = getattr(get_sync_client(), "access_grant", None)
        return await reader() if callable(reader) else None

    @staticmethod
    def _parse(grant: Grant) -> tuple[tuple[str, str], str] | None:
        if grant is None:
            return None
        jwt, claimed_user = grant
        try:
            user, session = grant_claims(jwt)
        except ValueError:
            return None
        return ((user, session), jwt) if user == claimed_user else None

    def _retire(self, owner: tuple[str, str] | None) -> None:
        if self._owner == owner and (owner is not None or self._organization_id is None):
            return
        self._owner, self._organization_id = owner, None
        self._revision += 1
        self._changed()

    def _snapshot(self) -> BrowserContext:
        return BrowserContext(self._boot_id, self._revision, self._organization_id)

    async def refresh(self) -> FreshContext | None:
        """Lock before reading the daemon; no stale caller snapshot is authority."""
        async with self._lock:
            parsed = self._parse(await self._grant())
            self._retire(parsed[0] if parsed else None)
            if parsed is None:
                return None
            owner, jwt = parsed
            return FreshContext(self._snapshot(), owner, jwt)

    async def compare_and_set(self, *, owner: tuple[str, str], daemon_jwt: str, engine_boot_id: str, expected_revision: int, organization_id: str | None) -> BrowserContext | None:
        """Final daemon read and CAS share one lock; no await follows the check."""
        async with self._lock:
            parsed = self._parse(await self._grant())
            self._retire(parsed[0] if parsed else None)
            if parsed is None or parsed != (owner, daemon_jwt):
                return None
            if engine_boot_id != self._boot_id or expected_revision != self._revision:
                return None
            self._organization_id = organization_id
            self._revision += 1
            self._changed()
            return self._snapshot()


_CONTEXT = LocalBrowserContext()
def get_local_browser_context() -> LocalBrowserContext: return _CONTEXT
def set_local_browser_context_for_test(context: LocalBrowserContext) -> None:
    global _CONTEXT
    _CONTEXT = context
