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
    _content_to_parts,
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
            config = {
                "mode": "chat",
                "route_mode": overrides.get("route_mode", "chat"),
                "model": overrides.get("model", ""),
            }
            cursor = await db.execute(
                """INSERT OR IGNORE INTO chat.conversation
                   (id, title, config, status, parent_conversation_id, variables,
                    overrides, initial_agent_id, source_app, created_by,
                    created_at, updated_at, message_count, is_favorite,
                    is_ephemeral, conversation_type, visibility, version,
                    metadata, cache_state, source_feature, exclude_from_kg)
                   VALUES (?, 'New conversation', ?, 'active', ?, ?, ?, ?,
                           'matrx_local', ?, ?, ?, 0, 0, 0, 'standard',
                           'private', 1, '{}', '{}', '', 0)""",
                (
                    conversation_id,
                    json.dumps(config, ensure_ascii=False),
                    parent_conversation_id,
                    json.dumps(variables or {}, ensure_ascii=False, default=str),
                    json.dumps(overrides, ensure_ascii=False, default=str),
                    overrides.get("agent_id"),
                    user_id,
                    _now(),
                    _now(),
                ),
            )
            if cursor.rowcount:
                await enqueue_change("chat", "conversation", conversation_id, db, commit=False)
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
                await enqueue_change("chat", "user_request", request_id, db, commit=False)
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
        else:
            conv_id = (
                conversation_id
                or getattr(completed, "conversation_id", None)
                or str(uuid.uuid4())
            )
            request = getattr(completed, "request", None)
            user_request_id = getattr(request, "request_id", None) or str(uuid.uuid4())
            raw_messages = list(getattr(completed, "messages", None) or [])

        message_ids: list[str] = []
        request_ids: list[str] = [user_request_id]

        db = get_db()

        # `completed.messages` is the FULL conversation history on every turn.
        # Derive a deterministic id from (conversation, position) so re-persisting
        # the history each turn is idempotent — only genuinely new positions
        # insert. CRITICAL: dedupe by POSITION, not just id — a conversation
        # pulled from the cloud (web-created) or written via data_routes has
        # ids that can never match the uuid5 scheme, and id-only dedupe would
        # duplicate the entire history on every turn (and push the duplicates).
        pos_rows = await db.fetchall(
            "SELECT position FROM chat.message WHERE conversation_id = ?",
            (conv_id,),
        )
        occupied = {r["position"] for r in pos_rows}
        now = _now()
        for position, msg in enumerate(raw_messages):
            if position in occupied:
                continue
            norm = _normalize_message(msg)
            if norm is None:
                continue
            msg_id = norm.get("id") or str(
                uuid.uuid5(_MSG_NAMESPACE, f"{conv_id}:{position}")
            )
            role = norm["role"]
            text = norm["content"]
            try:
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO chat.message
                       (id, conversation_id, role, position, status, content,
                        metadata, source, is_visible_to_user, is_visible_to_model,
                        content_chars, tool_results_chars, created_at, updated_at,
                        version)
                       VALUES (?, ?, ?, ?, 'active', ?, '{}', ?, 1, 1, ?, 0, ?, ?, 1)""",
                    (
                        msg_id,
                        conv_id,
                        role,
                        position,
                        _content_to_parts(text),
                        # Canonical source vocabulary (cloud CHECK constraint
                        # cx_message_source_check): user | agent_template |
                        # system. `source` = row ORIGIN, not authorship —
                        # assistant turns are source='user' too (aidream
                        # Message.source default). 'model' 400s on every
                        # push (MXL-D-052).
                        "user",
                        len(text),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount:
                    message_ids.append(msg_id)
                    await enqueue_change("chat", "message", msg_id, db, commit=False)
            except Exception:
                logger.error(
                    "[conv_store] Failed to persist message %s (conversation=%s)",
                    msg_id,
                    conv_id,
                    exc_info=True,
                )

        # Complete the user_request and touch the conversation rollups.
        await db.execute(
            """UPDATE chat.user_request
               SET status='completed', completed_at=?, updated_at=?, last_activity_at=?
               WHERE id = ?""",
            (now, now, now, user_request_id),
        )
        await enqueue_change("chat", "user_request", user_request_id, db, commit=False)

        await db.execute(
            """UPDATE chat.conversation
               SET message_count = (SELECT COUNT(*) FROM chat.message
                                    WHERE conversation_id = ? AND deleted_at IS NULL),
                   last_request_id = ?, last_request_status = 'completed', updated_at = ?
               WHERE id = ?""",
            (conv_id, user_request_id, now, conv_id),
        )
        await enqueue_change("chat", "conversation", conv_id, db, commit=False)
        await db.commit()

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

    async def _write_tool_call(self, row_id: str, data: dict[str, Any], replace: bool) -> None:
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
            'COALESCE(excluded."metadata", \'{}\'))'
            if c == "metadata"
            else f'"{c}" = excluded."{c}"'
            for c in columns
            if c not in ("id", "created_at")
        )
        await db.execute(
            f'INSERT INTO chat.tool_call ({col_sql}) VALUES ({placeholders}) '
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
            raise KeyError(f"conversation {conversation_id!r} not found in local SQLite")
        return {
            "id": conv.get("id"),
            "mode": conv.get("mode", "chat"),
            "model": conv.get("model", ""),
            "route_mode": conv.get("route_mode", "chat"),
            "agent_id": conv.get("agent_id"),
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
            raise KeyError(f"conversation {conversation_id!r} not found in local SQLite")

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
            for json_col in ("arguments", "metadata", "execution_events", "output_preview"):
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
            for row in await self._msgs.list_by_conversation(conversation_id):
                role = row.get("role") or "user"
                content = row.get("content") or ""
                if not content:
                    continue
                messages.append({"role": role, "content": content})
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


def _normalize_message(msg: Any) -> dict[str, Any] | None:
    """Convert a UnifiedMessage / dict into {id?, role, content-text}.

    UnifiedMessage.content is a list of UnifiedContent parts; we flatten to
    plain text for storage as a single canonical text part. Text parts are
    concatenated; any non-text parts are JSON-dumped so nothing is silently
    dropped.
    """
    if isinstance(msg, dict):
        role = msg.get("role") or "user"
        content = msg.get("content")
        msg_id = msg.get("id")
    else:
        role = getattr(msg, "role", None) or "user"
        content = getattr(msg, "content", None)
        msg_id = getattr(msg, "id", None)

    role = str(getattr(role, "value", role))

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for part in content:
            part_text = (
                part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
            )
            if isinstance(part_text, str):
                parts.append(part_text)
            else:
                try:
                    raw = part if isinstance(part, dict) else dict(getattr(part, "__dict__", {}))
                    raw = {k: v for k, v in raw.items() if v not in (None, [], {})}
                    if raw:
                        parts.append(json.dumps(raw, default=str))
                except Exception:
                    pass
        text = "\n".join(p for p in parts if p)
    elif content is None:
        text = ""
    else:
        text = str(content)

    row: dict[str, Any] = {"role": role, "content": text}
    if msg_id:
        row["id"] = str(msg_id)
    return row
