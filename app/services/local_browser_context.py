"""Engine-owned selected-organization state for the direct local browser bridge.

The state has no credentials. Its owner is derived afresh from the sync
daemon's atomic grant, and the daemon lifecycle retires it before dependent
services can adopt a changed account or session.
"""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import dataclass

from app.api.routes import _ENGINE_BOOT_ID


def grant_claims(token: str) -> tuple[str, str]:
    try:
        part = token.split(".")[1]
        padded = part + "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        sub, session_id = data["sub"], data["session_id"]
        canonical_session_id = str(uuid.UUID(session_id))
        if not isinstance(sub, str) or canonical_session_id != session_id:
            raise ValueError
        return sub, session_id
    except Exception as exc:
        raise ValueError("malformed daemon grant") from exc


@dataclass(frozen=True)
class BrowserContext:
    engine_boot_id: str
    revision: int
    organization_id: str | None


class LocalBrowserContext:
    """Process singleton. Callers use methods, never a raw app.state field."""

    def __init__(self, boot_id: str = _ENGINE_BOOT_ID) -> None:
        self._boot_id = boot_id
        self._revision = 0
        self._organization_id: str | None = None
        self._owner: tuple[str, str] | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _owner_for(grant: tuple[str, str] | None) -> tuple[str, str] | None:
        if grant is None:
            return None
        token, claimed_user_id = grant
        try:
            user_id, session_id = grant_claims(token)
        except ValueError:
            return None
        return (user_id, session_id) if claimed_user_id == user_id else None

    def _retire_unlocked(self, owner: tuple[str, str] | None) -> None:
        if self._owner == owner and (owner is not None or self._organization_id is None):
            return
        self._owner = owner
        self._organization_id = None
        self._revision += 1

    async def observe_daemon_grant(self, grant: tuple[str, str] | None) -> tuple[str, str] | None:
        """Immediately retire state for a daemon loss/account/session change."""
        owner = self._owner_for(grant)
        async with self._lock:
            self._retire_unlocked(owner)
        return owner

    async def fresh_for_daemon_grant(self, grant: tuple[str, str] | None) -> tuple[BrowserContext, tuple[str, str]] | None:
        owner = self._owner_for(grant)
        async with self._lock:
            self._retire_unlocked(owner)
            if owner is None:
                return None
            return self._snapshot_unlocked(), owner

    async def compare_and_set(self, *, owner: tuple[str, str], engine_boot_id: str, expected_revision: int, organization_id: str | None) -> BrowserContext | None:
        async with self._lock:
            if self._owner != owner or engine_boot_id != self._boot_id or expected_revision != self._revision:
                return None
            self._organization_id = organization_id
            self._revision += 1
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> BrowserContext:
        return BrowserContext(self._boot_id, self._revision, self._organization_id)


_CONTEXT = LocalBrowserContext()


def get_local_browser_context() -> LocalBrowserContext:
    return _CONTEXT


def set_local_browser_context_for_test(context: LocalBrowserContext) -> None:
    global _CONTEXT
    _CONTEXT = context
