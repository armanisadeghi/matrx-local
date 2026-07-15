"""Remote Catalogs — DB-backed catalogs for everything the shipped app
treats as data (models, LoRAs, voices, presets, prompts, provider patterns).

One anon-readable Supabase table (`public.catalog_entries`, app='matrx-local',
164 entries / 14 kinds) fetched at startup, cached to disk, with the legacy
compiled in-code lists demoted to the explicit fallback tier.
System-of-record: /Users/armanisadeghi/code/common-docs/remote-catalogs/FEATURE.md.
"""

from app.services.catalogs.models import (
    KNOWN_KINDS,
    CatalogEntry,
    CatalogsError,
    CatalogsFetchError,
    CatalogsValidationError,
    ResolvedCatalogs,
    is_valid_kind,
)
from app.services.catalogs.service import (
    CatalogsService,
    get_catalog,
    get_catalog_entry,
    get_catalogs_service,
)

__all__ = [
    "KNOWN_KINDS",
    "CatalogEntry",
    "CatalogsError",
    "CatalogsFetchError",
    "CatalogsService",
    "CatalogsValidationError",
    "ResolvedCatalogs",
    "get_catalog",
    "get_catalog_entry",
    "get_catalogs_service",
    "is_valid_kind",
]
