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
from app.services.paths.manager import all_paths, set_path, reset_path, safe_dir

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

from app.services.local_db.repositories import ApiKeysRepo, VALID_PROVIDERS  # noqa: E402


# Human-readable labels shown in the UI
_PROVIDER_LABELS: dict[str, dict[str, str]] = {
    "openai":    {"label": "OpenAI",    "description": "GPT-4o, GPT-4o Mini, o3, o4-mini"},
    "anthropic": {"label": "Anthropic", "description": "Claude Sonnet, Claude Haiku, Claude Opus"},
    "google":    {"label": "Google",    "description": "Gemini 2.5 Pro, Gemini 2.0 Flash"},
    "groq":      {"label": "Groq",      "description": "Llama 3.3 70B, Mixtral (fast inference)"},
    "together":  {"label": "Together AI","description": "Llama, Qwen, Mistral and 100+ open models"},
    "xai":       {"label": "xAI",       "description": "Grok-2, Grok-3"},
    "cerebras":  {"label": "Cerebras",  "description": "Llama 3.3 70B (wafer-scale inference)"},
    "huggingface": {
        "label": "Hugging Face",
        "description": "Read token for local GGUF downloads (XET / gated repos); sent only to huggingface.co",
    },
    "civitai": {
        "label": "Civitai",
        "description": "API key for downloading custom image models and LoRAs from Civitai; sent only to civitai.com",
    },
}


class ApiKeyStatus(BaseModel):
    provider: str
    label: str
    description: str
    configured: bool


class ApiKeyStatusList(BaseModel):
    providers: list[ApiKeyStatus]


class ApiKeySetRequest(BaseModel):
    key: str = Field(min_length=1)


@router.get("/api-keys", response_model=ApiKeyStatusList)
async def list_api_key_status() -> ApiKeyStatusList:
    """Return configuration status for every AI provider.

    Never returns actual key values — only whether a key is set.
    """
    repo = ApiKeysRepo()
    statuses = []
    for provider in sorted(VALID_PROVIDERS):
        meta = _PROVIDER_LABELS.get(provider, {"label": provider.title(), "description": ""})
        configured = await repo.is_configured(provider)
        statuses.append(ApiKeyStatus(
            provider=provider,
            label=meta["label"],
            description=meta["description"],
            configured=configured,
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
    meta = _PROVIDER_LABELS.get(provider, {"label": provider.title(), "description": ""})
    return ApiKeyStatus(
        provider=provider,
        label=meta["label"],
        description=meta["description"],
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
    meta = _PROVIDER_LABELS.get(provider, {"label": provider.title(), "description": ""})
    return ApiKeyStatus(
        provider=provider,
        label=meta["label"],
        description=meta["description"],
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
