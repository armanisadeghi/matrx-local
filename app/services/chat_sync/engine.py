"""Bidirectional sync engine for the chat.* canonical mirror.

One sync spine, same shape as the notes engine (documents/sync_engine.py):
an engine-owned background loop pulls credentials from the persisted
auth_tokens row every tick, pushes the sync_queue outbox, then pulls
incremental changes per table with checkpoints in sync_meta.

Doctrine (docs/SYNC_CONTRACT.md):
- Chat rows are append-mostly. Conflict policy is last-write-wins per ROW on
  ``updated_at``, EXCEPT a locally-changed row that is still pending in the
  outbox is never overwritten by a pull (the unpushed local change wins until
  it lands), and message rows are never destroyed — deletions travel as
  ``deleted_at`` tombstones in both directions.
- Loud failures: a permission/404 error from the cloud is an ERROR naming the
  table, never a silent skip.
- The cloud stamps ownership/audit columns (organization_id, created_by,
  updated_by, version) via triggers; pushes strip them.

Checkpoints: sync_meta rows keyed ``chat.<table>``; the keyset cursor
(cursor timestamp + pk of the last applied row) is stored as JSON in
``sync_meta.last_hash``.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

from app.common.system_logger import get_logger
from app.services.chat_sync.client import ChatSyncHTTPError, SupabaseChatClient
from app.services.local_db.database import get_db
from app.services.local_db.mirror_codec import decode_remote_row, encode_local_row
from app.services.local_db.mirror_schema import MIRROR_TABLES
from app.services.local_db.repositories import SyncMetaRepo, TokenRepo

logger = get_logger()

_SCHEMA = "chat"

# Parents before children — RLS on child tables authorizes through the
# conversation row, so it must exist in the cloud first.
_PUSH_ORDER = ["conversation", "user_request", "message", "tool_call", "media", "artifact"]

# Cloud-owned columns (filled by _stamp_actor/_stamp_org_default/_touch_row
# triggers); never pushed. updated_at IS pushed so cloud LWW reflects the
# real local edit time — the cloud _touch_row trigger only fills it when the
# payload omits it... it overwrites unconditionally, so the cloud timestamp
# wins and the echo-write below realigns the local row.
_STRIP_COLUMNS = frozenset({"organization_id", "created_by", "updated_by", "version"})

DEFAULT_INTERVAL = int(os.getenv("MATRX_CHAT_SYNC_INTERVAL", "300"))
_PULL_PAGE_SIZE = 500
_MAX_PAGES_PER_TABLE = 20
_PUSH_BATCH = 50
_PUSH_LIMIT_PER_CYCLE = 1000


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        raw = value.replace("Z", "+00:00").replace(" ", "T", 1)
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


class ChatSyncEngine:
    """Push the outbox, pull incremental changes, keep the mirror converged."""

    def __init__(self) -> None:
        self._client = SupabaseChatClient()
        self._meta = SyncMetaRepo()
        self._user_id: str | None = None
        self._sync_lock = asyncio.Lock()
        self._auto_task: asyncio.Task | None = None
        self._auto_stop = asyncio.Event()
        self._auto_last_skip_reason: str | None = None
        self._interval = DEFAULT_INTERVAL
        self._last_cycle: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, user_id: str, jwt: str) -> None:
        self._user_id = user_id
        self._client.set_jwt(jwt)

    @property
    def is_configured(self) -> bool:
        return bool(self._user_id and self._client.available)

    # ------------------------------------------------------------------
    # Full cycle
    # ------------------------------------------------------------------

    async def sync_cycle(self) -> dict[str, Any]:
        """One push + pull pass. Returns a summary dict."""
        if not self.is_configured:
            raise RuntimeError("chat sync engine not configured (no user/JWT)")
        async with self._sync_lock:
            pushed = await self._push_pending()
            pulled = await self._pull_changes()
        summary = {
            "pushed": pushed,
            "pulled": pulled,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self._last_cycle = summary
        return summary

    # ------------------------------------------------------------------
    # Outbound — drain the outbox
    # ------------------------------------------------------------------

    async def _push_pending(self) -> dict[str, Any]:
        db = get_db()
        rows = await db.fetchall(
            "SELECT id, entity_type, entity_id, attempts FROM sync_queue "
            "WHERE entity_type LIKE 'chat.%' ORDER BY created_at LIMIT ?",
            (_PUSH_LIMIT_PER_CYCLE,),
        )
        if not rows:
            return {"sent": 0, "failed": 0}

        by_table: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            table = r["entity_type"].split(".", 1)[1]
            by_table.setdefault(table, []).append(dict(r))

        known = set(MIRROR_TABLES[_SCHEMA])
        ordered = [t for t in _PUSH_ORDER if t in by_table] + sorted(
            t for t in by_table if t not in _PUSH_ORDER
        )

        sent = failed = 0
        for table in ordered:
            entries = by_table[table]
            if table not in known:
                logger.error(
                    "[chat_sync] outbox references unknown table chat.%s (%d rows) — "
                    "snapshot stale? Entries left in queue.",
                    table, len(entries),
                )
                failed += len(entries)
                continue
            try:
                s, f = await self._push_table(table, entries)
                sent += s
                failed += f
            except ChatSyncHTTPError as exc:
                if exc.is_auth:
                    logger.error(
                        "[chat_sync] PUSH ABORTED — cloud rejected our JWT on chat.%s "
                        "(HTTP %s). Waiting for a fresh token.",
                        table, exc.status_code,
                    )
                    failed += len(entries)
                    break
                logger.error("[chat_sync] push chat.%s failed: %s", table, exc)
                failed += len(entries)
        return {"sent": sent, "failed": failed}

    async def _push_table(self, table: str, entries: list[dict[str, Any]]) -> tuple[int, int]:
        db = get_db()
        spec = MIRROR_TABLES[_SCHEMA][table]
        pk = spec["pk"][0]

        payloads: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (queue_row, payload)
        for entry in entries:
            local = await db.fetchone(
                f'SELECT * FROM "{_SCHEMA}"."{table}" WHERE "{pk}" = ?',
                (entry["entity_id"],),
            )
            if local is None:
                logger.warning(
                    "[chat_sync] outbox row for chat.%s %s has no local row — dropping",
                    table, entry["entity_id"],
                )
                await self._meta.remove_from_queue(entry["id"])
                continue
            payload = encode_local_row(_SCHEMA, table, dict(local), strip=_STRIP_COLUMNS)
            # NOT NULL ownership column some child tables carry; local rows
            # written before sign-in may lack it.
            if "user_id" in spec["columns"] and payload.get("user_id") is None:
                payload["user_id"] = self._user_id
            payloads.append((entry, payload))

        sent = failed = 0
        for i in range(0, len(payloads), _PUSH_BATCH):
            batch = payloads[i : i + _PUSH_BATCH]
            try:
                returned = await self._client.upsert_rows(
                    table, [p for _, p in batch], pk_col=pk
                )
                # Remove the drained queue rows BEFORE applying the echo: a
                # mid-push local edit re-enqueues under a fresh queue id, so
                # after this delete "still pending" precisely means "changed
                # again since we read it" — and the echo defers to it.
                for entry, _ in batch:
                    await self._meta.remove_from_queue(entry["id"])
                await self._apply_cloud_echo(table, returned)
                sent += len(batch)
            except ChatSyncHTTPError as exc:
                if exc.is_auth:
                    raise
                # Isolate poison rows so one bad payload can't wedge the queue.
                logger.error(
                    "[chat_sync] batch push to chat.%s failed (HTTP %s) — retrying "
                    "rows individually: %s",
                    table, exc.status_code, exc.body,
                )
                for entry, payload in batch:
                    try:
                        returned = await self._client.upsert_rows(table, [payload], pk_col=pk)
                        await self._meta.remove_from_queue(entry["id"])
                        await self._apply_cloud_echo(table, returned)
                        sent += 1
                    except ChatSyncHTTPError as row_exc:
                        if row_exc.is_auth:
                            raise
                        failed += 1
                        await self._meta.increment_attempts(entry["id"])
                        logger.error(
                            "[chat_sync] PUSH FAILED chat.%s id=%s attempts=%d "
                            "HTTP %s: %s",
                            table, entry["entity_id"], entry["attempts"] + 1,
                            row_exc.status_code, row_exc.body,
                        )
        return sent, failed

    async def _apply_cloud_echo(self, table: str, returned: list[dict[str, Any]]) -> None:
        """Write cloud-stamped rows (trigger-filled columns, cloud updated_at)
        back into the mirror WITHOUT enqueueing, so the next pull doesn't see
        our own push as a foreign change. Rows with a NEW pending outbox entry
        (user edited mid-push) are left alone — their change wins locally
        until the next push."""
        for row in returned:
            await self._apply_remote_row(table, row, source="echo")
        await get_db().commit()

    # ------------------------------------------------------------------
    # Inbound — incremental pull per table
    # ------------------------------------------------------------------

    async def _pull_changes(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for table, spec in MIRROR_TABLES[_SCHEMA].items():
            cursor_col = spec["cursor_col"]
            if cursor_col is None:
                continue
            entity = f"{_SCHEMA}.{table}"
            try:
                applied = await self._pull_table(table, spec, cursor_col, entity)
                results[table] = applied
                await self._meta.set_last_sync(
                    entity,
                    status="success",
                    last_hash=await self._current_cursor_json(entity),
                )
            except ChatSyncHTTPError as exc:
                logger.error(
                    "[chat_sync] PULL FAILED for %s — HTTP %s: %s",
                    entity, exc.status_code, exc.body,
                )
                await self._meta.set_last_sync(
                    entity, status="error", last_hash=await self._current_cursor_json(entity),
                    error_message=f"HTTP {exc.status_code}: {exc.body}",
                )
                results[table] = {"error": exc.status_code}
                if exc.is_auth:
                    break
        return results

    async def _current_cursor_json(self, entity: str) -> str | None:
        return self._cursors.get(entity)

    # In-memory copy of the per-entity cursor JSON, flushed to sync_meta after
    # each table completes (and re-hydrated from sync_meta on first use).
    @property
    def _cursors(self) -> dict[str, str]:
        if not hasattr(self, "_cursor_cache"):
            self._cursor_cache: dict[str, str] = {}
        return self._cursor_cache

    async def _load_cursor(self, entity: str) -> tuple[str | None, str | None]:
        if entity not in self._cursors:
            meta = await self._meta.get_last_sync(entity)
            self._cursors[entity] = (meta or {}).get("last_hash") or ""
        raw = self._cursors.get(entity) or ""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        return data.get("ts"), data.get("id")

    async def _pull_table(
        self, table: str, spec: dict[str, Any], cursor_col: str, entity: str
    ) -> dict[str, int]:
        pk = spec["pk"][0]
        cursor_ts, cursor_id = await self._load_cursor(entity)
        db = get_db()
        applied = skipped = pages = 0

        while pages < _MAX_PAGES_PER_TABLE:
            rows = await self._client.get_rows_since(
                table,
                cursor_col=cursor_col,
                pk_col=pk,
                cursor_ts=cursor_ts,
                cursor_id=cursor_id,
                limit=_PULL_PAGE_SIZE,
            )
            if not rows:
                break
            pages += 1
            for row in rows:
                changed = await self._apply_remote_row(table, row, source="pull")
                if changed:
                    applied += 1
                else:
                    skipped += 1
            await db.commit()
            last = rows[-1]
            cursor_ts = last.get(cursor_col) or cursor_ts
            cursor_id = str(last.get(pk)) if last.get(pk) is not None else cursor_id
            self._cursors[entity] = json.dumps({"ts": cursor_ts, "id": cursor_id})
            if len(rows) < _PULL_PAGE_SIZE:
                break
        if pages >= _MAX_PAGES_PER_TABLE:
            logger.warning(
                "[chat_sync] pull for %s hit the %d-page cap this cycle — more rows "
                "remain; the next cycle continues from the checkpoint",
                entity, _MAX_PAGES_PER_TABLE,
            )
        return {"applied": applied, "skipped": skipped, "pages": pages}

    async def _apply_remote_row(
        self, table: str, remote: dict[str, Any], *, source: str
    ) -> bool:
        """Upsert one cloud row into the mirror (never enqueues).

        LWW guard: a row with a pending outbox entry keeps its local state
        unless the remote version is strictly newer. Nothing is ever
        hard-deleted here — tombstones arrive as deleted_at values.
        """
        spec = MIRROR_TABLES[_SCHEMA][table]
        pk = spec["pk"][0]
        db = get_db()
        decoded = decode_remote_row(_SCHEMA, table, remote)
        row_id = decoded.get(pk)
        if row_id is None:
            logger.error(
                "[chat_sync] %s row from cloud is missing its primary key %r — skipped",
                table, pk,
            )
            return False

        unknown = [c for c in remote if c not in spec["pg_types"]]
        if unknown:
            logger.warning(
                "[chat_sync] chat.%s cloud row carries columns not in the local "
                "snapshot %s — refresh schema_mirror/snapshot.json (values not stored)",
                table, unknown,
            )

        local = await db.fetchone(
            f'SELECT * FROM "{_SCHEMA}"."{table}" WHERE "{pk}" = ?', (str(row_id),)
        )
        if local is not None:
            pending = await db.fetchone(
                "SELECT 1 FROM sync_queue WHERE entity_type = ? AND entity_id = ?",
                (f"{_SCHEMA}.{table}", str(row_id)),
            )
            if source == "echo":
                # Our own push coming back cloud-stamped: apply it verbatim —
                # UNLESS the row changed again locally mid-push (a fresh
                # outbox entry exists); then the new local change wins until
                # the next push.
                if pending:
                    return False
            elif "updated_at" in spec["columns"]:
                remote_ts = _parse_ts(decoded.get("updated_at"))
                local_ts = _parse_ts(dict(local).get("updated_at"))
                if pending and not (remote_ts and local_ts and remote_ts > local_ts):
                    # Local unpushed change wins until it lands.
                    return False
                if remote_ts and local_ts and remote_ts <= local_ts:
                    return False

        cols = list(decoded.keys())
        col_sql = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        update_sql = ", ".join(f'"{c}" = excluded."{c}"' for c in cols if c != pk)
        await db.execute(
            f'INSERT INTO "{_SCHEMA}"."{table}" ({col_sql}) VALUES ({placeholders}) '
            f'ON CONFLICT("{pk}") DO UPDATE SET {update_sql}',
            tuple(decoded[c] for c in cols),
        )
        return True

    # ------------------------------------------------------------------
    # Background loop — engine-owned, creds from the persisted token row
    # ------------------------------------------------------------------

    @property
    def auto_sync_active(self) -> bool:
        return self._auto_task is not None and not self._auto_task.done()

    async def start_background_sync(self, interval_seconds: int | None = None) -> None:
        if self.auto_sync_active:
            return
        self._interval = interval_seconds or DEFAULT_INTERVAL
        self._auto_stop.clear()
        self._auto_task = asyncio.create_task(
            self._auto_sync_loop(self._interval), name="chat-auto-sync"
        )
        logger.info("[chat_sync] auto-sync started (interval=%ss)", self._interval)

    async def stop_background_sync(self) -> None:
        self._auto_stop.set()
        if self._auto_task:
            self._auto_task.cancel()
            try:
                await self._auto_task
            except asyncio.CancelledError:
                pass
            self._auto_task = None
        logger.info("[chat_sync] auto-sync stopped")

    async def _auto_sync_loop(self, interval_seconds: int) -> None:
        while not self._auto_stop.is_set():
            try:
                await self._auto_sync_tick()
            except Exception:
                logger.warning("[chat_sync] auto-sync tick crashed", exc_info=True)
            try:
                await asyncio.wait_for(self._auto_stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _auto_sync_tick(self) -> None:
        repo = TokenRepo()
        row = await repo.get()
        if not row or not row.get("access_token") or not row.get("user_id"):
            if self._auto_last_skip_reason != "no_token":
                logger.info("[chat_sync] idle — no signed-in user (will retry each tick)")
                self._auto_last_skip_reason = "no_token"
            return
        if repo.is_expired(row):
            if self._auto_last_skip_reason != "expired":
                logger.warning(
                    "[chat_sync] idle — stored JWT is expired; waiting for the "
                    "frontend to refresh it via POST /auth/token"
                )
                self._auto_last_skip_reason = "expired"
            return
        self._auto_last_skip_reason = None
        self.configure(row["user_id"], row["access_token"])
        await self.sync_cycle()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        db = get_db()
        pending = await db.fetchone(
            "SELECT COUNT(*) AS cnt FROM sync_queue WHERE entity_type LIKE 'chat.%'"
        )
        tables: dict[str, Any] = {}
        for meta in await self._meta.get_all_sync_status():
            entity = meta.get("entity_type") or ""
            if entity.startswith("chat."):
                tables[entity] = {
                    "last_synced_at": meta.get("last_synced_at"),
                    "status": meta.get("status"),
                    "error": meta.get("error_message"),
                    "cursor": meta.get("last_hash"),
                }
        return {
            "configured": self.is_configured,
            "auto_sync_active": self.auto_sync_active,
            "interval_seconds": self._interval,
            "pending_outbox": pending["cnt"] if pending else 0,
            "last_cycle": self._last_cycle,
            "tables": tables,
        }


_ENGINE: ChatSyncEngine | None = None


def get_chat_sync_engine() -> ChatSyncEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ChatSyncEngine()
    return _ENGINE
