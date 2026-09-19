"""Bidirectional sync for ONE custom Table's records, desktop <-> the store.

Same shape as ``app/services/chat_sync/engine.py`` — pull BEFORE push, a
checkpointed incremental pull, an outbox drained on reconnect, no silent
swallow — with the three differences the record store's contract forces:

* **Every read and write is a door.**  Pull is ``custom.read_records``, a new
  record is ``custom.anon_capture``, an edit is ``custom.record_update``.  No
  raw table is ever touched, and there is no second door.
* **The offline write is the contract, not a workaround.**  The device mints
  ``client_key`` before the first attempt (DOOR-21).  A replay of the same key
  returns the SAME record id and increments ``custom.anon_replay.replays``, so
  draining an outbox twice — the normal consequence of a crash between the
  write and the acknowledgement — cannot duplicate a record.
* **The whole path is gated on the campaign switch.**  When the store is off
  for this organization the feature is ABSENT and says so with a remedy
  (law 4).  It never half-works and never quietly writes nowhere.

The read door does not offer a "changed since" cursor — it offers
``p_limit``/``p_offset`` — so the pull is a bounded, resumable, checkpointed
pass over the Table rather than a keyset tail.  The checkpoint (``sync_meta``,
entity ``custom.record:<table_id>``) records the offset reached, so a pull
interrupted by a dropped connection resumes instead of restarting.
"""

from __future__ import annotations

import asyncio
import json
import platform as _platform
import uuid
from datetime import datetime, timezone
from typing import Any

from app.common.system_logger import get_logger
from app.services.local_db.database import get_db
from app.services.records_sync.client import CustomStoreClient, DoorTransport, RecordsStoreError

logger = get_logger()

_PAGE_SIZE = 200
_MAX_ATTEMPTS = 5
_QUEUE_ENTITY = "custom.record"

REMEDY_STORE_CLOSED = (
    "The custom record store is switched off for this organization, so the desktop "
    "records mirror is not available. Turn on the 'custom / system_enabled' setting "
    "for this organization and sync again."
)
REMEDY_OFFLINE = (
    "This device cannot reach AI Matrx right now. Your records are safe in the local "
    "mirror and everything you wrote is queued — it syncs by itself on reconnect."
)
REMEDY_STORE_UNREACHABLE = (
    "The custom record store's doors are not reachable over the client wire yet "
    "(the database does not expose the 'custom' schema to PostgREST). The records "
    "mirror stays off until it does; nothing was written and nothing was lost."
)


class RecordsMirrorUnavailable(RuntimeError):
    """The feature is absent right now, and this says why and what to do."""

    def __init__(self, reason: str, remedy: str) -> None:
        self.reason = reason
        self.remedy = remedy
        super().__init__(f"{reason} — {remedy}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordsSyncEngine:
    """Push the outbox, pull the Table, keep the local copy converged."""

    def __init__(self, client: CustomStoreClient | None = None) -> None:
        self._client = client or CustomStoreClient()
        self._user_id: str | None = None
        self._organization_id: str | None = None
        self._table_ids: list[str] = []
        self._device = f"matrx-local/{_platform.node()}"
        self._lock = asyncio.Lock()
        self._last_cycle: dict[str, Any] = {}
        self._gate: dict[str, Any] = {"checked_at": None, "open": None, "reason": None}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        user_id: str,
        jwt: str | None,
        organization_id: str,
        table_ids: list[str],
        transport: DoorTransport | None = None,
    ) -> None:
        self._user_id = user_id
        self._organization_id = organization_id
        self._table_ids = list(table_ids)
        if transport is not None:
            self._client.set_transport(transport)
        if jwt is not None:
            self._client.set_jwt(jwt)

    @property
    def is_configured(self) -> bool:
        return bool(self._user_id and self._organization_id and self._table_ids and self._client.available)

    async def configure_from_persisted_token(self, *, organization_id: str, table_ids: list[str]) -> bool:
        """Take the device's session from the sync daemon (the one token holder)."""
        from app.services.local_db.repositories import TokenRepo

        repo = TokenRepo()
        row = await repo.get()
        if not row or not row.get("access_token") or not row.get("user_id"):
            return False
        if repo.is_expired(row):
            return False
        self.configure(
            user_id=str(row["user_id"]),
            jwt=str(row["access_token"]),
            organization_id=organization_id,
            table_ids=table_ids,
        )
        return True

    # ------------------------------------------------------------------
    # The switch — law 4: absent and loud, never dead and never degraded
    # ------------------------------------------------------------------

    async def assert_store_open(self) -> None:
        if not self._organization_id:
            raise RecordsMirrorUnavailable(
                "No organization selected for the records mirror",
                "Choose the organization whose records this device should mirror.",
            )
        try:
            is_open = await self._client.store_is_open(self._organization_id)
        except RecordsStoreError as exc:
            if exc.is_transport:
                self._gate = {"checked_at": _now(), "open": None, "reason": "offline"}
                logger.error("[records_sync] store unreachable (offline): %s", exc)
                raise RecordsMirrorUnavailable(
                    "This device is offline, so the store's switch cannot be read", REMEDY_OFFLINE
                ) from exc
            if exc.is_unreachable_schema:
                self._gate = {"checked_at": _now(), "open": False, "reason": "wire-closed"}
                logger.error("[records_sync] store doors unreachable: %s", exc)
                raise RecordsMirrorUnavailable(
                    "The record store's doors are not on the client wire", REMEDY_STORE_UNREACHABLE
                ) from exc
            raise
        self._gate = {"checked_at": _now(), "open": bool(is_open), "reason": None if is_open else "switch-off"}
        if not is_open:
            logger.error(
                "[records_sync] store CLOSED for organization %s — mirror unavailable",
                self._organization_id,
            )
            raise RecordsMirrorUnavailable(
                "The custom record store is switched off for this organization",
                REMEDY_STORE_CLOSED,
            )

    # ------------------------------------------------------------------
    # Local authoring — works with the platform unreachable
    # ------------------------------------------------------------------

    async def create_record(self, table_id: str, document: dict[str, Any]) -> str:
        """Author a record locally.  Never touches the network.

        The client key is minted HERE, before any attempt, which is what makes
        the deferred write idempotent no matter how often it is replayed.
        """
        if not self._organization_id:
            raise RecordsMirrorUnavailable(
                "No organization selected for the records mirror",
                "Choose the organization whose records this device should mirror.",
            )
        db = get_db()
        local_id = str(uuid.uuid4())
        client_key = f"matrx-local:{local_id}"
        now = _now()
        await db.execute(
            "INSERT INTO custom_record_mirror (local_id, record_id, client_key, organization_id, "
            "table_id, document, version, level, origin, state, captured_at, updated_at) "
            "VALUES (?, NULL, ?, ?, ?, ?, NULL, NULL, 'local', 'pending', ?, ?)",
            (local_id, client_key, self._organization_id, table_id, json.dumps(document), now, now),
        )
        await db.execute(
            "INSERT INTO sync_queue (entity_type, entity_id, action, payload) VALUES (?, ?, 'capture', '{}')",
            (_QUEUE_ENTITY, local_id),
        )
        await db.commit()
        return local_id

    async def edit_record(self, local_id: str, patch: dict[str, Any]) -> None:
        """Edit a mirrored record locally.  Never touches the network.

        The outbox carries ONE pending intent per record: a queued capture
        already carries the whole document, so an edit folds into it; a queued
        update absorbs the new fields; otherwise a fresh update is enqueued.
        """
        db = get_db()
        row = await db.fetchone(
            "SELECT document FROM custom_record_mirror WHERE local_id = ?", (local_id,)
        )
        if row is None:
            raise KeyError(f"no mirrored record {local_id}")
        document = json.loads(row["document"] or "{}")
        document.update(patch)
        await db.execute(
            "UPDATE custom_record_mirror SET document = ?, state = 'pending', updated_at = ? "
            "WHERE local_id = ?",
            (json.dumps(document), _now(), local_id),
        )
        queued = await db.fetchone(
            "SELECT id, action, payload FROM sync_queue WHERE entity_type = ? AND entity_id = ? "
            "AND action IN ('capture','update') ORDER BY id DESC LIMIT 1",
            (_QUEUE_ENTITY, local_id),
        )
        if queued is None:
            await db.execute(
                "INSERT INTO sync_queue (entity_type, entity_id, action, payload) "
                "VALUES (?, ?, 'update', ?)",
                (_QUEUE_ENTITY, local_id, json.dumps({"patch": patch})),
            )
        elif queued["action"] == "update":
            previous = json.loads(queued["payload"] or "{}").get("patch", {})
            await db.execute(
                "UPDATE sync_queue SET payload = ? WHERE id = ?",
                (json.dumps({"patch": {**previous, **patch}}), queued["id"]),
            )
        await db.commit()

    # ------------------------------------------------------------------
    # One cycle
    # ------------------------------------------------------------------

    async def sync_cycle(self) -> dict[str, Any]:
        if not self.is_configured:
            raise RecordsMirrorUnavailable(
                "The records mirror is not configured (no signed-in user)",
                "Sign in to AI Matrx on this device and sync again.",
            )
        async with self._lock:
            await self.assert_store_open()
            # PULL FIRST, exactly as chat does: learn the store's newer rows
            # before offering ours, so a device that was offline for a week
            # cannot blindly overwrite a week of other people's edits.
            pulled = await self._pull()
            pushed = await self._push()
        summary = {"pulled": pulled, "pushed": pushed, "at": _now()}
        self._last_cycle = summary
        return summary

    # ------------------------------------------------------------------
    # Pull — read door only
    # ------------------------------------------------------------------

    async def _pull(self) -> dict[str, Any]:
        db = get_db()
        result: dict[str, Any] = {}
        for table_id in self._table_ids:
            entity = f"{_QUEUE_ENTITY}:{table_id}"
            checkpoint = await db.fetchone(
                "SELECT last_hash FROM sync_meta WHERE entity_type = ?", (entity,)
            )
            offset = 0
            if checkpoint is not None and checkpoint["last_hash"]:
                try:
                    offset = int(json.loads(checkpoint["last_hash"]).get("resume_offset", 0))
                except (ValueError, TypeError, json.JSONDecodeError):
                    offset = 0
            applied = skipped = 0
            try:
                while True:
                    rows = await self._client.read_records(
                        self._organization_id or "", table_id, limit=_PAGE_SIZE, offset=offset
                    )
                    for row in rows:
                        if await self._apply_remote_row(table_id, row):
                            applied += 1
                        else:
                            skipped += 1
                    offset += len(rows)
                    if len(rows) < _PAGE_SIZE:
                        break
                    await self._checkpoint(entity, {"resume_offset": offset}, status="running")
                await db.commit()
                await self._checkpoint(
                    entity, {"resume_offset": 0, "rows_seen": offset}, status="success"
                )
                result[table_id] = {"applied": applied, "kept_local": skipped, "rows": offset}
            except RecordsStoreError as exc:
                # Loud, checkpointed, resumable. Never a silent partial pull.
                await db.commit()
                await self._checkpoint(
                    entity, {"resume_offset": offset}, status="error", error=str(exc)
                )
                logger.error("[records_sync] pull custom.%s failed: %s", table_id, exc)
                result[table_id] = {
                    "applied": applied,
                    "kept_local": skipped,
                    "error": str(exc),
                    "resume_offset": offset,
                }
        return result

    async def _apply_remote_row(self, table_id: str, row: dict[str, Any]) -> bool:
        """Write one store row into the mirror.  Returns False when kept local."""
        db = get_db()
        record_id = str(row.get("id"))
        document = row.get("document") or {}
        level = row.get("level")
        existing = await db.fetchone(
            "SELECT local_id, state FROM custom_record_mirror WHERE record_id = ?", (record_id,)
        )
        if existing is not None and existing["state"] in ("pending", "conflict"):
            # A local edit that has not been offered yet is never clobbered by
            # a pull; the push resolves it (and a real divergence becomes a
            # durable two-copy conflict below).
            await db.execute(
                "UPDATE custom_record_mirror SET store_document = ?, level = ?, updated_at = ? "
                "WHERE local_id = ?",
                (json.dumps(document), level, _now(), existing["local_id"]),
            )
            return False
        if existing is None:
            await db.execute(
                "INSERT INTO custom_record_mirror (local_id, record_id, client_key, organization_id, "
                "table_id, document, version, level, origin, state, updated_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, NULL, ?, 'cloud', 'synced', ?)",
                (
                    record_id,
                    record_id,
                    self._organization_id,
                    table_id,
                    json.dumps(document),
                    level,
                    _now(),
                ),
            )
        else:
            await db.execute(
                "UPDATE custom_record_mirror SET document = ?, level = ?, state = 'synced', "
                "store_document = NULL, updated_at = ? WHERE local_id = ?",
                (json.dumps(document), level, _now(), existing["local_id"]),
            )
        return True

    # ------------------------------------------------------------------
    # Push — the outbox, drained on reconnect
    # ------------------------------------------------------------------

    async def _push(self) -> dict[str, Any]:
        db = get_db()
        entries = await db.fetchall(
            "SELECT id, entity_id, action, payload, attempts FROM sync_queue "
            "WHERE entity_type = ? ORDER BY id LIMIT 200",
            (_QUEUE_ENTITY,),
        )
        sent = failed = conflicts = dead = 0
        for entry in entries:
            row = await db.fetchone(
                "SELECT * FROM custom_record_mirror WHERE local_id = ?", (entry["entity_id"],)
            )
            if row is None:
                await self._dead_letter(entry["id"], "no mirrored record for this outbox entry")
                dead += 1
                continue
            try:
                if entry["action"] == "capture":
                    await self._push_capture(row)
                else:
                    await self._push_update(row, json.loads(entry["payload"] or "{}"))
                await db.execute("DELETE FROM sync_queue WHERE id = ?", (entry["id"],))
                await db.commit()
                sent += 1
            except RecordsStoreError as exc:
                if exc.is_conflict:
                    await self._record_conflict(row, exc)
                    await db.execute("DELETE FROM sync_queue WHERE id = ?", (entry["id"],))
                    await db.commit()
                    conflicts += 1
                    continue
                attempts = int(entry["attempts"] or 0) + 1
                if exc.is_permanent or attempts >= _MAX_ATTEMPTS:
                    await self._dead_letter(entry["id"], str(exc))
                    dead += 1
                else:
                    await db.execute(
                        "UPDATE sync_queue SET attempts = ? WHERE id = ?", (attempts, entry["id"])
                    )
                    await db.commit()
                    failed += 1
                logger.error(
                    "[records_sync] push %s %s failed (attempt %d): %s",
                    entry["action"], entry["entity_id"], attempts, exc,
                )
                if exc.is_transport or exc.is_auth:
                    # Offline or a dead token: stop the drain, keep the queue.
                    break
        return {"sent": sent, "retry": failed, "conflicts": conflicts, "dead": dead}

    async def _push_capture(self, row: Any) -> None:
        db = get_db()
        record_id = await self._client.anon_capture(
            row["organization_id"],
            row["client_key"],
            row["table_id"],
            json.loads(row["document"] or "{}"),
            device=self._device,
            captured_at=row["captured_at"],
        )
        await db.execute(
            "UPDATE custom_record_mirror SET record_id = ?, state = 'synced', updated_at = ? "
            "WHERE local_id = ?",
            (record_id, _now(), row["local_id"]),
        )

    async def _push_update(self, row: Any, payload: dict[str, Any]) -> None:
        db = get_db()
        if not row["record_id"]:
            raise RecordsStoreError(
                "record_update", 409, "record has never been captured — capture must drain first"
            )
        patch = payload.get("patch") or json.loads(row["document"] or "{}")
        version = await self._client.record_update(
            row["organization_id"],
            row["record_id"],
            patch,
            expected_version=row["version"],
        )
        await db.execute(
            "UPDATE custom_record_mirror SET version = ?, state = 'synced', store_document = NULL, "
            "updated_at = ? WHERE local_id = ?",
            (version, _now(), row["local_id"]),
        )

    async def _record_conflict(self, row: Any, exc: RecordsStoreError) -> None:
        """Both copies survive.  Nothing is overwritten and nothing is lost."""
        db = get_db()
        store_document = None
        try:
            store_document = await self._client.read_record(row["organization_id"], row["record_id"])
        except RecordsStoreError:
            logger.error("[records_sync] could not read the winning copy of %s", row["record_id"])
        await db.execute(
            "UPDATE custom_record_mirror SET state = 'conflict', store_document = ?, version = ?, "
            "updated_at = ? WHERE local_id = ?",
            (
                json.dumps(store_document) if store_document is not None else row["store_document"],
                exc.current_version,
                _now(),
                row["local_id"],
            ),
        )
        logger.error(
            "[records_sync] CONFLICT on record %s — both copies kept, version %s won: %s",
            row["record_id"], exc.current_version, exc,
        )

    async def _dead_letter(self, queue_id: int, reason: str) -> None:
        db = get_db()
        await db.execute(
            "UPDATE sync_queue SET action = 'dead', payload = ? WHERE id = ?",
            (json.dumps({"reason": reason[:500]}), queue_id),
        )
        await db.commit()
        logger.error("[records_sync] dead-lettered outbox entry %s: %s", queue_id, reason)

    async def _checkpoint(
        self, entity: str, cursor: dict[str, Any], *, status: str, error: str | None = None
    ) -> None:
        db = get_db()
        now = _now()
        await db.execute(
            "INSERT INTO sync_meta (entity_type, last_synced_at, last_hash, status, error_message, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(entity_type) DO UPDATE SET "
            "last_synced_at=excluded.last_synced_at, last_hash=excluded.last_hash, "
            "status=excluded.status, error_message=excluded.error_message, updated_at=excluded.updated_at",
            (entity, now, json.dumps(cursor), status, error, now),
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        db = get_db()
        pending = await db.fetchone(
            "SELECT COUNT(*) AS n FROM sync_queue WHERE entity_type = ? AND action IN ('capture','update')",
            (_QUEUE_ENTITY,),
        )
        dead = await db.fetchone(
            "SELECT COUNT(*) AS n FROM sync_queue WHERE entity_type = ? AND action = 'dead'",
            (_QUEUE_ENTITY,),
        )
        conflicts = await db.fetchone(
            "SELECT COUNT(*) AS n FROM custom_record_mirror WHERE state = 'conflict'"
        )
        mirrored = await db.fetchone("SELECT COUNT(*) AS n FROM custom_record_mirror")
        checkpoints = await db.fetchall(
            "SELECT entity_type, last_synced_at, last_hash, status, error_message FROM sync_meta "
            "WHERE entity_type LIKE ?",
            (f"{_QUEUE_ENTITY}:%",),
        )
        return {
            "configured": self.is_configured,
            "organization_id": self._organization_id,
            "tables": self._table_ids,
            "switch": self._gate,
            "mirrored_records": mirrored["n"] if mirrored else 0,
            "pending": pending["n"] if pending else 0,
            "dead_letters": dead["n"] if dead else 0,
            "conflicts": conflicts["n"] if conflicts else 0,
            "checkpoints": [dict(r) for r in checkpoints],
            "last_cycle": self._last_cycle,
        }


_engine: RecordsSyncEngine | None = None


def get_records_sync_engine() -> RecordsSyncEngine:
    global _engine
    if _engine is None:
        _engine = RecordsSyncEngine()
    return _engine
