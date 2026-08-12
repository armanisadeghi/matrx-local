"""Durable ordered outbox from local provider hooks to aidream."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import aiosqlite
from pydantic import ValidationError

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


def _stable_delivery_key(request: BridgeRequest, envelope_digest: str) -> str | None:
    if request.action.value == "append_native":
        return hashlib.sha256(f"append_native:{envelope_digest}".encode()).hexdigest()
    hook = request.hook_event
    if hook is None or hook.stable_event_id is None:
        return None
    identity = json.dumps(
        [
            request.provider.value,
            request.provider_session_id,
            request.stream_key,
            hook.stable_event_id,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _validate_upstream_acknowledgement(
    response: Any,
    request: BridgeRequest,
) -> None:
    """Prove a 2xx body durably accepted exactly this one hook event.

    A reverse proxy, stale server, or accidentally remounted route can return
    JSON with HTTP 2xx without committing the bridge entry. Deleting the local
    outbox row on that weak signal would turn a deployment mistake into data
    loss, so the response must satisfy the frozen BridgeResponse v1 receipt.
    """

    def _count(name: str) -> int:
        value = response.get(name) if isinstance(response, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AIDreamError(502, f"bridge acknowledgement has invalid {name}")
        return value

    if not isinstance(response, dict):
        raise AIDreamError(502, "bridge acknowledgement is not a JSON object")
    expected = {
        "schema_version": 1,
        "action": request.action.value,
        "provider": request.provider.value,
    }
    if request.action.value == "observe_hook":
        expected["fidelity"] = "event_mirror"
    for field, value in expected.items():
        if response.get(field) != value:
            raise AIDreamError(
                502,
                f"bridge acknowledgement {field} did not match request",
            )
    for field in ("session_id", "conversation_id"):
        try:
            UUID(str(response.get(field)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise AIDreamError(
                502,
                f"bridge acknowledgement has invalid {field}",
            ) from exc
    accepted = _count("accepted")
    duplicates = _count("duplicates")
    conflicts = _count("conflicts")
    expected_count = (
        1 if request.action.value == "observe_hook" else len(request.entries)
    )
    if request.action.value == "append_native" and response.get("fidelity") not in {
        "native",
        "event_mirror",
    }:
        raise AIDreamError(502, "bridge acknowledgement has invalid import fidelity")
    if conflicts != 0 or accepted + duplicates != expected_count:
        raise AIDreamError(
            502,
            "bridge acknowledgement did not account for every submitted entry",
        )


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
        dedupe_key = _stable_delivery_key(request, digest)
        outbox_id, duplicate = await self._commit_enqueue(
            dedupe_key=dedupe_key,
            serialized=serialized,
            digest=digest,
        )

        pending = await self.pending_count()
        self.wake()
        return LocalBridgeReceipt(
            receipt_id=outbox_id,
            duplicate=duplicate,
            pending=pending,
        )

    async def enqueue_many(self, requests: list[BridgeRequest]) -> dict[str, Any]:
        """Atomically persist a bounded import plan before reporting success."""
        if not requests:
            raise ValueError("at least one bridge request is required")
        prepared: list[tuple[str | None, str, str]] = []
        for request in requests:
            _payload, serialized, digest = _canonical_envelope(request)
            prepared.append((_stable_delivery_key(request, digest), serialized, digest))
        ids, duplicates = await self._commit_enqueue_many(prepared)
        pending = await self.pending_count()
        self.wake()
        return {
            "queued": len(ids) - duplicates,
            "duplicate_pending": duplicates,
            "receipt_ids": ids,
            "pending": pending,
        }

    async def _commit_enqueue_many(
        self,
        prepared: list[tuple[str | None, str, str]],
    ) -> tuple[list[int], int]:
        ids: list[int] = []
        duplicates = 0
        async with aiosqlite.connect(str(self._db.path)) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                for dedupe_key, serialized, digest in prepared:
                    if dedupe_key is None:
                        cursor = await connection.execute(
                            """INSERT INTO coding_session_bridge_outbox (
                                 dedupe_key, envelope_json, envelope_sha256
                               ) VALUES (?, ?, ?)""",
                            (None, serialized, digest),
                        )
                    else:
                        cursor = await connection.execute(
                            """INSERT INTO coding_session_bridge_outbox (
                                 dedupe_key, envelope_json, envelope_sha256
                               ) VALUES (?, ?, ?)
                               ON CONFLICT(dedupe_key) WHERE dedupe_key IS NOT NULL
                               DO NOTHING""",
                            (dedupe_key, serialized, digest),
                        )
                    if cursor.rowcount == 1:
                        ids.append(int(cursor.lastrowid))
                        continue
                    existing_cursor = await connection.execute(
                        """SELECT id, envelope_sha256
                           FROM coding_session_bridge_outbox
                           WHERE dedupe_key = ?""",
                        (dedupe_key,),
                    )
                    row = await existing_cursor.fetchone()
                    if row is None or str(row["envelope_sha256"]) != digest:
                        raise BridgeMutationConflict(
                            "stable import identity was reused with different bytes"
                        )
                    ids.append(int(row["id"]))
                    duplicates += 1
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        return ids, duplicates

    async def _commit_enqueue(
        self,
        *,
        dedupe_key: str | None,
        serialized: str,
        digest: str,
    ) -> tuple[int, bool]:
        """Use a private, FULL-sync transaction for the durable-ack boundary.

        The application's general repositories intentionally share one
        aiosqlite connection. Their multi-step writes can therefore commit or
        roll back one another when coroutines interleave. A hook 202 has a
        stronger promise: its row must already be independently durable. This
        short connection targets the same SQLite database (not a second
        store), takes one immediate transaction, fsyncs its WAL commit, and
        closes before the HTTP response is assembled.
        """

        async with aiosqlite.connect(str(self._db.path)) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                if dedupe_key is None:
                    cursor = await connection.execute(
                        """INSERT INTO coding_session_bridge_outbox (
                             dedupe_key, envelope_json, envelope_sha256
                           ) VALUES (?, ?, ?)""",
                        (None, serialized, digest),
                    )
                else:
                    cursor = await connection.execute(
                        """INSERT INTO coding_session_bridge_outbox (
                             dedupe_key, envelope_json, envelope_sha256
                           ) VALUES (?, ?, ?)
                           ON CONFLICT(dedupe_key) WHERE dedupe_key IS NOT NULL
                           DO NOTHING""",
                        (dedupe_key, serialized, digest),
                    )
                if cursor.rowcount == 1:
                    outbox_id = int(cursor.lastrowid)
                    duplicate = False
                else:
                    existing_cursor = await connection.execute(
                        """SELECT id, envelope_sha256
                           FROM coding_session_bridge_outbox
                           WHERE dedupe_key = ?""",
                        (dedupe_key,),
                    )
                    row = await existing_cursor.fetchone()
                    if row is None:
                        raise RuntimeError(
                            "stable bridge event disappeared inside its write transaction"
                        )
                    if str(row["envelope_sha256"]) != digest:
                        raise BridgeMutationConflict(
                            "stable hook event identity was reused with a different envelope"
                        )
                    outbox_id = int(row["id"])
                    duplicate = True
                await connection.commit()
                return outbox_id, duplicate
            except BaseException:
                await connection.rollback()
                raise

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
                return {
                    "sent": 0,
                    "failed": 0,
                    "blocked": "cloud_participation_disabled",
                }

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
                    """SELECT id, envelope_json, envelope_sha256, attempts, next_attempt_at
                       FROM coding_session_bridge_outbox ORDER BY id LIMIT 1"""
                )
                if row is None:
                    break
                if float(row["next_attempt_at"] or 0) > time.time():
                    break
                try:
                    serialized = str(row["envelope_json"])
                    actual_digest = hashlib.sha256(
                        serialized.encode("utf-8")
                    ).hexdigest()
                    if actual_digest != str(row["envelope_sha256"]):
                        raise AIDreamError(
                            500,
                            "persisted bridge envelope failed its SHA-256 integrity check",
                        )
                    try:
                        payload = json.loads(serialized)
                    except json.JSONDecodeError as exc:
                        raise AIDreamError(
                            500,
                            "persisted bridge envelope is not valid JSON",
                        ) from exc
                    try:
                        persisted_request = BridgeRequest.model_validate(payload)
                    except ValidationError as exc:
                        raise AIDreamError(
                            500,
                            "persisted bridge envelope no longer satisfies schema v1",
                        ) from exc
                    response = await client.post(
                        _SERVER_PATH,
                        payload,
                        jwt=str(token_row["access_token"]),
                        timeout=30.0,
                    )
                    _validate_upstream_acknowledgement(response, persisted_request)
                except (AIDreamOfflineError, AIDreamError) as exc:
                    await self._record_failure(
                        int(row["id"]), int(row["attempts"]), exc
                    )
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
            # Consume the wake signal before work. An enqueue that lands while
            # sync_pending is running then remains set and triggers the next
            # pass immediately instead of being cleared and delayed 15s.
            self._wake.clear()
            try:
                await self.sync_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[coding_session_bridge] publisher tick failed")
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
