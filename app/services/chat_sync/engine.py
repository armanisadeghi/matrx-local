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
import re
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


_DEAD_LETTER_ATTEMPTS = 5

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value or ""))


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


def _shape_compatible_batches(
    payloads: list[tuple[dict[str, Any], dict[str, Any]]],
    batch_size: int,
) -> list[list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Group PostgREST upserts by identical object keys.

    PostgREST rejects a JSON array when its objects have different key sets
    (PGRST102). Mirror encoding intentionally omits ``None`` values, so rows
    from the same table routinely have different shapes. Grouping retains the
    merge semantics without turning every healthy push into an error/retry
    cycle.
    """
    by_shape: dict[
        tuple[str, ...], list[tuple[dict[str, Any], dict[str, Any]]]
    ] = {}
    for item in payloads:
        shape = tuple(sorted(item[1]))
        by_shape.setdefault(shape, []).append(item)

    batches: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
    for items in by_shape.values():
        for index in range(0, len(items), batch_size):
            batches.append(items[index : index + batch_size])
    return batches


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
        self._warned_no_cursor: dict[str, bool] = {}

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
            # PULL FIRST. Push is an unconditional upsert (the cloud has no
            # row-version predicate), so the only LWW the cloud gets is what
            # we enforce here: learn the cloud's newer rows first — the apply
            # guard keeps genuinely-newer local pending edits — and only then
            # push what is still pending. Push-first would let a device that
            # was offline for a week blindly clobber a week of cloud edits.
            pulled = await self._pull_changes()
            pushed = await self._push_pending()
        summary = {
            "pushed": pushed,
            "pulled": pulled,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self._last_cycle = summary
        return summary

    async def configure_from_persisted_token(self) -> bool:
        """Refresh sync credentials from the desktop's durable auth token."""
        repo = TokenRepo()
        row = await repo.get()
        if not row or not row.get("access_token") or not row.get("user_id"):
            return False
        if repo.is_expired(row):
            return False
        self.configure(str(row["user_id"]), str(row["access_token"]))
        return True

    async def flush_pending(self) -> dict[str, Any]:
        """Push local chat writes immediately without waiting for the timer.

        AI turns use this before their terminal completion event so the web
        route can resolve the new canonical conversation as soon as it promotes
        from ``/chat/new`` to ``/chat/{id}``.
        """
        if not await self.configure_from_persisted_token():
            raise RuntimeError("chat sync engine not configured (no valid user/JWT)")
        async with self._sync_lock:
            pushed = await self._push_pending()
        return pushed

    async def hydrate_conversation(self, conversation_id: str) -> bool:
        """Pull one cloud conversation and its history into the local mirror.

        The periodic keyset pull is intentionally eventual. An interactive
        continuation cannot wait up to five minutes, and a cursor may already
        be past an older conversation, so this lookup is exact and RLS-scoped.
        """
        if not _looks_like_uuid(conversation_id):
            return False
        if not await self.configure_from_persisted_token():
            raise RuntimeError("chat sync engine not configured (no valid user/JWT)")

        async with self._sync_lock:
            rows = await self._client.get_rows(
                "conversation",
                params={"id": f"eq.{conversation_id}", "limit": "1"},
            )
            if not rows:
                return False

            await self._apply_remote_row("conversation", rows[0], source="pull")
            messages = await self._client.get_rows(
                "message",
                params={
                    "conversation_id": f"eq.{conversation_id}",
                    "order": "position.asc,created_at.asc,id.asc",
                    "limit": "5000",
                },
            )
            for row in messages:
                await self._apply_remote_row("message", row, source="pull")
            await get_db().commit()

        logger.info(
            "[chat_sync] targeted hydration complete conversation=%s messages=%d",
            conversation_id,
            len(messages),
        )
        return True

    # ------------------------------------------------------------------
    # Outbound — drain the outbox
    # ------------------------------------------------------------------

    async def _push_pending(self) -> dict[str, Any]:
        db = get_db()
        rows = await db.fetchall(
            "SELECT id, entity_type, entity_id, attempts FROM sync_queue "
            "WHERE entity_type LIKE 'chat.%' AND action != 'dead' "
            "ORDER BY created_at LIMIT ?",
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

        sent = failed = 0
        payloads: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (queue_row, payload)
        for entry in entries:
            entity_id = entry["entity_id"]
            if not _looks_like_uuid(entity_id):
                # Legacy localStorage-era ids (`<epoch>-<rand>`) can never
                # land in a uuid-typed cloud column — dead-letter instead of
                # retrying a guaranteed 22P02 forever. The row stays local.
                logger.error(
                    "[chat_sync] chat.%s id %r is not a UUID — cloud cannot store "
                    "it; DEAD-LETTERED (row remains local-only)",
                    table, entity_id,
                )
                await self._dead_letter(entry["id"])
                failed += 1
                continue
            local = await db.fetchone(
                f'SELECT * FROM "{_SCHEMA}"."{table}" WHERE "{pk}" = ?',
                (entity_id,),
            )
            if local is None:
                logger.warning(
                    "[chat_sync] outbox row for chat.%s %s has no local row — dropping",
                    table, entity_id,
                )
                await self._meta.remove_from_queue(entry["id"])
                continue
            local_row = dict(local)
            # Never push rows attributed to a DIFFERENT user (shared rows
            # pulled under this user's RLS, or rows from a previous account
            # on this machine). Pushing them either RLS-403s forever or, on
            # insert, re-attributes another user's content to this account.
            owner = local_row.get("created_by") or local_row.get("user_id")
            if owner and self._user_id and owner != self._user_id:
                logger.error(
                    "[chat_sync] chat.%s %s belongs to user %s (signed in: %s) — "
                    "DEAD-LETTERED, not pushed. Local edits to other users' rows "
                    "do not sync from this device.",
                    table, entity_id, owner, self._user_id,
                )
                await self._dead_letter(entry["id"])
                failed += 1
                continue
            payload = encode_local_row(_SCHEMA, table, local_row, strip=_STRIP_COLUMNS)
            # NOT NULL ownership column some child tables carry; local rows
            # written before sign-in may lack it (None or legacy '').
            if "user_id" in spec["columns"] and not payload.get("user_id"):
                payload["user_id"] = self._user_id
            payloads.append((entry, payload))

        for batch in _shape_compatible_batches(payloads, _PUSH_BATCH):
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
                        attempts = entry["attempts"] + 1
                        await self._meta.increment_attempts(entry["id"])
                        logger.error(
                            "[chat_sync] PUSH FAILED chat.%s id=%s attempts=%d "
                            "HTTP %s: %s",
                            table, entry["entity_id"], attempts,
                            row_exc.status_code, row_exc.body,
                        )
                        # Permanent 4xx after repeated tries can never succeed
                        # with the same payload; dead-letter so poison rows
                        # cannot starve the 1000-row push window forever.
                        if row_exc.is_permanent and attempts >= _DEAD_LETTER_ATTEMPTS:
                            logger.error(
                                "[chat_sync] chat.%s id=%s DEAD-LETTERED after %d "
                                "permanent failures — inspect via /chat/mirror/status",
                                table, entry["entity_id"], attempts,
                            )
                            await self._dead_letter(entry["id"])
        if failed:
            await self._meta.set_last_sync(
                f"{_SCHEMA}.{table}",
                status="error",
                last_hash=await self._current_cursor_json(f"{_SCHEMA}.{table}"),
                error_message=f"push: {failed} row(s) failed this cycle",
            )
        return sent, failed

    async def _dead_letter(self, queue_id: int) -> None:
        db = get_db()
        await db.execute(
            "UPDATE sync_queue SET action='dead', attempts=attempts+1 WHERE id = ?",
            (queue_id,),
        )
        await db.commit()

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
                if not self._warned_no_cursor.get(table):
                    logger.warning(
                        "[chat_sync] chat.%s has no updated_at/created_at column — "
                        "NOT pulled (no incremental cursor possible)",
                        table,
                    )
                    self._warned_no_cursor[table] = True
                results[table] = {"skipped": "no_cursor_column"}
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

        # The pending-outbox / LWW decision lives INSIDE the upsert statement.
        # A separate check-then-write pair leaves an event-loop-yield-wide
        # window in which a request handler can commit a fresh local edit that
        # this write would then silently overwrite (and the next push would
        # publish the overwritten state — permanent loss). One SQL statement
        # is atomic on the shared connection; no interleave is possible.
        #
        # Guard semantics:
        #   echo: apply unless the row changed again locally mid-push (a
        #         fresh outbox entry exists) — the new local change wins.
        #   pull: apply only if the remote row is strictly newer than the
        #         local one (timestamps normalized: local uses ...Z, cloud
        #         uses ...+00:00 — same instant must compare equal).
        entity_type = f"{_SCHEMA}.{table}"
        no_pending_sql = (
            "NOT EXISTS (SELECT 1 FROM main.sync_queue q "
            "WHERE q.entity_type = ? AND q.entity_id = ?)"
        )
        if source == "echo":
            guard_sql = no_pending_sql
            guard_params: tuple[Any, ...] = (entity_type, str(row_id))
        elif "updated_at" in spec["columns"]:
            # Remote strictly newer applies (even over a pending local edit —
            # it lost LWW; the surviving queue entry then pushes back the
            # merged row, a harmless echo). Remote older/equal never applies.
            guard_sql = (
                f"replace(COALESCE(excluded.updated_at, ''), 'Z', '+00:00') > "
                f'replace(COALESCE("{table}".updated_at, \'\'), \'Z\', \'+00:00\')'
            )
            guard_params = ()
        else:
            # Append-only tables (created_at cursor): apply unless a local
            # pending change exists for the same row.
            guard_sql = no_pending_sql
            guard_params = (entity_type, str(row_id))

        cols = list(decoded.keys())
        col_sql = ", ".join(f'"{c}"' for c in cols)
        placeholders = ", ".join("?" for _ in cols)
        update_sql = ", ".join(f'"{c}" = excluded."{c}"' for c in cols if c != pk)
        before = db.db.total_changes
        await db.execute(
            f'INSERT INTO "{_SCHEMA}"."{table}" ({col_sql}) VALUES ({placeholders}) '
            f'ON CONFLICT("{pk}") DO UPDATE SET {update_sql} WHERE {guard_sql}',
            tuple(decoded[c] for c in cols) + guard_params,
        )
        return db.db.total_changes > before

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
            "SELECT COUNT(*) AS cnt FROM sync_queue "
            "WHERE entity_type LIKE 'chat.%' AND action != 'dead'"
        )
        dead = await db.fetchone(
            "SELECT COUNT(*) AS cnt FROM sync_queue "
            "WHERE entity_type LIKE 'chat.%' AND action = 'dead'"
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
            "dead_letter": dead["cnt"] if dead else 0,
            "last_cycle": self._last_cycle,
            "tables": tables,
        }


_ENGINE: ChatSyncEngine | None = None


def get_chat_sync_engine() -> ChatSyncEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ChatSyncEngine()
    return _ENGINE
