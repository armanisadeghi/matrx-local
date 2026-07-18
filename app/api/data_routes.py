"""Data API routes — local-first reads from SQLite.

All endpoints read from the local SQLite database for instant response.
Cloud data is synced in the background by the SyncEngine.

Provides:
  GET  /data/models                    — AI models (cached from Supabase)
  GET  /data/agents                    — Agents / prompts (cached from Supabase)
  GET  /data/tools                     — Tools (from local manifest)
  GET  /data/tools/by-category         — Tools grouped by category

  GET  /data/conversations             — List conversations
  GET  /data/conversations/{id}        — Single conversation with messages
  POST /data/conversations             — Create a conversation
  PUT  /data/conversations/{id}        — Update conversation metadata
  DEL  /data/conversations/{id}        — Delete conversation + messages

  POST /data/conversations/{id}/messages  — Add a message
  PUT  /data/messages/{id}                — Update a message

  POST /data/sync                      — Trigger manual full sync
  GET  /data/sync/status               — Sync status per entity type

  POST /data/conversations/import      — Import conversations from localStorage JSON

NOTE: Diagnostic endpoints (debug state, live log stream) live at
      /setup/debug and /setup/logs — both are public (no auth required).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.common.system_logger import get_logger
from app.services.local_db.repositories import (
    ModelsRepo,
    AgentsRepo,
    ConversationsRepo,
    MessagesRepo,
    ToolsRepo,
)
from app.services.local_db.sync_engine import get_sync_engine

logger = get_logger()
router = APIRouter(prefix="/data", tags=["data"])


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------

class CreateConversationRequest(BaseModel):
    id: str
    title: str = "New conversation"
    mode: str = "chat"
    model: str = ""
    server_conversation_id: str | None = None
    route_mode: str = "chat"
    agent_id: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    mode: str | None = None
    model: str | None = None
    server_conversation_id: str | None = None
    route_mode: str | None = None
    agent_id: str | None = None


class CreateMessageRequest(BaseModel):
    id: str
    role: str = "user"
    content: str = ""
    model: str | None = None
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    error: str | None = None


class UpdateMessageRequest(BaseModel):
    content: str | None = None
    model: str | None = None
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    error: str | None = None


class ImportConversationsRequest(BaseModel):
    """Import conversations from the frontend's localStorage format."""
    conversations: list[dict[str, Any]]


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------

@router.get("/models")
async def list_models() -> dict[str, Any]:
    """Return all AI models from the local cache."""
    repo = ModelsRepo()
    models = await repo.list_all()
    return {"models": models, "total": len(models), "source": "local_db"}


# ------------------------------------------------------------------
# Agents
# ------------------------------------------------------------------

@router.get("/agents")
async def list_agents() -> dict[str, Any]:
    """Return all agents from the local cache.

    Resolves the authenticated user_id from the stored JWT so only this
    user's own prompts are returned alongside builtins.
    """
    from app.services.local_db.repositories import TokenRepo

    user_id: str | None = None
    try:
        token_row = await TokenRepo().get()
        if token_row:
            user_id = token_row.get("user_id") or None
    except Exception:
        pass

    repo = AgentsRepo()
    all_agents = await repo.list_all(user_id=user_id)
    builtins = [a for a in all_agents if a.get("source") == "builtin"]
    user = [a for a in all_agents if a.get("source") == "user"]
    return {
        "builtins": builtins,
        "user": user,
        "shared": [],
        "source": "local_db",
        "totals": {
            "builtins": len(builtins),
            "user": len(user),
            "shared": 0,
            "total": len(builtins) + len(user),
        },
    }


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """Return all tools from the local cache."""
    repo = ToolsRepo()
    tools = await repo.list_all()
    return {"tools": tools, "total": len(tools), "source": "local_db"}


@router.get("/tools/by-category")
async def list_tools_by_category() -> dict[str, Any]:
    """Return tools grouped by category."""
    repo = ToolsRepo()
    grouped = await repo.list_by_category()
    return {
        "categories": grouped,
        "total_categories": len(grouped),
        "total_tools": sum(len(v) for v in grouped.values()),
        "source": "local_db",
    }


# ------------------------------------------------------------------
# Conversations
# ------------------------------------------------------------------

@router.get("/conversations")
async def list_conversations(limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """Return conversations ordered by last update."""
    repo = ConversationsRepo()
    convs = await repo.list_all(limit=limit, offset=offset)
    total = await repo.count()
    return {"conversations": convs, "total": total}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str) -> dict[str, Any]:
    """Return a single conversation with all its messages."""
    conv_repo = ConversationsRepo()
    msg_repo = MessagesRepo()

    conv = await conv_repo.get(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await msg_repo.list_by_conversation(conv_id)
    return {**conv, "messages": messages}


@router.get("/conversations/{conv_id}/activity")
async def get_conversation_activity(conv_id: str) -> dict[str, Any]:
    """Return the canonical local conversation execution timeline.

    This keeps tool calls, executor traces, messages, media, and request rows
    correlated by their durable conversation/call identifiers. Consumers must
    not reconstruct tool activity by parsing display text from messages.
    """
    from app.services.ai.conversation_handler import get_conversation_store

    try:
        data = await get_conversation_store().get_conversation_data(conv_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return {**data, "source": "local_db"}


@router.post("/conversations", status_code=201)
async def create_conversation(req: CreateConversationRequest) -> dict[str, Any]:
    """Create a new conversation."""
    repo = ConversationsRepo()
    await repo.create(req.model_dump())
    return {"id": req.id, "status": "created"}


@router.put("/conversations/{conv_id}")
async def update_conversation(conv_id: str, req: UpdateConversationRequest) -> dict[str, Any]:
    """Update conversation metadata."""
    repo = ConversationsRepo()
    existing = await repo.get(conv_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        await repo.update(conv_id, updates)
    return {"id": conv_id, "status": "updated"}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str) -> dict[str, Any]:
    """Delete a conversation and all its messages."""
    repo = ConversationsRepo()
    await repo.delete(conv_id)
    return {"id": conv_id, "status": "deleted"}


# ------------------------------------------------------------------
# Messages
# ------------------------------------------------------------------

@router.post("/conversations/{conv_id}/messages", status_code=201)
async def create_message(conv_id: str, req: CreateMessageRequest) -> dict[str, Any]:
    """Add a message to a conversation."""
    conv_repo = ConversationsRepo()
    existing = await conv_repo.get(conv_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg_data = req.model_dump()
    msg_data["conversation_id"] = conv_id
    msg_repo = MessagesRepo()
    await msg_repo.create(msg_data)

    # Touch conversation updated_at
    await conv_repo.update(conv_id, {"title": existing.get("title", "New conversation")})
    return {"id": req.id, "conversation_id": conv_id, "status": "created"}


@router.put("/messages/{msg_id}")
async def update_message(msg_id: str, req: UpdateMessageRequest) -> dict[str, Any]:
    """Update a message (e.g., streaming content, tool results)."""
    msg_repo = MessagesRepo()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        await msg_repo.update(msg_id, updates)
    return {"id": msg_id, "status": "updated"}


# ------------------------------------------------------------------
# Import from localStorage
# ------------------------------------------------------------------

@router.post("/conversations/import")
async def import_conversations(req: ImportConversationsRequest) -> dict[str, Any]:
    """Import conversations from the frontend's localStorage JSON format.

    This is a one-time migration endpoint.  It creates conversations and
    messages in SQLite from the localStorage ``matrx-chat-conversations`` blob.
    Existing conversations with the same ID are skipped (idempotent).
    """
    conv_repo = ConversationsRepo()
    msg_repo = MessagesRepo()
    imported = 0
    skipped = 0

    for conv_data in req.conversations:
        conv_id = conv_data.get("id")
        if not conv_id:
            skipped += 1
            continue

        existing = await conv_repo.get(conv_id)
        if existing:
            skipped += 1
            continue

        await conv_repo.create({
            "id": conv_id,
            "title": conv_data.get("title", "Imported conversation"),
            "mode": conv_data.get("mode", "chat"),
            "model": conv_data.get("model", ""),
            "server_conversation_id": conv_data.get("serverConversationId"),
            "route_mode": conv_data.get("routeMode", "chat"),
            "agent_id": conv_data.get("agentId"),
            "created_at": conv_data.get("created_at"),
            "updated_at": conv_data.get("updated_at"),
        })

        messages = conv_data.get("messages", [])
        for msg in messages:
            await msg_repo.create({
                "id": msg.get("id", ""),
                "conversation_id": conv_id,
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
                "model": msg.get("model"),
                "tool_calls": msg.get("tool_calls"),
                "tool_results": msg.get("tool_results"),
                "error": msg.get("error"),
                "created_at": msg.get("timestamp"),
            })

        imported += 1

    return {"imported": imported, "skipped": skipped, "total": len(req.conversations)}


# ------------------------------------------------------------------
# Sync control
# ------------------------------------------------------------------

@router.post("/sync")
async def trigger_sync() -> dict[str, Any]:
    """Trigger a manual full sync from cloud → local."""
    engine = get_sync_engine()
    results = await engine.sync_all()
    return {"status": "completed", "results": results}


@router.get("/sync/status")
async def get_sync_status() -> dict[str, Any]:
    """Return the current sync status for all entity types."""
    engine = get_sync_engine()
    return await engine.get_status()


# ------------------------------------------------------------------
# Diagnostics — moved to /setup/debug (public, no auth required)
# ------------------------------------------------------------------

@router.get("/debug")
async def debug_state_redirect() -> dict[str, Any]:
    """Diagnostic endpoint moved to /setup/debug (public, no auth required).

    This stub preserves backward compatibility and redirects callers.
    """
    from fastapi.responses import RedirectResponse  # type: ignore[attr-defined]
    return RedirectResponse(url="/setup/debug", status_code=307)
