"""Typed models for the remote App Config system.

Schema v1 contract (system-of-record:
/Users/armanisadeghi/code/common-docs/systems/app-config/FEATURE.md): one anon-readable
Supabase row per app carrying non-secret runtime values — server URLs, feature
flags, an operator notice, and a minimum supported app version.

Parsing rules:
  - Tolerant of UNKNOWN keys (``extra="ignore"``) — adding keys to the row is
    free and must never break shipped clients.
  - Strict on KNOWN keys — a bad type or a non-URL value raises
    ``AppConfigValidationError``, which the fetcher treats as a fetch failure
    (loud warning, fall to the next precedence tier). An invalid payload is
    never partially applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    ValidationError,
    field_validator,
)

# Provenance tier of the currently-applied config, in precedence order.
# "env" is RESERVED and never emitted today: env overrides are per-key, not a
# whole-config tier, so the service keeps tier at remote/cache/defaults and
# reports active overrides separately (status_payload()["env_overrides"]).
Tier = Literal["env", "remote", "cache", "defaults"]


class AppConfigError(Exception):
    """Base error for the app_config service."""


class AppConfigValidationError(AppConfigError):
    """A remote/cached payload failed schema validation.

    Treated as a fetch failure by the service: log loudly, fall to the next
    precedence tier, never crash, never partially apply.
    """


class AppConfigFetchError(AppConfigError):
    """Every remote fetch path failed (network, HTTP, or validation)."""


class AppConfigNotice(BaseModel):
    """Operator broadcast shown in the UI (e.g. incident / update notice)."""

    model_config = ConfigDict(extra="ignore")

    level: Literal["info", "warning", "critical"]
    title: str
    body: str
    url: str | None = None


def _validate_service_url(value: str) -> str:
    """Require an http(s) URL; https for anything that isn't loopback.

    Remote config must never repoint a shipped client at a plaintext public
    endpoint. Loopback http is allowed so dev seed rows / local test rows can
    point at a locally-run service.
    """
    v = value.strip().rstrip("/")
    if v.startswith("https://"):
        return v
    if re.match(r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)", v + "/"):
        return v
    raise ValueError(
        f"service URL must be https:// (or http:// loopback for dev): {value!r}"
    )


class AppConfigV1(BaseModel):
    """The ``config`` JSONB payload, schema_version = 1."""

    model_config = ConfigDict(extra="ignore")

    aidream_server_url: str
    matrx_files_url: str
    scraper_server_url: str
    web_app_origin: str = "https://www.aimatrx.com"
    # StrictBool: a remote kill-switch must be a real boolean — "yes"/"1"
    # coercion would mask an operator typo as an applied flag.
    flags: dict[str, StrictBool] = {}
    notice: AppConfigNotice | None = None

    @field_validator(
        "aidream_server_url",
        "matrx_files_url",
        "scraper_server_url",
        "web_app_origin",
    )
    @classmethod
    def _urls_are_https(cls, value: str) -> str:
        return _validate_service_url(value)


class AppConfigRow(BaseModel):
    """One ``public.app_config`` row as returned by PostgREST / aidream."""

    model_config = ConfigDict(extra="ignore")

    app: str
    schema_version: int
    min_supported_app_version: str
    config: AppConfigV1
    updated_at: datetime | None = None


def parse_row(data: Any) -> AppConfigRow:
    """Parse a raw row object into ``AppConfigRow``.

    Raises ``AppConfigValidationError`` on any shape/type problem so callers
    have exactly one exception type meaning "this payload is unusable".
    """
    if not isinstance(data, dict):
        raise AppConfigValidationError(
            f"app_config row must be a JSON object, got {type(data).__name__}"
        )
    try:
        return AppConfigRow.model_validate(data)
    except ValidationError as exc:
        raise AppConfigValidationError(f"app_config row failed validation: {exc}") from exc


# ---------------------------------------------------------------------------
# Semver comparison (no third-party dep — PyInstaller spec churn avoidance)
# ---------------------------------------------------------------------------


def semver_tuple(version: str) -> tuple[int, ...] | None:
    """Parse the leading dotted-numeric part of a version string.

    ``"1.3.112" → (1, 3, 112)``; tolerates ``v`` prefixes and pre-release /
    build suffixes (``1.4.0-rc.1`` → ``(1, 4, 0)``). Returns None when no
    numeric component can be extracted — callers must treat that as
    "cannot gate" and log, never crash.
    """
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", version or "")
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def version_below(app_version: str, min_supported: str) -> bool:
    """True when ``app_version`` is strictly below ``min_supported``.

    Unparseable inputs gate to False (never lock a user out over a malformed
    string) — the caller logs the anomaly.
    """
    a = semver_tuple(app_version)
    b = semver_tuple(min_supported)
    if a is None or b is None:
        return False
    # Pad to equal length so 1.3 vs 1.3.0 compares equal.
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return a < b


@dataclass(frozen=True)
class ResolvedAppConfig:
    """The config the process is actually running on, plus its provenance.

    ``env_overrides`` maps payload keys (``aidream_server_url``, …) to the
    developer-override value when one is active — those win over ``row.config``
    for that key only (precedence tier 1).
    """

    row: AppConfigRow
    tier: Tier
    fetched_at: datetime | None
    env_overrides: dict[str, str]
    update_required: bool
    app_version: str

    def effective_url(self, key: str) -> str:
        override = self.env_overrides.get(key)
        if override:
            return override
        return str(getattr(self.row.config, key))
