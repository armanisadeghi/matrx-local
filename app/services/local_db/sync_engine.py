"""Background sync engine — pulls cloud data into local SQLite.

Architecture (see docs/SYNC_CONTRACT.md — the ratified sync contract)
---------------------------------------------------------------------
The cloud is the durable source of truth; local SQLite (~/.matrx/matrx.db) is
a FIRST-ACCESS REPLICA of it — a pull-only cache that makes reads instant and
offline-proof, never a competing server.  This engine is the ONLY component
allowed to write cloud catalog data into SQLite.  All other components read
from SQLite only (the replica IS the read path).

Sync sources:
  - Supabase RPC public.agx_get_list_full() (the ONE platform agent catalog,
    read as the signed-in user) → agents  [the mirror; ruling D4]
  - AIDream server (/api/ai-models) → ai_models
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
  The agent catalog RPC REQUIRES a JWT — there is no public
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
from app.services.local_db.repositories import (
    ModelsRepo,
    AgentsRepo,
    ToolsRepo,
    SyncMetaRepo,
    PromptsRepo,
    TokenRepo,
)
from app.services.aidream.client import get_aidream_client, AIDreamOfflineError
from app.services.agent_catalog.client import (
    CATALOG_RPC,
    AgentCatalogAuthError,
    AgentCatalogError,
    fetch_agent_catalog,
)

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
        """Run a full sync on startup, then periodically.

        This loop NEVER raises — individual sync failures are logged and
        retried on the next cycle.  The loop only exits when stopped.
        """
        try:
            await self.sync_all()
        except Exception:
            logger.error("[sync_engine] Initial sync failed", exc_info=True)

        while self._running:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            if not self._running:
                break
            try:
                await self.sync_all()
            except Exception:
                logger.error("[sync_engine] Periodic sync failed", exc_info=True)

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
                "models",
                status="skipped",
                error_message="AIDream server URL unavailable from app config",
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
                # DB-authored param shaping (aidream GET /api/ai-models →
                # AiCatalogManager.export_model_routing): matrx-ai's
                # build_catalog_call_profile consumes control_rules directly,
                # so this host shapes provider params exactly like the server
                # (adaptive vs budget Anthropic thinking is a per-model fact).
                "wire_format": row.get("wire_format"),
                "control_rules": row.get("control_rules"),
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
    # Agents sync (the platform catalog RPC → the `agents` mirror)
    # ------------------------------------------------------------------

    async def sync_agents(self) -> None:
        """Mirror the platform agent catalog into SQLite.

        THE SOURCE IS `public.agx_get_list_full()` — the same Supabase RPC
        matrx-frontend, matrx-extend and workflow-studio read. Ruling D4
        (Arman, 2026-09-08): matrx-local is never an exception; "offline" is a
        data LOCATION, never a different list, structure or format. The rows
        land in `agents` byte-identically, in the order the database returned
        them.

        Historically this method read the aidream route ``GET /agents``, whose
        membership is builtins + agents the caller created — every SHARED and
        ORG-SHARED agent was missing, and only 7 of the 19 catalog columns
        arrived. That was a second catalog, and it is gone.

        Per-agent execution detail (variables/settings) is NOT synced here and
        never was membership: it is read ONE agent at a time, lazily, from
        `public.agx_get_execution_full(p_agent_id)` when
        `GET /agents/catalog/{agent_id}/execution` is asked — 468 agents are
        not 468 RPC calls at startup.

        Failure posture (nothing silent):
          - no/expired JWT   -> loud skip, mirror kept, sync_meta `skipped`
          - RPC auth refusal -> loud error, mirror kept, sync_meta `error`
          - network failure  -> loud error, mirror kept, sync_meta `offline`
          - empty result     -> mirror kept, sync_meta `error` (an empty
                                catalog is indistinguishable from lost access)
        """
        # ── Auth gate — the catalog RPC has no anonymous variant ───────
        token_row = await self._token_repo.get()
        jwt: str | None = None
        user_id = ""
        if token_row and not self._token_repo.is_expired(token_row):
            jwt = token_row.get("access_token")
            user_id = token_row.get("user_id", "") or ""

        if not jwt:
            reason = "stored JWT is expired" if token_row else "no stored JWT"
            logger.warning(
                "[sync_engine] Agent catalog sync SKIPPED — %s. %s() is called as "
                "the signed-in user (RLS decides membership), so there is no "
                "anonymous refresh. Keeping the previously mirrored catalog; "
                "sign in to refresh it.",
                reason,
                CATALOG_RPC,
            )
            await self._sync_meta.set_last_sync(
                "agents",
                status="skipped",
                error_message=f"{CATALOG_RPC} requires authentication — {reason}",
            )
            return

        # ── The catalog: the RPC, verbatim ─────────────────────────────
        try:
            rows = await fetch_agent_catalog(jwt)
        except AgentCatalogAuthError as exc:
            logger.error(
                "[sync_engine] Agent catalog sync FAILED — the stored JWT was "
                "rejected by %s. Keeping the previously mirrored catalog rather "
                "than serving a membership this user may no longer have; sign in "
                "again to refresh. (%s)",
                CATALOG_RPC,
                exc,
            )
            await self._sync_meta.set_last_sync(
                "agents", status="error", error_message=str(exc)
            )
            return
        except AgentCatalogError as exc:
            logger.error(
                "[sync_engine] Agent catalog sync FAILED (%s) — keeping the "
                "previously mirrored catalog. It may be stale.",
                exc,
            )
            await self._sync_meta.set_last_sync(
                "agents", status="offline", error_message=str(exc)
            )
            return

        if not rows:
            logger.error(
                "[sync_engine] %s returned ZERO rows — every user sees at least "
                "the active builtins, so this is a failure, not an empty catalog. "
                "Keeping the previously mirrored rows.",
                CATALOG_RPC,
            )
            await self._sync_meta.set_last_sync(
                "agents",
                status="error",
                error_message=f"{CATALOG_RPC} returned zero rows — mirror kept",
            )
            return

        mirrored = await self._agents_repo.replace_catalog(rows, user_id=user_id)

        await self._sync_meta.set_last_sync("agents", last_hash=_hash_list(rows))

        shared = sum(
            1
            for r in rows
            if r.get("access_level") not in ("owner", "system") and not r.get("is_owner")
        )
        logger.info(
            "[sync_engine] Agent catalog mirrored from %s: %d row(s) "
            "(%d shared/org-shared)",
            CATALOG_RPC,
            mirrored,
            shared,
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


def get_sync_engine() -> SyncEngine:
    global _instance
    if _instance is None:
        _instance = SyncEngine()
    return _instance
