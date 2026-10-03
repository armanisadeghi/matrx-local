"""Matrx Local's side of ``matrx_coding_history``'s ports.

The importer and the capture reconciler live in ``packages/matrx-coding-history`` (shared with
Matrx 2). This module hands them what only Matrx Local has: the sync daemon's token
(``TokenRepo``), the AIDream client, the ``claude_capture_backfill`` table, and — passed straight
through, unchanged — the durable hook delivery queue (``CodingSessionBridgeOutbox``) and
``SyncMetaRepo``, which already satisfy the ``BridgeSink`` and ``SyncMeta`` ports.
"""

from __future__ import annotations

from typing import Any

from matrx_coding_history.ports import CloudError, CloudOffline

from app.services.aidream.client import AIDreamClient, AIDreamError, AIDreamOfflineError
from app.services.local_db.database import LocalDatabase
from app.services.local_db.repositories import TokenRepo
from app.services.local_db.write_gate import write_gate


class AIDreamCloudHttp:
    """``CloudHttp`` over Matrx Local's AIDreamClient, with one caller's token."""

    def __init__(self, client: AIDreamClient, jwt: str) -> None:
        self._client = client
        self._jwt = jwt

    async def get_json(self, path: str) -> Any:
        try:
            return await self._client.get(path, jwt=self._jwt)
        except AIDreamOfflineError as exc:
            raise CloudOffline(str(exc)) from exc
        except AIDreamError as exc:
            raise CloudError(exc.status, str(exc), body=getattr(exc, "body", None)) from exc

    async def post_json(self, path: str, body: Any, *, timeout: float) -> Any:
        try:
            return await self._client.post(path, body, jwt=self._jwt, timeout=timeout)
        except AIDreamOfflineError as exc:
            raise CloudOffline(str(exc)) from exc
        except AIDreamError as exc:
            raise CloudError(exc.status, str(exc), body=getattr(exc, "body", None)) from exc


class TokenRepoAccess:
    """``AccessTokens`` over the sync daemon's grant (``TokenRepo``)."""

    def __init__(self, tokens: TokenRepo) -> None:
        self._tokens = tokens

    async def user_id(self) -> str | None:
        row = await self._tokens.get()
        if not row or not row.get("user_id"):
            return None
        return str(row.get("user_id"))

    async def access_token(self) -> str | None:
        row = await self._tokens.get()
        if (
            not row
            or not row.get("access_token")
            or not row.get("user_id")
            or self._tokens.is_expired(row)
        ):
            return None
        return str(row["access_token"])


class SqliteBackfillLedger:
    """``BackfillLedger`` over the ``claude_capture_backfill`` table (SQL unchanged)."""

    def __init__(self, db: LocalDatabase) -> None:
        self._db = db

    async def attempts(self) -> dict[str, dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT session_key, source_revision, attempts, last_error "
            "FROM claude_capture_backfill"
        )
        return {str(row["session_key"]): dict(row) for row in rows}

    async def record_attempt(
        self, session_key: str, source_state: str | None, error: str | None
    ) -> None:
        """Record the outcome for one stat-fenced recovery candidate.

        Only failures consume retry budget. A successful enqueue clears the
        counter, and a changed stat fence is a genuinely new input deserving a
        fresh budget.
        """
        # Top-level write span on the shared connection; this insert lost the
        # lock race to the hook bridge at startup on 2026-09-12 ("pass FAILED").
        async with write_gate():
            await self._db.execute(
                """INSERT INTO claude_capture_backfill
                       (session_key, source_revision, attempts, last_error,
                        enqueued_at, updated_at)
                   VALUES (?, ?, CASE WHEN ? IS NULL THEN 0 ELSE 1 END, ?,
                           datetime('now'), datetime('now'))
                   ON CONFLICT(session_key) DO UPDATE SET
                       attempts = CASE
                           WHEN excluded.last_error IS NULL THEN 0
                           WHEN claude_capture_backfill.source_revision IS excluded.source_revision
                           THEN claude_capture_backfill.attempts + 1
                           ELSE 1
                       END,
                       source_revision = excluded.source_revision,
                       last_error = excluded.last_error,
                       enqueued_at = excluded.enqueued_at,
                       updated_at = excluded.updated_at""",
                (session_key, source_state, error, error),
            )
            await self._db.commit()

    async def recent(self, limit: int) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT session_key, attempts, last_error, enqueued_at "
            "FROM claude_capture_backfill ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def exhausted(self, max_attempts: int) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            """SELECT session_key, attempts, last_error, enqueued_at
               FROM claude_capture_backfill
               WHERE attempts >= ? AND last_error IS NOT NULL
               ORDER BY updated_at DESC""",
            (max_attempts,),
        )
        return [dict(row) for row in rows]


__all__ = [
    "AIDreamCloudHttp",
    "SqliteBackfillLedger",
    "TokenRepoAccess",
]
