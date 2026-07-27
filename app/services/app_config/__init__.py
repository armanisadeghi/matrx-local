"""Remote App Config — non-secret runtime values for the shipped desktop app.

One anon-readable Supabase row (`public.app_config`, app='matrx-local')
carrying server URLs, feature flags, min supported version, and an operator
notice — fetched at startup, cached to disk, compiled defaults as last resort.
System-of-record: /Users/armanisadeghi/code/common-docs/systems/app-config/FEATURE.md.
"""

from app.services.app_config.models import (
    AppConfigNotice,
    AppConfigRow,
    AppConfigV1,
    ResolvedAppConfig,
)
from app.services.app_config.service import (
    AppConfigService,
    get_aidream_server_url,
    get_app_config,
    get_app_config_service,
    get_flag,
    get_matrx_files_url,
    get_notice,
    get_scraper_server_url,
    get_web_app_origin,
)

__all__ = [
    "AppConfigNotice",
    "AppConfigRow",
    "AppConfigService",
    "AppConfigV1",
    "ResolvedAppConfig",
    "get_aidream_server_url",
    "get_app_config",
    "get_app_config_service",
    "get_flag",
    "get_matrx_files_url",
    "get_notice",
    "get_scraper_server_url",
    "get_web_app_origin",
]
