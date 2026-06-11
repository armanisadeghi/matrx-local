"""SQLite-backed ConversationHandler for matrx-ai client mode.

matrx-ai's client mode requires a ConversationHandler object that persists
conversations, user requests, messages, and tool call logs locally instead
of writing to the cloud database directly.

This implementation delegates all storage to the local SQLite database
(~/.matrx/matrx.db) via the existing repository layer, keeping SQLite as
the single source of truth consistent with the rest of the application.
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

_HANDLER_INSTANCE: "LocalConversationHandler | None" = None


def get_conversation_handler() -> "LocalConversationHandler":
    """Return the singleton handler instance, creating it lazily if needed."""
    global _HANDLER_INSTANCE
    if _HANDLER_INSTANCE is None:
        _HANDLER_INSTANCE = LocalConversationHandler()
    return _HANDLER_INSTANCE


class LocalConversationHandler:
    """Implements matrx_ai.client_mode.config.ConversationHandler via local SQLite.

    All five protocol methods are async and delegate to the existing
    ConversationsRepo / MessagesRepo plus two new tables:
    - user_requests: one row per AI interaction
    - tool_call_logs: one row per tool invocation
    """

    def __init__(self) -> None:
        self._convs = ConversationsRepo()
        self._msgs = MessagesRepo()

    # ------------------------------------------------------------------
    # ConversationHandler protocol
    # ------------------------------------------------------------------

    async def ensure_conversation_exists(
        self,
        conversation_id: str,
        user_id: str,
        parent_conversation_id: str | None = None,
        variables: dict[str, Any] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
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
        logger.debug("[conv_handler] Created conversation %s for user %s", conversation_id, user_id)

    async def create_pending_user_request(
        self,
        request_id: str,
        conversation_id: str,
        user_id: str,
    ) -> None:
        db = get_db()
        await db.execute(
            """INSERT OR IGNORE INTO user_requests
               (id, conversation_id, user_id, status, created_at, updated_at)
               VALUES (?, ?, ?, 'pending', datetime('now'), datetime('now'))""",
            (request_id, conversation_id, user_id),
        )
        await db.commit()
        logger.debug("[conv_handler] Created pending request %s", request_id)

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
        """
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
                logger.warning(
                    "[conv_handler] Failed to persist message %s",
                    msg_dict.get("id"),
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
            "[conv_handler] Persisted request %s: %d new messages",
            user_request_id,
            len(message_ids),
        )
        return {
            "conversation_id": conv_id,
            "user_request_id": user_request_id,
            "message_ids": message_ids,
            "request_ids": request_ids,
        }

    async def log_tool_call_start(
        self,
        row_id: str,
        data: dict[str, Any],
    ) -> None:
        db = get_db()
        await db.execute(
            """INSERT OR REPLACE INTO tool_call_logs
               (id, conversation_id, user_request_id, status, data, created_at, updated_at)
               VALUES (?, ?, ?, 'running', ?, datetime('now'), datetime('now'))""",
            (
                row_id,
                data.get("conversation_id"),
                data.get("user_request_id"),
                json.dumps(data),
            ),
        )
        await db.commit()

    async def log_tool_call_update(
        self,
        row_id: str,
        data: dict[str, Any],
    ) -> None:
        db = get_db()
        status = data.get("status", "completed")
        await db.execute(
            """UPDATE tool_call_logs
               SET status = ?, data = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (status, json.dumps(data), row_id),
        )
        await db.commit()

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
        """
        conv = await self._convs.get(conversation_id)
        if not conv:
            return {}
        messages: list[dict[str, Any]] = []
        try:
            for row in await self._msgs.list_by_conversation(conversation_id):
                role = row.get("role") or "user"
                content = row.get("content") or ""
                if not content:
                    continue
                messages.append({"role": role, "content": content})
        except Exception:
            logger.warning(
                "[conv_handler] Could not load history for %s",
                conversation_id,
                exc_info=True,
            )
        return {
            "id": conv.get("id"),
            "mode": conv.get("mode", "chat"),
            "model": conv.get("model", ""),
            "route_mode": conv.get("route_mode", "chat"),
            "agent_id": conv.get("agent_id"),
            "messages": messages,
        }


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
