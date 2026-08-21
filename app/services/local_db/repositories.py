"""Data access repositories for each SQLite table.

Each repo provides typed CRUD operations over the local database.
All reads are instant (local SQLite), all writes commit immediately.
JSON fields (lists, dicts) are serialized/deserialized transparently.
"""

from __future__ import annotations

import asyncio
import base64

import json
from datetime import datetime, timezone
from typing import Any

from app.common.system_logger import get_logger
from app.services.local_db.database import get_db, LocalDatabase

logger = get_logger()

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    return dict(row)


# ==================================================================
# ModelsRepo — ai_models table
# ==================================================================

class ModelsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_all(self, include_deprecated: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ai_models"
        if not include_deprecated:
            sql += " WHERE is_deprecated = 0"
        sql += " ORDER BY is_primary DESC, provider, common_name"
        rows = await self._db.fetchall(sql)
        return [self._deserialize(r) for r in rows]

    async def get(self, model_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM ai_models WHERE id = ?", (model_id,))
        return self._deserialize(row) if row else None

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM ai_models WHERE name = ?", (name,))
        return self._deserialize(row) if row else None

    async def upsert(self, model: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO ai_models (id, name, common_name, provider, endpoints,
               capabilities, context_window, max_tokens, is_primary, is_premium,
               is_deprecated, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, common_name=excluded.common_name,
                 provider=excluded.provider, endpoints=excluded.endpoints,
                 capabilities=excluded.capabilities, context_window=excluded.context_window,
                 max_tokens=excluded.max_tokens, is_primary=excluded.is_primary,
                 is_premium=excluded.is_premium, is_deprecated=excluded.is_deprecated,
                 raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
            (
                model["id"],
                model.get("name", ""),
                model.get("common_name", ""),
                model.get("provider", ""),
                _json_dumps(model.get("endpoints", [])),
                _json_dumps(model.get("capabilities", [])),
                model.get("context_window"),
                model.get("max_tokens"),
                int(model.get("is_primary", False)),
                int(model.get("is_premium", False)),
                int(model.get("is_deprecated", False)),
                _json_dumps(model),
                _now(),
            ),
        )
        await self._db.commit()

    async def upsert_many(self, models: list[dict[str, Any]]) -> None:
        for m in models:
            await self.upsert(m)

    async def delete_missing(self, keep_ids: set[str]) -> int:
        """Remove models not in keep_ids. Returns count deleted."""
        if not keep_ids:
            return 0
        placeholders = ",".join("?" for _ in keep_ids)
        cursor = await self._db.execute(
            f"DELETE FROM ai_models WHERE id NOT IN ({placeholders})",
            tuple(keep_ids),
        )
        await self._db.commit()
        return cursor.rowcount

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) as cnt FROM ai_models WHERE is_deprecated = 0")
        return row["cnt"] if row else 0

    def _deserialize(self, row) -> dict[str, Any]:
        d = _row_to_dict(row)
        d["endpoints"] = _json_loads(d.get("endpoints", "[]")) or []
        d["capabilities"] = _json_loads(d.get("capabilities", "[]")) or []
        d["is_primary"] = bool(d.get("is_primary", 0))
        d["is_premium"] = bool(d.get("is_premium", 0))
        d["is_deprecated"] = bool(d.get("is_deprecated", 0))
        d["raw_json"] = _json_loads(d.get("raw_json", "{}")) or {}
        return d


# ==================================================================
# AgentsRepo — agents table
# ==================================================================

class AgentsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_all(
        self,
        source: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return active agents.

        For user-sourced agents, pass user_id to restrict results to that user.
        Builtins (source='builtin') are always returned regardless of user_id.
        If user_id is None, all agents are returned (use only for admin/debug).
        """
        if user_id is not None:
            # Return builtins unconditionally + user agents only for this user.
            rows = await self._db.fetchall(
                "SELECT * FROM agents WHERE is_active = 1 "
                "AND (source = 'builtin' OR user_id = ?) "
                "ORDER BY source, name",
                (user_id,),
            )
        elif source:
            rows = await self._db.fetchall(
                "SELECT * FROM agents WHERE source = ? AND is_active = 1 ORDER BY name",
                (source,),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM agents WHERE is_active = 1 ORDER BY source, name"
            )
        return [self._deserialize(r) for r in rows]

    async def get(self, agent_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM agents WHERE id = ?", (agent_id,))
        return self._deserialize(row) if row else None

    async def upsert(self, agent: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO agents (id, name, description, source, user_id,
               category, tags, is_favorite, variable_defaults, settings,
               is_active, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, description=excluded.description,
                 source=excluded.source, user_id=excluded.user_id,
                 category=excluded.category, tags=excluded.tags,
                 is_favorite=excluded.is_favorite,
                 variable_defaults=excluded.variable_defaults,
                 settings=excluded.settings, is_active=excluded.is_active,
                 raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
            (
                agent["id"],
                agent.get("name", ""),
                agent.get("description", ""),
                agent.get("source", "builtin"),
                agent.get("user_id", ""),
                agent.get("category", ""),
                _json_dumps(agent.get("tags", [])),
                int(bool(agent.get("is_favorite", False))),
                _json_dumps(agent.get("variable_defaults", [])),
                _json_dumps(agent.get("settings", {})),
                int(agent.get("is_active", True)),
                _json_dumps(agent),
                _now(),
            ),
        )
        await self._db.commit()

    async def upsert_many(self, agents: list[dict[str, Any]]) -> None:
        for a in agents:
            await self.upsert(a)

    async def delete_by_source(
        self, source: str, keep_ids: set[str], user_id: str | None = None
    ) -> int:
        """Delete agents by source, keeping only keep_ids.

        For user-sourced agents, pass user_id to scope the delete to that user
        only — prevents one user's sync from removing another user's agents.
        """
        user_clause = " AND user_id = ?" if (source == "user" and user_id is not None) else ""
        user_args: tuple = (user_id,) if (source == "user" and user_id is not None) else ()

        if not keep_ids:
            cursor = await self._db.execute(
                f"DELETE FROM agents WHERE source = ?{user_clause}",
                (source, *user_args),
            )
        else:
            placeholders = ",".join("?" for _ in keep_ids)
            cursor = await self._db.execute(
                f"DELETE FROM agents WHERE source = ? AND id NOT IN ({placeholders}){user_clause}",
                (source, *keep_ids, *user_args),
            )
        await self._db.commit()
        return cursor.rowcount

    def _deserialize(self, row) -> dict[str, Any]:
        d = _row_to_dict(row)
        d["variable_defaults"] = _json_loads(d.get("variable_defaults", "[]")) or []
        d["settings"] = _json_loads(d.get("settings", "{}")) or {}
        d["is_active"] = bool(d.get("is_active", 1))
        d["raw_json"] = _json_loads(d.get("raw_json", "{}")) or {}
        return d


# ==================================================================
# AgentExecutionDefinitionsRepo — opaque canonical execution cache
# ==================================================================

class AgentExecutionDefinitionsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def get(
        self, definition_id: str, *, is_version: bool
    ) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            """SELECT * FROM agent_execution_definitions
               WHERE definition_id = ? AND is_version = ?""",
            (definition_id, int(is_version)),
        )
        if not row:
            return None
        data = _row_to_dict(row)
        data["definition_json"] = _json_loads(data.get("definition_json"))
        data["is_version"] = bool(data.get("is_version"))
        return data

    async def upsert(self, definition: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO agent_execution_definitions
               (definition_id, is_version, definition_json, definition_hash,
                revision, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(definition_id, is_version) DO UPDATE SET
                 definition_json=excluded.definition_json,
                 definition_hash=excluded.definition_hash,
                 revision=excluded.revision,
                 fetched_at=excluded.fetched_at""",
            (
                definition["definition_id"],
                int(bool(definition.get("is_version", False))),
                _json_dumps(definition),
                definition["definition_hash"],
                definition.get("revision"),
                _now(),
            ),
        )
        await self._db.commit()

    async def delete(self, definition_id: str, *, is_version: bool) -> None:
        await self._db.execute(
            """DELETE FROM agent_execution_definitions
               WHERE definition_id = ? AND is_version = ?""",
            (definition_id, int(is_version)),
        )
        await self._db.commit()


# ==================================================================
# ConversationsRepo — canonical chat.conversation mirror table
#
# The bespoke `conversations` table is GONE (migration V10). This repo now
# fronts the structural mirror of the cloud's chat.conversation and keeps
# the old dict shape for its callers (data_routes, chat UI):
#   mode / route_mode / model  <-> config JSON
#   agent_id                   <-> initial_agent_id
#   server_conversation_id     ==  id (the local id IS the canonical id now)
# Local-origin writes enqueue an outbox row so the chat sync engine pushes
# them to the cloud. Deletes are soft (deleted_at tombstone), like the cloud.
# ==================================================================

def _conv_to_compat(row) -> dict[str, Any]:
    d = _row_to_dict(row)
    if not d:
        return d
    config = _json_loads(d.get("config")) or {}
    if not isinstance(config, dict):
        config = {}
    return {
        "id": d.get("id"),
        "title": d.get("title") or "New conversation",
        "mode": config.get("mode", "chat"),
        "model": config.get("model", ""),
        "server_conversation_id": d.get("id"),
        "route_mode": config.get("route_mode", "chat"),
        "agent_id": d.get("initial_agent_id"),
        "agent_version_id": d.get("initial_agent_version_id"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
        "is_favorite": bool(d.get("is_favorite")),
        "status": d.get("status") or "active",
    }


class ConversationsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_all(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM chat.conversation WHERE deleted_at IS NULL "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [_conv_to_compat(r) for r in rows]

    async def get(self, conv_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM chat.conversation WHERE id = ? AND deleted_at IS NULL",
            (conv_id,),
        )
        return _conv_to_compat(row) if row else None

    async def create(self, conv: dict[str, Any]) -> None:
        from app.services.local_db.outbox import enqueue_change

        now = _now()
        config = {
            "mode": conv.get("mode", "chat"),
            "route_mode": conv.get("route_mode", "chat"),
            "model": conv.get("model", ""),
        }
        owner = conv.get("created_by") or await TokenRepo(self._db).get_owner_user_id()
        metadata: dict[str, Any] = {}
        if conv.get("server_conversation_id") and conv["server_conversation_id"] != conv["id"]:
            # Legacy aidream server-conversation link — preserved, not discarded.
            metadata["legacy_server_conversation_id"] = conv["server_conversation_id"]
        await self._db.execute(
            """INSERT INTO chat.conversation
               (id, title, config, status, initial_agent_id, source_app,
                created_by, created_at, updated_at, message_count, is_favorite,
                is_ephemeral, conversation_type, visibility, version,
                metadata, variables, overrides, cache_state,
                source_feature, exclude_from_kg)
               VALUES (?, ?, ?, 'active', ?, 'matrx_local', ?, ?, ?, 0, 0,
                       0, 'standard', 'private', 1, ?, '{}', '{}', '{}', '', 0)""",
            (
                conv["id"],
                conv.get("title", "New conversation"),
                _json_dumps(config),
                conv.get("agent_id"),
                owner,
                conv.get("created_at") or now,
                conv.get("updated_at") or now,
                _json_dumps(metadata),
            ),
        )
        await enqueue_change("chat", "conversation", conv["id"], self._db, commit=False)
        await self._db.commit()

    async def update(self, conv_id: str, updates: dict[str, Any]) -> None:
        from app.services.local_db.outbox import enqueue_change

        sets: list[str] = []
        params: list[Any] = []
        if "title" in updates:
            sets.append("title = ?")
            params.append(updates["title"])
        if "agent_id" in updates:
            sets.append("initial_agent_id = ?")
            params.append(updates["agent_id"])
        config_updates = {
            k: updates[k] for k in ("mode", "route_mode", "model") if k in updates
        }
        if config_updates:
            # Merge into the existing config JSON without clobbering other keys.
            sets.append("config = json_patch(COALESCE(config, '{}'), ?)")
            params.append(_json_dumps(config_updates))
        if updates.get("server_conversation_id"):
            # Legacy aidream server-conversation link — the local id IS the
            # canonical id now, but callers that still send this mapping get
            # it preserved instead of silently discarded.
            sets.append("metadata = json_patch(COALESCE(metadata, '{}'), ?)")
            params.append(_json_dumps(
                {"legacy_server_conversation_id": updates["server_conversation_id"]}
            ))
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(conv_id)
        await self._db.execute(
            f"UPDATE chat.conversation SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        await enqueue_change("chat", "conversation", conv_id, self._db, commit=False)
        await self._db.commit()

    async def delete(self, conv_id: str) -> None:
        """Soft-delete (tombstone) the conversation AND its messages — the
        bespoke schema cascade-deleted messages; the mirror keeps that
        contract with tombstones so dead conversations' messages neither
        show up locally nor keep syncing as live rows."""
        from app.services.local_db.outbox import enqueue_change

        now = _now()
        await self._db.execute(
            "UPDATE chat.conversation SET deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (now, now, conv_id),
        )
        msg_rows = await self._db.fetchall(
            "SELECT id FROM chat.message WHERE conversation_id = ? AND deleted_at IS NULL",
            (conv_id,),
        )
        await self._db.execute(
            "UPDATE chat.message SET deleted_at = ?, updated_at = ? "
            "WHERE conversation_id = ? AND deleted_at IS NULL",
            (now, now, conv_id),
        )
        for r in msg_rows:
            await enqueue_change("chat", "message", r["id"], self._db, commit=False)
        await enqueue_change("chat", "conversation", conv_id, self._db, commit=False)
        await self._db.commit()

    async def count(self) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) as cnt FROM chat.conversation WHERE deleted_at IS NULL"
        )
        return row["cnt"] if row else 0


# ==================================================================
# MessagesRepo — canonical chat.message mirror table
#
# Compat mapping for callers that predate the mirror:
#   content (plain text)  <-> content JSON parts [{type:"text",text:...}]
#   model / tool_calls / tool_results  <-> metadata JSON
#   error (text)          <-> error JSON {"message": ...}
# ==================================================================

def _content_to_parts(text: str) -> str:
    return _json_dumps([{"type": "text", "text": text}] if text else [])


def _parts_to_text(raw: Any) -> str:
    parts = _json_loads(raw) if isinstance(raw, str) else raw
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                out.append(text)
            elif part:
                out.append(_json_dumps(part))
        elif isinstance(part, str):
            out.append(part)
    return "\n".join(p for p in out if p)


def _msg_to_compat(row) -> dict[str, Any]:
    d = _row_to_dict(row)
    if not d:
        return d
    meta = _json_loads(d.get("metadata")) or {}
    if not isinstance(meta, dict):
        meta = {}
    error = _json_loads(d.get("error"))
    if isinstance(error, dict):
        error = error.get("message") or _json_dumps(error)
    return {
        "id": d.get("id"),
        "conversation_id": d.get("conversation_id"),
        "role": d.get("role") or "user",
        "content": _parts_to_text(d.get("content")),
        "model": meta.get("model"),
        "tool_calls": meta.get("tool_calls"),
        "tool_results": meta.get("tool_results"),
        "error": error,
        "created_at": d.get("created_at"),
        "position": d.get("position"),
    }


class MessagesRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_by_conversation(self, conv_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM chat.message WHERE conversation_id = ? AND deleted_at IS NULL "
            "ORDER BY position, created_at",
            (conv_id,),
        )
        return [_msg_to_compat(r) for r in rows]

    async def create(self, msg: dict[str, Any]) -> None:
        from app.services.local_db.outbox import enqueue_change

        now = _now()
        meta: dict[str, Any] = {}
        for key in ("model", "tool_calls", "tool_results"):
            if msg.get(key):
                meta[key] = msg[key]
        position = msg.get("position")
        if position is None:
            row = await self._db.fetchone(
                "SELECT COALESCE(MAX(position) + 1, 0) AS pos FROM chat.message "
                "WHERE conversation_id = ?",
                (msg["conversation_id"],),
            )
            position = row["pos"] if row else 0
        role = msg.get("role", "user")
        error = msg.get("error")
        await self._db.execute(
            """INSERT INTO chat.message
               (id, conversation_id, role, position, status, content, metadata,
                error, source, is_visible_to_user, is_visible_to_model,
                content_chars, tool_results_chars, created_at, updated_at, version)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, 1, 1, ?, 0, ?, ?, 1)""",
            (
                msg["id"],
                msg["conversation_id"],
                role,
                position,
                _content_to_parts(msg.get("content", "")),
                _json_dumps(meta),
                _json_dumps({"message": error}) if error else None,
                # Canonical vocabulary (aidream db/models/chat.py Message.source):
                # 'user' | 'agent_template' | 'system' — enforced by the cloud
                # CHECK constraint cx_message_source_check. `source` is the
                # ORIGIN of the row (user session vs template vs system
                # injection), NOT turn authorship — `role` carries that. Every
                # message created in a user's local session is source='user',
                # assistant/tool turns included (MXL-D-052: writing 'model'
                # here made every AI message 400 on push, permanently).
                "user",
                len(msg.get("content", "") or ""),
                msg.get("created_at") or now,
                msg.get("created_at") or now,
            ),
        )
        await enqueue_change("chat", "message", msg["id"], self._db, commit=False)
        await self._db.commit()

    async def create_many(self, messages: list[dict[str, Any]]) -> None:
        for m in messages:
            await self.create(m)

    async def update(self, msg_id: str, updates: dict[str, Any]) -> None:
        from app.services.local_db.outbox import enqueue_change

        sets: list[str] = []
        params: list[Any] = []
        if "content" in updates:
            sets.append("content = ?")
            params.append(_content_to_parts(updates["content"] or ""))
            sets.append("content_chars = ?")
            params.append(len(updates["content"] or ""))
        if "error" in updates:
            sets.append("error = ?")
            params.append(
                _json_dumps({"message": updates["error"]}) if updates["error"] else None
            )
        meta_updates = {
            k: updates[k] for k in ("model", "tool_calls", "tool_results") if k in updates
        }
        if meta_updates:
            sets.append("metadata = json_patch(COALESCE(metadata, '{}'), ?)")
            params.append(_json_dumps(meta_updates))
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(msg_id)
        await self._db.execute(
            f"UPDATE chat.message SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        await enqueue_change("chat", "message", msg_id, self._db, commit=False)
        await self._db.commit()

    async def delete_by_conversation(self, conv_id: str) -> int:
        """Tombstone every live message in a conversation (cloud semantics)."""
        from app.services.local_db.outbox import enqueue_change

        now = _now()
        rows = await self._db.fetchall(
            "SELECT id FROM chat.message WHERE conversation_id = ? AND deleted_at IS NULL",
            (conv_id,),
        )
        cursor = await self._db.execute(
            "UPDATE chat.message SET deleted_at = ?, updated_at = ? "
            "WHERE conversation_id = ? AND deleted_at IS NULL",
            (now, now, conv_id),
        )
        for r in rows:
            await enqueue_change("chat", "message", r["id"], self._db, commit=False)
        await self._db.commit()
        return cursor.rowcount


# ==================================================================
# ToolsRepo — tools table
# ==================================================================

class ToolsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_all(self, source: str | None = None) -> list[dict[str, Any]]:
        if source:
            rows = await self._db.fetchall(
                "SELECT * FROM tools WHERE source = ? ORDER BY category, name",
                (source,),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM tools ORDER BY category, name"
            )
        return [self._deserialize(r) for r in rows]

    async def list_by_category(self) -> dict[str, list[dict[str, Any]]]:
        all_tools = await self.list_all()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for t in all_tools:
            cat = t.get("category", "other")
            grouped.setdefault(cat, []).append(t)
        return grouped

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM tools WHERE name = ?", (name,))
        return self._deserialize(row) if row else None

    async def upsert(self, tool: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO tools (id, name, description, category, tags,
               parameters, source, version, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, description=excluded.description,
                 category=excluded.category, tags=excluded.tags,
                 parameters=excluded.parameters, source=excluded.source,
                 version=excluded.version, raw_json=excluded.raw_json,
                 updated_at=excluded.updated_at""",
            (
                tool.get("id", tool.get("name", "")),
                tool.get("name", ""),
                tool.get("description", ""),
                tool.get("category", ""),
                _json_dumps(tool.get("tags", [])),
                _json_dumps(tool.get("parameters", {})),
                tool.get("source", "local"),
                tool.get("version", "1"),
                _json_dumps(tool),
                _now(),
            ),
        )
        await self._db.commit()

    async def upsert_many(self, tools: list[dict[str, Any]]) -> None:
        for t in tools:
            await self.upsert(t)

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) as cnt FROM tools")
        return row["cnt"] if row else 0

    def _deserialize(self, row) -> dict[str, Any]:
        d = _row_to_dict(row)
        d["tags"] = _json_loads(d.get("tags", "[]")) or []
        d["parameters"] = _json_loads(d.get("parameters", "{}")) or {}
        d["raw_json"] = _json_loads(d.get("raw_json", "{}")) or {}
        return d


# ==================================================================
# SyncMetaRepo — sync_meta + sync_queue tables
# ==================================================================

class SyncMetaRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def get_last_sync(self, entity_type: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM sync_meta WHERE entity_type = ?", (entity_type,)
        )
        return _row_to_dict(row) if row else None

    async def set_last_sync(
        self,
        entity_type: str,
        *,
        status: str = "success",
        last_hash: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = _now()
        await self._db.execute(
            """INSERT INTO sync_meta (entity_type, last_synced_at, last_hash, status, error_message, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(entity_type) DO UPDATE SET
                 last_synced_at=excluded.last_synced_at, last_hash=excluded.last_hash,
                 status=excluded.status, error_message=excluded.error_message,
                 updated_at=excluded.updated_at""",
            (entity_type, now, last_hash, status, error_message, now),
        )
        await self._db.commit()

    async def get_all_sync_status(self) -> list[dict[str, Any]]:
        rows = await self._db.fetchall("SELECT * FROM sync_meta ORDER BY entity_type")
        return [_row_to_dict(r) for r in rows]

    # -- Sync queue --

    async def enqueue(self, entity_type: str, entity_id: str, action: str, payload: dict) -> None:
        await self._db.execute(
            """INSERT INTO sync_queue (entity_type, entity_id, action, payload)
               VALUES (?, ?, ?, ?)""",
            (entity_type, entity_id, action, _json_dumps(payload)),
        )
        await self._db.commit()

    async def dequeue(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM sync_queue ORDER BY created_at LIMIT ?", (limit,)
        )
        return [_row_to_dict(r) for r in rows]

    async def remove_from_queue(self, queue_id: int) -> None:
        await self._db.execute("DELETE FROM sync_queue WHERE id = ?", (queue_id,))
        await self._db.commit()

    async def increment_attempts(self, queue_id: int) -> None:
        await self._db.execute(
            "UPDATE sync_queue SET attempts = attempts + 1 WHERE id = ?", (queue_id,)
        )
        await self._db.commit()

    async def pending_count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) as cnt FROM sync_queue")
        return row["cnt"] if row else 0


# ==================================================================
# TokenRepo — auth_tokens table (single row: key='current_user')
# ==================================================================

_TOKEN_KEY = "current_user"


class TokenRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def get(self) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM auth_tokens WHERE key = ?", (_TOKEN_KEY,)
        )
        if not row:
            return None
        data = _row_to_dict(row)
        # Decrypt tokens that were encrypted at rest (transparent for legacy
        # plaintext rows). An undecryptable token decrypts to None → the
        # caller sees no usable session and re-auth kicks in.
        from app.services.local_db.secret_store import unprotect

        if data.get("access_token") is not None:
            data["access_token"] = unprotect(data["access_token"])
        if data.get("refresh_token") is not None:
            data["refresh_token"] = unprotect(data["refresh_token"])
        return data

    async def save(
        self,
        access_token: str,
        user_id: str,
        refresh_token: str | None = None,
        expires_at: int | None = None,
    ) -> None:
        # 🚨 Do not restore a passthrough: protect() is the fail-loud encrypted
        # storage resolver, and plaintext is not an equivalent credential store.
        from app.services.local_db.secret_store import protect

        await self._db.execute(
            """INSERT INTO auth_tokens (key, access_token, refresh_token, user_id, expires_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 access_token=excluded.access_token,
                 refresh_token=excluded.refresh_token,
                 user_id=excluded.user_id,
                 expires_at=excluded.expires_at,
                 updated_at=excluded.updated_at""",
            (
                _TOKEN_KEY,
                protect(access_token),
                protect(refresh_token),
                user_id,
                expires_at,
                _now(),
            ),
        )
        await self._db.commit()

    async def get_owner_user_id(self) -> str | None:
        """Return the signed-in owner's user_id without decrypting tokens.

        Used by the auth layer to authorize remote (tunnel) callers: only the
        user who signed into THIS instance may drive it remotely. Reads just
        the plaintext user_id column, so it's cheap and avoids touching the
        keychain on every request.
        """
        row = await self._db.fetchone(
            "SELECT user_id FROM auth_tokens WHERE key = ?", (_TOKEN_KEY,)
        )
        if not row:
            return None
        uid = _row_to_dict(row).get("user_id")
        return uid or None

    async def clear(self) -> None:
        await self._db.execute("DELETE FROM auth_tokens WHERE key = ?", (_TOKEN_KEY,))
        await self._db.commit()

    def is_expired(self, token_row: dict[str, Any]) -> bool:
        """True when the ACCESS token is expired.

        The JWT's own ``exp`` claim is the source of truth — the stored
        ``expires_at`` column has been observed carrying the SESSION
        (refresh-token) expiry (~7 days) while the access token was already
        dead, which made every engine-owned sync loop run "configured" into
        guaranteed 401s (MXL-D-046). The column is only a fallback for rows
        whose token cannot be decoded.
        """
        import base64
        import json as _json
        import time

        token = token_row.get("access_token")
        if isinstance(token, str) and token.count(".") == 2:
            try:
                payload_b64 = token.split(".")[1]
                payload_b64 += "=" * (-len(payload_b64) % 4)
                claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
                exp = claims.get("exp")
                if exp is not None:
                    return int(time.time()) >= int(exp)
            except Exception:
                logger.warning(
                    "[auth_tokens] could not decode JWT exp claim — falling back "
                    "to the stored expires_at column (known to over-report)"
                )
        expires_at = token_row.get("expires_at")
        if not expires_at:
            return False
        return int(time.time()) >= int(expires_at)


# ==================================================================
# PromptBuiltinsRepo — prompt_builtins table
# ==================================================================

class PromptBuiltinsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_all(self, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM prompt_builtins"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"
        rows = await self._db.fetchall(sql)
        return [self._deserialize(r) for r in rows]

    async def get(self, builtin_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM prompt_builtins WHERE id = ?", (builtin_id,)
        )
        return self._deserialize(row) if row else None

    async def upsert(self, builtin: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO prompt_builtins
               (id, name, description, category, tags, variable_defaults, settings, is_active, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, description=excluded.description,
                 category=excluded.category, tags=excluded.tags,
                 variable_defaults=excluded.variable_defaults,
                 settings=excluded.settings, is_active=excluded.is_active,
                 raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
            (
                builtin["id"],
                builtin.get("name", ""),
                builtin.get("description", ""),
                builtin.get("category", ""),
                _json_dumps(builtin.get("tags", [])),
                _json_dumps(builtin.get("variable_defaults", [])),
                _json_dumps(builtin.get("settings", {})),
                int(builtin.get("is_active", True)),
                _json_dumps(builtin),
                _now(),
            ),
        )
        await self._db.commit()

    async def upsert_many(self, builtins: list[dict[str, Any]]) -> None:
        for b in builtins:
            await self.upsert(b)

    async def delete_missing(self, keep_ids: set[str]) -> int:
        if not keep_ids:
            return 0
        placeholders = ",".join("?" for _ in keep_ids)
        cursor = await self._db.execute(
            f"DELETE FROM prompt_builtins WHERE id NOT IN ({placeholders})",
            tuple(keep_ids),
        )
        await self._db.commit()
        return cursor.rowcount

    async def count(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) as cnt FROM prompt_builtins WHERE is_active = 1")
        return row["cnt"] if row else 0

    def _deserialize(self, row) -> dict[str, Any]:
        d = _row_to_dict(row)
        d["tags"] = _json_loads(d.get("tags", "[]")) or []
        d["variable_defaults"] = _json_loads(d.get("variable_defaults", "[]")) or []
        d["settings"] = _json_loads(d.get("settings", "{}")) or {}
        d["is_active"] = bool(d.get("is_active", 1))
        d["raw_json"] = _json_loads(d.get("raw_json", "{}")) or {}
        return d


# ==================================================================
# PromptsRepo — prompts table (user-owned prompts)
# ==================================================================

class PromptsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM prompts WHERE user_id = ? ORDER BY name",
            (user_id,),
        )
        return [self._deserialize(r) for r in rows]

    async def list_all(self, user_id: str | None = None) -> list[dict[str, Any]]:
        if user_id:
            return await self.list_for_user(user_id)
        rows = await self._db.fetchall("SELECT * FROM prompts ORDER BY name")
        return [self._deserialize(r) for r in rows]

    async def get(self, prompt_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id:
            row = await self._db.fetchone(
                "SELECT * FROM prompts WHERE id = ? AND user_id = ?",
                (prompt_id, user_id),
            )
        else:
            row = await self._db.fetchone("SELECT * FROM prompts WHERE id = ?", (prompt_id,))
        return self._deserialize(row) if row else None

    async def upsert(self, prompt: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO prompts
               (id, user_id, name, description, category, tags, variable_defaults,
                settings, is_favorite, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 user_id=excluded.user_id, name=excluded.name,
                 description=excluded.description, category=excluded.category,
                 tags=excluded.tags, variable_defaults=excluded.variable_defaults,
                 settings=excluded.settings, is_favorite=excluded.is_favorite,
                 raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
            (
                prompt["id"],
                prompt.get("user_id", ""),
                prompt.get("name", ""),
                prompt.get("description", ""),
                prompt.get("category", ""),
                _json_dumps(prompt.get("tags", [])),
                _json_dumps(prompt.get("variable_defaults", [])),
                _json_dumps(prompt.get("settings", {})),
                int(prompt.get("is_favorite", False)),
                _json_dumps(prompt),
                _now(),
            ),
        )
        await self._db.commit()

    async def upsert_many(self, prompts: list[dict[str, Any]]) -> None:
        for p in prompts:
            await self.upsert(p)

    async def delete_for_user(self, user_id: str, keep_ids: set[str]) -> int:
        if not keep_ids:
            cursor = await self._db.execute(
                "DELETE FROM prompts WHERE user_id = ?", (user_id,)
            )
        else:
            placeholders = ",".join("?" for _ in keep_ids)
            cursor = await self._db.execute(
                f"DELETE FROM prompts WHERE user_id = ? AND id NOT IN ({placeholders})",
                (user_id, *keep_ids),
            )
        await self._db.commit()
        return cursor.rowcount

    async def count(self, user_id: str | None = None) -> int:
        if user_id:
            row = await self._db.fetchone(
                "SELECT COUNT(*) as cnt FROM prompts WHERE user_id = ?",
                (user_id,),
            )
        else:
            row = await self._db.fetchone("SELECT COUNT(*) as cnt FROM prompts")
        return row["cnt"] if row else 0

    def _deserialize(self, row) -> dict[str, Any]:
        d = _row_to_dict(row)
        d["tags"] = _json_loads(d.get("tags", "[]")) or []
        d["variable_defaults"] = _json_loads(d.get("variable_defaults", "[]")) or []
        d["settings"] = _json_loads(d.get("settings", "{}")) or {}
        d["is_favorite"] = bool(d.get("is_favorite", 0))
        d["raw_json"] = _json_loads(d.get("raw_json", "{}")) or {}
        return d


# ==================================================================
# NotesRepo — notes table (primary local store for offline-first notes)
# ==================================================================

class NotesRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_all(
        self,
        folder_id: str | None = None,
        include_deleted: bool = False,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        clauses: list[str] = []
        if not include_deleted:
            clauses.append("is_deleted = 0")
        if folder_id is not None:
            clauses.append("folder_id = ?")
            params.append(folder_id)
        if search:
            clauses.append("(label LIKE ? OR title LIKE ? OR content LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.fetchall(
            f"SELECT * FROM notes{where} ORDER BY updated_at DESC", tuple(params)
        )
        return [self._deserialize(r) for r in rows]

    async def get(self, note_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM notes WHERE id = ?", (note_id,))
        return self._deserialize(row) if row else None

    async def get_by_file_path(self, file_path: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM notes WHERE file_path = ? AND is_deleted = 0", (file_path,)
        )
        return self._deserialize(row) if row else None

    async def upsert(self, note: dict[str, Any]) -> None:
        now = _now()
        await self._db.execute(
            """INSERT INTO notes
               (id, user_id, folder_id, title, label, content, content_hash, file_path,
                is_deleted, is_pinned, tags, sync_version, supabase_updated_at,
                sync_status, last_synced_at, sync_enabled, remote_content_hash,
                folder_name, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 user_id=excluded.user_id, folder_id=excluded.folder_id,
                 title=excluded.title, label=excluded.label,
                 content=excluded.content,
                 content_hash=excluded.content_hash, file_path=excluded.file_path,
                 is_deleted=excluded.is_deleted, is_pinned=excluded.is_pinned,
                 tags=excluded.tags, sync_version=excluded.sync_version,
                 supabase_updated_at=excluded.supabase_updated_at,
                 sync_status=excluded.sync_status,
                 last_synced_at=excluded.last_synced_at,
                 sync_enabled=excluded.sync_enabled,
                 remote_content_hash=excluded.remote_content_hash,
                 folder_name=excluded.folder_name,
                 metadata=excluded.metadata,
                 updated_at=excluded.updated_at""",
            (
                note["id"],
                note.get("user_id", ""),
                note.get("folder_id"),
                note.get("title", note.get("label", "")),
                note.get("label", note.get("title", "")),
                note.get("content", ""),
                note.get("content_hash"),
                note.get("file_path"),
                int(note.get("is_deleted", False)),
                int(note.get("is_pinned", False)),
                _json_dumps(note.get("tags", [])),
                note.get("sync_version", 0),
                note.get("supabase_updated_at") or note.get("updated_at"),
                note.get("sync_status", "never_synced"),
                note.get("last_synced_at"),
                int(note.get("sync_enabled", True)),
                note.get("remote_content_hash"),
                note.get("folder_name", "General"),
                _json_dumps(note.get("metadata", {})),
                note.get("created_at", now),
                note.get("updated_at", now),
            ),
        )
        await self._db.commit()

    async def upsert_many(self, notes: list[dict[str, Any]]) -> None:
        for n in notes:
            await self.upsert(n)

    async def update_fields(self, note_id: str, updates: dict[str, Any]) -> None:
        allowed = {
            "title", "label", "content", "content_hash", "file_path",
            "folder_id", "folder_name", "tags", "metadata", "is_deleted",
            "is_pinned", "sync_version", "sync_status", "last_synced_at",
            "sync_enabled", "remote_content_hash", "supabase_updated_at",
        }
        sets: list[str] = []
        params: list[Any] = []
        for key, val in updates.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            if key in ("tags", "metadata"):
                params.append(_json_dumps(val))
            elif key in ("is_deleted", "is_pinned", "sync_enabled"):
                params.append(int(bool(val)))
            else:
                params.append(val)
        if not sets:
            return
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(note_id)
        await self._db.execute(
            f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", tuple(params)
        )
        await self._db.commit()

    async def soft_delete(self, note_id: str) -> None:
        await self._db.execute(
            "UPDATE notes SET is_deleted = 1, updated_at = ? WHERE id = ?",
            (_now(), note_id),
        )
        await self._db.commit()

    async def hard_delete(self, note_id: str) -> None:
        await self._db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        await self._db.commit()

    async def list_syncable(self) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM notes WHERE is_deleted = 0 AND sync_enabled = 1 "
            "ORDER BY updated_at DESC"
        )
        return [self._deserialize(r) for r in rows]

    async def list_pending_push(self) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM notes WHERE sync_status IN ('never_synced', 'pending_push', 'failed') "
            "AND sync_enabled = 1 AND is_deleted = 0 ORDER BY updated_at DESC"
        )
        return [self._deserialize(r) for r in rows]

    async def list_excluded(self) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM notes WHERE sync_status = 'excluded' OR sync_enabled = 0 "
            "ORDER BY updated_at DESC"
        )
        return [self._deserialize(r) for r in rows]

    async def set_sync_status(
        self, note_id: str, status: str, remote_hash: str | None = None
    ) -> None:
        params: list[Any] = [status, _now()]
        sets = ["sync_status = ?", "updated_at = ?"]
        if status == "synced":
            sets.append("last_synced_at = ?")
            params.append(_now())
        if remote_hash is not None:
            sets.append("remote_content_hash = ?")
            params.append(remote_hash)
        params.append(note_id)
        await self._db.execute(
            f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", tuple(params)
        )
        await self._db.commit()

    async def set_excluded(self, note_id: str, excluded: bool) -> None:
        status = "excluded" if excluded else "never_synced"
        await self._db.execute(
            "UPDATE notes SET sync_status = ?, sync_enabled = ?, updated_at = ? WHERE id = ?",
            (status, int(not excluded), _now(), note_id),
        )
        await self._db.commit()

    async def count(self, user_id: str | None = None) -> int:
        if user_id:
            row = await self._db.fetchone(
                "SELECT COUNT(*) as cnt FROM notes WHERE user_id = ? AND is_deleted = 0",
                (user_id,),
            )
        else:
            row = await self._db.fetchone(
                "SELECT COUNT(*) as cnt FROM notes WHERE is_deleted = 0"
            )
        return row["cnt"] if row else 0

    def _deserialize(self, row) -> dict[str, Any]:
        d = _row_to_dict(row)
        d["tags"] = _json_loads(d.get("tags", "[]")) or []
        d["metadata"] = _json_loads(d.get("metadata", "{}")) or {}
        d["is_deleted"] = bool(d.get("is_deleted", 0))
        d["is_pinned"] = bool(d.get("is_pinned", 0))
        d["sync_enabled"] = bool(d.get("sync_enabled", 1))
        return d


# ==================================================================
# NoteVersionsRepo — note_versions table (local version history)
# ==================================================================

class NoteVersionsRepo:
    """Local version history for notes — works fully offline."""

    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_for_note(self, note_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM note_versions WHERE note_id = ? ORDER BY version_number DESC LIMIT ?",
            (note_id, limit),
        )
        return [_row_to_dict(r) for r in rows]

    async def get_version(self, note_id: str, version_number: int) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM note_versions WHERE note_id = ? AND version_number = ?",
            (note_id, version_number),
        )
        return _row_to_dict(row) if row else None

    async def next_version_number(self, note_id: str) -> int:
        row = await self._db.fetchone(
            "SELECT MAX(version_number) as max_v FROM note_versions WHERE note_id = ?",
            (note_id,),
        )
        return (row["max_v"] or 0) + 1 if row else 1

    async def create_snapshot(
        self,
        note_id: str,
        content: str,
        label: str = "",
        user_id: str = "",
        change_source: str = "local",
    ) -> dict[str, Any]:
        import hashlib
        from uuid import uuid4

        version_number = await self.next_version_number(note_id)
        c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        version_id = str(uuid4())
        now = _now()

        await self._db.execute(
            """INSERT INTO note_versions
               (id, note_id, user_id, content, content_hash, label,
                version_number, change_source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, note_id, user_id, content, c_hash, label,
             version_number, change_source, now),
        )
        await self._db.commit()

        return {
            "id": version_id,
            "note_id": note_id,
            "version_number": version_number,
            "label": label,
            "content": content,
            "content_hash": c_hash,
            "change_source": change_source,
            "created_at": now,
        }

    async def delete_for_note(self, note_id: str) -> None:
        await self._db.execute("DELETE FROM note_versions WHERE note_id = ?", (note_id,))
        await self._db.commit()

    async def prune(self, note_id: str, keep: int = 100) -> None:
        """Keep only the most recent `keep` versions for a note."""
        await self._db.execute(
            """DELETE FROM note_versions WHERE note_id = ? AND id NOT IN (
                SELECT id FROM note_versions WHERE note_id = ?
                ORDER BY version_number DESC LIMIT ?
            )""",
            (note_id, note_id, keep),
        )
        await self._db.commit()


# ==================================================================
# NoteFoldersRepo — note_folders table
# ==================================================================

class NoteFoldersRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def list_for_user(
        self, user_id: str, include_deleted: bool = False
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM note_folders WHERE user_id = ?"
        if not include_deleted:
            sql += " AND is_deleted = 0"
        sql += " ORDER BY path"
        rows = await self._db.fetchall(sql, (user_id,))
        return [self._deserialize(r) for r in rows]

    async def get(self, folder_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM note_folders WHERE id = ?", (folder_id,)
        )
        return self._deserialize(row) if row else None

    async def upsert(self, folder: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO note_folders
               (id, user_id, parent_id, name, path, is_deleted, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 user_id=excluded.user_id, parent_id=excluded.parent_id,
                 name=excluded.name, path=excluded.path,
                 is_deleted=excluded.is_deleted, updated_at=excluded.updated_at""",
            (
                folder["id"],
                folder.get("user_id", ""),
                folder.get("parent_id"),
                folder.get("name", ""),
                folder.get("path", ""),
                int(folder.get("is_deleted", False)),
                folder.get("created_at", _now()),
                folder.get("updated_at", _now()),
            ),
        )
        await self._db.commit()

    async def upsert_many(self, folders: list[dict[str, Any]]) -> None:
        for f in folders:
            await self.upsert(f)

    def _deserialize(self, row) -> dict[str, Any]:
        d = _row_to_dict(row)
        d["is_deleted"] = bool(d.get("is_deleted", 0))
        return d


# ==================================================================
# AppInstanceRepo — app_instance table (single-row: key='self')
# ==================================================================

_INSTANCE_KEY = "self"


class AppInstanceRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def get(self) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM app_instance WHERE key = ?", (_INSTANCE_KEY,)
        )
        if not row:
            return None
        d = _row_to_dict(row)
        d["raw_json"] = _json_loads(d.get("raw_json", "{}")) or {}
        return d

    async def save(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO app_instance
               (key, instance_id, instance_name, user_id, platform, os_version,
                architecture, hostname, registered_at, last_heartbeat, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 instance_id=excluded.instance_id, instance_name=excluded.instance_name,
                 user_id=excluded.user_id, platform=excluded.platform,
                 os_version=excluded.os_version, architecture=excluded.architecture,
                 hostname=excluded.hostname, registered_at=excluded.registered_at,
                 last_heartbeat=excluded.last_heartbeat,
                 raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
            (
                _INSTANCE_KEY,
                data.get("instance_id", ""),
                data.get("instance_name", "My Computer"),
                data.get("user_id", ""),
                data.get("platform", ""),
                data.get("os_version", ""),
                data.get("architecture", ""),
                data.get("hostname", ""),
                data.get("registered_at"),
                data.get("last_heartbeat"),
                _json_dumps(data),
                _now(),
            ),
        )
        await self._db.commit()

    async def update_heartbeat(self) -> None:
        await self._db.execute(
            "UPDATE app_instance SET last_heartbeat = ?, updated_at = ? WHERE key = ?",
            (_now(), _now(), _INSTANCE_KEY),
        )
        await self._db.commit()


# ==================================================================
# AppSettingsRepo — app_settings table (single-row: key='settings')
# ==================================================================

_SETTINGS_KEY = "settings"


_APP_SETTINGS_WRITE_LOCK = asyncio.Lock()


class AppSettingsRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self._db = db or get_db()

    async def get_all(self) -> dict[str, Any]:
        row = await self._db.fetchone(
            "SELECT settings FROM app_settings WHERE key = ?", (_SETTINGS_KEY,)
        )
        if not row:
            return {}
        return _json_loads(row["settings"]) or {}

    async def save_all(self, settings: dict[str, Any]) -> None:
        await self._db.execute(
            """INSERT INTO app_settings (key, settings, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 settings=excluded.settings, updated_at=excluded.updated_at""",
            (_SETTINGS_KEY, _json_dumps(settings), _now()),
        )
        await self._db.commit()

    async def get(self, key: str, default: Any = None) -> Any:
        all_settings = await self.get_all()
        return all_settings.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        # Serialize read-modify-write on the single JSON blob row: two
        # concurrent writers would each get_all → mutate → save_all and the
        # loser silently erases the winner's key (this blob also stores the
        # user's API keys via ApiKeysRepo).
        async with _APP_SETTINGS_WRITE_LOCK:
            all_settings = await self.get_all()
            all_settings[key] = value
            await self.save_all(all_settings)

    async def set_many(self, updates: dict[str, Any]) -> None:
        async with _APP_SETTINGS_WRITE_LOCK:
            all_settings = await self.get_all()
            all_settings.update(updates)
            await self.save_all(all_settings)


# ==================================================================
# ApiKeysRepo — stores user-supplied AI provider API keys
#
# Keys live inside the app_settings blob under the "api_keys" entry:
#   {"api_keys": {"openai": "<base64>", "anthropic": "<base64>", ...}}
#
# Values are base64-encoded before write and decoded on read to reduce
# accidental exposure in log output and screenshots.  This is not
# encryption — the local SQLite file (~/.matrx/matrx.db) is user-owned.
# ==================================================================

_API_KEYS_SETTINGS_KEY = "api_keys"

def _encode_key(value: str) -> str:
    # 🚨 Do not restore base64/plaintext substitution; protect() must raise when
    # the keychain-backed encryption resource is missing.
    from app.services.local_db.secret_store import protect

    protected = protect(value)
    assert protected is not None
    return protected


def _decode_key(encoded: str) -> str:
    from app.services.local_db.secret_store import unprotect

    # Encrypted values (enc:v1:) round-trip through unprotect; an
    # undecryptable one yields "" so a stale key isn't used as-is.
    if encoded.startswith("enc:v1:"):
        return unprotect(encoded) or ""
    try:
        return base64.b64decode(encoded.encode()).decode()
    except Exception:
        return encoded  # graceful fallback if value was stored plain


class ApiKeysRepo:
    """Read/write per-provider API keys from the app_settings SQLite blob."""

    def __init__(self, db: LocalDatabase | None = None):
        self._settings = AppSettingsRepo(db)

    async def get_all(self) -> dict[str, str]:
        """Return {provider: plaintext_key} for all stored providers."""
        raw: dict[str, str] = await self._settings.get(_API_KEYS_SETTINGS_KEY, {})
        return {k: _decode_key(v) for k, v in raw.items() if isinstance(v, str)}

    async def get(self, provider: str) -> str | None:
        """Return plaintext key for one provider, or None if not set."""
        all_keys = await self.get_all()
        return all_keys.get(provider)

    async def set(self, provider: str, key: str) -> None:
        """Store a key for one provider (encrypted/base64-encoded).

        The whole read-modify-write runs under the shared blob lock — doing
        the api_keys-dict read outside it let two concurrent key saves drop
        one of the keys.
        """
        async with _APP_SETTINGS_WRITE_LOCK:
            all_settings = await self._settings.get_all()
            raw = dict(all_settings.get(_API_KEYS_SETTINGS_KEY) or {})
            raw[provider] = _encode_key(key)
            all_settings[_API_KEYS_SETTINGS_KEY] = raw
            await self._settings.save_all(all_settings)

    async def delete(self, provider: str) -> None:
        """Remove a stored key for one provider."""
        async with _APP_SETTINGS_WRITE_LOCK:
            all_settings = await self._settings.get_all()
            raw = dict(all_settings.get(_API_KEYS_SETTINGS_KEY) or {})
            raw.pop(provider, None)
            all_settings[_API_KEYS_SETTINGS_KEY] = raw
            await self._settings.save_all(all_settings)

    async def is_configured(self, provider: str) -> bool:
        """Return True if a non-empty key is stored for this provider."""
        key = await self.get(provider)
        return bool(key and key.strip())
