"""SQLite-backed ModelCatalog — matrx-ai's model lookup + routing seam.

Implements the ``matrx_ai.catalog.ModelCatalog`` Protocol (``async
list_models()`` / ``async get_model(id_or_name)``) on top of the local
``ai_models`` SQLite cache, which the SyncEngine populates from aidream's
``GET /api/ai-models`` (docs/SYNC_CONTRACT.md: cloud is the durable source of
truth; SQLite is the working replica).

Registered once at startup via ``matrx_ai.configure(model_catalog=...)``
(app/services/ai/engine.py). After that, EVERY model lookup and call-routing
decision in matrx-ai resolves through this catalog — the ORM model manager is
never constructed in this process.

wire_format enrichment
----------------------
The 2026-07 aidream catalog reshape (aidream c7cfe4349) moved ``endpoints``
and ``provider`` off the top-level model row into ``metadata.legacy``; the
served rows carry ``api_class`` but no ``wire_format``. matrx-ai routes a
catalog model by (1) explicit ``wire_format``, (2) a known ``endpoints``
entry, (3) LOUD derivation from ``api_class`` — and RAISES when none resolve.

We enrich host-side where the mapping is certain: a legacy endpoint token
that IS a UnifiedAIClient dispatch attr (``anthropic_chat``, ``openai_chat``,
…) becomes the explicit ``wire_format``. Anything else is deliberately left
to matrx-ai's api_class derivation so an unroutable model fails loudly
instead of being guessed at here.

Runtime models (the local llama-server) are matrx-ai's business:
``local_llm_registry`` registers them via
``matrx_ai.catalog.register_runtime_model`` and they take precedence over
this catalog in every lookup — this module never sees them.
"""

from __future__ import annotations

from typing import Any

from app.common.system_logger import get_logger

logger = get_logger()

_CATALOG_INSTANCE: "SqliteModelCatalog | None" = None


def get_model_catalog() -> "SqliteModelCatalog":
    """Return the singleton catalog, creating it lazily."""
    global _CATALOG_INSTANCE
    if _CATALOG_INSTANCE is None:
        _CATALOG_INSTANCE = SqliteModelCatalog()
    return _CATALOG_INSTANCE


def _known_wire_formats() -> frozenset[str]:
    # Local import: matrx_ai.catalog pulls the provider stack; keep this
    # module cheap to import (it is imported during engine configure()).
    from matrx_ai.catalog import WIRE_FORMATS

    return WIRE_FORMATS


def enrich_model_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one ai_models SQLite row into the matrx-ai catalog shape.

    The row is the SyncEngine's cached dict (see sync_engine.sync_models):
    top-level ``id``/``name``/``provider``/``endpoints``/``capabilities``
    plus ``raw_json`` carrying everything the sync stored (api_class,
    pricing, ...). Enrichment adds an explicit ``wire_format`` when a legacy
    endpoint token is a known UnifiedAIClient dispatch attr.
    """
    raw = row.get("raw_json") or {}
    model: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    # Top-level cached columns win over raw_json (they are the deserialized,
    # type-correct values).
    for key in ("id", "name", "common_name", "provider", "endpoints", "capabilities"):
        value = row.get(key)
        if value not in (None, "", []):
            model[key] = value
    if row.get("context_window") is not None:
        model.setdefault("context_window", row["context_window"])
    if row.get("max_tokens") is not None:
        model.setdefault("max_tokens", row["max_tokens"])

    if not model.get("wire_format"):
        endpoints = model.get("endpoints") or []
        if isinstance(endpoints, (list, tuple)):
            known = _known_wire_formats()
            for candidate in endpoints:
                if isinstance(candidate, str) and candidate in known:
                    model["wire_format"] = candidate
                    break
    return model


class SqliteModelCatalog:
    """matrx_ai.catalog.ModelCatalog over the local ai_models SQLite cache."""

    async def list_models(self) -> list[dict[str, Any]]:
        """Every cached, non-deprecated model in catalog shape."""
        from app.services.local_db.repositories import ModelsRepo

        rows = await ModelsRepo().list_all(include_deprecated=False)
        return [enrich_model_dict(r) for r in rows]

    async def get_model(self, id_or_name: str) -> dict[str, Any] | None:
        """The model whose id OR name matches, or None."""
        from app.services.local_db.repositories import ModelsRepo

        repo = ModelsRepo()
        key = str(id_or_name)
        row = await repo.get(key)
        if row is None:
            row = await repo.get_by_name(key)
        if row is None:
            logger.warning(
                "[model_catalog] Model %r not found in the local ai_models cache "
                "(cache empty or never synced? check /chat/sync/status)",
                key,
            )
            return None
        return enrich_model_dict(row)
