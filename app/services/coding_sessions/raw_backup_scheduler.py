"""AUTOMATIC RAW BACKUP — upload a session's full file after its turns end, only when opted in.

The cloud keeps a coding session's text and tool names; the full transcript is
the provider's own file on this computer. When the owner turned on the Feature
Knob ``coding_session_bridge.raw_transcript_backup`` (their own override, else
their organization's, else the platform default — off), this computer uploads
that file through the server's ONE door after each run or turn ends.

Rules this module guarantees (and its tests prove):

* **Policy first, bytes never while off.** Every upload is preceded by
  ``GET /coding-sessions/raw-transcripts/backup-policy``. With ``enabled``
  false nothing is read for upload and nothing is sent.
* **Debounced.** At most one upload per session per ``debounce_seconds`` (a
  knob the server answers with), plus ONE trailing upload after the window so
  the final state is always backed up.
* **Never the same bytes twice.** The server answers the SHA-256 it already
  holds; an unchanged file is not sent.

Triggers (never a new timer of its own): a delivered turn-end / import envelope
from the bridge outbox, and the existing capture reconciler's pass for Claude
chats that changed.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.common.system_logger import get_logger

logger = get_logger()

_POLICY_PATH = "/coding-sessions/raw-transcripts/backup-policy"
_UPLOAD_PATH = "/coding-sessions/raw-transcripts/backup"

#: Hook events that end a turn or a run — the moments a backup is due.
TURN_END_EVENTS = frozenset({"Stop", "StopFailure", "SubagentStop", "SessionEnd"})


@dataclass(frozen=True)
class BackupPolicy:
    enabled: bool
    debounce_seconds: int
    backup_sha256: str | None = None


PolicyFn = Callable[[str, str, str | None], Awaitable[BackupPolicy]]
UploadFn = Callable[[str, str, str | None, bytes], Awaitable[None]]
LocateFn = Callable[[str, str], Any]


@dataclass
class _SessionState:
    last_upload_at: float | None = None
    last_sha256: str | None = None
    trailing: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RawBackupScheduler:
    def __init__(
        self,
        *,
        policy: PolicyFn,
        upload: UploadFn,
        locate: LocateFn,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._policy = policy
        self._upload = upload
        self._locate = locate
        self._clock = clock
        self._sleep = sleep
        self._sessions: dict[tuple[str, str], _SessionState] = {}
        self.uploads = 0
        self.skipped_off = 0

    async def notify(
        self,
        provider: str,
        provider_session_id: str,
        native_session_id: str,
        provider_project_key: str | None = None,
    ) -> None:
        """A turn or run of this session ended: back it up now, later, or not at all."""
        if provider not in {"claude_code", "codex"}:
            return
        key = (provider, provider_session_id)
        state = self._sessions.setdefault(key, _SessionState())
        if state.trailing is not None and not state.trailing.done():
            return  # the trailing upload will carry this change
        async with state.lock:
            policy = await self._policy(provider, provider_session_id, provider_project_key)
            if not policy.enabled:
                self.skipped_off += 1
                return
            now = self._clock()
            window = max(int(policy.debounce_seconds), 1)
            if state.last_upload_at is None or now - state.last_upload_at >= window:
                await self._upload_now(state, provider, provider_session_id, native_session_id,
                                       provider_project_key, policy)
                return
            delay = window - (now - state.last_upload_at)
            state.trailing = asyncio.create_task(
                self._trailing(state, delay, provider, provider_session_id,
                               native_session_id, provider_project_key)
            )

    async def _trailing(
        self,
        state: _SessionState,
        delay: float,
        provider: str,
        provider_session_id: str,
        native_session_id: str,
        provider_project_key: str | None,
    ) -> None:
        await self._sleep(delay)
        async with state.lock:
            # The owner may have turned backup off meanwhile: ask again.
            policy = await self._policy(provider, provider_session_id, provider_project_key)
            if not policy.enabled:
                self.skipped_off += 1
                return
            await self._upload_now(state, provider, provider_session_id, native_session_id,
                                   provider_project_key, policy)

    async def _upload_now(
        self,
        state: _SessionState,
        provider: str,
        provider_session_id: str,
        native_session_id: str,
        provider_project_key: str | None,
        policy: BackupPolicy,
    ) -> None:
        located = self._locate(provider, native_session_id)
        if located is None:
            return
        content = located.main.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest in {state.last_sha256, policy.backup_sha256}:
            state.last_sha256 = digest
            return
        await self._upload(provider, provider_session_id, provider_project_key, content)
        state.last_upload_at = self._clock()
        state.last_sha256 = digest
        self.uploads += 1

    async def wait_idle(self) -> None:
        """For tests and shutdown: let every trailing upload finish."""
        pending = [s.trailing for s in self._sessions.values() if s.trailing and not s.trailing.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------- the real wiring


async def _access_token() -> str | None:
    from app.services.local_db.database import get_db
    from app.services.local_db.repositories import TokenRepo

    row = await TokenRepo(get_db()).get()
    token = row.get("access_token") if row else None
    return str(token) if token else None


def notify_from_request(request: Any) -> None:
    """A bridge envelope was delivered: schedule a backup when it ended a turn or imported."""
    action = getattr(getattr(request, "action", None), "value", None)
    provider = getattr(getattr(request, "provider", None), "value", None)
    provider_session_id = getattr(request, "provider_session_id", None)
    if not isinstance(provider, str) or not isinstance(provider_session_id, str):
        return
    if action == "observe_hook":
        hook = getattr(request, "hook_event", None)
        if getattr(hook, "name", None) not in TURN_END_EVENTS:
            return
        schedule_notify(provider, provider_session_id, provider_session_id)
        return
    if action == "append_native":
        source = getattr(request, "source_metadata", None)
        native = getattr(source, "provider_native_session_id", None)
        if native is None:
            return
        schedule_notify(
            provider,
            provider_session_id,
            str(native),
            getattr(request, "provider_project_key", None),
        )


async def _server_policy(
    provider: str, provider_session_id: str, provider_project_key: str | None
) -> BackupPolicy:
    from app.services.aidream.client import get_aidream_client

    client = get_aidream_client()
    token = await _access_token()
    if client is None or token is None:
        return BackupPolicy(enabled=False, debounce_seconds=0)
    from urllib.parse import quote

    query = (
        f"?provider={quote(provider)}&provider_session_id={quote(provider_session_id)}"
        + (f"&provider_project_key={quote(provider_project_key)}" if provider_project_key else "")
    )
    answer = await client.get(_POLICY_PATH + query, jwt=token)
    return BackupPolicy(
        enabled=bool(answer.get("enabled")),
        debounce_seconds=int(answer.get("debounce_seconds") or 0),
        backup_sha256=answer.get("backup_sha256"),
    )


async def _server_upload(
    provider: str, provider_session_id: str, provider_project_key: str | None, content: bytes
) -> None:
    from app.services.aidream.client import get_aidream_client

    client = get_aidream_client()
    token = await _access_token()
    if client is None or token is None:
        return
    params = {"provider": provider, "provider_session_id": provider_session_id}
    if provider_project_key:
        params["provider_project_key"] = provider_project_key
    await client.post_bytes(
        _UPLOAD_PATH, content, params=params, content_type="application/x-ndjson", jwt=token
    )


def _locate(provider: str, native_session_id: str) -> Any:
    from app.services.coding_sessions.raw_transcript import locate

    return locate(provider, native_session_id)


_scheduler: RawBackupScheduler | None = None


def get_raw_backup_scheduler() -> RawBackupScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = RawBackupScheduler(policy=_server_policy, upload=_server_upload, locate=_locate)
    return _scheduler


def schedule_notify(
    provider: str,
    provider_session_id: str,
    native_session_id: str,
    provider_project_key: str | None = None,
) -> None:
    """Fire-and-forget from a delivery path; a failure is logged loudly, never raised."""

    async def run() -> None:
        try:
            await get_raw_backup_scheduler().notify(
                provider, provider_session_id, native_session_id, provider_project_key
            )
        except Exception as exc:  # noqa: BLE001 — a backup never breaks delivery
            logger.warning(
                "[raw_backup] automatic backup of %s %s failed: %s",
                provider, provider_session_id, exc,
            )

    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        pass
