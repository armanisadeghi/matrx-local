"""User API key manager — the host-side key seam for matrx-ai.

Primary path (matrx-ai >= 0.3.0): SqliteKeyResolver
---------------------------------------------------
matrx-ai resolves every provider API key through the host-injected
``api_key_resolver`` (``matrx_ai.configure(api_key_resolver=...)``) BEFORE
falling back to ``os.environ``. ``get_key_resolver()`` returns the callable
we register: a synchronous ``(env_var_name) -> str | None`` lookup against an
in-memory cache of the user's SQLite-stored keys (``ApiKeysRepo``).

The cache is:
  1. warmed once at startup by ``load_user_keys_into_env()``,
  2. updated instantly by ``set_user_key()`` / ``delete_user_key()``.

matrx-ai memoizes provider SDK clients ON THE RESOLVED KEY VALUE, so a key
rotation through the resolver takes effect on the next request — no restart.

DEPRECATED: os.environ mutation shim (remove after 2026-09-01)
--------------------------------------------------------------
For belt-and-suspenders during the 0.3.0 rollout we ALSO keep mutating
``os.environ`` exactly as the pre-0.3.0 code did (``_inject`` / ``_erase``).
Anything still reading provider keys straight from the environment (e.g.
``/chat/ai-status``, third-party SDK auto-discovery) keeps working unchanged.
Once one release has shipped on the resolver path and /chat/ai-status reads
through ``get_cached_user_keys()``, delete ``_inject``/``_erase`` and the
env writes — the resolver is the single source of truth.

Precedence (highest → lowest), enforced by matrx-ai's resolve_api_key():
  1. User-stored key in SQLite (via the resolver / injected env var)
  2. .env / shell environment (Arman's dev keys, or future CI injection)
"""

from __future__ import annotations

import os
from collections.abc import Callable

from app.common.system_logger import get_logger

logger = get_logger()

# Maps provider name → list of env var names that matrx_ai providers read
# (matrx-ai's resolve_api_key() call sites define the canonical names).
# google needs both because google providers resolve GEMINI_API_KEY first and
# GOOGLE_API_KEY second; fastino mirrors matrx-ai's dual lookup
# resolve_api_key("PIONEER_API_KEY", "FASTINO_API_KEY").
PROVIDER_ENV_MAP: dict[str, list[str]] = {
    "openai":     ["OPENAI_API_KEY"],
    "anthropic":  ["ANTHROPIC_API_KEY"],
    "google":     ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "groq":       ["GROQ_API_KEY"],
    "together":   ["TOGETHER_API_KEY"],
    "xai":        ["XAI_API_KEY"],
    "cerebras":   ["CEREBRAS_API_KEY"],
    "elevenlabs": ["ELEVENLABS_API_KEY"],
    "fastino":    ["PIONEER_API_KEY", "FASTINO_API_KEY"],
    # Hugging Face Hub (local GGUF downloads via desktop + any Python hub usage)
    "huggingface": ["HUGGING_FACE_HUB_TOKEN", "HF_TOKEN"],
    # Civitai — custom image model / LoRA downloads (read via
    # app.services.media_gen.paths.read_civitai_key; sent only to civitai.com).
    "civitai": ["CIVITAI_API_KEY", "CIVITAI_API_TOKEN"],
}

# Reverse index: env var name → provider (first provider that declares it wins;
# the map has no cross-provider collisions today).
_ENV_TO_PROVIDER: dict[str, str] = {
    env_var: provider
    for provider, env_vars in PROVIDER_ENV_MAP.items()
    for env_var in env_vars
}

# ---------------------------------------------------------------------------
# In-memory key cache — the synchronous read point for the matrx-ai resolver
# ---------------------------------------------------------------------------
# matrx-ai calls the resolver synchronously per request; SQLite reads are
# async, so we keep a module-level {provider: key} cache maintained by the
# three async entry points below (warm at startup, update on set/delete).

_user_keys: dict[str, str] = {}


def _resolve_env_var(env_var_name: str) -> str | None:
    """The api_key_resolver callable body: env var name → user-stored key."""
    provider = _ENV_TO_PROVIDER.get(env_var_name)
    if provider is None:
        return None
    return _user_keys.get(provider) or None


def get_key_resolver() -> Callable[[str], str | None]:
    """Return the resolver registered via matrx_ai.configure(api_key_resolver=...)."""
    return _resolve_env_var


def get_cached_user_keys() -> dict[str, str]:
    """Snapshot of the in-memory user key cache ({provider: key})."""
    return dict(_user_keys)


# ---------------------------------------------------------------------------
# DEPRECATED env-mutation shim (see module docstring; remove after 2026-09-01)
# ---------------------------------------------------------------------------

def _inject(provider: str, key: str) -> None:
    """DEPRECATED shim: mirror the key into os.environ for legacy readers."""
    for env_var in PROVIDER_ENV_MAP.get(provider, []):
        os.environ[env_var] = key
    logger.debug("[key_manager] Injected key for provider '%s' into os.environ (deprecated shim)", provider)


def _erase(provider: str) -> None:
    """DEPRECATED shim: remove the mirrored env var names for this provider."""
    for env_var in PROVIDER_ENV_MAP.get(provider, []):
        os.environ.pop(env_var, None)
    logger.debug("[key_manager] Removed env vars for provider '%s' (deprecated shim)", provider)


# ---------------------------------------------------------------------------
# Async entry points (startup + settings routes)
# ---------------------------------------------------------------------------

async def load_user_keys_into_env() -> int:
    """Load all user-stored API keys from SQLite into the resolver cache.

    Called once during the async startup phase. Returns the number of
    providers whose keys were loaded. Also mirrors into os.environ via the
    deprecated shim (see module docstring).

    User-stored keys take precedence over .env / shell environment.
    """
    try:
        from app.services.local_db.repositories import ApiKeysRepo
        repo = ApiKeysRepo()
        keys = await repo.get_all()
    except Exception as exc:
        logger.warning("[key_manager] Could not load user API keys from SQLite: %s", exc)
        return 0

    count = 0
    for provider, key in keys.items():
        if key and key.strip():
            _user_keys[provider] = key.strip()
            _inject(provider, key.strip())
            count += 1

    if count:
        logger.info(
            "[key_manager] Loaded %d user-stored API key(s) into resolver cache ✓", count
        )
    else:
        logger.debug("[key_manager] No user-stored API keys found in SQLite")

    return count


async def set_user_key(provider: str, key: str) -> None:
    """Save a new key for one provider to SQLite and activate it immediately.

    Updates the resolver cache (matrx-ai re-keys provider clients on the
    resolved value, so the very next AI request uses the new key) and mirrors
    into os.environ via the deprecated shim.
    """
    from app.services.local_db.repositories import ApiKeysRepo
    repo = ApiKeysRepo()
    await repo.set(provider, key.strip())
    _user_keys[provider] = key.strip()
    _inject(provider, key.strip())
    logger.info("[key_manager] User key saved and activated for provider '%s'", provider)


async def delete_user_key(provider: str) -> None:
    """Remove a stored key from SQLite, the resolver cache, and the env shim.

    Clears the same names `_inject` sets (e.g. HF_TOKEN) so image-gen and hub
    downloads stop using a removed Hugging Face token without requiring a full
    process restart. If you rely on a key only in shell/.env and never saved it
    in the app, deleting in UI does not remove .env — restart would reload it.
    """
    from app.services.local_db.repositories import ApiKeysRepo
    repo = ApiKeysRepo()
    await repo.delete(provider)
    _user_keys.pop(provider, None)
    _erase(provider)
    logger.info("[key_manager] User key deleted and deactivated for provider '%s'", provider)
