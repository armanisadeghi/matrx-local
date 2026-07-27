"""Typed models for the Remote Catalogs consumer.

One generic shape for every catalog kind (system-of-record:
/Users/armanisadeghi/code/common-docs/systems/remote-catalogs/FEATURE.md): rows of
``public.catalog_entries`` for ``app = 'matrx-local'``. The ``payload`` JSONB
is a faithful snake_case mirror of the legacy compiled struct for that kind —
per-kind adaptation back into the legacy dataclasses happens at the consumer
(``app/services/*/models.py`` accessors), never here.

Parsing rules mirror app_config exactly:
  - Tolerant of UNKNOWN keys (``extra="ignore"``) — new columns are free.
  - Strict on KNOWN keys — a bad type raises ``CatalogsValidationError``,
    which the fetcher treats as a fetch failure for that path (loud warning,
    fall to the next precedence tier). An invalid payload is never partially
    applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.common.system_logger import get_logger

from pydantic import BaseModel, ConfigDict, ValidationError

logger = get_logger()

# Provenance tier of the currently-applied catalog set, in precedence order.
Tier = Literal["remote", "cache", "defaults"]

# Every kind slug this app consumes (ratified list + the two extraction
# additions ``tts_model_file`` / ``ner_pii_labels`` — see the cross-repo
# FEATURE.md and the seed extraction REPORT). Accessors and the public
# ``/catalogs/{kind}`` route validate against this set so a typo'd kind
# screams instead of returning a silent empty list.
KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "llm_model",
        "whisper_model",
        "image_gen_model",
        "video_gen_model",
        "lora",
        "workflow_preset",
        "system_prompt",
        "tts_voice",
        "tts_language",
        "tts_model_file",
        "ner_model",
        "ner_pii_labels",
        "wake_word_model",
        "api_key_provider",
    }
)

_KIND_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def is_valid_kind(kind: str) -> bool:
    """True when ``kind`` is a well-formed slug AND one this app knows."""
    return bool(_KIND_SLUG_RE.match(kind)) and kind in KNOWN_KINDS


class CatalogsError(Exception):
    """Base error for the catalogs service."""


class CatalogsValidationError(CatalogsError):
    """A remote/cached payload failed schema validation.

    Treated as a fetch failure by the service: log loudly, fall to the next
    precedence tier, never crash, never partially apply.
    """


class CatalogsFetchError(CatalogsError):
    """Every remote fetch path failed (network, HTTP, or validation)."""


class CatalogEntry(BaseModel):
    """One ``public.catalog_entries`` row (or a compiled-fallback mirror)."""

    model_config = ConfigDict(extra="ignore")

    kind: str
    key: str
    schema_version: int = 1
    payload: dict[str, Any]
    artifact_url: str | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None
    min_app_version: str | None = None
    is_active: bool = True
    sort_order: int = 0
    notes: str | None = None
    updated_at: datetime | None = None


@dataclass
class ParseReport:
    """Per-parse fault-isolation stats, surfaced up to the service status.

    ``skipped`` counts malformed rows dropped by ``parse_entries``;
    ``reasons`` carries one human-readable line per dropped row.
    """

    skipped: int = 0
    reasons: list[str] = field(default_factory=list)


# Sanity floor against systemic corruption: one bad row is that row's
# problem; more than this fraction malformed means the payload itself is
# broken (schema change, half-written response) and must not be applied.
MALFORMED_ROW_FRACTION_LIMIT = 0.2


def parse_entries(data: Any, *, report: ParseReport | None = None) -> list[CatalogEntry]:
    """Parse a raw list of row objects into ``CatalogEntry`` models.

    Per-entry fault isolation: a malformed row is SKIPPED with a loud error
    log (kind/key/reason) and counted on ``report`` — one bad DB row must
    never pin the app to a stale tier across all 14 kinds. The whole payload
    is rejected (``CatalogsValidationError``) only when it is not a list,
    when it is empty / parses to empty, or when more than
    ``MALFORMED_ROW_FRACTION_LIMIT`` of the rows are malformed (systemic
    corruption, not a bad row).
    """
    if not isinstance(data, list):
        raise CatalogsValidationError(
            f"catalog_entries payload must be a JSON array, got {type(data).__name__}"
        )
    if not data:
        raise CatalogsValidationError(
            "catalog_entries payload is EMPTY — the live catalog always has "
            "rows, so zero rows means a broken query/RLS, not a real state"
        )
    entries: list[CatalogEntry] = []
    skipped: list[str] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            reason = f"entry #{i} is not a JSON object (got {type(raw).__name__})"
            skipped.append(reason)
            logger.error("[catalogs] SKIPPING malformed catalog row: %s", reason)
            continue
        try:
            entries.append(CatalogEntry.model_validate(raw))
        except ValidationError as exc:
            reason = (
                f"entry #{i} (kind={raw.get('kind')!r}, key={raw.get('key')!r}) "
                f"failed validation: {exc}"
            )
            skipped.append(reason)
            logger.error("[catalogs] SKIPPING malformed catalog row: %s", reason)
            continue
    if report is not None:
        report.skipped += len(skipped)
        report.reasons.extend(skipped)
    if not entries:
        raise CatalogsValidationError(
            f"catalog_entries payload has NO parseable rows "
            f"({len(skipped)}/{len(data)} malformed)"
        )
    if len(skipped) > MALFORMED_ROW_FRACTION_LIMIT * len(data):
        raise CatalogsValidationError(
            f"{len(skipped)}/{len(data)} catalog rows are malformed — above "
            f"the {MALFORMED_ROW_FRACTION_LIMIT:.0%} systemic-corruption "
            "floor, refusing to apply this payload"
        )
    return entries


@dataclass(frozen=True)
class ResolvedCatalogs:
    """The catalog set the process is running on, plus its provenance."""

    entries: list[CatalogEntry]
    tier: Tier
    fetched_at: datetime | None
    app_version: str
