"""Match platform Vault items to matrx-local AI providers.

The desktop knows providers as short slugs (``anthropic``, ``huggingface``,
``civitai``…) declared in ``app.services.ai.provider_grants``. The platform
vault stores the same credentials as items carrying a ``provider_key`` and
fields carrying an ``env_key`` alias. Both sides already use the SAME
vocabulary — ``provider_key='anthropic'``, ``env_key='ANTHROPIC_API_KEY'`` —
so matching is a lookup, never a guess. Nothing here invents a slug.

Two lookup tables, in this order:

1. ``provider_grants.ENV_VAR_TO_PROVIDER`` — the canonical names matrx-ai
   itself resolves. Authoritative.
2. the ``api_key_provider`` Remote Catalog — the SAME alias table the .env
   bulk-import already uses (``BRAVE_SEARCH_API_KEY`` → brave, ``CLAUDE_KEY``
   → anthropic, …). Consulting it here is what makes a key the user stored in
   the vault under a common alias findable, and it keeps ONE alias list for
   both import paths instead of a second hand-maintained one.

What this module does NOT do: decide precedence. The resolution order for a
provider key lives in ``app.services.ai.key_manager`` and only there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.common.system_logger import get_logger
from app.services.ai.provider_grants import ENV_VAR_TO_PROVIDER, VALID_PROVIDERS
from app.services.credential_vault.client import (
    VaultRef,
    VaultState,
    VaultUnavailable,
    list_user_items,
    resolve,
)

logger = get_logger()

# Conventional field keys for a single-secret credential, in preference order.
# Only consulted when neither provider_key nor env_key identified the field.
_CONVENTIONAL_FIELD_KEYS: tuple[str, ...] = (
    "api_key",
    "token",
    "access_token",
    "key",
    "secret",
)


@dataclass(frozen=True)
class VaultProviderCandidate:
    """One vault field that can supply one local provider's key.

    Metadata only — never carries a plaintext value.
    """

    provider: str
    item_id: str
    item_name: str
    field_key: str
    handling: str
    can_reveal: bool

    @property
    def ref(self) -> VaultRef:
        return VaultRef(item_id=self.item_id, field_key=self.field_key)

    @property
    def resolvable(self) -> bool:
        """Whether this client may ask the server for the value at all.

        ``sealed`` never crosses the API boundary, and a ``revealable`` field
        needs reveal capability. Filtering here keeps one un-resolvable ref
        from failing an entire batch resolve.
        """
        if self.handling == "sealed":
            return False
        if self.handling == "revealable":
            return self.can_reveal
        return True


@dataclass(frozen=True)
class VaultProviderSnapshot:
    """The result of one vault fetch — a STATE, never an exception."""

    state: VaultState
    message: str = ""
    candidates: tuple[VaultProviderCandidate, ...] = ()
    values: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.state == "ready"

    def candidate_for(self, provider: str) -> VaultProviderCandidate | None:
        for candidate in self.candidates:
            if candidate.provider == provider:
                return candidate
        return None


def _catalog_alias_index() -> dict[str, str]:
    """env var (UPPER) → provider, from the ``api_key_provider`` catalog.

    Same rows the .env bulk import matches on, so a vault field named
    ``BRAVE_SEARCH_API_KEY`` resolves exactly like a pasted one. Catalog
    unavailable → empty; the canonical index below still works.
    """
    from app.services.catalogs import get_catalog

    index: dict[str, str] = {}
    try:
        entries = get_catalog("api_key_provider")
    except Exception as exc:  # noqa: BLE001 — an alias table is a nicety
        logger.debug("[credential_vault] api_key_provider catalog unavailable: %s", exc)
        return index
    for entry in entries:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        names = payload.get("names")
        provider = names[0] if isinstance(names, list) and names else entry.key
        if provider not in VALID_PROVIDERS:
            continue
        for env_var in payload.get("env_var_names") or []:
            if isinstance(env_var, str):
                index.setdefault(env_var.upper(), provider)
    return index


def _env_index() -> dict[str, str]:
    """Alias table under the canonical one — canonical always wins."""
    return {**_catalog_alias_index(), **ENV_VAR_TO_PROVIDER}


def _env_vars_for(provider: str, index: dict[str, str]) -> set[str]:
    return {env for env, p in index.items() if p == provider}


def _fields_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw = item.get("fields")
    if not isinstance(raw, list):
        return []
    return [
        f
        for f in raw
        if isinstance(f, dict) and f.get("is_active", True) and f.get("field_key")
    ]


def _provider_of(
    item: dict[str, Any], fields: list[dict[str, Any]], index: dict[str, str]
) -> str | None:
    """The local provider slug this item supplies, or None.

    1. ``provider_key`` — the vault's own provider identity (authoritative).
    2. a field's ``env_key`` alias mapped through the env→provider index, so a
       plain ``env_value`` item named ``ANTHROPIC_API_KEY`` (or the aliased
       ``BRAVE_SEARCH_API_KEY``) works with no provider_key at all.
    """
    provider_key = str(item.get("provider_key") or "").strip().lower()
    if provider_key in VALID_PROVIDERS:
        return provider_key
    for f in fields:
        env_key = str(f.get("env_key") or "").strip().upper()
        if env_key and env_key in index:
            return index[env_key]
    return None


def _key_field_of(
    provider: str, fields: list[dict[str, Any]], index: dict[str, str]
) -> dict[str, Any] | None:
    """Which field of an item actually holds this provider's API key.

    Returning the WRONG field is worse than returning none — the vault groups
    several unrelated credentials under one provider slug (a Google Analytics
    service-account JSON and a Gemini API key are both ``provider_key=google``),
    and handing a service-account document to matrx-ai as ``GEMINI_API_KEY``
    would fail in a way nobody could explain. So: match on the provider's own
    env aliases, else a conventional single-secret name, else give up.
    """
    provider_env_vars = _env_vars_for(provider, index)
    for f in fields:
        env_key = str(f.get("env_key") or "").strip().upper()
        if env_key and env_key in provider_env_vars:
            return f
    for name in _CONVENTIONAL_FIELD_KEYS:
        for f in fields:
            if str(f.get("field_key")).strip().lower() == name:
                return f
    # A lone unnamed field is this item's secret — but only when it does not
    # advertise an env alias that belongs to something else entirely.
    if len(fields) == 1:
        env_key = str(fields[0].get("env_key") or "").strip().upper()
        if not env_key or env_key in provider_env_vars:
            return fields[0]
    return None


def build_candidates(items: list[dict[str, Any]]) -> list[VaultProviderCandidate]:
    """Map masked vault items onto provider candidates.

    When several items claim the same provider the LAST-UPDATED one wins, so
    the choice is deterministic and matches what the user most recently
    touched in the web vault.
    """
    index = _env_index()
    best: dict[str, tuple[str, VaultProviderCandidate]] = {}
    for item in items:
        fields = _fields_of(item)
        if not fields:
            continue
        provider = _provider_of(item, fields, index)
        if provider is None:
            continue
        key_field = _key_field_of(provider, fields, index)
        if key_field is None:
            logger.debug(
                "[credential_vault] item %s matches provider '%s' but no single "
                "key field could be identified — skipped",
                item.get("id"),
                provider,
            )
            continue
        caps = item.get("capabilities")
        can_reveal = bool(caps.get("can_reveal")) if isinstance(caps, dict) else False
        candidate = VaultProviderCandidate(
            provider=provider,
            item_id=str(item.get("id") or ""),
            item_name=str(item.get("display_name") or provider),
            field_key=str(key_field.get("field_key")),
            handling=str(key_field.get("handling") or "revealable"),
            can_reveal=can_reveal,
        )
        if not candidate.item_id:
            continue
        updated_at = str(item.get("updated_at") or "")
        current = best.get(provider)
        if current is None or updated_at >= current[0]:
            best[provider] = (updated_at, candidate)
    return [best[provider][1] for provider in sorted(best)]


async def _resolve_values(
    candidates: list[VaultProviderCandidate],
) -> dict[str, str]:
    """Resolve candidate values, one batch call with a per-ref fallback.

    aidream's ``/vault/resolve`` is all-or-nothing: a single ref it declines
    raises for the whole batch. ``resolvable`` already filters the causes we
    can see, but a server-side denial we cannot predict (recent-auth policy,
    a race with a revoked grant) would otherwise blank EVERY provider. On a
    batch failure we retry per ref and scream — a recovery firing means a real
    denial we did not model.
    """
    wanted = [c for c in candidates if c.resolvable]
    if not wanted:
        return {}
    try:
        values = await resolve([c.ref for c in wanted])
    except VaultUnavailable as exc:
        if exc.state in ("no_session", "unconfigured", "offline"):
            raise
        logger.warning(
            "[credential_vault] batch resolve of %d Vault field(s) failed (%s: %s) "
            "— falling back to per-field resolve so one denial cannot blank them all",
            len(wanted),
            exc.state,
            exc.message,
        )
        values = {}
        for candidate in wanted:
            try:
                values.update(await resolve([candidate.ref]))
            except VaultUnavailable as one_exc:
                if one_exc.state in ("no_session", "unconfigured", "offline"):
                    raise
                logger.warning(
                    "[credential_vault] Vault field %s/%s (provider '%s') could not "
                    "be resolved: %s",
                    candidate.item_id,
                    candidate.field_key,
                    candidate.provider,
                    one_exc.message,
                )
    return {
        c.provider: values[c.ref.resolve_key]
        for c in wanted
        if values.get(c.ref.resolve_key, "").strip()
    }


async def fetch_provider_snapshot(
    *, resolve_providers: set[str] | None = None
) -> VaultProviderSnapshot:
    """List the user's vault items and resolve the values that are needed.

    ``resolve_providers`` limits which candidates get their plaintext pulled
    down. The caller passes only the providers it has no local key for, so a
    secret the desktop already owns is never fetched a second time. ``None``
    means resolve every candidate.

    Never raises: unavailability comes back as a state with a message.
    """
    try:
        items = await list_user_items()
    except VaultUnavailable as exc:
        return VaultProviderSnapshot(state=exc.state, message=exc.message)

    candidates = build_candidates(items)
    to_resolve = [
        c
        for c in candidates
        if resolve_providers is None or c.provider in resolve_providers
    ]
    try:
        values = await _resolve_values(to_resolve)
    except VaultUnavailable as exc:
        return VaultProviderSnapshot(
            state=exc.state, message=exc.message, candidates=tuple(candidates)
        )
    return VaultProviderSnapshot(
        state="ready", candidates=tuple(candidates), values=values
    )


async def resolve_one(candidate: VaultProviderCandidate) -> str:
    """Pull ONE candidate's plaintext value (user-initiated import).

    Raises ``VaultUnavailable`` — the caller is a route that renders the state.
    """
    if candidate.handling == "sealed":
        raise VaultUnavailable(
            "denied",
            f"'{candidate.item_name}' holds a sealed value — sealed credentials "
            "are never released to a client.",
        )
    values = await resolve([candidate.ref])
    value = values.get(candidate.ref.resolve_key, "").strip()
    if not value:
        raise VaultUnavailable(
            "denied",
            f"AI Matrx did not release a value for '{candidate.item_name}'.",
        )
    return value
