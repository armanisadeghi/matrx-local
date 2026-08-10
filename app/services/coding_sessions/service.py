"""Durable ordered outbox from local provider hooks to aidream."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from app.common.system_logger import get_logger
from app.services.aidream.client import (
    AIDreamClient,
    AIDreamError,
    AIDreamOfflineError,
    get_aidream_client,
)
from app.services.coding_sessions.models import BridgeRequest, LocalBridgeReceipt
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import TokenRepo

logger = get_logger()

_SERVER_PATH = "/coding-sessions/bridge"
_PUBLISH_INTERVAL_SECONDS = 15.0
_MAX_BATCH = 100
_MAX_BACKOFF_SECONDS = 60.0


class BridgeMutationConflict(ValueError):
    """A provider reused a stable event identity with different content."""


def _canonical_envelope(request: BridgeRequest) -> tuple[dict[str, Any], str, str]:
    payload = request.model_dump(mode="json", exclude_none=True)
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return payload, serialized, digest


def _stable_delivery_key(request: BridgeRequest) -> str | None:
    hook = request.hook_event
    if hook is None or hook.stable_event_id is None:
        return None
    identity = json.dumps(
        [
            request.provider.value,
            request.provider_session_id,
            request.stream_key,
            hook.name,
            hook.stable_event_id,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class CodingSessionBridgeOutbox:
    """Persists first, then uploads the oldest envelope until acknowledged.

    Successful server acknowledgement deletes the local row. If aidream
    accepted a request but the response was lost, the unchanged persisted
    envelope is replayed after restart; server-side bridge idempotency makes
    that retry a no-op.
    """

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        client: AIDreamClient | None = None,
        client_factory: Callable[[], AIDreamClient | None] = get_aidream_client,
        token_repo: TokenRepo | None = None,
        cloud_enabled: bool | None = None,
    ) -> None:
        self._db = db or get_db()
        self._client = client
        self._client_factory = client_factory
        self._tokens = token_repo or TokenRepo(self._db)
        if cloud_enabled is None:
            from app.config import CLOUD_PARTICIPATION_ENABLED

            cloud_enabled = CLOUD_PARTICIPATION_ENABLED
        self._cloud_enabled = cloud_enabled
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._sync_lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start_background(self) -> None:
        if self.active:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._publisher_loop(), name="coding-session-bridge-outbox"
        )

    async def stop_background(self) -> None:
        self._stopping = True
        self._wake.set()
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def enqueue(self, request: BridgeRequest) -> LocalBridgeReceipt:
        """Commit an exact envelope before returning a receipt."""
        _payload, serialized, digest = _canonical_envelope(request)
        dedupe_key = _stable_delivery_key(request)
        duplicate = False
        try:
            cursor = await self._db.execute(
                """INSERT INTO coding_session_bridge_outbox (
                     dedupe_key, envelope_json, envelope_sha256
                   ) VALUES (?, ?, ?)""",
                (dedupe_key, serialized, digest),
            )
            await self._db.commit()
            outbox_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            await self._db.db.rollback()
            if dedupe_key is None:
                raise
            row = await self._db.fetchone(
                "SELECT id, envelope_sha256 FROM coding_session_bridge_outbox "
                "WHERE dedupe_key = ?",
                (dedupe_key,),
            )
            if row is None:
                raise
            if row["envelope_sha256"] != digest:
                raise BridgeMutationConflict(
                    "stable hook event identity was reused with different content"
                )
            outbox_id = int(row["id"])
            duplicate = True

        pending = await self.pending_count()
        self.wake()
        return LocalBridgeReceipt(
            receipt_id=outbox_id,
            duplicate=duplicate,
            pending=pending,
        )

    async def pending_count(self) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS count FROM coding_session_bridge_outbox"
        )
        return int(row["count"]) if row else 0

    async def status(self) -> dict[str, Any]:
        row = await self._db.fetchone(
            """SELECT id, attempts, next_attempt_at, last_error, created_at
               FROM coding_session_bridge_outbox ORDER BY id LIMIT 1"""
        )
        return {
            "active": self.active,
            "cloud_enabled": self._cloud_enabled,
            "pending": await self.pending_count(),
            "oldest": dict(row) if row else None,
            "server_path": f"/api{_SERVER_PATH}",
        }

    async def sync_pending(self, *, limit: int = _MAX_BATCH) -> dict[str, Any]:
        """Upload in insertion order and stop at the first deferred/failing row."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        async with self._sync_lock:
            if not self._cloud_enabled:
                await self._defer_head("cloud_participation_disabled", increment=False)
                return {"sent": 0, "failed": 0, "blocked": "cloud_participation_disabled"}

            token_row = await self._tokens.get()
            if (
                not token_row
                or not token_row.get("access_token")
                or not token_row.get("user_id")
                or self._tokens.is_expired(token_row)
            ):
                await self._defer_head("no_active_user_jwt", increment=False)
                return {"sent": 0, "failed": 0, "blocked": "no_active_user_jwt"}

            client = self._client or self._client_factory()
            if client is None:
                await self._defer_head("aidream_server_unconfigured", increment=False)
                return {
                    "sent": 0,
                    "failed": 0,
                    "blocked": "aidream_server_unconfigured",
                }

            sent = 0
            while sent < limit:
                row = await self._db.fetchone(
                    """SELECT id, envelope_json, attempts, next_attempt_at
                       FROM coding_session_bridge_outbox ORDER BY id LIMIT 1"""
                )
                if row is None:
                    break
                if float(row["next_attempt_at"] or 0) > time.time():
                    break
                payload = json.loads(str(row["envelope_json"]))
                try:
                    await client.post(
                        _SERVER_PATH,
                        payload,
                        jwt=str(token_row["access_token"]),
                        timeout=30.0,
                    )
                except (AIDreamOfflineError, AIDreamError) as exc:
                    await self._record_failure(int(row["id"]), int(row["attempts"]), exc)
                    return {"sent": sent, "failed": 1, "blocked": None}

                await self._db.execute(
                    "DELETE FROM coding_session_bridge_outbox WHERE id = ?",
                    (int(row["id"]),),
                )
                await self._db.commit()
                sent += 1
            return {"sent": sent, "failed": 0, "blocked": None}

    async def _defer_head(self, reason: str, *, increment: bool) -> None:
        row = await self._db.fetchone(
            "SELECT id, attempts FROM coding_session_bridge_outbox ORDER BY id LIMIT 1"
        )
        if row is None:
            return
        attempts = int(row["attempts"]) + (1 if increment else 0)
        await self._db.execute(
            """UPDATE coding_session_bridge_outbox
               SET attempts=?, last_error=?, next_attempt_at=?,
                   updated_at=datetime('now') WHERE id=?""",
            (
                attempts,
                reason[:1000],
                time.time() + _PUBLISH_INTERVAL_SECONDS,
                int(row["id"]),
            ),
        )
        await self._db.commit()

    async def _record_failure(
        self, outbox_id: int, prior_attempts: int, exc: Exception
    ) -> None:
        attempts = prior_attempts + 1
        backoff = min(_MAX_BACKOFF_SECONDS, float(2 ** min(attempts, 6)))
        await self._db.execute(
            """UPDATE coding_session_bridge_outbox
               SET attempts=?, last_error=?, next_attempt_at=?,
                   updated_at=datetime('now') WHERE id=?""",
            (attempts, str(exc)[:1000], time.time() + backoff, outbox_id),
        )
        await self._db.commit()
        logger.warning(
            "[coding_session_bridge] upload deferred id=%s attempt=%s: %s",
            outbox_id,
            attempts,
            exc,
        )

    async def _publisher_loop(self) -> None:
        while not self._stopping:
            try:
                await self.sync_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[coding_session_bridge] publisher tick failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=_PUBLISH_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass


_instance: CodingSessionBridgeOutbox | None = None


def get_coding_session_bridge_outbox() -> CodingSessionBridgeOutbox:
    global _instance
    if _instance is None:
        _instance = CodingSessionBridgeOutbox()
    return _instance


__all__ = [
    "BridgeMutationConflict",
    "CodingSessionBridgeOutbox",
    "get_coding_session_bridge_outbox",
]
