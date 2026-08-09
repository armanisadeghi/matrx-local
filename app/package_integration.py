"""app/package_integration.py — one-time host wiring for the `matrx_*` packages.

Every `matrx_*` package (matrx-utils, matrx-files, matrx-scraper, matrx-ai,
matrx-orm, …) resolves its host-specific values through the SHARED process-global
`matrx_utils.conf.settings`. That object starts out unconfigured and raises
``NotConfiguredError`` on the first attribute read, so an unconfigured host does
not fail at import or at boot — it fails deep inside a feature, at first use.

That is exactly how this was discovered (2026-08-09): `matrx_scraper`'s
`DomainFilter` builds a `matrx_files.FileManager("scraper")` to cache the
EasyList adblock rules, `FileHandler.__init__` reads ``settings.BASE_DIR``, and
the engine blew up on the FIRST HTML PARSE. The fix is not "configure it inside
the scraper" — `configure_settings()` is process-global and refuses a second
call, so it belongs in ONE explicit startup phase ahead of every consumer
(mirrors `aidream/package_integration.py::configure_packages`).

ISOLATION (CLAUDE.md Hard Rule 9 / defect MXL-D-043 — NON-NEGOTIABLE):
``BASE_DIR`` derives from ``app.config.MATRX_HOME_DIR``, never a hardcoded
``~/.matrx``. A dev engine writes its package scratch into ``~/.matrx-dev`` (or
the ``--fresh`` temp home) and only a packaged run writes into ``~/.matrx``.

We deliberately configure with ``env_first=False``: our derived values win, and
`settings` still falls back to the environment for any name we do not define.
``BASE_DIR`` is config, not an env var — a stray ``BASE_DIR`` in the environment
must never be able to pull the dev engine's package scratch into the live world.
"""

from __future__ import annotations

from typing import Any

from app.common.system_logger import get_logger

# The unified engine log — a plain `logging.getLogger(__name__)` here would emit
# INFO into the root logger, which is pinned at WARNING, so the confirmation
# line would never reach system.log or the Activity tab.
logger = get_logger()

_SERVICE = "matrx_settings"


class MatrxLocalSettings:
    """The settings object handed to `matrx_utils.conf`.

    Only names an installed `matrx_*` package actually reads AND matrx-local can
    honestly answer belong here. Audited 2026-08-09 across matrx-ai,
    matrx-connect, matrx-files, matrx-graph, matrx-orm, matrx-runtime,
    matrx-scraper and matrx-utils:

      * ``BASE_DIR``   — matrx-files `FileHandler` (the scraper adblock cache,
                         the Office handlers), matrx-orm codegen writer,
                         matrx-utils dir-structure/dataclass helpers.
      * ``TEMP_DIR``   — matrx-orm sql-utils + matrx-utils profiler scratch.
                         Kept coherent with ``BASE_DIR`` (packages assume
                         ``<base>/temp``) rather than pointing at the app's own
                         platform cache dir.
      * ``LOG_VCPRINT`` — matrx-utils `MatrixPrintLog`. True so package-level
                         errors reach the unified log instead of only stdout.

    Deliberately NOT supplied (and they must stay that way):
      * ``DEFAULT_DB_PROJECT`` / ``ADMIN_PYTHON_ROOT`` / ``ADMIN_TS_ROOT`` —
        matrx-orm codegen + Postgres adapter. This engine runs no ORM codegen
        and has no server database project; inventing values would make a
        misconfiguration silent instead of loud.
      * ``SUPABASE_SECRET_KEY`` (matrx-files cloud_sync) — a service-role
        secret. It cannot exist on a machine the user owns (CLAUDE.md
        § Security posture, rule 4). File sync here goes through
        `app/services/file_sync` with the user's own JWT.
    """

    def __init__(self, base_dir: Any) -> None:
        from pathlib import Path

        base = Path(base_dir)
        self.BASE_DIR = str(base)
        self.TEMP_DIR = base / "temp"
        self.LOG_VCPRINT = True


def configure_matrx_packages() -> MatrxLocalSettings:
    """Configure `matrx_utils.conf.settings` exactly once, at startup.

    Raises on failure — the caller (app/main.py lifespan) reports it to the
    launcher registry. Never swallow: an unconfigured `settings` means every
    matrx_* consumer fails later, somewhere else, for a reason that looks
    nothing like this.
    """
    from matrx_utils.conf import configure_settings

    from app.config import MATRX_HOME_DIR

    host_settings = MatrxLocalSettings(MATRX_HOME_DIR)
    configure_settings(host_settings, env_first=False)
    logger.info(
        "[app/package_integration.py] matrx_* settings configured "
        "(BASE_DIR=%s, TEMP_DIR=%s)",
        host_settings.BASE_DIR,
        host_settings.TEMP_DIR,
    )
    return host_settings
