"""Engine runtime settings — configurable from the desktop UI.

Includes:
- Engine settings (headless_scraping, scrape_delay)
- Wake word engine preference (whisper vs openWakeWord, model, threshold)
- Forbidden URL list
- Storage path overrides (GET /settings/paths, PUT /settings/paths/{name},
  DELETE /settings/paths/{name} to reset to default)
"""

from __future__ import annotations

import re
from typing import Any, List, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.cloud_sync.settings_sync import get_settings_sync
from app.services.local_db.repositories import AppSettingsRepo
from app.services.paths.manager import all_paths, reset_path, set_path
from app.services.action_needed.registry import get_action_needed_registry

router = APIRouter(prefix="/settings", tags=["settings"])


class EngineSettings(BaseModel):
    headless_scraping: bool = True
    scrape_delay: float = 1.0


class ForbiddenUrlsResponse(BaseModel):
    urls: List[str]


class ForbiddenUrlEntry(BaseModel):
    url: str


def _normalize_forbidden_url(raw: str) -> str:
    """Normalise to bare domain/pattern for storage (strips scheme, trailing slash)."""
    url = raw.strip()
    url = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    url = url.rstrip("/")
    return url.lower()


def is_url_forbidden(target_url: str) -> bool:
    """Return True if ``target_url`` matches any forbidden URL pattern."""
    sync = get_settings_sync()
    forbidden: list[str] = sync.get("forbidden_urls", [])
    if not forbidden:
        return False
    target = target_url.lower()
    target_norm = re.sub(r"^https?://", "", target).rstrip("/")
    for pattern in forbidden:
        if pattern.startswith("*"):
            suffix = pattern.lstrip("*").lstrip(".")
            if target_norm == suffix or target_norm.endswith("." + suffix) or ("/" + suffix) in target_norm:
                return True
        elif target_norm == pattern or target_norm.startswith(pattern + "/"):
            return True
    return False


@router.get("", response_model=EngineSettings)
async def get_settings() -> EngineSettings:
    sync = get_settings_sync()
    return EngineSettings(
        headless_scraping=sync.get("headless_scraping", True),
        scrape_delay=sync.get("scrape_delay", 1.0),
    )


@router.put("", response_model=EngineSettings)
async def update_settings(req: EngineSettings) -> EngineSettings:
    sync = get_settings_sync()
    sync.set_many({
        "headless_scraping": req.headless_scraping,
        "scrape_delay": req.scrape_delay,
    })
    return req


# ── Forbidden URL list ────────────────────────────────────────────────────────

@router.get("/forbidden-urls", response_model=ForbiddenUrlsResponse)
async def get_forbidden_urls() -> ForbiddenUrlsResponse:
    sync = get_settings_sync()
    return ForbiddenUrlsResponse(urls=sync.get("forbidden_urls", []))


@router.post("/forbidden-urls", response_model=ForbiddenUrlsResponse)
async def add_forbidden_url(entry: ForbiddenUrlEntry) -> ForbiddenUrlsResponse:
    normalized = _normalize_forbidden_url(entry.url)
    if not normalized:
        raise HTTPException(status_code=422, detail="URL must not be empty")
    sync = get_settings_sync()
    current: list[str] = sync.get("forbidden_urls", [])
    if normalized not in current:
        current = [*current, normalized]
        sync.set("forbidden_urls", current)
    return ForbiddenUrlsResponse(urls=current)


@router.delete("/forbidden-urls/{url:path}", response_model=ForbiddenUrlsResponse)
async def remove_forbidden_url(url: str) -> ForbiddenUrlsResponse:
    normalized = _normalize_forbidden_url(url)
    sync = get_settings_sync()
    current: list[str] = sync.get("forbidden_urls", [])
    updated = [u for u in current if u != normalized]
    sync.set("forbidden_urls", updated)
    return ForbiddenUrlsResponse(urls=updated)


@router.put("/forbidden-urls", response_model=ForbiddenUrlsResponse)
async def replace_forbidden_urls(body: ForbiddenUrlsResponse) -> ForbiddenUrlsResponse:
    normalized = [_normalize_forbidden_url(u) for u in body.urls if u.strip()]
    normalized = list(dict.fromkeys(normalized))  # deduplicate, preserve order
    sync = get_settings_sync()
    sync.set("forbidden_urls", normalized)
    return ForbiddenUrlsResponse(urls=normalized)


# ── Wake word settings ────────────────────────────────────────────────────────

_WW_SETTINGS_KEY = "wake_word"

_WW_DEFAULTS: dict[str, Any] = {
    "engine": "whisper",          # "whisper" | "oww"
    "oww_model": "hey_jarvis",    # OWW model name (when engine == "oww")
    "oww_threshold": 0.5,         # detection confidence threshold
    "custom_keyword": "hey matrix",  # keyword for the whisper engine
}


class WakeWordSettings(BaseModel):
    engine: Literal["whisper", "oww"] = "whisper"
    oww_model: str = "hey_jarvis"
    oww_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    custom_keyword: str = "hey matrix"


@router.get("/wake-word", response_model=WakeWordSettings)
async def get_wake_word_settings() -> WakeWordSettings:
    repo = AppSettingsRepo()
    stored: dict[str, Any] = await repo.get(_WW_SETTINGS_KEY, {})
    merged = {**_WW_DEFAULTS, **stored}
    return WakeWordSettings(**merged)


@router.put("/wake-word", response_model=WakeWordSettings)
async def save_wake_word_settings(req: WakeWordSettings) -> WakeWordSettings:
    repo = AppSettingsRepo()
    await repo.set(_WW_SETTINGS_KEY, req.model_dump())
    return req


# ── Storage paths ─────────────────────────────────────────────────────────────

class PathEntry(BaseModel):
    name: str
    label: str
    current: str
    default: str
    is_custom: bool
    user_visible: bool


class PathUpdateRequest(BaseModel):
    path: str


@router.get("/paths", response_model=list[PathEntry])
async def get_paths() -> list[dict[str, Any]]:
    """Return all storage path configurations with their current resolved values."""
    return all_paths()


@router.put("/paths/{name}", response_model=PathEntry)
async def update_path(name: str, req: PathUpdateRequest) -> dict[str, Any]:
    """Set a custom path for a named storage location.

    The directory is created immediately to validate accessibility.
    If creation fails, a 422 is returned with the error message.
    """
    result = set_path(name, req.path)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "Could not create directory"))
    # Return the updated entry
    entries = all_paths()
    entry = next((e for e in entries if e["name"] == name), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Path '{name}' not found")
    return entry


@router.delete("/paths/{name}", response_model=PathEntry)
async def reset_path_to_default(name: str) -> dict[str, Any]:
    """Reset a path override back to the compiled default."""
    result = reset_path(name)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("error", "Reset failed"))
    entries = all_paths()
    entry = next((e for e in entries if e["name"] == name), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Path '{name}' not found")
    return entry


class PathStats(BaseModel):
    name: str
    file_count: int
    size_bytes: int
    exists: bool


@router.get("/paths/{name}/stats", response_model=PathStats)
async def get_path_stats(name: str) -> dict[str, Any]:
    """Return file count and total size for a storage path."""
    import asyncio
    from pathlib import Path as _Path

    entries = all_paths()
    entry = next((e for e in entries if e["name"] == name), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Path '{name}' not found")

    target = _Path(entry["current"])
    if not target.exists():
        return {"name": name, "file_count": 0, "size_bytes": 0, "exists": False}

    def _count() -> tuple[int, int]:
        count = 0
        total = 0
        try:
            for f in target.rglob("*"):
                if f.is_file():
                    count += 1
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return count, total

    loop = asyncio.get_event_loop()
    file_count, size_bytes = await loop.run_in_executor(None, _count)
    return {"name": name, "file_count": file_count, "size_bytes": size_bytes, "exists": True}


# ── AI provider API keys ───────────────────────────────────────────────────────
#
# Keys are stored (base64-obfuscated) in the local SQLite app_settings blob and
# injected into os.environ at runtime so matrx_ai picks them up on every request.
#
# The GET endpoint never returns actual key values — only whether a key is set.
# This prevents keys from being accidentally logged or sent over the network.

from app.services.local_db.repositories import ApiKeysRepo  # noqa: E402
from app.services.ai.provider_grants import PROVIDER_GRANTS, VALID_PROVIDERS  # noqa: E402


class ApiKeyStatus(BaseModel):
    provider: str
    label: str
    description: str
    # A key saved on THIS machine. Unchanged meaning — `configured` has always
    # been "the local store has one", and the Remove button still keys on it.
    configured: bool
    # Whether this provider's key can be checked against a free auth-only
    # endpoint. False → the UI hides the Test button instead of offering one
    # that can only ever answer "unsupported".
    testable: bool = False
    # Where the key a request would ACTUALLY use comes from — the resolution
    # order in key_manager (local over Vault). `vault` means the user saved it
    # once elsewhere and this signed-in session is supplying it here.
    source: Literal["local", "vault", "none"] = "none"
    # The Vault item's display name when source == "vault" (or when a Vault
    # copy exists behind a local key), so the UI never shows an unattributed
    # value.
    vault_item_name: str | None = None


class ApiKeyStatusList(BaseModel):
    providers: list[ApiKeyStatus]


class ApiKeySetRequest(BaseModel):
    key: str = Field(min_length=1)


@router.get("/api-keys", response_model=ApiKeyStatusList)
async def list_api_key_status() -> ApiKeyStatusList:
    """Return configuration status for every AI provider.

    Never returns actual key values — only whether a key is set and where the
    effective one comes from.
    """
    from app.services.ai.key_manager import get_vault_key_origins, get_vault_keys
    from app.services.ai.key_validation import PROVIDER_SPECS

    repo = ApiKeysRepo()
    vault_keys = get_vault_keys()
    vault_origins = get_vault_key_origins()
    statuses = []
    for provider in sorted(VALID_PROVIDERS):
        meta = PROVIDER_GRANTS[provider]
        configured = await repo.is_configured(provider)
        if configured:
            source: Literal["local", "vault", "none"] = "local"
        elif provider in vault_keys:
            source = "vault"
        else:
            source = "none"
        statuses.append(ApiKeyStatus(
            provider=provider,
            label=meta.label,
            description=meta.description,
            configured=configured,
            testable=provider in PROVIDER_SPECS,
            source=source,
            vault_item_name=vault_origins.get(provider),
        ))
    return ApiKeyStatusList(providers=statuses)


class ApiKeyValueResponse(BaseModel):
    """Plaintext key — only exposed for the Hugging Face bridge (desktop downloads)."""

    key: str


@router.get("/api-keys/huggingface/value", response_model=ApiKeyValueResponse)
async def get_huggingface_token_value() -> ApiKeyValueResponse:
    """Return the stored Hugging Face token for the native download client.

    Same auth as other /settings routes (Bearer). Used by the desktop app to
    pass the token into Rust for GGUF downloads; not shown in the API Keys UI.
    """
    repo = ApiKeysRepo()
    raw = await repo.get("huggingface")
    if not raw or not raw.strip():
        raise HTTPException(status_code=404, detail="Hugging Face token not configured")
    return ApiKeyValueResponse(key=raw.strip())


@router.put("/api-keys/{provider}", response_model=ApiKeyStatus)
async def set_api_key(provider: str, req: ApiKeySetRequest) -> ApiKeyStatus:
    """Store an API key for one provider and inject it into os.environ immediately.

    The next AI request will use the new key without any restart required.
    """
    if provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{provider}'. Valid: {sorted(VALID_PROVIDERS)}",
        )
    from app.services.ai.key_manager import set_user_key
    await set_user_key(provider, req.key)
    meta = PROVIDER_GRANTS[provider]
    return ApiKeyStatus(
        provider=provider,
        label=meta.label,
        description=meta.description,
        configured=True,
    )


@router.delete("/api-keys/{provider}", response_model=ApiKeyStatus)
async def delete_api_key(provider: str) -> ApiKeyStatus:
    """Remove a stored API key for one provider.

    The os.environ entry from .env / shell is NOT removed — only the
    user-saved SQLite entry is deleted.
    """
    if provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{provider}'. Valid: {sorted(VALID_PROVIDERS)}",
        )
    from app.services.ai.key_manager import delete_user_key
    await delete_user_key(provider)
    meta = PROVIDER_GRANTS[provider]
    return ApiKeyStatus(
        provider=provider,
        label=meta.label,
        description=meta.description,
        configured=False,
    )


class BulkApiKeyEntry(BaseModel):
    provider: str
    key: str = Field(min_length=1)


class BulkApiKeyRequest(BaseModel):
    keys: list[BulkApiKeyEntry]


class BulkApiKeyResult(BaseModel):
    saved: list[str]
    skipped: list[str]
    errors: dict[str, str]


@router.post("/api-keys/bulk", response_model=BulkApiKeyResult)
async def set_api_keys_bulk(req: BulkApiKeyRequest) -> BulkApiKeyResult:
    """Save multiple AI provider API keys in one request.

    Invalid providers are returned in `skipped`.  Per-key errors are
    returned in `errors` keyed by provider name.
    """
    from app.services.ai.key_manager import set_user_key
    saved: list[str] = []
    skipped: list[str] = []
    errors: dict[str, str] = {}

    for entry in req.keys:
        provider = entry.provider.strip().lower()
        if provider not in VALID_PROVIDERS:
            skipped.append(entry.provider)
            continue
        try:
            await set_user_key(provider, entry.key.strip())
            saved.append(provider)
        except Exception as exc:  # noqa: BLE001
            errors[provider] = str(exc)

    return BulkApiKeyResult(saved=saved, skipped=skipped, errors=errors)


# ── Key validation ─────────────────────────────────────────────────────────────
#
# "Is this key real?" — answered by calling the provider's own free, auth-only
# endpoint (whoami / model list). No inference, no credits, no cost to the user.
# The verdict is tri-state (valid / invalid / unknown / unsupported): a provider
# outage or a dead network yields `unknown`, never `invalid`, because telling a
# user their good key is bad sends them to rotate a working credential.
#
# These routes ALWAYS return 200 with the verdict in the body. A rejected key is
# a successful *check*, not a failed request — and the desktop engine client
# discards the response body on non-2xx, so a 400 here would reach the UI as an
# unreadable "failed: 400" with the reason stripped off.

class ValidateApiKeyRequest(BaseModel):
    # Optional: validate a key the user has typed but not yet saved. Omitted →
    # validate the key currently stored for this provider.
    key: str | None = None


class ApiKeyValidation(BaseModel):
    provider: str
    verdict: Literal["valid", "invalid", "unknown", "unsupported"]
    message: str
    account: str | None = None
    status_code: int | None = None


@router.post("/api-keys/{provider}/validate", response_model=ApiKeyValidation)
async def validate_api_key(provider: str, req: ValidateApiKeyRequest | None = None) -> ApiKeyValidation:
    """Check one provider's key against its free auth-only endpoint.

    Validates the supplied `key` if given (so a typed-but-unsaved key can be
    tested before committing it), otherwise the stored one.
    """
    if provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{provider}'. Valid: {sorted(VALID_PROVIDERS)}",
        )

    from app.services.ai.key_validation import validate_key, validate_stored_key

    candidate = (req.key or "").strip() if req else ""
    if candidate:
        result = await validate_key(provider, candidate)
    else:
        result = await validate_stored_key(provider)

    # This is a live answer to an explicit user action. It is deliberately not
    # persisted as readiness state: a past response is not proof about the
    # current provider, network, account grant, or credential status.
    if not candidate and result.verdict in {"valid", "unsupported"}:
        await get_action_needed_registry().resolve_matching(
            lambda item: item.action.provider == provider
        )

    return ApiKeyValidation(
        provider=result.provider,
        verdict=result.verdict,
        message=result.message,
        account=result.account,
        status_code=result.status_code,
    )


class ValidateAllResult(BaseModel):
    results: list[ApiKeyValidation]


@router.post("/api-keys/validate-all", response_model=ValidateAllResult)
async def validate_all_api_keys() -> ValidateAllResult:
    """Check every stored key at once, concurrently. Free — bills nothing."""
    from app.services.ai.key_validation import PROVIDER_SPECS, validate_many

    repo = ApiKeysRepo()
    stored = await repo.get_all()
    # Only providers that have a key AND a way to test it.
    testable = {
        provider: key
        for provider, key in stored.items()
        if key and key.strip() and provider in PROVIDER_SPECS
    }

    results = await validate_many(testable)
    for result in results:
        if result.verdict in {"valid", "unsupported"}:
            await get_action_needed_registry().resolve_matching(
                lambda item, provider=result.provider: item.action.provider == provider
            )

    return ValidateAllResult(
        results=[
            ApiKeyValidation(
                provider=r.provider,
                verdict=r.verdict,
                message=r.message,
                account=r.account,
                status_code=r.status_code,
            )
            for r in results
        ]
    )


# ── Credential Vault as a key source ───────────────────────────────────────────
#
# The platform has ONE credential system (users.credential_items +
# users.user_secrets, served by aidream at /api/vault/*). matrx-local is a
# CONSUMER of it: a key the user saved once in the web app, the extension, or
# here is available in all three.
#
# Resolution order (owned by key_manager, documented in its module docstring):
#   1. local ApiKeysRepo  — offline-first, behaviour unchanged
#   2. Credential Vault   — fills gaps only, never shadows a local key
#
# The local store is NOT replaced. These routes only expose the Vault as an
# additional source and let the user copy one value down into the local store.
#
# No session → the Vault source is a STATE with a prompt, never an error
# (CLAUDE.md § Security & configuration posture, rule 3), so every route here
# returns 200 with a `state` discriminator.


class VaultCredentialEntry(BaseModel):
    """One Vault-held credential that maps to a local AI provider. Metadata
    only — a plaintext value is never part of this payload."""

    provider: str
    label: str
    item_id: str
    item_name: str
    field_key: str
    handling: str
    # False for `sealed` values and for `revealable` ones this user cannot
    # reveal: the value can never be released to this client.
    available: bool
    # A local key already exists for this provider, so it wins resolution.
    local_key_present: bool
    # This Vault value is currently supplying the provider's key.
    in_use: bool


class VaultSourceStatus(BaseModel):
    # ready | no_session | unconfigured | offline | denied | error
    state: str
    message: str = ""
    entries: list[VaultCredentialEntry] = Field(default_factory=list)


async def _vault_source_status(*, refresh: bool) -> VaultSourceStatus:
    from app.services.ai.key_manager import (
        get_local_user_keys,
        get_vault_keys,
        refresh_vault_keys,
    )
    from app.services.credential_vault.provider_keys import fetch_provider_snapshot

    if refresh:
        snapshot = await refresh_vault_keys()
    else:
        # Listing is a masked, non-plaintext read — cheap and safe to do on
        # every panel open. Values stay in whatever state the last refresh
        # left them.
        snapshot = await fetch_provider_snapshot(resolve_providers=set())

    if snapshot.state != "ready":
        return VaultSourceStatus(state=snapshot.state, message=snapshot.message)

    local = get_local_user_keys()
    in_use = get_vault_keys()
    entries = [
        VaultCredentialEntry(
            provider=c.provider,
            label=PROVIDER_GRANTS[c.provider].label,
            item_id=c.item_id,
            item_name=c.item_name,
            field_key=c.field_key,
            handling=c.handling,
            available=c.resolvable,
            local_key_present=c.provider in local,
            in_use=c.provider in in_use,
        )
        for c in snapshot.candidates
        if c.provider in PROVIDER_GRANTS
    ]
    return VaultSourceStatus(state="ready", entries=entries)


@router.get("/api-keys/vault", response_model=VaultSourceStatus)
async def get_vault_key_source() -> VaultSourceStatus:
    """Which of the signed-in user's Vault credentials map to AI providers."""
    return await _vault_source_status(refresh=False)


@router.post("/api-keys/vault/refresh", response_model=VaultSourceStatus)
async def refresh_vault_key_source() -> VaultSourceStatus:
    """Re-read the Vault and re-activate the keys it supplies, right now."""
    return await _vault_source_status(refresh=True)


class VaultImportRequest(BaseModel):
    provider: str


class VaultImportResult(BaseModel):
    state: str
    message: str
    provider: str
    configured: bool = False


@router.post("/api-keys/vault/import", response_model=VaultImportResult)
async def import_vault_key(req: VaultImportRequest) -> VaultImportResult:
    """Copy one Vault credential into the local key store.

    Additive on purpose: the Vault tier already supplies the value at runtime,
    but a user who wants the key to keep working with no session and no network
    pulls it down here. After this the provider's source becomes `local`.
    """
    provider = req.provider.strip().lower()
    if provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider '{provider}'. Valid: {sorted(VALID_PROVIDERS)}",
        )

    from app.services.ai.key_manager import set_user_key
    from app.services.credential_vault.client import VaultUnavailable
    from app.services.credential_vault.provider_keys import (
        fetch_provider_snapshot,
        resolve_one,
    )

    snapshot = await fetch_provider_snapshot(resolve_providers=set())
    if snapshot.state != "ready":
        return VaultImportResult(
            state=snapshot.state, message=snapshot.message, provider=provider
        )

    candidate = snapshot.candidate_for(provider)
    if candidate is None:
        return VaultImportResult(
            state="not_found",
            message=f"Your Vault has no credential for {PROVIDER_GRANTS[provider].label}.",
            provider=provider,
        )

    try:
        value = await resolve_one(candidate)
    except VaultUnavailable as exc:
        return VaultImportResult(
            state=exc.state, message=exc.message, provider=provider
        )

    await set_user_key(provider, value)
    await get_action_needed_registry().resolve_matching(
        lambda item, p=provider: item.action.provider == p
    )
    return VaultImportResult(
        state="ready",
        message=f"Saved '{candidate.item_name}' from your Vault to this device.",
        provider=provider,
        configured=True,
    )
