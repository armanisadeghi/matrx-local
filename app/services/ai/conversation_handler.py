"""SQLite-backed ConversationStore for matrx-ai client-host mode.

Implements the ``matrx_ai.client_host.ConversationStore`` Protocol (matrx-ai
>= 0.3.0). The store is injected via ``matrx_ai.configure(conversation_store=
...)`` at startup (app/services/ai/engine.py); after that, EVERY conversation
write the classic execution path makes (gate, persist, tool logging) and the
history read delegate here.

Storage is the CANONICAL LOCAL MIRROR of the cloud chat schema
(``chat.conversation`` / ``chat.message`` / ``chat.user_request`` /
``chat.tool_call`` — see app/services/local_db/mirror.py). Local ids ARE the
canonical cloud ids; every write enqueues an outbox row so the chat sync
engine pushes the turn to the cloud (docs/SYNC_CONTRACT.md — contract gap #1
is closed by this pipeline).

Contract notes (matrx_ai/client_host/store.py is the source of truth):
  - The STORE owns idempotency: ``ensure_conversation_exists`` and
    ``create_pending_user_request`` may be called multiple times for the same
    ids (``ensure_user_request_exists`` in the gate also dispatches to
    ``create_pending_user_request``) — repeats must be no-ops.
  - matrx-ai swallows store failures with its own logs, so this store LOGS
    LOUDLY ITSELF before letting an exception propagate — a silent local
    persistence failure would otherwise be invisible.
  - ``get_conversation_config`` must RAISE when the conversation does not
    exist (the resolver treats that as "no local state").

Canonical-mapping notes:
  - ``chat.user_request`` has no conversation_id column in the cloud schema
    (the link lives in ``chat.request`` rows, which a client host does not
    produce). We keep the linkage in ``user_request.metadata.conversation_id``
    so local reads can group requests per conversation; the cloud accepts the
    metadata untouched.
  - matrx-ai's tool-log ``data`` dict is already shaped like a
    ``chat.tool_call`` row (it was written for the cx ORM this table came
    from), so tool logging maps keys straight onto canonical columns and
    keeps any unknown keys under ``metadata`` — nothing is dropped.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from app.common.system_logger import get_logger
from app.services.local_db.database import get_db
from app.services.local_db.mirror_schema import MIRROR_TABLES
from app.services.local_db.outbox import enqueue_change
from app.services.local_db.repositories import (
    ConversationsRepo,
    MessagesRepo,
    _now,
)

logger = get_logger()

_STORE_INSTANCE: "SQLiteConversationStore | None" = None


def get_conversation_store() -> "SQLiteConversationStore":
    """Return the singleton store instance, creating it lazily if needed."""
    global _STORE_INSTANCE
    if _STORE_INSTANCE is None:
        _STORE_INSTANCE = SQLiteConversationStore()
    return _STORE_INSTANCE


# Back-compat alias for any older call sites/tests (pre-0.3.0 naming).
get_conversation_handler = get_conversation_store


# Columns of chat.tool_call, straight from the generated mirror schema —
# used to split matrx-ai's tool-log data dict into canonical columns vs
# metadata extras.
_TOOL_CALL_COLUMNS: dict[str, str] = MIRROR_TABLES["chat"]["tool_call"]["columns"]


def _to_sql_value(value: Any, sqlite_type: str) -> Any:
    """Serialize a python value for a mirror column."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return int(value) if sqlite_type == "INTEGER" else float(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if sqlite_type == "TEXT" and not isinstance(value, str):
        return str(value)
    return value


class SQLiteConversationStore:
    """matrx_ai.client_host.ConversationStore backed by the canonical mirror."""

    def __init__(self) -> None:
        self._convs = ConversationsRepo()
        self._msgs = MessagesRepo()

    # ------------------------------------------------------------------
    # ConversationStore protocol — gate
    # ------------------------------------------------------------------

    async def ensure_conversation_exists(
        self,
        conversation_id: str,
        user_id: str,
        parent_conversation_id: str | None = None,
        variables: dict[str, Any] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        try:
            db = get_db()
            overrides = overrides or {}
            from matrx_connect.context.app_context import try_get_app_context

            ctx = try_get_app_context()
            config = {
                "mode": "chat",
                "route_mode": overrides.get("route_mode", "chat"),
                "model": (
                    (ctx.metadata or {}).get("initial_model", "")
                    if ctx is not None
                    else overrides.get("model", "")
                ),
            }
            cursor = await db.execute(
                """INSERT OR IGNORE INTO chat.conversation
                   (id, title, config, status, parent_conversation_id, variables,
                    overrides, initial_agent_id, initial_agent_version_id,
                    source_app, created_by,
                    created_at, updated_at, message_count, is_favorite,
                    is_ephemeral, conversation_type, visibility, version,
                    metadata, cache_state, source_feature, exclude_from_kg)
                   VALUES (?, 'New conversation', ?, 'active', ?, ?, ?, ?, ?,
                           'matrx_local', ?, ?, ?, 0, 0, 0, 'standard',
                           'private', 1, '{}', '{}', '', 0)""",
                (
                    conversation_id,
                    json.dumps(config, ensure_ascii=False),
                    parent_conversation_id,
                    json.dumps(variables or {}, ensure_ascii=False, default=str),
                    json.dumps(overrides, ensure_ascii=False, default=str),
                    ctx.agent_id if ctx is not None else None,
                    ctx.agent_version_id if ctx is not None else None,
                    user_id,
                    _now(),
                    _now(),
                ),
            )
            if cursor.rowcount:
                await enqueue_change(
                    "chat", "conversation", conversation_id, db, commit=False
                )
                logger.debug(
                    "[conv_store] Created conversation %s for user %s",
                    conversation_id,
                    user_id,
                )
            await db.commit()
        except Exception:
            # matrx-ai swallows store failures with its own log line; scream
            # here so the local failure is visible in the engine log.
            logger.error(
                "[conv_store] ensure_conversation_exists FAILED for %s — local "
                "conversation persistence is broken for this turn",
                conversation_id,
                exc_info=True,
            )
            raise

    async def create_pending_user_request(
        self,
        request_id: str,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Insert (or confirm) a user-request row with status='pending'.

        Called from BOTH gate paths (create_pending_user_request AND
        ensure_user_request_exists) — a repeat call for the same request_id
        must be a no-op, which INSERT OR IGNORE guarantees.
        """
        try:
            if conversation_id:
                await self.ensure_conversation_exists(conversation_id, user_id)
            db = get_db()
            now = _now()
            cursor = await db.execute(
                """INSERT OR IGNORE INTO chat.user_request
                   (id, user_id, status, source_app, metadata, created_by,
                    created_at, updated_at, last_activity_at,
                    total_input_tokens, total_output_tokens, total_cached_tokens,
                    total_tokens, iterations, total_tool_calls, version)
                   VALUES (?, ?, 'pending', 'matrx_local', ?, ?, ?, ?, ?,
                           0, 0, 0, 0, 1, 0, 1)""",
                (
                    request_id,
                    user_id,
                    json.dumps({"conversation_id": conversation_id}),
                    user_id,
                    now,
                    now,
                    now,
                ),
            )
            if cursor.rowcount:
                await enqueue_change(
                    "chat", "user_request", request_id, db, commit=False
                )
            await db.commit()
            logger.debug("[conv_store] Ensured pending request %s", request_id)
        except Exception:
            logger.error(
                "[conv_store] create_pending_user_request FAILED for request %s "
                "(conversation=%r) — request-status tracking is broken for this turn",
                request_id,
                conversation_id,
                exc_info=True,
            )
            raise

    async def reserve_stream_messages(
        self,
        config: Any,
        conversation_id: str,
        user_id: str,
        emitter: Any,
    ) -> dict[int, str]:
        """Create and announce the first user/assistant anchors for a turn.

        matrx-ai deliberately disables its ORM WriteCoordinator in client-host
        mode, which also skips the server's normal message reservations. The
        desktop store owns those rows here so the frontend receives the same
        durable ``record_reserved`` contract while chunks are streaming.
        """
        messages = list(getattr(config, "messages", None) or [])
        if not messages:
            return {}

        await self.ensure_conversation_exists(conversation_id, user_id)
        user_position = len(messages) - 1
        assistant_position = len(messages)
        user_norm = _normalize_message(messages[user_position])
        if not user_norm or user_norm["role"] != "user":
            return {}

        db = get_db()
        now = _now()
        reservations: dict[int, str] = {}
        rows_to_announce: list[tuple[str, int, str]] = []

        async def _insert(
            position: int,
            role: str,
            status: str,
            content: list[dict[str, Any]],
        ) -> str:
            existing = await db.fetchone(
                "SELECT id FROM chat.message WHERE conversation_id = ? AND position = ?",
                (conversation_id, position),
            )
            if existing:
                return str(existing["id"])
            message_id = str(
                uuid.uuid5(_MSG_NAMESPACE, f"{conversation_id}:{position}")
            )
            await db.execute(
                """INSERT INTO chat.message
                   (id, conversation_id, role, position, status, content,
                    metadata, source, is_visible_to_user, is_visible_to_model,
                    content_chars, tool_results_chars, created_at, updated_at,
                    version)
                   VALUES (?, ?, ?, ?, ?, ?, '{}', 'user', 1, 1, ?, 0, ?, ?, 1)""",
                (
                    message_id,
                    conversation_id,
                    role,
                    position,
                    status,
                    _serialize_content(content),
                    _content_chars(content),
                    now,
                    now,
                ),
            )
            await enqueue_change("chat", "message", message_id, db, commit=False)
            rows_to_announce.append((role, position, message_id))
            return message_id

        reservations[user_position] = await _insert(
            user_position,
            "user",
            "active",
            user_norm["content"],
        )
        reservations[assistant_position] = await _insert(
            assistant_position,
            "assistant",
            "pending",
            [],
        )
        await db.commit()

        from matrx_connect.reservations import get_tracker

        tracker = get_tracker()
        for role, position, message_id in rows_to_announce:
            await tracker.reserve(
                emitter=emitter,
                db_project="matrx",
                table="message",
                parent_refs={"conversation_id": conversation_id},
                metadata={
                    "role": role,
                    "position": position,
                    "position_kind": "logical_index",
                },
                record_id=message_id,
            )
        return reservations

    # ------------------------------------------------------------------
    # ConversationStore protocol — persistence
    # ------------------------------------------------------------------

    async def persist_completed_request(
        self,
        completed: Any,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist all data from a completed AI execution.

        The `completed` object is matrx-ai's CompletedRequest dataclass (or a
        plain dict in tests). IMPORTANT: on the dataclass, ``messages`` and
        ``conversation_id`` are *properties* and ``request_id`` lives at
        ``completed.request.request_id`` — none of them appear in ``__dict__``,
        so they must be read via the typed accessors. Reading ``__dict__``
        (the old behavior) silently persisted zero messages and updated zero
        user_request rows.

        May be called more than once per request (mid-run flush + final); the
        deterministic message-id scheme below makes repeats idempotent.
        """
        try:
            return await self._persist_completed_request(completed, conversation_id)
        except Exception:
            logger.error(
                "[conv_store] persist_completed_request FAILED (conversation=%r) — "
                "this turn is NOT saved locally",
                conversation_id,
                exc_info=True,
            )
            raise

    async def _persist_completed_request(
        self,
        completed: Any,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        if isinstance(completed, dict):
            conv_id: str = (
                conversation_id or completed.get("conversation_id") or str(uuid.uuid4())
            )
            user_request_id: str = (
                completed.get("user_request_id")
                or completed.get("request_id")
                or str(uuid.uuid4())
            )
            raw_messages = completed.get("messages") or []
            completed_metadata = dict(completed.get("metadata") or {})
            user_request_storage: dict[str, Any] = {}
        else:
            conv_id = (
                conversation_id
                or getattr(completed, "conversation_id", None)
                or str(uuid.uuid4())
            )
            request = getattr(completed, "request", None)
            user_request_id = getattr(request, "request_id", None) or str(uuid.uuid4())
            raw_messages = list(getattr(completed, "messages", None) or [])
            completed_metadata = dict(getattr(completed, "metadata", None) or {})
            user_request_storage = {}
            to_storage_dict = getattr(completed, "to_storage_dict", None)
            if callable(to_storage_dict):
                try:
                    storage = to_storage_dict()
                    user_request_storage = dict(storage.get("user_request") or {})
                except Exception:
                    logger.warning(
                        "[conv_store] could not serialize completed request summary",
                        exc_info=True,
                    )

        message_ids: list[str] = []
        request_ids: list[str] = [user_request_id]
        assistant_rows_to_announce: list[tuple[str, int]] = []
        tool_call_links: list[tuple[str, str]] = []

        db = get_db()

        # `completed.messages` is the FULL conversation history on every turn.
        # Derive a deterministic id from (conversation, position) so re-persisting
        # the history each turn is idempotent — only genuinely new positions
        # insert. CRITICAL: dedupe by POSITION, not just id — a conversation
        # pulled from the cloud (web-created) or written via data_routes has
        # ids that can never match the uuid5 scheme, and id-only dedupe would
        # duplicate the entire history on every turn (and push the duplicates).
        pos_rows = await db.fetchall(
            "SELECT id, position, role, status, content, metadata "
            "FROM chat.message WHERE conversation_id = ?",
            (conv_id,),
        )
        occupied = {r["position"]: dict(r) for r in pos_rows}
        now = _now()
        for position, msg in enumerate(raw_messages):
            norm = _normalize_message(msg)
            if norm is None:
                continue
            existing = occupied.get(position)
            message_status = _canonical_message_status(norm.get("status"))
            message_metadata = norm.get("metadata") or {}
            if existing:
                content = norm["content"]
                serialized_content = _serialize_content(content)
                serialized_metadata = json.dumps(
                    message_metadata,
                    ensure_ascii=False,
                    default=str,
                )
                same_message_id = bool(
                    norm.get("id") and str(norm["id"]) == str(existing["id"])
                )
                terminal_update = (
                    existing.get("role") == norm["role"]
                    and message_status in {"failed", "abandoned", "deleted"}
                )
                may_update = (
                    existing.get("status") == "pending"
                    or same_message_id
                    or terminal_update
                )
                changed = (
                    existing.get("role") != norm["role"]
                    or existing.get("status") != message_status
                    or existing.get("content") != serialized_content
                    or existing.get("metadata") != serialized_metadata
                )
                if may_update and changed:
                    await db.execute(
                        """UPDATE chat.message
                           SET role = ?, status = ?, content = ?, metadata = ?,
                               content_chars = ?, updated_at = ?
                           WHERE id = ?""",
                        (
                            norm["role"],
                            message_status,
                            serialized_content,
                            serialized_metadata,
                            _content_chars(content),
                            now,
                            existing["id"],
                        ),
                    )
                    message_ids.append(str(existing["id"]))
                    await enqueue_change(
                        "chat", "message", str(existing["id"]), db, commit=False
                    )
                for call_id in _message_tool_call_ids(norm["content"]):
                    tool_call_links.append((str(existing["id"]), call_id))
                continue
            msg_id = norm.get("id") or str(
                uuid.uuid5(_MSG_NAMESPACE, f"{conv_id}:{position}")
            )
            role = norm["role"]
            content = norm["content"]
            try:
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO chat.message
                       (id, conversation_id, role, position, status, content,
                        metadata, source, is_visible_to_user, is_visible_to_model,
                        content_chars, tool_results_chars, created_at, updated_at,
                        version)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, 0, ?, ?, 1)""",
                    (
                        msg_id,
                        conv_id,
                        role,
                        position,
                        message_status,
                        _serialize_content(content),
                        json.dumps(message_metadata, ensure_ascii=False, default=str),
                        # Canonical source vocabulary (cloud CHECK constraint
                        # cx_message_source_check): user | agent_template |
                        # system. `source` = row ORIGIN, not authorship —
                        # assistant turns are source='user' too (aidream
                        # Message.source default). 'model' 400s on every
                        # push (MXL-D-052).
                        "user",
                        _content_chars(content),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount:
                    message_ids.append(msg_id)
                    await enqueue_change("chat", "message", msg_id, db, commit=False)
                    if role == "assistant":
                        assistant_rows_to_announce.append((msg_id, position))
                for call_id in _message_tool_call_ids(content):
                    tool_call_links.append((msg_id, call_id))
            except Exception:
                logger.error(
                    "[conv_store] Failed to persist message %s (conversation=%s)",
                    msg_id,
                    conv_id,
                    exc_info=True,
                )

        for message_id, call_id in tool_call_links:
            tool_rows = await db.fetchall(
                "SELECT id, message_id FROM chat.tool_call "
                "WHERE conversation_id = ? AND call_id = ?",
                (conv_id, call_id),
            )
            for tool_row in tool_rows:
                if tool_row["message_id"] == message_id:
                    continue
                await db.execute(
                    "UPDATE chat.tool_call SET message_id = ?, updated_at = ? WHERE id = ?",
                    (message_id, now, tool_row["id"]),
                )
                await enqueue_change(
                    "chat", "tool_call", str(tool_row["id"]), db, commit=False
                )

        # Complete the user_request and touch the conversation rollups.
        request_status = _canonical_request_status(
            user_request_storage.get("status") or completed_metadata.get("status")
        )
        existing_request = await db.fetchone(
            "SELECT metadata FROM chat.user_request WHERE id = ?", (user_request_id,)
        )
        request_metadata = _deserialize_json_object(
            existing_request["metadata"] if existing_request else None
        )
        request_metadata.update(dict(user_request_storage.get("metadata") or {}))
        request_metadata.update(completed_metadata)
        raw_request_status = completed_metadata.get(
            "status",
            user_request_storage.get("status"),
        )
        if raw_request_status is not None:
            request_metadata["engine_status"] = str(
                getattr(raw_request_status, "value", raw_request_status)
            )
        request_metadata.setdefault("conversation_id", conv_id)
        request_error = user_request_storage.get("error") or completed_metadata.get(
            "error"
        )
        if isinstance(request_error, (dict, list)):
            request_error = json.dumps(request_error, ensure_ascii=False, default=str)
        elif request_error is not None:
            request_error = str(request_error)
        await db.execute(
            """UPDATE chat.user_request
               SET status=?, completed_at=?, updated_at=?, last_activity_at=?,
                   metadata=?, error=?
               WHERE id = ?""",
            (
                request_status,
                now,
                now,
                now,
                json.dumps(request_metadata, ensure_ascii=False, default=str),
                request_error,
                user_request_id,
            ),
        )
        await enqueue_change("chat", "user_request", user_request_id, db, commit=False)

        await db.execute(
            """UPDATE chat.conversation
                   SET message_count = (SELECT COUNT(*) FROM chat.message
                                    WHERE conversation_id = ? AND deleted_at IS NULL),
                   last_request_id = ?, last_request_status = ?, updated_at = ?
               WHERE id = ?""",
            (conv_id, user_request_id, request_status, now, conv_id),
        )
        await enqueue_change("chat", "conversation", conv_id, db, commit=False)
        await db.commit()

        if assistant_rows_to_announce:
            from matrx_connect.context.app_context import try_get_app_context
            from matrx_connect.reservations import get_tracker

            ctx = try_get_app_context()
            emitter = ctx.emitter if ctx is not None else None
            if emitter is not None:
                tracker = get_tracker()
                for message_id, position in assistant_rows_to_announce:
                    await tracker.reserve(
                        emitter=emitter,
                        db_project="matrx",
                        table="message",
                        parent_refs={"conversation_id": conv_id},
                        metadata={
                            "role": "assistant",
                            "position": position,
                            "position_kind": "logical_index",
                        },
                        record_id=message_id,
                    )

        logger.debug(
            "[conv_store] Persisted request %s: %d new messages",
            user_request_id,
            len(message_ids),
        )
        return {
            "conversation_id": conv_id,
            "user_request_id": user_request_id,
            "message_ids": message_ids,
            "request_ids": request_ids,
        }

    # ------------------------------------------------------------------
    # ConversationStore protocol — tool logging
    # ------------------------------------------------------------------

    async def log_tool_call_start(
        self,
        row_id: str,
        data: dict[str, Any],
    ) -> None:
        try:
            await self._write_tool_call(row_id, data, replace=True)
        except Exception:
            logger.error(
                "[conv_store] log_tool_call_start FAILED for row %s (tool=%r)",
                row_id,
                data.get("tool_name"),
                exc_info=True,
            )
            raise

    async def log_tool_call_update(
        self,
        row_id: str,
        data: dict[str, Any],
    ) -> None:
        try:
            await self._write_tool_call(row_id, data, replace=True)
        except Exception:
            logger.error(
                "[conv_store] log_tool_call_update FAILED for row %s",
                row_id,
                exc_info=True,
            )
            raise

    async def _write_tool_call(
        self, row_id: str, data: dict[str, Any], replace: bool
    ) -> None:
        """Upsert a chat.tool_call row from matrx-ai's tool-log data dict.

        Known keys map straight onto canonical columns; anything else is
        preserved under metadata (nothing silently dropped). Update calls
        merge over the existing row via INSERT OR REPLACE after re-reading —
        matrx-ai sends the full data dict on updates too, so replace is safe.
        """
        db = get_db()
        row: dict[str, Any] = {"id": row_id}
        extras: dict[str, Any] = {}
        for key, value in data.items():
            if key == "id":
                continue
            if key in _TOOL_CALL_COLUMNS:
                row[key] = value
            else:
                extras[key] = value

        meta = row.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {"_raw": meta}
        if extras:
            meta = {**meta, **extras}
        row["metadata"] = meta
        row.setdefault("status", "running")
        row.setdefault("created_at", _now())
        row["updated_at"] = _now()

        columns = list(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        col_sql = ", ".join(f'"{c}"' for c in columns)
        values = tuple(
            _to_sql_value(row[c], _TOOL_CALL_COLUMNS.get(c, "TEXT")) for c in columns
        )
        # ON CONFLICT upsert keeps columns from the start row that the update
        # dict doesn't carry (INSERT OR REPLACE would null them out);
        # metadata merges so start-time extras survive partial updates; and
        # created_at is insert-only — matrx-ai update dicts don't carry it,
        # so letting it through here falsified every completed tool call's
        # birth timestamp with the update time.
        update_sql = ", ".join(
            '"metadata" = json_patch(COALESCE("metadata", \'{}\'), '
            "COALESCE(excluded.\"metadata\", '{}'))"
            if c == "metadata"
            else f'"{c}" = excluded."{c}"'
            for c in columns
            if c not in ("id", "created_at")
        )
        await db.execute(
            f"INSERT INTO chat.tool_call ({col_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {update_sql}",
            values,
        )
        await enqueue_change("chat", "tool_call", row_id, db, commit=False)
        await db.commit()

    # ------------------------------------------------------------------
    # ConversationStore protocol — reads
    # ------------------------------------------------------------------

    async def get_conversation_config(
        self,
        conversation_id: str,
    ) -> dict[str, Any]:
        """Return the stored conversation config for ConversationResolver.

        Must include ``messages`` — matrx-ai feeds this dict to
        ``UnifiedConfig.from_dict`` on every AgentCache miss (i.e. whenever a
        conversation is continued after an engine restart). Without them the
        model sees only the new user input and the whole history is silently
        dropped. ``UnifiedMessage.parse_content`` accepts plain-string content.

        Raises KeyError when the conversation does not exist (protocol
        requirement — the resolver treats that as "no local state").
        """
        conv = await self._convs.get(conversation_id)
        if not conv:
            raise KeyError(
                f"conversation {conversation_id!r} not found in local SQLite"
            )
        return {
            "id": conv.get("id"),
            "mode": conv.get("mode", "chat"),
            "model": conv.get("model", ""),
            "route_mode": conv.get("route_mode", "chat"),
            "agent_id": conv.get("agent_id"),
            "agent_version_id": conv.get("agent_version_id"),
            "messages": await self._load_history(conversation_id),
        }

    async def get_conversation_data(
        self,
        conversation_id: str,
    ) -> dict[str, Any]:
        """Return the flat conversation data dict (mirrors
        ``CxManagers.get_conversation_data``): conversation row, messages,
        tool calls, media, user requests, requests."""
        conv = await self._convs.get(conversation_id)
        if not conv:
            raise KeyError(
                f"conversation {conversation_id!r} not found in local SQLite"
            )

        messages = await self._msgs.list_by_conversation(conversation_id)

        db = get_db()
        request_rows = await db.fetchall(
            "SELECT * FROM chat.user_request "
            "WHERE json_extract(metadata, '$.conversation_id') = ? AND deleted_at IS NULL "
            "ORDER BY created_at",
            (conversation_id,),
        )
        user_requests = [dict(r) for r in request_rows]

        tool_rows = await db.fetchall(
            "SELECT * FROM chat.tool_call "
            "WHERE conversation_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (conversation_id,),
        )
        tool_calls: list[dict[str, Any]] = []
        for r in tool_rows:
            row = dict(r)
            for json_col in (
                "arguments",
                "metadata",
                "execution_events",
                "output_preview",
            ):
                raw = row.get(json_col)
                if isinstance(raw, str) and raw:
                    try:
                        row[json_col] = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning(
                            "[conv_store] Corrupt chat.tool_call.%s for row %s — "
                            "returning raw string",
                            json_col,
                            row.get("id"),
                        )
            tool_calls.append(row)

        media_rows = await db.fetchall(
            "SELECT * FROM chat.media "
            "WHERE conversation_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (conversation_id,),
        )

        return {
            "conversation": conv,
            "messages": messages,
            "tool_calls": tool_calls,
            "media": [dict(r) for r in media_rows],
            "user_requests": user_requests,
            "requests": user_requests,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_history(self, conversation_id: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        try:
            rows = await get_db().fetchall(
                "SELECT id, role, status, content, metadata FROM chat.message "
                "WHERE conversation_id = ? AND deleted_at IS NULL "
                "ORDER BY position, created_at",
                (conversation_id,),
            )
            for raw_row in rows:
                row = dict(raw_row)
                role = row.get("role") or "user"
                content = _deserialize_content(row.get("content"))
                if not content:
                    continue
                message: dict[str, Any] = {
                    "id": row.get("id"),
                    "role": role,
                    "content": content,
                }
                status = row.get("status")
                if status and status != "active":
                    message["status"] = status
                metadata = _deserialize_json_object(row.get("metadata"))
                if metadata:
                    message["metadata"] = metadata
                messages.append(message)
        except Exception:
            logger.error(
                "[conv_store] Could not load history for %s — the model will "
                "not see prior turns",
                conversation_id,
                exc_info=True,
            )
        return messages


# Back-compat alias (pre-0.3.0 the class was named for the old
# client_mode.ConversationHandler protocol).
LocalConversationHandler = SQLiteConversationStore


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Stable namespace for deriving deterministic message ids from
# (conversation_id, position) so repeated full-history persists are idempotent.
_MSG_NAMESPACE = uuid.UUID("7df1aa44-43e5-4dca-9c4f-3f2f6f8a1b9e")

_MESSAGE_STATUSES = {
    "active",
    "condensed",
    "summary",
    "deleted",
    "pending",
    "abandoned",
    "failed",
}
_PAUSED_REQUEST_STATUSES = {
    "suspended_awaiting_client",
    "suspended_provider_overload",
    "paused_loop_guard",
    "max_iterations_exceeded",
    "truncated",
}


def _canonical_message_status(value: Any) -> str:
    status = str(getattr(value, "value", value) or "active")
    return status if status in _MESSAGE_STATUSES else "active"


def _canonical_request_status(value: Any) -> str:
    status = str(getattr(value, "value", value) or "completed")
    if status in {"failed", "cancelled", "paused"}:
        return status
    if status in _PAUSED_REQUEST_STATUSES:
        return "paused"
    return "completed"


def _message_tool_call_ids(content: list[dict[str, Any]]) -> list[str]:
    return [
        str(block.get("call_id") or block.get("id"))
        for block in content
        if block.get("type") == "tool_call"
        and (block.get("call_id") or block.get("id"))
    ]


def _normalize_message(msg: Any) -> dict[str, Any] | None:
    """Convert a UnifiedMessage/dict to canonical ``chat.message`` content.

    Structured content must remain structured. Flattening tool calls/results
    into JSON-looking text makes the transcript appear correct only until the
    page reloads, at which point neither the frontend nor conversation replay
    can recognize the tool graph.
    """
    storage = None
    to_storage_dict = getattr(msg, "to_storage_dict", None)
    if callable(to_storage_dict):
        storage = to_storage_dict()

    if isinstance(storage, dict):
        role = storage.get("role") or "user"
        content = storage.get("content")
        msg_id = storage.get("id")
        metadata = storage.get("metadata")
        status = storage.get("status")
    if isinstance(msg, dict):
        role = (storage or msg).get("role") or "user"
        content = (storage or msg).get("content")
        msg_id = (storage or msg).get("id")
        metadata = (storage or msg).get("metadata")
        status = (storage or msg).get("status")
    elif not isinstance(storage, dict):
        role = getattr(msg, "role", None) or "user"
        content = getattr(msg, "content", None)
        msg_id = getattr(msg, "id", None)
        metadata = getattr(msg, "metadata", None)
        status = getattr(msg, "status", None)

    role = str(getattr(role, "value", role))
    blocks = _normalize_content_blocks(content)
    row: dict[str, Any] = {"role": role, "content": blocks}
    if msg_id:
        row["id"] = str(msg_id)
    if isinstance(metadata, dict) and metadata:
        row["metadata"] = metadata
    if status and str(status) != "active":
        row["status"] = str(status)
    return row


def _normalize_content_blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        content = [content]

    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            if part:
                blocks.append({"type": "text", "text": part})
            continue
        if isinstance(part, dict):
            raw = dict(part)
        else:
            serializer = getattr(part, "to_storage_dict", None)
            if callable(serializer):
                raw = serializer()
            else:
                raw = dict(getattr(part, "__dict__", {}))
        if not isinstance(raw, dict):
            continue
        raw = {key: value for key, value in raw.items() if value not in (None, [], {})}
        if not raw:
            continue
        if "type" not in raw and isinstance(raw.get("text"), str):
            raw["type"] = "text"
        blocks.append(raw)
    return blocks


def _serialize_content(content: list[dict[str, Any]]) -> str:
    return json.dumps(content, ensure_ascii=False, default=str)


def _deserialize_content(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return _normalize_content_blocks(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return _normalize_content_blocks(raw)
        return _normalize_content_blocks(decoded)
    return _normalize_content_blocks(raw)


def _deserialize_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _content_chars(content: list[dict[str, Any]]) -> int:
    return sum(
        len(text)
        for block in content
        if isinstance(block, dict)
        for text in [block.get("text")]
        if isinstance(text, str)
    )
