"""Background sync engine — pulls cloud data into local SQLite.

Architecture (see docs/SYNC_CONTRACT.md — the ratified sync contract)
---------------------------------------------------------------------
The cloud is the durable source of truth; local SQLite (~/.matrx/matrx.db) is
a FIRST-ACCESS REPLICA of it — a pull-only cache that makes reads instant and
offline-proof, never a competing server.  This engine is the ONLY component
allowed to write cloud catalog data into SQLite.  All other components read
from SQLite only (the replica IS the read path).

Sync sources:
  - AIDream server (/api/ai-models, /api/agents)
    → ai_models, prompt_builtins, agents tables
  - Local tool catalog (app.tools.catalog.get_catalog)
    → tools table

Lifecycle:
  1. On startup: full sync of models, agents, tools
  2. Periodic: re-syncs every N minutes
  3. On demand: call sync_all() or an individual sync_* method

Offline behaviour:
  If the AIDream server is unreachable, the sync cycle is skipped with a
  warning.  SQLite keeps whatever was cached from the last successful sync
  and the app continues to work normally.

User JWT:
  The agent catalog (/api/agents) now REQUIRES a JWT — there is no public
  builtins variant anymore. The engine reads the JWT from the auth_tokens
  SQLite table (written by React via POST /auth/token). If no valid token is
  stored, the agent sync is skipped entirely, the previously cached agents are
  kept, and the skip is logged loudly (never silently swallowed).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Optional

from app.common.system_logger import get_logger
from app.services.local_db.database import get_db
from app.services.local_db.repositories import (
    ModelsRepo,
    AgentsRepo,
    ToolsRepo,
    SyncMetaRepo,
    PromptBuiltinsRepo,
    PromptsRepo,
    TokenRepo,
)
from app.services.aidream.client import get_aidream_client, AIDreamOfflineError

logger = get_logger()

_instance: Optional["SyncEngine"] = None

DEFAULT_SYNC_INTERVAL = 600  # 10 minutes


def _log_sync_task_result(task: "asyncio.Task") -> None:
    """Done-callback for the background sync loop.

    A bare ``lambda _: None`` swallowed a crashing sync loop entirely — the
    task would die and never sync again with zero trace. Surface any real
    exception loudly (cancellation is normal on stop()).
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("[sync_engine] Background sync loop crashed", exc_info=exc)


class SyncEngine:
    """Pulls cloud data into the local SQLite database."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._interval = DEFAULT_SYNC_INTERVAL
        self._running = False
        self._models_repo = ModelsRepo()
        self._agents_repo = AgentsRepo()
        self._tools_repo = ToolsRepo()
        self._sync_meta = SyncMetaRepo()
        self._builtins_repo = PromptBuiltinsRepo()
        self._prompts_repo = PromptsRepo()
        self._token_repo = TokenRepo()

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, interval: int | None = None) -> None:
        """Start the background sync loop."""
        if self._task and not self._task.done():
            return
        if interval:
            self._interval = interval
        self._running = True
        self._task = asyncio.create_task(self._sync_loop())
        self._task.add_done_callback(_log_sync_task_result)
        logger.info("[sync_engine] Background sync started (interval=%ds)", self._interval)

    def stop(self) -> None:
        """Cancel the background sync loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("[sync_engine] Background sync stopped")

    async def _sync_loop(self) -> None:
        """Run a full sync on startup, then periodically."""
        await self.sync_all()

        while self._running:
            await asyncio.sleep(self._interval)
            if not self._running:
                break
            await self.sync_all()

    # ------------------------------------------------------------------
    # Full sync
    # ------------------------------------------------------------------

    async def sync_all(self) -> dict[str, str]:
        """Run all sync tasks.  Returns status per entity type."""
        results: dict[str, str] = {}

        for name, fn in [
            ("models", self.sync_models),
            ("agents", self.sync_agents),
            ("tools", self.sync_tools),
        ]:
            try:
                await fn()
                results[name] = "success"
            except AIDreamOfflineError as exc:
                results[name] = "offline"
                logger.warning("[sync_engine] %s sync skipped — server offline: %s", name, exc)
            except Exception:
                results[name] = "error"
                logger.warning("[sync_engine] %s sync failed", name, exc_info=True)

        return results

    # ------------------------------------------------------------------
    # Models sync
    # ------------------------------------------------------------------

    async def sync_models(self) -> None:
        """Pull AI models from AIDream server and cache in SQLite."""
        client = get_aidream_client()
        if client is None:
            logger.debug("[sync_engine] AIDream client not available — skipping model sync")
            await self._sync_meta.set_last_sync(
                "models", status="skipped", error_message="AIDREAM_SERVER_URL_LIVE not set"
            )
            return

        models_raw = await client.fetch_models()

        endpoint_map = {
            "anthropic_chat": "anthropic",
            "anthropic_adaptive": "anthropic",
            "openai_chat": "openai",
            "google_chat": "google",
            "groq_chat": "groq",
            "together_chat": "together",
            "xai_chat": "xai",
            "cerebras_chat": "cerebras",
        }
        # The 2026-07 aidream catalog reshape (aidream c7cfe4349) dropped the
        # top-level `endpoints` column; api_class is now the routing field.
        # Legacy endpoints survive under metadata.legacy.endpoints. Map both.
        api_class_prefix_map = {
            "anthropic": "anthropic",
            "openai": "openai",
            "google": "google",
            "gemini": "google",
            "groq": "groq",
            "together": "together",
            "xai": "xai",
            "grok": "xai",
            "cerebras": "cerebras",
        }

        models_to_save: list[dict[str, Any]] = []
        for row in models_raw:
            if row.get("is_deprecated"):
                continue
            metadata = row.get("metadata") or {}
            legacy = metadata.get("legacy") if isinstance(metadata, dict) else {}
            legacy = legacy if isinstance(legacy, dict) else {}

            endpoints: list[str] = row.get("endpoints") or legacy.get("endpoints") or []
            if isinstance(endpoints, str):
                try:
                    endpoints = json.loads(endpoints)
                except Exception:
                    endpoints = []
            api_class = str(row.get("api_class") or "")

            provider = None
            for ep in endpoints:
                p = endpoint_map.get(ep)
                if p:
                    provider = p
                    break
            if not provider and api_class:
                for prefix, p in api_class_prefix_map.items():
                    if api_class.startswith(prefix):
                        provider = p
                        break
            if not provider:
                # Not a locally-runnable chat provider (image/video/tts/... or
                # unknown vendor) — keep the existing chat-only cache behavior.
                continue
            # Keep the chat-only filter honest under the api_class fallback:
            # only text-out models belong in the chat model list.
            capabilities = row.get("capabilities") or {}
            if isinstance(capabilities, dict):
                output = capabilities.get("output") or []
                if output and "text" not in output:
                    continue

            models_to_save.append({
                "id": row.get("id", ""),
                "name": row.get("name", ""),
                "common_name": row.get("common_name", ""),
                "provider": provider,
                "endpoints": endpoints,
                "capabilities": capabilities,
                "context_window": row.get("context_window"),
                "max_tokens": row.get("max_tokens"),
                "is_primary": bool(row.get("is_primary", False)),
                "is_premium": bool(row.get("is_premium", False)),
                "is_deprecated": False,
                # Routing fields for the matrx-ai SqliteModelCatalog
                # (app/services/ai/model_catalog.py) — ride along in raw_json.
                "api_class": api_class or None,
                "pricing": legacy.get("pricing"),
                "controls": row.get("controls"),
            })

        await self._models_repo.upsert_many(models_to_save)

        keep_ids = {m["id"] for m in models_to_save}
        removed = await self._models_repo.delete_missing(keep_ids)

        data_hash = _hash_list(models_to_save)
        await self._sync_meta.set_last_sync("models", last_hash=data_hash)

        count = await self._models_repo.count()
        logger.info(
            "[sync_engine] Models synced: %d cached (%d removed)", count, removed
        )

    # ------------------------------------------------------------------
    # Agents sync (builtins + user prompts → prompt_builtins, prompts, agents)
    # ------------------------------------------------------------------

    async def sync_agents(self) -> None:
        """Pull the unified agent catalog from AIDream's GET /api/agents.

        /api/agents requires a JWT and returns platform agents (formerly prompt
        builtins, now rows in ``agent.definition``) plus the user's own agents,
        as one list with no ownership field. We therefore treat the whole
        catalog as the ``builtin`` source of the local cache.

        Writes to two tables:
          - prompt_builtins: the canonical catalog records (diagnostics count)
          - agents: merged view consumed by the /chat/agents read route

        If no valid JWT is stored (logged out / expired token), the fetch is
        SKIPPED, the previously cached catalog is KEPT, and the skip is logged
        loudly — never a silent swallow, never a crash.
        """
        client = get_aidream_client()
        if client is None:
            logger.debug("[sync_engine] AIDream client not available — skipping agent sync")
            await self._sync_meta.set_last_sync(
                "agents", status="skipped", error_message="AIDREAM_SERVER_URL_LIVE not set"
            )
            return

        # ── Auth gate — /api/agents has no anonymous variant ───────────
        token_row = await self._token_repo.get()
        jwt: str | None = None
        user_id = ""
        if token_row and not self._token_repo.is_expired(token_row):
            jwt = token_row.get("access_token")
            user_id = token_row.get("user_id", "")

        if not jwt:
            reason = "stored JWT is expired" if token_row else "no stored JWT"
            logger.warning(
                "[sync_engine] Agent sync SKIPPED — %s. /api/agents requires "
                "authentication (no public builtins endpoint anymore). Keeping "
                "the previously cached agent catalog; sign in to refresh it.",
                reason,
            )
            await self._sync_meta.set_last_sync(
                "agents",
                status="skipped",
                error_message=f"/api/agents requires auth — {reason}",
            )
            return

        # ── Fetch the unified catalog (platform + user agents) ─────────
        agents_raw = await client.fetch_agents(jwt)

        catalog_to_save: list[dict[str, Any]] = []
        for a in agents_raw:
            catalog_to_save.append({
                "id": a.get("id", ""),
                "name": a.get("name", ""),
                "description": a.get("description", ""),
                "category": a.get("category", ""),
                "tags": a.get("tags") or [],
                # New endpoint calls the variable list "variables"; the old
                # builtins endpoint called it "variable_defaults".
                "variable_defaults": a.get("variables") or a.get("variable_defaults") or [],
                "settings": _extract_settings(a),
                "is_active": True,
            })

        await self._builtins_repo.upsert_many(catalog_to_save)
        catalog_keep = {a["id"] for a in catalog_to_save}
        await self._builtins_repo.delete_missing(catalog_keep)

        # ── Populate the merged agents table (read by /chat/agents) ────
        catalog_agents: list[dict[str, Any]] = []
        for a in catalog_to_save:
            catalog_agents.append({
                "id": a["id"],
                "name": a["name"],
                "description": a["description"],
                "source": "builtin",
                "user_id": "",
                "category": a.get("category", ""),
                "tags": a.get("tags") or [],
                "is_favorite": False,
                "variable_defaults": a["variable_defaults"],
                "settings": a["settings"],
                "is_active": True,
            })

        await self._agents_repo.upsert_many(catalog_agents)

        # Guard the empty case: delete_by_source treats an empty keep set as
        # "delete everything for this source". A transient empty payload from
        # the server would otherwise wipe the entire agent list until the next
        # successful sync — keep the cache instead.
        if catalog_keep:
            await self._agents_repo.delete_by_source("builtin", catalog_keep)
            # The unified catalog now includes the user's own agents, so the
            # legacy per-user "user" source is retired. Clear any stale rows
            # left from the old two-endpoint sync so they don't linger.
            await self._agents_repo.delete_by_source("user", set())

        data_hash = _hash_list(catalog_to_save)
        await self._sync_meta.set_last_sync("agents", last_hash=data_hash)

        logger.info(
            "[sync_engine] Agents synced: %d agents in unified catalog",
            len(catalog_to_save),
        )

    # ------------------------------------------------------------------
    # Tools sync
    # ------------------------------------------------------------------

    async def sync_tools(self) -> None:
        """Cache the local tool catalog into SQLite for fast access."""
        from app.tools.catalog import get_catalog

        tools_to_save = []
        for entry in get_catalog():
            tools_to_save.append({
                "id": entry.cloud_name,
                "name": entry.cloud_name,
                "description": entry.description,
                "category": entry.category,
                "tags": list(entry.tags),
                "parameters": entry.input_schema,
                "source": "local",
                "version": entry.version,
            })

        await self._tools_repo.upsert_many(tools_to_save)

        data_hash = _hash_list(tools_to_save)
        await self._sync_meta.set_last_sync("tools", last_hash=data_hash)

        count = await self._tools_repo.count()
        logger.info("[sync_engine] Tools synced: %d cached", count)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """Return sync status for all entity types."""
        all_meta = await self._sync_meta.get_all_sync_status()
        pending = await self._sync_meta.pending_count()
        return {
            "running": self._running,
            "interval_seconds": self._interval,
            "entities": {m["entity_type"]: m for m in all_meta},
            "pending_queue": pending,
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _hash_list(items: list[dict]) -> str:
    raw = json.dumps(items, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _extract_settings(row: dict[str, Any]) -> dict[str, Any]:
    """Extract a normalized settings dict from a raw prompt/builtin row."""
    settings = row.get("settings") or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            settings = {}
    return {
        "model_id": settings.get("model_id") or row.get("model_id"),
        "temperature": settings.get("temperature") or row.get("temperature"),
        "max_tokens": (
            settings.get("max_tokens")
            or settings.get("max_output_tokens")
            or row.get("max_tokens")
        ),
        "stream": settings.get("stream", True),
        "tools": settings.get("tools") or [],
    }


def get_sync_engine() -> SyncEngine:
    global _instance
    if _instance is None:
        _instance = SyncEngine()
    return _instance
