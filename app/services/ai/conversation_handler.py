"""SQLite-backed ConversationStore for matrx-ai client-host mode.

Implements the ``matrx_ai.client_host.ConversationStore`` Protocol (matrx-ai
>= 0.3.0). The store is injected via ``matrx_ai.configure(conversation_store=
...)`` at startup (app/services/ai/engine.py); after that, EVERY conversation
write the classic execution path makes (gate, persist, tool logging) and the
history read delegate here instead of the cx_ ORM tables.

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

All storage delegates to the local SQLite database (~/.matrx/matrx.db) via
the existing repository layer, keeping SQLite as the single working store
consistent with docs/SYNC_CONTRACT.md (conversations/messages are currently
LOCAL-ONLY — no reconnect push pipeline yet; contract gap #1).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from app.common.system_logger import get_logger
from app.services.local_db.repositories import (
    ConversationsRepo,
    MessagesRepo,
)
from app.services.local_db.database import get_db

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


class SQLiteConversationStore:
    """matrx_ai.client_host.ConversationStore backed by local SQLite.

    All seven protocol methods are async and delegate to the existing
    ConversationsRepo / MessagesRepo plus two dedicated tables:
    - user_requests: one row per AI interaction
    - tool_call_logs: one row per tool invocation
    """

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
            existing = await self._convs.get(conversation_id)
            if existing:
                return
            await self._convs.create(
                {
                    "id": conversation_id,
                    "title": "New conversation",
                    "mode": "chat",
                    "model": "",
                    "server_conversation_id": None,
                    "route_mode": overrides.get("route_mode", "chat") if overrides else "chat",
                    "agent_id": overrides.get("agent_id") if overrides else None,
                }
            )
            logger.debug(
                "[conv_store] Created conversation %s for user %s", conversation_id, user_id
            )
        except sqlite3.IntegrityError:
            # Lost a create race with a concurrent ensure — the row exists now,
            # which is exactly what "ensure" means.
            logger.debug("[conv_store] Conversation %s created concurrently", conversation_id)
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
            # user_requests.conversation_id has a NOT NULL + FK constraint;
            # self-heal the parent row when the gate hands us a conversation
            # we have not seen (ensure_* ordering is not guaranteed for
            # cross-conversation requests).
            if conversation_id:
                await self.ensure_conversation_exists(conversation_id, user_id)
            db = get_db()
            await db.execute(
                """INSERT OR IGNORE INTO user_requests
                   (id, conversation_id, user_id, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', datetime('now'), datetime('now'))""",
                (request_id, conversation_id, user_id),
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

    # ------------------------------------------------------------------
    # ConversationStore protocol — persistence
    # ------------------------------------------------------------------

    async def persist_completed_request(
        self,
        completed: Any,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist all data from a completed AI execution to SQLite.

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

        # `completed.messages` is the FULL conversation history on every turn.
        # Derive a deterministic id from (conversation, position) so re-persisting
        # the history each turn is idempotent — only genuinely new positions insert.
        for position, msg in enumerate(raw_messages):
            msg_dict = _normalize_message(msg)
            if msg_dict is None:
                continue
            msg_dict.setdefault(
                "id", str(uuid.uuid5(_MSG_NAMESPACE, f"{conv_id}:{position}"))
            )
            msg_dict["conversation_id"] = conv_id
            try:
                await self._msgs.create(msg_dict)
                message_ids.append(msg_dict["id"])
            except sqlite3.IntegrityError:
                # Already persisted on a previous turn — expected for history rows.
                pass
            except Exception:
                logger.error(
                    "[conv_store] Failed to persist message %s (conversation=%s)",
                    msg_dict.get("id"),
                    conv_id,
                    exc_info=True,
                )

        # Update the user_request row to status=completed
        db = get_db()
        await db.execute(
            """UPDATE user_requests SET status='completed', updated_at=datetime('now')
               WHERE id = ?""",
            (user_request_id,),
        )
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
            db = get_db()
            status = data.get("status") or "running"
            await db.execute(
                """INSERT OR REPLACE INTO tool_call_logs
                   (id, conversation_id, user_request_id, status, data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
                (
                    row_id,
                    data.get("conversation_id"),
                    data.get("user_request_id"),
                    status,
                    json.dumps(data, default=str),
                ),
            )
            await db.commit()
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
            db = get_db()
            status = data.get("status", "completed")
            await db.execute(
                """UPDATE tool_call_logs
                   SET status = ?, data = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (status, json.dumps(data, default=str), row_id),
            )
            await db.commit()
        except Exception:
            logger.error(
                "[conv_store] log_tool_call_update FAILED for row %s",
                row_id,
                exc_info=True,
            )
            raise

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
            "SELECT * FROM user_requests WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        )
        user_requests = [dict(r) for r in request_rows]

        tool_rows = await db.fetchall(
            "SELECT * FROM tool_call_logs WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        )
        tool_calls: list[dict[str, Any]] = []
        for r in tool_rows:
            row = dict(r)
            try:
                row["data"] = json.loads(row.get("data") or "{}")
            except Exception:
                logger.warning(
                    "[conv_store] Corrupt tool_call_logs.data for row %s — returning raw string",
                    row.get("id"),
                )
            tool_calls.append(row)

        return {
            "conversation": conv,
            "messages": messages,
            "tool_calls": tool_calls,
            # No local media pipeline for conversations yet (media lives in the
            # media vault, keyed by generation — not linked to conversations).
            "media": [],
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
    """Convert a UnifiedMessage / dict into a row for the local messages table.

    UnifiedMessage.content is a list of UnifiedContent parts; the messages
    table stores plain text. Text parts are concatenated; any non-text parts
    are JSON-dumped so nothing is silently dropped.
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
