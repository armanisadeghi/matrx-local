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

THE RESOLUTION ORDER (the one place it is decided)
--------------------------------------------------
When any part of the app needs a provider API key, exactly this order applies:

  1. **Local key store** (``ApiKeysRepo`` → ``_user_keys``) — the user's own
     key saved on THIS machine. Offline-first: it works with no network and
     no session, and its behaviour is unchanged by anything below.
  2. **Platform Credential Vault** (``_vault_keys``) — a key the same user
     saved once anywhere (web app, extension, here) and that aidream released
     to this signed-in session. Fills gaps only; it can never shadow a local
     key.
  3. **.env / shell environment** — developer-only, per CLAUDE.md
     § Security & configuration posture rule 1. matrx-ai falls back to
     ``os.environ`` when this resolver returns None.

Tiers 1 and 2 are ADDITIVE, never a replacement: the local store remains the
canonical offline path, and a Vault-sourced key is always identifiable
(``get_vault_key_origins()``) so the UI can say where a value came from.
The Vault tier is unavailable — a STATE, not an error — when nobody is
signed in or aidream is unreachable; the app behaves exactly as it did
before it existed.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.common.system_logger import get_logger
from app.services.ai.provider_grants import (
    ENV_VAR_TO_PROVIDER,
    PROVIDER_GRANTS,
    VALID_PROVIDERS,
)

if TYPE_CHECKING:
    from app.services.credential_vault.provider_keys import VaultProviderSnapshot

logger = get_logger()

# Maps provider name → list of env var names that matrx_ai providers read
# (matrx-ai's resolve_api_key() call sites define the canonical names).
# google needs both because google providers resolve GEMINI_API_KEY first and
# GOOGLE_API_KEY second; fastino mirrors matrx-ai's dual lookup
# resolve_api_key("PIONEER_API_KEY", "FASTINO_API_KEY").
PROVIDER_ENV_MAP: dict[str, list[str]] = {
    key: list(spec.env_vars) for key, spec in PROVIDER_GRANTS.items()
}

# Reverse index: env var name → provider. Derived once in provider_grants so
# the credential-vault consumer matches a vault field's env_key alias with the
# exact same map (see provider_grants.ENV_VAR_TO_PROVIDER).
_ENV_TO_PROVIDER: dict[str, str] = ENV_VAR_TO_PROVIDER

# ---------------------------------------------------------------------------
# In-memory key cache — the synchronous read point for the matrx-ai resolver
# ---------------------------------------------------------------------------
# matrx-ai calls the resolver synchronously per request; SQLite reads are
# async, so we keep a module-level {provider: key} cache maintained by the
# three async entry points below (warm at startup, update on set/delete).

_user_keys: dict[str, str] = {}
_user_keys_loaded = False
_user_keys_load_lock = asyncio.Lock()

# Tier 2 — values released by the platform Credential Vault for the signed-in
# user, keyed the same way. Populated only by refresh_vault_keys(); always
# read AFTER _user_keys so a local key can never be shadowed.
_vault_keys: dict[str, str] = {}
_vault_origins: dict[str, str] = {}  # provider → vault item display name
_vault_refresh_lock = asyncio.Lock()


def _effective_keys() -> dict[str, str]:
    """Local over Vault — THE resolution order, applied in one place."""
    return {**_vault_keys, **_user_keys}


def _resolve_env_var(env_var_name: str) -> str | None:
    """The api_key_resolver callable body: env var name → user's key.

    Local store first, Credential Vault second (see module docstring). Returns
    None when neither tier has one, which is what makes matrx-ai fall through
    to ``os.environ``.
    """
    provider = _ENV_TO_PROVIDER.get(env_var_name.upper())
    if provider is None:
        return None
    return _user_keys.get(provider) or _vault_keys.get(provider) or None


def get_key_resolver() -> Callable[[str], str | None]:
    """Return the resolver registered via matrx_ai.configure(api_key_resolver=...)."""
    return _resolve_env_var


def get_cached_user_keys() -> dict[str, str]:
    """Snapshot of the EFFECTIVE key cache ({provider: key}).

    Local keys over Vault-supplied ones — the same order the matrx-ai resolver
    applies, so every consumer (scraper, media-gen, key validation, chat
    preflight) sees exactly the key a request would actually use.

    Async entry points that can run while the app is connecting must use
    ``get_user_keys_when_ready()``. Until loading completes, an empty cache is
    not evidence that the user has no configured keys.
    """
    return _effective_keys()


def get_local_user_keys() -> dict[str, str]:
    """Only the keys stored on THIS machine (tier 1) — no Vault values.

    For callers that must distinguish "saved here" from "available via the
    signed-in session", such as the API-keys settings surface.
    """
    return dict(_user_keys)


def get_vault_keys() -> dict[str, str]:
    """Only the Vault-supplied keys (tier 2), including ones a local key hides."""
    return dict(_vault_keys)


def get_vault_key_origins() -> dict[str, str]:
    """{provider: vault item display name} for every Vault-supplied key.

    Lets the UI name where a value came from instead of showing an
    unattributed key.
    """
    return dict(_vault_origins)


def user_keys_loaded() -> bool:
    """Whether SQLite has populated the canonical runtime key snapshot."""
    return _user_keys_loaded


async def get_user_keys_when_ready() -> dict[str, str]:
    """Return the authoritative effective snapshot, loading SQLite first."""
    if not _user_keys_loaded:
        await load_user_keys_into_env()
    return _effective_keys()


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
    global _user_keys_loaded

    async with _user_keys_load_lock:
        if _user_keys_loaded:
            return len(_user_keys)

        from app.services.local_db.repositories import ApiKeysRepo

        try:
            keys = await ApiKeysRepo().get_all()
        except Exception as exc:
            # Storage failure is not an authoritative empty snapshot. Leave the
            # cache unready so need-time callers retry instead of warning that
            # every provider key is missing.
            logger.warning(
                "[key_manager] Could not load user API keys from SQLite: %s", exc
            )
            raise

        snapshot = {
            provider: key.strip()
            for provider, key in keys.items()
            if key and key.strip()
        }
        # Publish the complete snapshot without an await between providers.
        # Consumers can observe not-ready or fully ready, never partial state.
        _user_keys.clear()
        _user_keys.update(snapshot)
        for provider, key in snapshot.items():
            _inject(provider, key)
        _user_keys_loaded = True

        if snapshot:
            logger.info(
                "[key_manager] Loaded %d user-stored API key(s) into resolver cache ✓",
                len(snapshot),
            )
        else:
            logger.debug("[key_manager] No user-stored API keys found in SQLite")

        return len(snapshot)


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
    # Tier 2 takes over if the Vault has this provider: removing the local copy
    # must not also drop a key the signed-in session still legitimately
    # supplies, or the env shim and the resolver would disagree.
    vault_key = _vault_keys.get(provider)
    if vault_key:
        _inject(provider, vault_key)
        logger.info(
            "[key_manager] Local key deleted for provider '%s'; the Credential "
            "Vault value from '%s' now supplies it",
            provider,
            _vault_origins.get(provider, "your Vault"),
        )
        return
    logger.info("[key_manager] User key deleted and deactivated for provider '%s'", provider)


# ---------------------------------------------------------------------------
# Tier 2 — the platform Credential Vault
# ---------------------------------------------------------------------------


async def refresh_vault_keys(
    *, include_local: bool = False
) -> "VaultProviderSnapshot":
    """Refresh the Vault-supplied key tier for the signed-in user.

    Only providers with NO local key have their plaintext pulled down (pass
    ``include_local=True`` to resolve every candidate) — a secret the desktop
    already owns is never fetched a second time.

    Offline-safe and never raises: an unavailable Vault comes back as a
    snapshot state, and the previous Vault values are DISCARDED rather than
    left stale, so the resolver can never serve a key the session no longer
    has. Local keys are untouched in every case.
    """
    from app.services.credential_vault.provider_keys import fetch_provider_snapshot

    async with _vault_refresh_lock:
        if not _user_keys_loaded:
            await load_user_keys_into_env()
        wanted = None if include_local else set(VALID_PROVIDERS) - set(_user_keys)
        snapshot = await fetch_provider_snapshot(resolve_providers=wanted)

        if not snapshot.ok:
            if _vault_keys:
                logger.info(
                    "[key_manager] Credential Vault unavailable (%s) — dropping %d "
                    "previously supplied key(s); local keys are unaffected",
                    snapshot.state,
                    len(_vault_keys),
                )
            _clear_vault_tier()
            return snapshot

        origins = {
            c.provider: c.item_name
            for c in snapshot.candidates
            if c.provider in snapshot.values
        }
        previous = set(_vault_keys)
        _vault_keys.clear()
        _vault_keys.update(snapshot.values)
        _vault_origins.clear()
        _vault_origins.update(origins)
        for provider in previous - set(_vault_keys):
            if provider not in _user_keys:
                _erase(provider)
        for provider, key in _vault_keys.items():
            if provider not in _user_keys:
                _inject(provider, key)

        if _vault_keys:
            logger.info(
                "[key_manager] Credential Vault supplied %d provider key(s): %s ✓",
                len(_vault_keys),
                ", ".join(sorted(_vault_keys)),
            )
        else:
            logger.debug(
                "[key_manager] Credential Vault reachable; no additional provider "
                "keys to supply"
            )
        return snapshot


def _clear_vault_tier() -> None:
    for provider in list(_vault_keys):
        if provider not in _user_keys:
            _erase(provider)
    _vault_keys.clear()
    _vault_origins.clear()


def clear_vault_keys() -> None:
    """Drop every Vault-supplied key. Called on sign-out.

    Local keys survive — signing out of AI Matrx must not disable the keys the
    user typed into this machine.
    """
    had = len(_vault_keys)
    _clear_vault_tier()
    if had:
        logger.info(
            "[key_manager] Cleared %d Credential Vault key(s) on sign-out", had
        )
