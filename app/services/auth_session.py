"""Process-local ordering for the desktop engine's credential delivery."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4
from app.services.local_db.repositories import TokenRepo

class SessionFenceError(Exception): code = "session_generation_changed"
class SessionGenerationRequired(SessionFenceError): code = "session_generation_required"
class SessionCleanupPending(SessionFenceError): code = "session_cleanup_pending"

@dataclass(frozen=True)
class CleanupSnapshot:
    retired_generation: str
    status: str

@dataclass(frozen=True)
class SessionSnapshot:
    generation: str
    credential_revision: int
    subject: str | None
    cleanup: CleanupSnapshot | None = None

@dataclass(frozen=True)
class ClearResult:
    snapshot: SessionSnapshot
    row: dict[str, Any] | None
    retired_generation: str
    attempt_id: str

class AuthSessionCoordinator:
    def __init__(self, repo: TokenRepo | None = None) -> None:
        self._repo = repo or TokenRepo(); self._lock = asyncio.Lock()
        self._generation = str(uuid4()); self._revision = 0; self._subject: str | None = None
        self._cleanup_pending = False; self._cleanup_failed = False; self._cleanup_running = False
        self._retired_generation: str | None = None; self._cleanup_row: dict[str, Any] | None = None
        self._cleanup_attempt: str | None = None
    async def snapshot(self) -> SessionSnapshot:
        async with self._lock:
            if self._cleanup_pending:
                return SessionSnapshot(self._generation, self._revision, None, CleanupSnapshot(self._retired_generation or "", "failed" if self._cleanup_failed else "running"))
            row = await self._repo.get(); self._subject = (row or {}).get("user_id") or self._subject
            return SessionSnapshot(self._generation, self._revision, self._subject)
    async def install(self, *, generation: str | None, revision: int | None, subject: str, access_token: str, refresh_token: str | None, expires_at: int | None, allow_initialize: bool) -> SessionSnapshot:
        if generation is None or revision is None: raise SessionGenerationRequired()
        async with self._lock:
            if self._cleanup_pending: raise SessionCleanupPending()
            row = await self._repo.get(); current = (row or {}).get("user_id") or None
            if generation != self._generation or revision != self._revision or (current is None and not allow_initialize) or (current is not None and current != subject): raise SessionFenceError()
            await self._repo.save(access_token=access_token, user_id=subject, refresh_token=refresh_token, expires_at=expires_at)
            self._subject = subject; self._revision += 1
            return SessionSnapshot(self._generation, self._revision, subject)
    async def current_token_matches(self, *, generation: str | None, revision: int | None, subject: str, token: str) -> None:
        if generation is None or revision is None: raise SessionGenerationRequired()
        async with self._lock:
            row = await self._repo.get()
            if self._cleanup_pending or generation != self._generation or revision != self._revision or not row or row.get("user_id") != subject or row.get("access_token") != token: raise SessionFenceError()
    async def clear(self, *, generation: str | None, revision: int | None, token: str | None = None) -> ClearResult | None:
        if generation is None or revision is None: raise SessionGenerationRequired()
        async with self._lock:
            if self._cleanup_pending:
                if generation != self._generation or revision != 0 or self._cleanup_running or not self._cleanup_failed:
                    raise SessionCleanupPending()
                if token is not None and (not self._cleanup_row or self._cleanup_row.get("access_token") != token): return None
                self._cleanup_failed = False; self._cleanup_running = True; self._cleanup_attempt = str(uuid4())
                try:
                    await self._repo.clear()
                except Exception as exc:
                    self._cleanup_running = False; self._cleanup_failed = True
                    raise SessionCleanupPending() from exc
                return ClearResult(SessionSnapshot(self._generation, 0, None, CleanupSnapshot(self._retired_generation or "", "running")), self._cleanup_row, self._retired_generation or "", self._cleanup_attempt)
            if generation != self._generation or revision != self._revision: raise SessionFenceError()
            row = await self._repo.get()
            if token is not None and (not row or row.get("access_token") != token): return None
            retired = self._generation
            self._generation = str(uuid4()); self._revision = 0; self._subject = None
            self._cleanup_pending = True; self._cleanup_failed = False; self._cleanup_running = True
            self._retired_generation = retired; self._cleanup_row = row; self._cleanup_attempt = str(uuid4())
            try:
                await self._repo.clear()
            except Exception as exc:
                self._cleanup_running = False; self._cleanup_failed = True
                raise SessionCleanupPending() from exc
            return ClearResult(SessionSnapshot(self._generation, 0, None, CleanupSnapshot(retired, "running")), row, retired, self._cleanup_attempt)

    async def finish_cleanup(self, *, retired_generation: str, new_generation: str, attempt_id: str, success: bool) -> None:
        async with self._lock:
            if (not self._cleanup_pending or retired_generation != self._retired_generation or new_generation != self._generation or attempt_id != self._cleanup_attempt):
                raise SessionFenceError()
            self._cleanup_running = False
            if success:
                self._cleanup_pending = False; self._cleanup_failed = False; self._retired_generation = None
                self._cleanup_row = None; self._cleanup_attempt = None
            else:
                self._cleanup_failed = True


_coordinator: AuthSessionCoordinator | None = None
def get_auth_session() -> AuthSessionCoordinator:
    global _coordinator
    if _coordinator is None: _coordinator = AuthSessionCoordinator()
    return _coordinator
