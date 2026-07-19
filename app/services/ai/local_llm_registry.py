"""Local LLM registry — bridges the running llama-server with the matrx-ai agent pipeline.

When the Tauri frontend starts a local llama-server it notifies the Python engine via
POST /chat/local-llm/connect with the port and model name.  This module:

  1. Stores the port and model name in module-level state.
  2. Creates a GenericOpenAIChat instance pointed at http://127.0.0.1:{port}/v1.
  3. Registers that instance with matrx-ai's UnifiedAIClient
     (``register_generic_openai_instance``) so requests whose model resolves
     to ``api_class="generic_openai_standard"`` are routed locally.
  4. Registers a synthetic runtime model via
     ``matrx_ai.catalog.register_runtime_model`` so "local/<model_name>" is
     resolvable by name without a DB/catalog lookup. Runtime models are
     checked FIRST in every matrx-ai model lookup — this is the public
     0.3.0 replacement for the old ``AiModelManager._api_cache`` pokes.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

# KNOWN UPSTREAM DEFECT (matrx-ai 0.3.0): a cold import of
# matrx_ai.providers.* triggers the providers ↔ orchestrator circular import.
# Importing the orchestrator first breaks the cycle deterministically. The
# engine does this at startup too (app/services/ai/engine.py) — this copy
# keeps the module safe when imported standalone (tests, scripts). Tracked in
# .matrx/AGENT_TASKS.md — remove when matrx-ai makes the executor import lazy.
import matrx_ai.orchestrator  # noqa: F401  (import-order fix, see above)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_local_llm_port: int | None = None
_local_llm_model: str | None = None


# ---------------------------------------------------------------------------
# Model name helper
# ---------------------------------------------------------------------------

def _local_model_name(model_name: str) -> str:
    """Return the canonical local model name used as the lookup key."""
    if model_name.startswith("local/"):
        return model_name
    return f"local/{model_name}"


def _bare_model_name(model_name: str) -> str:
    """Return the model name without the public ``local/`` prefix."""
    if model_name.startswith("local/"):
        return model_name.removeprefix("local/")
    return model_name


def _probe_llama_server(port: int) -> tuple[bool, str | None]:
    """Return whether the registered llama-server is reachable right now."""
    request = Request(f"http://127.0.0.1:{port}/v1/models", method="GET")
    try:
        with urlopen(request, timeout=1.0) as response:
            if 200 <= response.status < 300:
                return True, None
            return False, f"llama-server returned HTTP {response.status}"
    except URLError as exc:
        return False, str(exc.reason)
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Runtime model registration (matrx_ai.catalog)
# ---------------------------------------------------------------------------

def _register_runtime_model(model_name: str) -> None:
    """Register the synthetic local model in matrx-ai's runtime catalog.

    The entry uses api_class="generic_openai_standard" + the
    "generic_openai_chat" endpoint token so UnifiedAIClient routes it to the
    GenericOpenAIChat dispatch branch. Re-registering the same name replaces
    the previous entry (idempotent per name).
    """
    from matrx_ai.catalog import register_runtime_model

    canonical = _local_model_name(model_name)
    register_runtime_model({
        "id": canonical,
        "name": canonical,
        "api_class": "generic_openai_standard",
        "display_name": f"{model_name} (Local)",
        "provider": "local",
        "is_active": True,
        "endpoints": ["generic_openai_chat"],
    })
    logger.info(
        "[local_llm_registry] Registered runtime model '%s' in matrx-ai catalog ✓",
        canonical,
    )


def _unregister_runtime_model(model_name: str) -> None:
    """Remove the synthetic local model from matrx-ai's runtime catalog."""
    from matrx_ai.catalog import unregister_runtime_model

    canonical = _local_model_name(model_name)
    if unregister_runtime_model(canonical):
        logger.info(
            "[local_llm_registry] Unregistered runtime model '%s' from matrx-ai catalog",
            canonical,
        )


# ---------------------------------------------------------------------------
# GenericOpenAIChat instance management
# ---------------------------------------------------------------------------

def _register_instance_with_unified_client(model_name: str, instance: Any) -> None:
    """Register the GenericOpenAIChat instance with matrx-ai's UnifiedAIClient registry."""
    from matrx_ai.providers.unified_client import register_generic_openai_instance

    canonical = _local_model_name(model_name)
    register_generic_openai_instance(canonical, instance)
    # Also register under "default" so it can be used as a fallback.
    register_generic_openai_instance("default", instance)
    logger.info(
        "[local_llm_registry] Registered GenericOpenAIChat instance for '%s' ✓",
        canonical,
    )


def _unregister_instance_from_unified_client(model_name: str) -> None:
    """Unregister the instance from matrx-ai's UnifiedAIClient registry."""
    from matrx_ai.providers.unified_client import unregister_generic_openai_instance

    canonical = _local_model_name(model_name)
    unregister_generic_openai_instance(canonical)
    unregister_generic_openai_instance("default")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def set_local_llm(port: int, model_name: str) -> bool:
    """Register a running local llama-server for use by the agent pipeline.

    Creates a GenericOpenAIChat instance pointed at http://127.0.0.1:{port}/v1,
    registers a runtime model entry in matrx-ai's catalog, and registers the
    instance with UnifiedAIClient.

    Returns True on success, False on failure (logged with full traceback).
    """
    global _local_llm_port, _local_llm_model

    try:
        from matrx_ai.providers.generic_openai import GenericOpenAIChat

        _local_llm_port = port
        _local_llm_model = model_name

        base_url = f"http://127.0.0.1:{port}"
        instance = GenericOpenAIChat(
            base_url=base_url,
            api_key="none",
            provider_name="local_llama",
        )

        canonical = _local_model_name(model_name)
        _register_runtime_model(model_name)
        _register_instance_with_unified_client(model_name, instance)

        logger.info(
            "[local_llm_registry] Local LLM registered: model='%s', port=%d, base_url=%s/v1 ✓",
            canonical,
            port,
            base_url,
        )
        return True

    except Exception:
        logger.error(
            "[local_llm_registry] Failed to register local LLM (port=%d, model=%s)",
            port,
            model_name,
            exc_info=True,
        )
        _local_llm_port = None
        _local_llm_model = None
        return False


def clear_local_llm() -> None:
    """Deregister the local LLM — called when llama-server stops."""
    global _local_llm_port, _local_llm_model

    if _local_llm_model:
        try:
            _unregister_runtime_model(_local_llm_model)
            _unregister_instance_from_unified_client(_local_llm_model)
        except Exception:
            logger.error(
                "[local_llm_registry] Failed to deregister local LLM '%s' — "
                "a stale routing entry may remain until restart",
                _local_llm_model,
                exc_info=True,
            )
        logger.info(
            "[local_llm_registry] Local LLM deregistered (was: model='%s', port=%s)",
            _local_llm_model,
            _local_llm_port,
        )

    _local_llm_port = None
    _local_llm_model = None


def is_local_llm_available() -> bool:
    """Return True if a local LLM is currently registered for routing.

    Reachability is intentionally not probed here.  A busy or cold
    llama-server can temporarily miss a health deadline while remaining the
    correct routing target.
    """
    return _local_llm_port is not None and _local_llm_model is not None


def get_local_llm_status() -> dict[str, Any]:
    """Return a side-effect-free local-LLM status snapshot.

    A status read must never unregister the desktop-owned llama-server.  The
    process may be cold-loading a model or serving a long inference and fail a
    short ``/v1/models`` probe even though it is healthy.  Explicit lifecycle
    events remain the only authority for clearing the registration.
    """
    registered = _local_llm_port is not None and _local_llm_model is not None
    reachable = False
    error: str | None = None

    if registered and _local_llm_port is not None:
        reachable, error = _probe_llama_server(_local_llm_port)
        if not reachable:
            logger.warning(
                "[local_llm_registry] Registered local LLM is unreachable "
                "right now (port=%s, model=%s): %s; preserving registration",
                _local_llm_port,
                _local_llm_model,
                error or "unknown error",
            )

    return {
        "available": registered and reachable,
        "port": _local_llm_port,
        "model_name": _local_llm_model,
        "canonical_model_name": _local_model_name(_local_llm_model) if _local_llm_model else None,
        "reachable": reachable,
        "error": error,
        # matrx-ai >= 0.3.0 always ships GenericOpenAIChat; kept for API
        # response shape compatibility with the desktop frontend.
        "matrx_ai_support": True,
        "instructions": (
            None
            if registered and reachable
            else (
                "The local model is registered but not responding yet."
                if registered
                else "Start a local model first."
            )
        ),
    }


def resolve_local_llm_model(requested_model: str | None = None) -> dict[str, Any] | None:
    """Resolve an OpenAI-compatible model id to the active llama-server target.

    The public API exposes ``local/<name>`` while llama-server generally expects
    its bare model name. Accept both spellings for SDK ergonomics, but return
    the bare name to use in the proxied request body.
    """
    if _local_llm_port is None or _local_llm_model is None:
        return None

    bare = _bare_model_name(_local_llm_model)
    canonical = _local_model_name(bare)
    if requested_model is not None and requested_model not in {bare, canonical}:
        return None

    return {
        "port": _local_llm_port,
        "base_url": f"http://127.0.0.1:{_local_llm_port}/v1",
        "model_name": bare,
        "canonical_model_name": canonical,
    }
