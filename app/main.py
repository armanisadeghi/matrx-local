import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_routes import router as admin_router
from app.api.catalog_routes import router as catalog_router
from app.api.routes import router as api_router
from app.api.tool_routes import router as tool_router
from app.api.sandbox_routes import router as sandbox_router
from app.api.remote_scraper_routes import router as remote_scraper_router
from app.api.settings_routes import router as settings_router
from app.api.document_routes import router as document_router  # notes — local-first
from app.api.proxy_routes import router as proxy_router
from app.api.cloud_sync_routes import router as cloud_sync_router
from app.api.chat_routes import router as chat_router
from app.api.data_routes import router as data_router
from app.api.permissions_routes import router as permissions_router
from app.api.capabilities_routes import router as capabilities_router
from app.api.auth import AuthMiddleware, auth_router
from app.launcher import get_registry as _get_launcher_registry
from app.api.token_routes import router as token_router
from app.api.fetch_proxy_routes import router as fetch_proxy_router
from app.api.tunnel_routes import router as tunnel_router
from app.api.setup_routes import router as setup_router
from app.api.platform_routes import router as platform_router
from app.api.wake_word_routes import router as wake_word_router
from app.api.model_repo_routes import router as model_repo_router
from app.api.hardware_routes import (
    router as hardware_router,
    run_initial_detection as run_hardware_detection,
)
from app.api.image_gen_routes import router as image_gen_router
from app.api.prompt_matrix_routes import router as prompt_matrix_router
from app.api.video_gen_routes import router as video_gen_router
from app.api.media_library_routes import router as media_library_router
from app.api.media_vault_routes import router as media_vault_router
from app.api.file_sync_routes import router as file_sync_router
from app.api.tts_routes import router as tts_router
from app.api.ner_routes import router as ner_router
from app.api.openai_compat_routes import router as openai_compat_router
from app.api.hf_token_routes import router as hf_token_router
from app.api.scrape_routes import router as scrape_router
from app.api.extension_bridge_routes import router as extension_bridge_router
from app.api.extension_routes import router as extension_router
from app.services.downloads.routes import router as downloads_router
from app.config import (
    ALLOWED_ORIGINS,
    ALLOWED_ORIGIN_REGEX,
    MATRX_HOME_DIR,
)
from app.common.system_logger import get_logger
import app.common.access_log as access_log
from app.common.platform_ctx import refresh_capabilities
from app.services.scraper.engine import get_scraper_engine
from app.services.proxy.server import DEFAULT_PROXY_PORT, get_proxy_server
from app.services.tunnel.manager import get_tunnel_manager
from app.services.cloud_sync.settings_sync import get_settings_sync
from app.services.ai.engine import (
    initialize_matrx_ai,
    load_tools_and_register,
    warm_jwt_cache,
)
from app.services.ai.key_manager import load_user_keys_into_env
from app.services.local_db.database import get_db
from app.services.local_db.sync_engine import get_sync_engine
from app.tools.tools.scheduler import restore_scheduled_tasks
import app.services.scraper.retry_queue as retry_queue
import app.services.scraper.scrape_store as scrape_store
from app.websocket_manager import WebSocketManager

logger = get_logger()
websocket_manager = WebSocketManager()


async def _ensure_playwright_browsers() -> None:
    """Install Playwright browsers if they are not already present.

    Browsers are NOT bundled inside the PyInstaller sidecar binary to avoid
    macOS codesign failures with Chrome's nested framework structure.
    This function auto-installs them to PLAYWRIGHT_BROWSERS_PATH on first
    startup (runs in the background so it does not block the server).
    """
    import os

    # The default path here must match the one set by hooks/runtime_hook.py
    # for the frozen binary. In development the env var is usually unset, so
    # we default to ~/.matrx/playwright-browsers and write it into os.environ
    # so every subsequent Playwright call (including ScraperEngine.start())
    # uses the same path — Playwright reads PLAYWRIGHT_BROWSERS_PATH at
    # import time, so the env var must be set before any playwright import.
    browsers_path = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(MATRX_HOME_DIR / "playwright-browsers"),
    )
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path

    # Quick check: skip install if at least one versioned browser directory exists.
    browser_markers = ("chromium-", "firefox-", "webkit-", "chromium_headless_shell-")
    if os.path.isdir(browsers_path) and any(
        e.startswith(m) for m in browser_markers for e in os.listdir(browsers_path)
    ):
        logger.debug(
            "[app/main.py] Playwright browsers already present at %s", browsers_path
        )
        return

    logger.info(
        "[app/main.py] Playwright browsers not found — installing to %s (this may take a minute)...",
        browsers_path,
    )
    os.makedirs(browsers_path, exist_ok=True)

    # Build the install command.  compute_driver_executable() returns a
    # (node_binary, cli.js) tuple — str()-ing it produces a broken path string.
    # Fall back to `sys.executable -m playwright install` for frozen binaries
    # where the driver directory is not bundled.
    import sys

    try:
        from playwright._impl._driver import compute_driver_executable  # type: ignore[import]

        node_exe, cli_js = compute_driver_executable()
        # Verify the node binary actually exists; if not, use the Python fallback
        if not os.path.isfile(node_exe):
            raise FileNotFoundError(f"Playwright node binary not found: {node_exe}")
        cmd = [node_exe, cli_js, "install", "chromium", "firefox", "webkit"]
    except Exception as exc:
        logger.info(
            "[app/main.py] Playwright driver binary not available (%s) — "
            "falling back to `python -m playwright install`",
            exc,
        )
        cmd = [
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
            "firefox",
            "webkit",
        ]

    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": browsers_path}

    async def _install() -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                logger.info("[app/main.py] Playwright browsers installed successfully")
            else:
                logger.warning(
                    "[app/main.py] Playwright browser install exited %d: %s",
                    proc.returncode,
                    (stdout or b"").decode("utf-8", errors="replace")[:500],
                )
        except Exception:
            logger.warning(
                "[app/main.py] Playwright browser install task failed", exc_info=True
            )

    # Run in background so the server starts immediately; keep a reference to
    # prevent the task from being garbage-collected before it finishes.
    _browser_install_task = asyncio.create_task(_install())
    _browser_install_task.add_done_callback(lambda _: None)  # suppress GC warning


# JWT truncation for verbose request logging (show first/last parts only)
_JWT_HEAD = 20
_JWT_TAIL = 12


def _truncate_jwt(val: str) -> str:
    """Truncate JWT-like strings for logging: first N + ... + last M chars."""
    if len(val) < 60:
        return val
    parts = val.split(".")
    if len(parts) != 3:
        # Check if it's a long string that looks like a token even if not 3 parts
        if len(val) > 100:
            return f"{val[:_JWT_HEAD]}...{val[-_JWT_TAIL:]}"
        return val
    if not all(re.match(r"^[A-Za-z0-9_-]+$", p) for p in parts):
        return val
    return f"{val[:_JWT_HEAD]}...{val[-_JWT_TAIL:]}"


def _sanitize_url(url: str | object) -> str:
    """Hide sensitive query params in URLs for logging.

    Covers ``token`` plus the OAuth credentials that flow through
    /auth/callback (``code``, ``access_token``, ``refresh_token``) — these
    previously landed in the access log in plaintext because only ``token``
    was matched.
    """
    url_str = str(url)

    def _redact(m: "re.Match") -> str:
        val = m.group(2)
        # Always redact — OAuth codes are often short and would slip past the
        # length-gated JWT truncator, ending up in the log verbatim.
        if len(val) <= 12:
            shown = f"{val[:2]}…"
        else:
            shown = f"{val[:_JWT_HEAD]}…{val[-_JWT_TAIL:]}"
        return m.group(1) + shown

    return re.sub(
        r"([?&](?:token|code|access_token|refresh_token)=)([^&]+)",
        _redact,
        url_str,
    )


def _sanitize_body_for_log(obj):
    """Recursively sanitize body for logging: truncate JWTs only."""
    if isinstance(obj, dict):
        return {k: _sanitize_body_for_log(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_body_for_log(v) for v in obj]
    if isinstance(obj, str):
        return _truncate_jwt(obj)
    return obj


def _request_body_for_log(path: str, body):
    """Return a log-safe request body.

    The OpenAI-compatible surface carries user prompts, speech text, embedding
    input, and multipart audio. Those payloads are private by default and can
    be large, so log shape only.
    """
    if path == "/v1" or path.startswith("/v1/"):
        if body is None:
            return None
        if isinstance(body, dict):
            return {
                "_redacted": "openai-compatible request body",
                "keys": sorted(body.keys()),
            }
        return {"_redacted": "openai-compatible request body"}
    return _sanitize_body_for_log(body)


def _format_request_details(request: Request, body=None) -> str:
    """Format request headers and other metadata for detailed error logging."""
    headers = dict(request.headers)
    # Sanitize Authorization header
    if "authorization" in headers:
        auth = headers["authorization"]
        if auth.lower().startswith("bearer "):
            headers["authorization"] = f"Bearer {_truncate_jwt(auth[7:])}"
        else:
            headers["authorization"] = _truncate_jwt(auth)

    # Filter out other sensitive headers if any (already handled standard ones)
    return f"Method: {request.method} | URL: {_sanitize_url(request.url)} | Headers: {headers} | Body: {body}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    import time as _startup_time

    _t0 = _startup_time.monotonic()
    # Service registry: every managed service reports its lifecycle here so
    # GET /admin/status and the diagnostic dump have a single source of truth.
    # See app/launcher.py for the full ownership/propagation contract.
    _registry = _get_launcher_registry()
    logger.info(
        "[app/main.py] ── Matrx Local startup ─────────────────────────────────────"
    )
    logger.info("[app/main.py] CORS allowed origins: %s", ALLOWED_ORIGINS)

    # NOTE: There is intentionally no SUPABASE_JWT_SECRET / HS256 path here.
    # The engine runs on the user's own machine — it has no secure place
    # to store a server-side JWT signing secret. The /extension/* surface
    # uses JWKS for asymmetric tokens (when SUPABASE_URL is set) and
    # falls back to presence-only on loopback for HS256 tokens. See
    # app/api/extension_auth.py for the full posture.

    # Phase 00: Remote app config (server URLs, flags, min version).
    # MUST run before every sync engine / client that consumes a server URL.
    # NEVER blocks startup: the resolved config comes from disk cache or
    # compiled defaults synchronously; the network fetch runs as a background
    # task with a 6h refresh loop. See app/services/app_config/FEATURE.md.
    print("[phase:app-config] Resolving runtime config...", flush=True)
    logger.info("[app/main.py] Phase 00: Resolving remote app config...")
    _registry.starting("app_config")
    try:
        from app.services.app_config import get_app_config_service

        _app_config_service = get_app_config_service()  # sync: cache/defaults
        _boot_resolved = _app_config_service.resolved
        _registry.degraded(
            "app_config",
            reason=f"running on {_boot_resolved.tier} config until remote fetch completes",
            tier=_boot_resolved.tier,
            fetched_at=_boot_resolved.fetched_at.isoformat()
            if _boot_resolved.fetched_at
            else None,
        )
        await _app_config_service.start_background()  # non-blocking fetch + loop
        logger.info(
            "[app/main.py] Phase 00: App config applied (tier=%s) ✓ — background refresh running",
            _boot_resolved.tier,
        )
        print(
            f"[phase:app-config] Config applied ({_boot_resolved.tier})", flush=True
        )
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 00: App config service FAILED — consumers will "
            "read compiled defaults",
            exc_info=True,
        )
        print("[phase:app-config] App config FAILED (compiled defaults)", flush=True)
        _registry.failed("app_config", exc)

    # Phase 00b: Remote catalogs (models, LoRAs, voices, presets, prompts).
    # Sibling of Phase 00 — same never-blocks contract: the resolved catalog
    # set comes from disk cache or the compiled fallback synchronously; the
    # network fetch runs as a background task with a 6h refresh loop.
    # See app/services/catalogs/FEATURE.md.
    print("[phase:catalogs] Resolving remote catalogs...", flush=True)
    logger.info("[app/main.py] Phase 00b: Resolving remote catalogs...")
    _registry.starting("catalogs")
    try:
        from app.services.catalogs import get_catalogs_service

        _catalogs_service = get_catalogs_service()  # sync: cache/compiled
        _boot_catalogs = _catalogs_service.resolved
        _registry.degraded(
            "catalogs",
            reason=f"running on {_boot_catalogs.tier} catalogs until remote fetch completes",
            tier=_boot_catalogs.tier,
            fetched_at=_boot_catalogs.fetched_at.isoformat()
            if _boot_catalogs.fetched_at
            else None,
        )
        await _catalogs_service.start_background()  # non-blocking fetch + loop
        logger.info(
            "[app/main.py] Phase 00b: Catalogs applied (tier=%s, %d entries) ✓ — "
            "background refresh running",
            _boot_catalogs.tier,
            len(_boot_catalogs.entries),
        )
        print(
            f"[phase:catalogs] Catalogs applied ({_boot_catalogs.tier})", flush=True
        )
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 00b: Catalogs service FAILED — consumers will "
            "read the compiled fallback catalogs",
            exc_info=True,
        )
        print("[phase:catalogs] Catalogs FAILED (compiled fallback)", flush=True)
        _registry.failed("catalogs", exc)

    # Phase 0a: Open local SQLite database (offline-first data store).
    # This MUST be the first phase — all data reads come from SQLite.
    # The database lives at ~/.matrx/matrx.db (outside the app folder) so it
    # survives reinstalls and updates.
    print("[phase:database] Opening local database...", flush=True)
    logger.info("[app/main.py] Phase 0a: Opening local database...")
    _registry.starting("database")
    try:
        local_db = get_db()
        await local_db.connect()
        logger.info(
            "[app/main.py] Phase 0a: Local database ready ✓ (%s)", local_db.path
        )
        print("[phase:database] Local database ready", flush=True)
        _registry.ready("database", path=str(local_db.path))
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 0a: Local database FAILED — data endpoints will use fallbacks",
            exc_info=True,
        )
        print("[phase:database] Local database FAILED (fallbacks active)", flush=True)
        _registry.failed("database", exc)

    # Phase 0a (post): Warm the in-memory JWT cache from SQLite so matrx-ai has
    # the user's token available immediately on first authenticated API call.
    try:
        await warm_jwt_cache()
    except Exception:
        logger.warning(
            "[app/main.py] Phase 0a: JWT cache warm failed (non-fatal)", exc_info=True
        )

    # Phase 0a (post2): Load user-stored AI provider API keys from SQLite into
    # os.environ so matrx_ai picks them up on every request.  Runs before
    # initialize_matrx_ai() so the keys are available during AI engine setup —
    # and, CRITICALLY, before the download manager starts: start() resumes
    # incomplete downloads immediately, and a resumed Hugging Face download that
    # beats this line reads a token that isn't there yet and goes out
    # unauthenticated, failing 401 on a gated repo the user has full access to.
    # Do not move the download manager back above this.
    try:
        loaded = await load_user_keys_into_env()
        logger.info(
            "[app/main.py] Phase 0a: Loaded %d user API key(s) into env ✓", loaded
        )
    except Exception:
        logger.warning(
            "[app/main.py] Phase 0a: User API key load failed (non-fatal)",
            exc_info=True,
        )

    # Phase 0a (image-gen): If the user has previously installed image-gen packages
    # via the in-app installer, inject the packages directory into sys.path now so
    # torch/diffusers are importable in this process.  This is a no-op when the
    # frozen binary's runtime_hook.py already handled it, and a no-op when packages
    # are not yet installed.
    try:
        from app.services.image_gen.installer import (
            inject_image_gen_path,
            start_compatibility_migration,
        )

        # Mandatory release migration: the runtime hook withholds the old
        # directory before any app import. Start an automatic upgrade now, but
        # do not delay unrelated engine features when the user is offline.
        # Image generation remains hard-gated until verification activates the
        # fixed packages in this process.
        migration = await start_compatibility_migration()
        if migration:
            logger.info("[app/main.py] Phase 0a: automatic image-gen runtime migration started")

        if inject_image_gen_path():
            logger.info(
                "[app/main.py] Phase 0a: image-gen packages injected into sys.path ✓"
            )
            # CRITICAL: service.py runs _check_deps() at import time (line 52), before
            # this path injection. Reload DEPS_AVAILABLE now that sys.path is correct.
            try:
                from app.services.image_gen import service as _ig_svc

                _ig_svc.DEPS_AVAILABLE, _ig_svc.DEPS_REASON = _ig_svc._check_deps()
                # video_gen shares the same package install and takes the same
                # import-time snapshot — reload it too or /video-gen/status
                # reports packages_installed=false after a fresh boot.
                from app.services.video_gen import service as _vg_svc

                _vg_svc.DEPS_AVAILABLE, _vg_svc.DEPS_REASON = _vg_svc._check_deps()
                logger.info(
                    "[app/main.py] Phase 0a: media-gen service deps reloaded: image=%s video=%s",
                    _ig_svc.DEPS_AVAILABLE,
                    _vg_svc.DEPS_AVAILABLE,
                )
            except Exception:
                logger.warning(
                    "[app/main.py] Phase 0a: media-gen deps reload failed (non-fatal)",
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "[app/main.py] Phase 0a: image-gen path injection failed (non-fatal)",
            exc_info=True,
        )

    # Phase 0a (capabilities): Whisper / other managed optional packages.
    try:
        from app.services.capabilities.installer import inject_all_capability_paths

        injected = inject_all_capability_paths()
        if injected:
            logger.info(
                "[app/main.py] Phase 0a: capability packages injected: %s ✓",
                ", ".join(injected),
            )
    except Exception:
        logger.warning(
            "[app/main.py] Phase 0a: capability path injection failed (non-fatal)",
            exc_info=True,
        )

    # Phase 0a (post3): Start the universal download manager.
    # Must run after the database is connected (Phase 0a) because it reads
    # any incomplete downloads from SQLite on startup for crash recovery — and
    # after BOTH the API keys are loaded (post2) AND the image-gen/capability
    # package paths are injected (blocks above), because start() resumes
    # incomplete downloads the moment it runs. A resumed Hugging Face diffusers
    # download imports huggingface_hub from the injected packages dir; when the
    # manager started before the injection it failed with a false
    # "AI packages are not installed" in the very session whose later log
    # printed image=True video=True. Do not move this above the injections.
    _registry.starting("downloads")
    try:
        from app.services.downloads.manager import get_download_manager

        dl_manager = get_download_manager()
        await dl_manager.start()
        logger.info("[app/main.py] Phase 0a: Download manager started ✓")
        _registry.ready("downloads")
    except Exception as exc:
        logger.warning(
            "[app/main.py] Phase 0a: Download manager failed to start (non-fatal)",
            exc_info=True,
        )
        _registry.degraded("downloads", reason=f"start failed: {exc}")

    # Phase 0b: Ensure Playwright browsers are installed (auto-installs if missing).
    # Browsers are NOT bundled in the PyInstaller binary (bundling causes macOS
    # codesign failures with Chrome's nested framework structure). They are
    # downloaded on first startup to PLAYWRIGHT_BROWSERS_PATH (~/.matrx/playwright-browsers
    # when running as a frozen binary, or the default Playwright cache in development).
    print("[phase:browsers] Checking browser engine...", flush=True)
    logger.info("[app/main.py] Phase 0b: Checking Playwright browsers...")
    try:
        await _ensure_playwright_browsers()
        logger.info("[app/main.py] Phase 0b: Playwright browsers ready ✓")
        print("[phase:browsers] Browser engine ready", flush=True)
    except Exception:
        logger.warning(
            "[app/main.py] Phase 0b: Playwright browser check failed — browser automation may not work",
            exc_info=True,
        )
        print(
            "[phase:browsers] Browser engine check failed (scraping limited)",
            flush=True,
        )

    # Phase 0c: Probe hardware/permission capabilities once at startup.
    # Populates CAPABILITIES in platform_ctx (mic, GPU, screen capture, etc.)
    # so /platform/context returns fully-populated data from the first request.
    logger.info("[app/main.py] Phase 0c: Probing platform capabilities...")
    try:
        await refresh_capabilities()
        logger.info("[app/main.py] Phase 0c: Platform capabilities probed ✓")
    except Exception:
        logger.warning(
            "[app/main.py] Phase 0c: Capability probe failed — some flags will be null",
            exc_info=True,
        )

    # Phase 0d: Full system hardware detection.
    # Runs once at startup; result is cached and served from GET /hardware.
    # Cloud push (to app_instances.system_hardware) is scheduled as a background
    # task inside run_initial_detection() so it does not delay startup.
    logger.info("[app/main.py] Phase 0d: Detecting system hardware...")
    try:
        await run_hardware_detection()
        logger.info("[app/main.py] Phase 0d: Hardware detection complete ✓")
    except Exception:
        logger.warning(
            "[app/main.py] Phase 0d: Hardware detection failed — /hardware will re-probe on first request",
            exc_info=True,
        )

    # Phase 1: Initialize matrx-ai (wires the client-host seams).
    # This MUST run before build_ai_app() is called — the /ai surface prep
    # path reads the seam registry configured here.
    print("[phase:ai] Initializing AI engine...", flush=True)
    logger.info("[app/main.py] Phase 1: Initializing matrx-ai engine...")
    _registry.starting("ai_engine")
    try:
        initialize_matrx_ai()
        logger.info("[app/main.py] Phase 1: matrx-ai initialized ✓")
        print("[phase:ai] AI engine initialized", flush=True)
        _registry.ready("ai_engine")
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 1: matrx-ai initialization FAILED — AI endpoints will not work. "
            "Check SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in .env",
            exc_info=True,
        )
        print("[phase:ai] AI engine init FAILED", flush=True)
        _registry.failed("ai_engine", exc)

    # Phase 1b: Mount the host-owned AI surface (app/api/ai_routes.py) now
    # that matrx-ai's client-host seams are configured. ONE app, THREE mounts:
    # /ai is the aidream-compatible canonical surface (what matrx-frontend's
    # local-runtime mode targets); /v2/ai is the current frontend spine using
    # the same request/stream contract; /chat/ai is the desktop UI's historical
    # path. Must run after initialize_matrx_ai() — the prep path reads the seam
    # registry (conversation store / model catalog / key resolver).
    logger.info("[app/main.py] Phase 1b: Mounting host-owned /ai surface...")
    try:
        from app.api.ai_routes import build_ai_app

        _ai_surface = build_ai_app()
        app.mount("/ai", _ai_surface)
        app.mount("/v2/ai", _ai_surface)
        app.mount("/chat/ai", _ai_surface)
        logger.info(
            "[app/main.py] Phase 1b: /ai surface mounted at /ai, /v2/ai, "
            "and /chat/ai ✓"
        )
    except Exception:
        logger.error(
            "[app/main.py] Phase 1b: /ai surface mount FAILED — AI chat/agent endpoints will be unavailable",
            exc_info=True,
        )

    # Phase 2: Load tool registry from DB and register all local OS tools.
    print("[phase:tools] Loading tool registry...", flush=True)
    logger.info("[app/main.py] Phase 2: Loading tool registry...")
    _registry.starting("tools")
    try:
        tool_count = await load_tools_and_register()
        logger.info("[app/main.py] Phase 2: Tool registry loaded ✓")
        print("[phase:tools] Tool registry loaded", flush=True)
        if tool_count and tool_count > 0:
            _registry.ready("tools", tool_count=tool_count)
        else:
            # matrx-ai reported 0 registered tools — the registry is "up" but
            # every AI tool call will fail. Report the truth (DEGRADED), don't
            # cheerfully claim READY.
            _registry.degraded(
                "tools",
                reason="0 tools registered — AI tool calls will fail",
                tool_count=0,
            )
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 2: Tool registration FAILED — AI may not have tool access",
            exc_info=True,
        )
        print("[phase:tools] Tool registry FAILED", flush=True)
        _registry.failed("tools", exc)

    # Phase 2b: Start background sync engine (cloud → local SQLite).
    # This pulls models, agents, and tools from Supabase into the local DB
    # so all /data/* endpoints respond instantly from SQLite.
    logger.info("[app/main.py] Phase 2b: Starting background data sync...")
    _registry.starting("sync_engine")
    try:
        sync_engine = get_sync_engine()
        sync_engine.start()
        logger.info("[app/main.py] Phase 2b: Background sync started ✓")
        _registry.ready("sync_engine")
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 2b: Background sync FAILED to start — local data may be stale",
            exc_info=True,
        )
        _registry.failed("sync_engine", exc)

    # Phase 2c: Engine-owned notes auto-sync (documents ↔ workbench.notes).
    # Credentials come from the persisted auth_tokens row, so this works even
    # if the user never opens the Notes UI — the historical failure mode was
    # months of no sync because everything hinged on frontend traffic.
    _registry.starting("notes_sync")
    try:
        from app.services.documents.sync_engine import sync_engine as _doc_sync

        await _doc_sync.start_background_sync()
        logger.info("[app/main.py] Phase 2c: Notes auto-sync started ✓")
        _registry.ready("notes_sync")
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 2c: Notes auto-sync FAILED to start — notes will only sync manually",
            exc_info=True,
        )
        _registry.failed("notes_sync", exc)

    # Phase 2d: Engine-owned chat mirror sync (chat.* <-> cloud). Same
    # credential model as notes: the persisted auth_tokens row feeds each
    # tick, so local turns push and cloud conversations pull without any
    # frontend involvement. See app/services/chat_sync/engine.py.
    _registry.starting("chat_sync")
    try:
        from app.services.chat_sync import get_chat_sync_engine

        await get_chat_sync_engine().start_background_sync()
        logger.info("[app/main.py] Phase 2d: Chat mirror auto-sync started ✓")
        _registry.ready("chat_sync")
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 2d: Chat mirror auto-sync FAILED to start — local "
            "chats will not reach the cloud until triggered manually",
            exc_info=True,
        )
        _registry.failed("chat_sync", exc)

    # Phase 2e: Engine-owned file sync (files.* mirror + ~/Documents/Matrx/Files
    # replica of the matrx-files cloud tree). Same credential model: the
    # persisted auth_tokens row feeds each tick. Mode (off|pointers|full)
    # comes from settings; 'off' keeps the loop alive but idle.
    # See app/services/file_sync/engine.py.
    _registry.starting("file_sync")
    try:
        from app.services.file_sync import get_file_sync_engine

        await get_file_sync_engine().start_background_sync()
        logger.info("[app/main.py] Phase 2e: File sync started ✓")
        _registry.ready("file_sync")
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 2e: File sync FAILED to start — the local file "
            "replica will not update until triggered manually",
            exc_info=True,
        )
        _registry.failed("file_sync", exc)

    # Phase 2f: Cloud tool-call delegation client (suspend/resume, headless).
    # Sweeps GET /ai/user/pending_calls for delegated calls bound to this
    # desktop, executes them via the canonical dispatcher, POSTs results to
    # the aidream server, and resumes the suspended agent loop. The Phase 7
    # broadcast subscription feeds it low-latency wake hints; the poll is the
    # correctness backstop. See app/services/delegation/FEATURE.md.
    from app.config import CLOUD_PARTICIPATION_ENABLED

    if not CLOUD_PARTICIPATION_ENABLED:
        # Dev isolation (run.py): a coordination-silent engine never claims the
        # user's cloud-dispatched delegated tool calls — that would race the
        # installed app and corrupt live-test signals. This is a STATE, not a
        # failure.
        _registry.starting("delegation")
        _registry.stopped("delegation")
        logger.info(
            "[app/main.py] Phase 2f: Delegation client DISABLED "
            "(MATRX_CLOUD_PARTICIPATION=0 — dev coordination isolation). "
            "This engine will not claim cloud-delegated tool calls."
        )
    else:
        _registry.starting("delegation")
        try:
            from app.services.delegation import get_delegation_engine

            await get_delegation_engine().start_background()
            logger.info("[app/main.py] Phase 2f: Delegation client started ✓")
            _registry.ready("delegation")
        except Exception as exc:
            logger.error(
                "[app/main.py] Phase 2f: Delegation client FAILED to start — cloud "
                "agent turns cannot delegate tools to this desktop",
                exc_info=True,
            )
            _registry.failed("delegation", exc)

    # Phase 3: Start scraper engine
    print("[phase:scraper] Starting scraper engine...", flush=True)
    logger.info("[app/main.py] Phase 3: Starting scraper engine...")
    _registry.starting("scraper")
    engine = get_scraper_engine()
    try:
        await engine.start()
        logger.info("[app/main.py] Phase 3: Scraper engine started ✓")
        print("[phase:scraper] Scraper engine ready", flush=True)
        _registry.ready("scraper")
    except Exception as exc:
        logger.error(
            "[app/main.py] Phase 3: Scraper engine FAILED to start — scraping tools will be unavailable",
            exc_info=True,
        )
        print(
            "[phase:scraper] Scraper engine FAILED (scraping unavailable)", flush=True
        )
        _registry.failed("scraper", exc)

    # Guarded like every other phase: an exception here would abort lifespan
    # startup AFTER the scraper/database children are already running, and the
    # post-yield teardown would never execute for them.
    try:
        restored = await restore_scheduled_tasks()
        if restored:
            logger.info(
                "[app/main.py] Scheduler: %d task(s) restored from previous session",
                restored,
            )
    except Exception:
        logger.error(
            "[app/main.py] Scheduled-task restore FAILED (non-fatal)", exc_info=True
        )

    # Read the actual port Uvicorn is going to bind (written by run.py)
    # We need this to ensure the proxy doesn't collide with it, and to point the tunnel at it.
    try:
        import json
        from app.config import MATRX_HOME_DIR

        disc_data = json.loads((MATRX_HOME_DIR / "local.json").read_text())
        main_server_port = disc_data.get("port", 22140)
    except Exception:
        main_server_port = 22140

    # Phase 4: Start HTTP proxy if enabled in settings
    try:
        settings_sync = get_settings_sync()
        proxy_enabled = settings_sync.get("proxy_enabled", True)
    except Exception:
        logger.error(
            "[app/main.py] Settings read FAILED — proxy defaults on", exc_info=True
        )
        proxy_enabled = True
    logger.info("[app/main.py] Phase 4: HTTP proxy enabled=%s", proxy_enabled)
    if proxy_enabled:
        print("[phase:proxy] Starting local HTTP proxy...", flush=True)
        _registry.starting("proxy")
        proxy_port = settings_sync.get("proxy_port", DEFAULT_PROXY_PORT)
        try:
            proxy = get_proxy_server()
            # Guard against port collision with the main server
            if proxy_port == main_server_port:
                fallback_port = main_server_port + 40
                logger.warning(
                    "[app/main.py] Phase 4: Proxy port %d collides with main server port! "
                    "Falling back to %d.",
                    proxy_port,
                    fallback_port,
                )
                proxy_port = fallback_port

            logger.info(
                "[app/main.py] Phase 4: Starting proxy on 127.0.0.1:%d...", proxy_port
            )
            await proxy.start(port=proxy_port)
            logger.info(
                "[app/main.py] Phase 4: HTTP proxy started ✓ on port %d", proxy_port
            )
            print(f"[phase:proxy] HTTP proxy ready on port {proxy_port}", flush=True)
            _registry.ready("proxy", port=proxy_port)
        except OSError as exc:
            logger.error(
                "[app/main.py] Phase 4: HTTP proxy FAILED to start — port %d is already in use. "
                "Another process is holding this port. Kill it with: lsof -ti:%d | xargs kill -9  "
                "Error: %s",
                proxy_port,
                proxy_port,
                exc,
            )
            print("[phase:proxy] HTTP proxy FAILED (port in use)", flush=True)
            _registry.failed("proxy", f"port {proxy_port} in use: {exc}")
        except Exception as exc:
            logger.error(
                "[app/main.py] Phase 4: HTTP proxy FAILED to start", exc_info=True
            )
            print("[phase:proxy] HTTP proxy FAILED", flush=True)
            _registry.failed("proxy", exc)

    # Phase 5: Start Cloudflare tunnel (quick tunnel for all users — no account needed).
    # Each instance gets a unique random URL from Cloudflare's trycloudflare.com pool.
    # The URL is pushed to Supabase so mobile/web can discover it via app_instances lookup.
    # Users with a CLOUDFLARE_TUNNEL_TOKEN get a stable named tunnel URL instead.
    # User-controlled setting. Never inherit a packaged-process environment
    # variable as an end-user preference.
    tunnel_enabled = settings_sync.get("tunnel_enabled")
    logger.info("[app/main.py] Phase 5: Tunnel enabled=%s", tunnel_enabled)
    if tunnel_enabled:
        print("[phase:tunnel] Starting Cloudflare tunnel...", flush=True)
        _registry.starting("tunnel", upstream_port=main_server_port)
        try:
            from app.services.tunnel.manager import get_tunnel_manager as _get_tm

            _tm = _get_tm()
            _tunnel_url = await _tm.start(port=main_server_port)
            if _tunnel_url:
                logger.info("[app/main.py] Phase 5: Tunnel active ✓ → %s", _tunnel_url)
                print(f"[phase:tunnel] Tunnel active: {_tunnel_url}", flush=True)
                _registry.ready(
                    "tunnel",
                    url=_tunnel_url,
                    mode="named" if _tm._token else "quick",
                )
                try:
                    from app.services.cloud_sync.instance_manager import (
                        get_instance_manager as _get_im,
                    )

                    await _get_im().update_tunnel_url(_tunnel_url, active=True)
                except Exception:
                    logger.warning(
                        "[app/main.py] Phase 5: failed to publish tunnel URL to Supabase",
                        exc_info=True,
                    )
                # Write the tunnel URL into the discovery file — this is the
                # documented contract for matrx-extend; previously only the
                # manual POST /tunnel/start route did it, so a normal boot
                # left local.json without tunnel_url/tunnel_ws.
                try:
                    import sys as _sys

                    _run_mod = _sys.modules.get("run") or _sys.modules.get("__main__")
                    if _run_mod is not None and hasattr(
                        _run_mod, "update_discovery_tunnel"
                    ):
                        _run_mod.update_discovery_tunnel(_tunnel_url)
                except Exception:
                    logger.debug(
                        "[app/main.py] Phase 5: discovery tunnel write failed",
                        exc_info=True,
                    )
                # Seed the runtime introspection singleton so
                # ``GET /extension/tunnel/status`` reports the correct
                # state from the very first request post-boot.
                try:
                    from app.api.tunnel_state import set_tunnel_url

                    set_tunnel_url(
                        _tunnel_url,
                        mode="named" if _tm._token else "quick",
                        uptime_seconds=_tm.uptime_seconds,
                    )
                except Exception:
                    logger.debug(
                        "[app/main.py] Phase 5: tunnel state singleton update failed",
                        exc_info=True,
                    )
            else:
                logger.warning(
                    "[app/main.py] Phase 5: Tunnel started but no URL captured within timeout"
                )
                print("[phase:tunnel] Tunnel started but no URL captured", flush=True)
                _registry.degraded(
                    "tunnel", reason="started but no URL captured within timeout"
                )
        except Exception as exc:
            logger.error("[app/main.py] Phase 5: Tunnel FAILED to start", exc_info=True)
            print("[phase:tunnel] Tunnel FAILED to start", flush=True)
            _registry.failed("tunnel", exc)

    # Background heartbeat: updates last_seen and retries failed syncs
    async def _heartbeat_loop() -> None:
        consecutive_failures = 0
        while True:
            await asyncio.sleep(300)  # 5 minutes
            sync = get_settings_sync()
            if not sync.is_configured:
                continue
            try:
                await sync.heartbeat()
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                # Loud on the first failure, then every 10th, so a persistent
                # outage stays visible without flooding the log every 5 min.
                if consecutive_failures == 1 or consecutive_failures % 10 == 0:
                    logger.warning(
                        "Heartbeat failed (%d consecutive failure%s)",
                        consecutive_failures,
                        "" if consecutive_failures == 1 else "s",
                        exc_info=True,
                    )

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # Start retry queue poller (polls remote server for failed scrapes to retry locally)
    try:
        retry_queue.start()
    except Exception:
        logger.error(
            "[app/main.py] Retry queue start FAILED (non-fatal)", exc_info=True
        )

    # Start scrape store background sync (pushes pending local scrapes to cloud,
    # retries failed ones, surfaces sync errors to the user via /scrapes/sync-status)
    try:
        scrape_store.start_sync()
    except Exception:
        logger.error(
            "[app/main.py] Scrape store sync start FAILED (non-fatal)", exc_info=True
        )

    # Phase 6: Extension bridge self-check.
    # Walks /extension/* routes, JWT posture, tunnel + metrics + discovery
    # file in <1s and emits a single multi-line summary log block. The
    # result is cached in app.api.extension_boot_check and surfaced via
    # GET /extension/boot-check so the desktop UI can render the same
    # data the user just saw scroll past in the terminal.
    #
    # Non-fatal: any check failure flips summary.ok to False but does not
    # block startup — the bridge can be partially broken (e.g. tunnel
    # down) and the rest of the engine still needs to come up.
    try:
        from app.api.extension_boot_check import run_extension_boot_check

        await run_extension_boot_check(app)
    except Exception:
        logger.warning(
            "[app/main.py] Phase 6: Extension bridge self-check crashed (non-fatal)",
            exc_info=True,
        )

    # Phase 7: Cross-component broadcast subscription.
    # Activates the inbound router (app/api/cross_component_router.py) so
    # wake hints from aidream and RPC envelopes from other components can
    # reach this engine. Gated by the `extension_broadcast_enabled` user
    # setting (default ON) — the same gate extension_broadcast.py honors
    # for publishes. The old MATRX_BRIDGE_BROADCAST_ENABLED env var was
    # removed (.env notes the cutover); gating on it here left the
    # subscribe path permanently dead while the setting claimed ON.
    #
    # Mixed-reality wiring: at startup we look up the persisted user_id
    # from the auth_tokens SQLite row. If a session is already present
    # (user previously signed in), we subscribe immediately. If not,
    # POST /auth/token / DELETE /auth/token (token_routes.py) handle the
    # login/logout path and call connect_broadcast / disconnect_broadcast
    # so the subscription tracks the live signed-in identity. The user
    # id latched here is stashed on app.state.broadcast_user_id so the
    # shutdown teardown can match the subscribe that started it.
    app.state.broadcast_user_id = None
    from app.api.extension_broadcast import is_broadcast_enabled

    if not CLOUD_PARTICIPATION_ENABLED:
        logger.info(
            "[app/main.py] Phase 7: cross-component broadcast SKIPPED — cloud "
            "coordination disabled (MATRX_CLOUD_PARTICIPATION=0, dev isolation). "
            "This engine stays off the shared per-user bridge channel."
        )
    elif is_broadcast_enabled():
        try:
            from app.api.extension_broadcast import connect_broadcast
            from app.services.local_db.repositories import TokenRepo

            row = await TokenRepo().get()
            user_id = (row or {}).get("user_id")
            if user_id:
                await connect_broadcast(user_id)
                app.state.broadcast_user_id = user_id
                logger.info(
                    "[app/main.py] Phase 7: cross-component broadcast subscribed for user_id=%s",
                    user_id,
                )
            else:
                logger.info(
                    "[app/main.py] Phase 7: cross-component broadcast skipped — no signed-in user "
                    "(will subscribe on next POST /auth/token)"
                )
        except Exception as exc:
            logger.warning(
                "[app/main.py] Phase 7: cross-component broadcast failed to start (non-fatal): %s",
                exc,
                exc_info=True,
            )
    else:
        logger.info(
            "[app/main.py] Phase 7: cross-component broadcast disabled "
            "(extension_broadcast_enabled=false in settings)"
        )

    # Phase 8: matrx-scheduler host (surface='desktop').
    #
    # The desktop sidecar participates in the cross-component scheduling
    # spine — matrx-scheduler subscribes to sch_task rows tagged with
    # surfaces[] containing 'desktop' or 'any', claims them atomically,
    # runs them, and reports back. Mirrors aidream's surface='server'
    # wiring (aidream/aidream/api/app.py around 'AIDREAM_SCHEDULER').
    #
    # Independent of Phase 7's broadcast subscription — both can coexist
    # cleanly. Phase 7 uses Realtime Broadcast for live RPC envelopes;
    # Phase 8 uses Postgres polling against the sch_* tables.
    #
    # Activation is gated by MATRX_LOCAL_SCHEDULER_ENABLED (default off).
    # The Supabase client carries the user's JWT when one is persisted
    # in auth_tokens — RLS filters sch_* rows to that user only. When no
    # user is signed in, the scanner polls but sees zero rows.
    #
    # See app/services/scheduler_host.py for the full host posture.
    app.state.scheduler_host_started = False
    try:
        from app.services.scheduler_host import (
            configure_scheduler_host,
            is_scheduler_enabled,
            start_scheduler_host,
        )

        if is_scheduler_enabled():
            from supabase import create_async_client  # type: ignore[import-not-found]
            from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL
            from app.services.local_db.repositories import TokenRepo

            if not (SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY):
                logger.warning(
                    "[app/main.py] Phase 8: matrx-scheduler host disabled — "
                    "SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY missing"
                )
            else:
                _scheduler_supabase = await create_async_client(
                    SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY
                )
                # Attach the persisted JWT so PostgREST queries from the
                # scanner carry the user's identity and RLS scopes the
                # sch_task / sch_run rows to that user. If no JWT is
                # persisted yet (user not signed in), the scanner still
                # polls but RLS returns zero rows — the next POST
                # /auth/token (when the desktop UI delivers a session)
                # is the natural place to re-attach the new token;
                # follow-up work, not blocking for the host plumb.
                try:
                    _tok_row = await TokenRepo().get()
                    _access_token = (_tok_row or {}).get("access_token")
                    if _access_token:
                        try:
                            _scheduler_supabase.postgrest.auth(_access_token)
                            logger.info(
                                "[app/main.py] Phase 8: scheduler Supabase client "
                                "attached persisted user JWT (RLS-scoped)"
                            )
                        except Exception:
                            logger.debug(
                                "[app/main.py] Phase 8: postgrest.auth() failed (non-fatal)",
                                exc_info=True,
                            )
                except Exception:
                    logger.debug(
                        "[app/main.py] Phase 8: TokenRepo read failed (non-fatal)",
                        exc_info=True,
                    )

                _configured = await configure_scheduler_host(_scheduler_supabase)
                if _configured:
                    _started = await start_scheduler_host()
                    if _started:
                        app.state.scheduler_host_started = True
                        logger.info(
                            "[app/main.py] Phase 8: matrx-scheduler host started ✓"
                        )
                    else:
                        logger.warning(
                            "[app/main.py] Phase 8: scheduler configure succeeded "
                            "but start did not run (flag check changed?)"
                        )
        else:
            logger.debug(
                "[app/main.py] Phase 8: matrx-scheduler host disabled "
                "(MATRX_LOCAL_SCHEDULER_ENABLED not set)"
            )
    except Exception as exc:
        logger.warning(
            "[app/main.py] Phase 8: matrx-scheduler host failed to start (non-fatal): %s",
            exc,
            exc_info=True,
        )

    elapsed = _startup_time.monotonic() - _t0
    # Media services are lazy, but their health/recovery contracts must be
    # visible before a user first opens either page.
    from app.services.media_gen.recovery import register_media_recovery_controllers

    register_media_recovery_controllers()
    logger.info(
        "[app/main.py] ── Startup complete in %.1fs — scraper=%s, proxy=%s ──────────────",
        elapsed,
        engine.is_ready,
        get_proxy_server().running,
    )
    print(f"[phase:ready] Engine ready in {elapsed:.1f}s", flush=True)

    yield

    logger.info(
        "[app/main.py] ── Matrx Local shutdown ────────────────────────────────────"
    )
    # Cascade shutdown: this is where the engine fulfills its half of the
    # ownership contract — Rust signaled us to stop, now we stop every child
    # we own in reverse-startup order. Rust must NOT pkill any of these
    # children behind our back; doing so races with these stops and produces
    # the "ended unexpectedly" crash reports we're trying to eliminate.
    # See app/launcher.py for the full ownership rules.

    # Stop the image queue worker before other services. Queue records and init
    # images remain persisted; an engine restart resumes them.
    if _registry.current_state("image_gen") not in (None, "stopped"):
        try:
            await _registry.recover("image_gen", "stop", timeout_s=5.0)
        except Exception as exc:
            logger.warning("[app/main.py] Image generation stop did not complete: %s", exc)
    if _registry.current_state("video_gen") not in (None, "stopped"):
        try:
            await _registry.recover("video_gen", "stop", timeout_s=5.0)
        except Exception as exc:
            logger.warning("[app/main.py] Video generation stop did not complete: %s", exc)

    # ── Phase S00: Stop matrx-scheduler host (mirrors Phase 8) ────────────
    # Stop the scanner BEFORE tearing down the broadcast / Supabase clients
    # so an in-flight tick doesn't try to write to a closed connection.
    # Best-effort + hard-timeouted: a wedged scanner can never block engine
    # shutdown.
    if getattr(app.state, "scheduler_host_started", False):
        try:
            from app.services.scheduler_host import stop_scheduler_host

            await asyncio.wait_for(stop_scheduler_host(), timeout=5.0)
            logger.info("[app/main.py] Phase S00: matrx-scheduler host stopped ✓")
        except asyncio.TimeoutError:
            logger.warning(
                "[app/main.py] Phase S00: scheduler stop timed out after 5s — "
                "forcing teardown"
            )
        except Exception:
            logger.debug(
                "[app/main.py] Phase S00: scheduler stop failed (non-fatal)",
                exc_info=True,
            )

    # ── Phase S0: Tear down cross-component broadcast subscription ────────
    # Mirrors Phase 7 startup. The disconnect is best-effort + hard-timeouted
    # so a wedged Supabase realtime socket can never block engine shutdown.
    _broadcast_user_id = getattr(app.state, "broadcast_user_id", None)
    if _broadcast_user_id:
        try:
            from app.api.extension_broadcast import disconnect_broadcast

            await asyncio.wait_for(
                disconnect_broadcast(_broadcast_user_id), timeout=3.0
            )
            logger.info(
                "[app/main.py] Phase S0: cross-component broadcast disconnected for user_id=%s",
                _broadcast_user_id,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[app/main.py] Phase S0: cross-component broadcast disconnect timed out after 3s"
            )
        except Exception:
            logger.debug(
                "[app/main.py] Phase S0: cross-component broadcast disconnect failed (non-fatal)",
                exc_info=True,
            )

    # ── Phase S1: Cancel background asyncio tasks (fast, non-blocking) ────
    # App-config refresh loop (mirrors Phase 00). Best-effort + timeouted.
    try:
        from app.services.app_config import get_app_config_service as _get_app_cfg

        _app_cfg = _get_app_cfg()
        if _app_cfg.active:
            _registry.stopping("app_config")
            await asyncio.wait_for(_app_cfg.stop_background(), timeout=2.0)
            _registry.stopped("app_config")
            logger.info("[app/main.py] App config refresh loop stopped ✓")
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("[app/main.py] App config loop did not stop cleanly: %s", exc)
        _registry.stopped("app_config")

    # Catalogs refresh loop (mirrors Phase 00b). Best-effort + timeouted.
    try:
        from app.services.catalogs import get_catalogs_service as _get_catalogs

        _catalogs = _get_catalogs()
        if _catalogs.active:
            _registry.stopping("catalogs")
            await asyncio.wait_for(_catalogs.stop_background(), timeout=2.0)
            _registry.stopped("catalogs")
            logger.info("[app/main.py] Catalogs refresh loop stopped ✓")
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("[app/main.py] Catalogs loop did not stop cleanly: %s", exc)
        _registry.stopped("catalogs")

    _registry.stopping("sync_engine")
    try:
        sync_eng = get_sync_engine()
        sync_eng.stop()
        if hasattr(sync_eng, "_task") and sync_eng._task and not sync_eng._task.done():
            try:
                await asyncio.wait_for(sync_eng._task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        _registry.stopped("sync_engine")
    except Exception:
        _registry.stopped("sync_engine")

    # Await the cancellations so finally-blocks run before the loop closes
    # (fire-and-forget cancel produced "Task was destroyed but it is pending").
    await retry_queue.stop_async()
    await scrape_store.stop_sync_async()
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass

    # ── Phase S2: Stop Python wake word service (release microphone) ──────
    try:
        from app.services.wake_word.service import get_wake_word_service

        oww_svc = get_wake_word_service()
        if oww_svc._running:
            await asyncio.wait_for(oww_svc.stop(), timeout=3.0)
            logger.info("[app/main.py] Wake word service stopped ✓")
    except (asyncio.TimeoutError, Exception):
        logger.debug("[app/main.py] Wake word service cleanup skipped or timed out")

    # ── Phase S3: Cancel all scheduled tasks + kill prevent-sleep process ─
    try:
        from app.tools.tools.scheduler import shutdown_scheduler

        await asyncio.wait_for(shutdown_scheduler(), timeout=3.0)
        logger.info("[app/main.py] Scheduler shut down ✓")
    except (asyncio.TimeoutError, Exception):
        logger.debug("[app/main.py] Scheduler cleanup skipped or timed out")

    # ── Phase S4: Stop file watches + document watcher ─────────────────
    try:
        from app.tools.tools.file_watch import shutdown_file_watches

        shutdown_file_watches()
        logger.info("[app/main.py] File watches stopped ✓")
    except Exception:
        logger.debug("[app/main.py] File watch cleanup skipped")

    # Split try blocks + terminal registry states: a failed/hung auto-sync stop
    # must be LOUD and must not leave the registry wedged in "stopping", and
    # must not skip the watcher stop that shared its old try block.
    from app.services.documents.sync_engine import sync_engine as _doc_sync

    if _doc_sync.auto_sync_active:
        _registry.stopping("notes_sync")
        try:
            await asyncio.wait_for(_doc_sync.stop_background_sync(), timeout=3.0)
            _registry.stopped("notes_sync")
            logger.info("[app/main.py] Notes auto-sync stopped ✓")
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning(
                "[app/main.py] Notes auto-sync did not stop cleanly: %s", exc
            )
            _registry.stopped("notes_sync")

    from app.services.chat_sync import get_chat_sync_engine as _get_chat_sync

    _chat_sync = _get_chat_sync()
    if _chat_sync.auto_sync_active:
        _registry.stopping("chat_sync")
        try:
            await asyncio.wait_for(_chat_sync.stop_background_sync(), timeout=3.0)
            _registry.stopped("chat_sync")
            logger.info("[app/main.py] Chat mirror auto-sync stopped ✓")
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning(
                "[app/main.py] Chat mirror auto-sync did not stop cleanly: %s", exc
            )
            _registry.stopped("chat_sync")

    from app.services.file_sync import get_file_sync_engine as _get_file_sync

    _file_sync = _get_file_sync()
    if _file_sync.auto_sync_active or _file_sync.watcher_active:
        _registry.stopping("file_sync")
        try:
            await asyncio.wait_for(_file_sync.stop_background_sync(), timeout=3.0)
            _registry.stopped("file_sync")
            logger.info("[app/main.py] File sync stopped ✓")
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("[app/main.py] File sync did not stop cleanly: %s", exc)
            _registry.stopped("file_sync")

    from app.services.delegation import get_delegation_engine as _get_delegation

    _delegation = _get_delegation()
    if _delegation.active:
        _registry.stopping("delegation")
        try:
            await asyncio.wait_for(_delegation.stop_background(), timeout=3.0)
            _registry.stopped("delegation")
            logger.info("[app/main.py] Delegation client stopped ✓")
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning("[app/main.py] Delegation client did not stop cleanly: %s", exc)
            _registry.stopped("delegation")

    if _doc_sync.watcher_active:
        try:
            await asyncio.wait_for(_doc_sync.stop_watcher(), timeout=3.0)
            logger.info("[app/main.py] Document file watcher stopped ✓")
        except (asyncio.TimeoutError, Exception) as exc:
            logger.warning(
                "[app/main.py] Document watcher did not stop cleanly: %s", exc
            )

    # ── Phase S5: Stop network services (proxy, tunnel, scraper) ─────────
    # Each stop is wrapped in asyncio.wait_for with a hard timeout to prevent
    # a stuck service (hung TCP connection, blocked I/O, zombie Playwright
    # process) from blocking the entire teardown sequence.  Without these
    # timeouts, a single hung stop can exhaust the entire 25-second force-exit
    # budget and cause Python to be SIGKILL'd with ports still bound.
    if _registry.current_state("proxy") in ("ready", "degraded", "starting"):
        _registry.stopping("proxy")
        try:
            proxy = get_proxy_server()
            await asyncio.wait_for(proxy.stop(), timeout=5.0)
            logger.info("[app/main.py] HTTP proxy stopped ✓")
            _registry.stopped("proxy")
        except asyncio.TimeoutError:
            logger.warning(
                "[app/main.py] HTTP proxy stop timed out after 5s — forcing teardown"
            )
            _registry.failed("proxy", "stop timed out after 5s")
        except Exception as exc:
            logger.error(
                "[app/main.py] HTTP proxy failed to stop cleanly", exc_info=True
            )
            _registry.failed("proxy", exc)
    else:
        logger.debug(
            "[app/main.py] S5: proxy state=%s — skipping stop",
            _registry.current_state("proxy"),
        )

    if _registry.current_state("tunnel") not in ("ready", "degraded", "starting"):
        logger.debug(
            "[app/main.py] S5: tunnel state=%s — skipping stop",
            _registry.current_state("tunnel"),
        )
    else:
        _registry.stopping("tunnel")
        try:
            tm = get_tunnel_manager()
            if tm.running:
                await asyncio.wait_for(tm.stop(), timeout=7.0)
                logger.info("[app/main.py] Tunnel stopped ✓")
                try:
                    from app.services.cloud_sync.instance_manager import (
                        get_instance_manager as _get_im,
                    )

                    await asyncio.wait_for(
                        _get_im().update_tunnel_url(None, active=False), timeout=2.0
                    )
                except (asyncio.TimeoutError, Exception):
                    logger.warning(
                        "[app/main.py] Shutdown: failed to clear tunnel URL in Supabase",
                        exc_info=True,
                    )
                # Mirror the inactive state into the runtime singleton.
                try:
                    from app.api.tunnel_state import mark_tunnel_inactive

                    mark_tunnel_inactive()
                except Exception:
                    pass
                # Clear the tunnel fields from the discovery file so
                # matrx-extend doesn't read a dead URL after shutdown.
                try:
                    import sys as _sys

                    _run_mod = _sys.modules.get("run") or _sys.modules.get("__main__")
                    if _run_mod is not None and hasattr(
                        _run_mod, "update_discovery_tunnel"
                    ):
                        _run_mod.update_discovery_tunnel(None)
                except Exception:
                    pass
            # Surface cloudflared's exit code + last lines into the registry so
            # diagnostic dumps reveal "exit code 1" reasons instead of swallowing them.
            _registry.stopped(
                "tunnel",
                cloudflared_exit_code=tm.last_exit_code,
                cloudflared_recent_output=tm.recent_output[-30:],
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[app/main.py] Tunnel stop timed out after 7s — forcing teardown"
            )
            _registry.failed(
                "tunnel",
                "stop timed out after 7s — cloudflared subprocess may be wedged",
                cloudflared_recent_output=get_tunnel_manager().recent_output[-30:],
            )
        except Exception as exc:
            logger.error("[app/main.py] Tunnel failed to stop cleanly", exc_info=True)
            try:
                tail = get_tunnel_manager().recent_output[-30:]
            except Exception:
                tail = []
            _registry.failed("tunnel", exc, cloudflared_recent_output=tail)

    _registry.stopping("scraper")
    try:
        await asyncio.wait_for(engine.stop(), timeout=8.0)
        logger.info("[app/main.py] Scraper engine stopped ✓")
        _registry.stopped("scraper")
    except asyncio.TimeoutError:
        logger.warning(
            "[app/main.py] Scraper engine stop timed out after 8s — forcing teardown"
        )
        _registry.failed(
            "scraper", "stop timed out after 8s — Playwright may be wedged"
        )
    except Exception as exc:
        logger.error(
            "[app/main.py] Scraper engine failed to stop cleanly", exc_info=True
        )
        _registry.failed("scraper", exc)

    # ── Phase S6: Close Playwright browser instances from tool usage ──────
    try:
        from app.tools.tools.browser_automation import (
            _browser_instances,
            _browser_contexts,
            _playwright_instance,
        )

        for bt, browser in list(_browser_instances.items()):
            try:
                await asyncio.wait_for(browser.close(), timeout=3.0)
            except Exception:
                pass
        _browser_instances.clear()
        _browser_contexts.clear()

        if _playwright_instance is not None:
            try:
                await asyncio.wait_for(_playwright_instance.stop(), timeout=3.0)
            except Exception:
                pass

        logger.info("[app/main.py] Browser automation contexts closed ✓")
    except Exception:
        logger.debug(
            "[app/main.py] Browser automation cleanup skipped (no active contexts)"
        )

    # ── Phase S6b: Stop download manager (before SQLite close) ──────────
    try:
        from app.services.downloads.manager import get_download_manager

        await get_download_manager().stop()
        logger.info("[app/main.py] Download manager stopped ✓")
    except Exception:
        logger.debug("[app/main.py] Download manager cleanup skipped")

    # ── Phase S7: Close local SQLite database (must be last) ─────────────
    try:
        await get_db().close()
    except Exception:
        pass

    logger.info("[app/main.py] ── Shutdown complete ────────────────────────────────")


def _app_version() -> str:
    """Resolve the real package version so /version and /openapi.json don't
    advertise a stale hardcoded number."""
    try:
        from importlib.metadata import version as _meta_version

        return _meta_version("matrx-local")
    except Exception:
        return "0.0.0"


app = FastAPI(
    title="Matrx Local",
    description="Local companion service for AI Matrx — browser-to-filesystem bridge",
    version=_app_version(),
    lifespan=lifespan,
)

# NotesAccessError → clean 503. On macOS without Full Disk Access, notes
# writes (state.json rename, note files) raise NotesAccessError; without this
# handler that surfaced as an unhandled ASGI 500 to the client. Return the
# actionable message and the degraded reason so the UI can guide the user.
from fastapi.responses import JSONResponse as _JSONResponse
from app.services.documents.file_manager import NotesAccessError as _NotesAccessError


@app.exception_handler(_NotesAccessError)
async def _notes_access_error_handler(_request: Request, exc: _NotesAccessError):
    return _JSONResponse(
        status_code=exc.http_status,
        content={
            "detail": str(exc),
            "reason": exc.reason,
            "notes_access_degraded": True,
            "op": exc.op,
        },
    )


app.include_router(auth_router)  # OAuth callback — must be before AuthMiddleware
app.include_router(token_router)  # Token sync — React pushes JWT to Python
# Admin endpoints (/admin/status, /admin/shutdown, /admin/diagnose) — used by
# the Tauri shell to coordinate engine lifecycle without reaching across to
# kill engine-owned children. Listed in _PUBLIC_PATHS in app/api/auth.py.
# See app/launcher.py for the ownership/propagation contract.
app.include_router(admin_router)
app.include_router(catalog_router)
app.include_router(api_router)
app.include_router(tool_router, prefix="/tools", tags=["tools"])
# Orchestrator-shape sandbox dispatch — invoked by aidream's local-proxy
# reverse-proxy on behalf of agents that bind to this PC as a compute target.
# Auth happens per-route via validate_extension_principal (Supabase JWT from
# the user, validated via JWKS when configured / presence-only on loopback).
app.include_router(sandbox_router, prefix="/sandbox", tags=["sandbox"])
app.include_router(remote_scraper_router)
app.include_router(settings_router)
app.include_router(document_router, prefix="/notes")
app.include_router(proxy_router)
app.include_router(cloud_sync_router)
app.include_router(chat_router)
app.include_router(data_router)
app.include_router(permissions_router)
app.include_router(capabilities_router)
app.include_router(fetch_proxy_router)
app.include_router(tunnel_router)
app.include_router(setup_router)
app.include_router(platform_router)
app.include_router(wake_word_router)
app.include_router(model_repo_router)
app.include_router(hardware_router)
app.include_router(image_gen_router)
app.include_router(prompt_matrix_router)
app.include_router(video_gen_router)
app.include_router(media_library_router)
app.include_router(media_vault_router)
app.include_router(file_sync_router)
app.include_router(tts_router)
app.include_router(ner_router)
app.include_router(openai_compat_router)
app.include_router(hf_token_router)
app.include_router(scrape_router)
app.include_router(extension_router)
# Bridge-test endpoints — back the desktop frontend's "Bridge Test" page.
# Same /extension prefix, gated by the same Bearer-token middleware. Kept
# in a separate router file so the contract surface (extension_router)
# stays focused on what the Chrome extension itself depends on.
app.include_router(extension_bridge_router)
app.include_router(downloads_router)

# NOTE: the host-owned AI surface (app/api/ai_routes.py build_ai_app) is
# mounted at /ai AND /chat/ai in the lifespan handler (Phase 1b) AFTER
# initialize_matrx_ai() wires the client-host seams the prep path reads.

# ── Middleware registration ────────────────────────────────────────────────────
#
# Starlette/FastAPI processes add_middleware() calls in REVERSE registration order:
# the LAST add_middleware() call becomes the OUTERMOST wrapper.
# @app.middleware("http") also calls add_middleware() internally.
#
# Required order (outermost → innermost):
#   1. CORSMiddleware  — must be first to handle OPTIONS preflights before auth
#   2. AuthMiddleware  — validates Bearer tokens for protected routes
#   3. log_requests    — logs all requests/responses (innermost, sees final status codes)
#
# To achieve this, we register in REVERSE: log_requests first, then Auth, then CORS.
# We define log_requests as a regular function and register it via add_middleware so
# we control placement explicitly (unlike @app.middleware which always inserts at [0]).


async def _log_requests_dispatch(request: Request, call_next):
    import json as _json
    import time as _time

    t0 = _time.monotonic()
    path = request.url.path
    query = str(request.url.query) if request.url.query else ""
    display_path = f"{path}?{query}" if query else path

    # High-frequency polling routes and CORS preflights — log at DEBUG to keep
    # the terminal readable.  Only genuinely interesting one-off requests stay at INFO.
    _SILENT_PATHS = frozenset(
        {
            "/health",
            "/tools/list",
            "/cloud/heartbeat",
            "/ports",
            "/version",
            "/logs/access",
            # SSE streaming endpoints — connection is INFO, individual frames are not logged
            "/logs/stream",
            "/logs/access/stream",
            "/setup/logs",
            # Setup/dashboard polling (fires every 2-5s)
            "/setup/status",
            # AI status — polled on page mount
            "/chat/ai-status",
            "/chat/local-llm/status",
            "/chat/delegation/status",
            # Device monitoring polling (fires every 2-10s)
            "/devices/system",
            "/devices/permissions",
            "/devices/audio",
            # Notes polling
            "/notes/tree",
            "/notes/notes",
            "/notes/sync/status",
            # Settings reads (fetched on every page mount)
            "/settings/paths",
            "/settings/forbidden-urls",
            # Status endpoints polled by Settings page
            "/proxy/status",
            "/tunnel/status",
            "/cloud/instance",
            "/cloud/instances",
            "/capabilities",
            # Hardware polling
            "/hardware/status",
            # TTS status polling
            "/tts/status",
            # Scrape sync polling
            "/scrapes/sync-status",
            # Download manager SSE stream + status polling
            "/downloads/stream",
            "/downloads",
        }
    )

    # OPTIONS preflights are always silent — they carry no data.
    is_options = request.method == "OPTIONS"
    log = logger.debug if (path in _SILENT_PATHS or is_options) else logger.info

    # Read body for mutating methods (doesn't consume the ASGI stream).
    body = None
    try:
        if request.method in ("POST", "PUT", "PATCH"):
            body = await request.json()
            if body is not None:
                body = _request_body_for_log(path, body)
    except Exception:
        pass

    # ── Request line ──────────────────────────────────────────────────────────
    # At INFO level: compact single line (method + path). Full body goes to DEBUG
    # only, avoiding kilobytes of JSON flooding the INFO stream for every POST.
    log("→ %s %s", request.method, display_path)
    if body is not None:
        body_str = _json.dumps(body, indent=2, ensure_ascii=False)
        logger.debug("   body: %s", body_str)

    response = await call_next(request)
    duration_ms = (_time.monotonic() - t0) * 1000

    # ── Response line ─────────────────────────────────────────────────────────
    if response.status_code >= 500:
        logger.error(
            "← %d %s %s  (%.0fms)",
            response.status_code,
            request.method,
            display_path,
            duration_ms,
        )
        logger.error("  %s", _format_request_details(request, body))
    elif response.status_code >= 400:
        logger.warning(
            "← %d %s %s  (%.0fms)",
            response.status_code,
            request.method,
            display_path,
            duration_ms,
        )
        logger.warning("  %s", _format_request_details(request, body))
    else:
        log("← %d %s  (%.0fms)", response.status_code, request.method, duration_ms)

    # Write structured access-log entry (unchanged — consumed by UI).
    access_log.record(
        method=request.method,
        path=path,
        query=_sanitize_url(query),
        origin=request.headers.get("origin", ""),
        user_agent=request.headers.get("user-agent", ""),
        status=response.status_code,
        duration_ms=duration_ms,
    )

    return response


# Register middlewares in reverse desired order (last registered = outermost):
#   Step 1: log_requests (will be innermost after CORS and Auth are added)
app.add_middleware(BaseHTTPMiddleware, dispatch=_log_requests_dispatch)
#   Step 2: AuthMiddleware (wraps log_requests; inner to CORS)
app.add_middleware(AuthMiddleware)
#   Step 3: CORSMiddleware (outermost — handles OPTIONS preflights first)
#
# allow_origins:        exact origins (localhost + all Tauri app origins)
# allow_origin_regex:   wildcard subdomains (*.aimatrx.com, Vercel previews, etc.)
# allow_headers:        explicit list — "*" is invalid when allow_credentials=True
# allow_private_network: responds to Access-Control-Request-Private-Network headers
#                        sent by Windows WebView2 (Edge-based engine) for requests
#                        from http://tauri.localhost → http://127.0.0.1:*
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-User-Id", "X-API-Key", "Accept"],
    allow_private_network=True,
    # The AI streaming surface's response headers the browser must be able to
    # read cross-origin (matrx-frontend asserts X-Conversation-ID on every
    # /ai stream open — see run-ai-stream.ts).
    expose_headers=["X-Conversation-ID", "X-Request-ID"],
    max_age=600,
)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Auth check — BaseHTTPMiddleware does NOT intercept WebSocket upgrades.
    # We validate the token here manually.
    url = _sanitize_url(websocket.url)
    logger.info(f"WebSocket connecting: {url}")

    # Accept the token from the Authorization header OR the ?token= query
    # param (browsers can't set headers on a WS upgrade; non-browser clients
    # and the desktop test panel use the header). Mirrors extension_auth's
    # _extract_bearer_ws precedence.
    _auth_hdr = websocket.headers.get("authorization", "")
    if _auth_hdr.lower().startswith("bearer "):
        token = _auth_hdr[7:].strip() or None
    else:
        token = websocket.query_params.get("token")
    if not token:
        logger.warning(
            f"WebSocket rejected - missing token: {url} | Headers: {dict(websocket.headers)}"
        )
        await websocket.close(code=1008, reason="Missing auth token")
        return

    # Tunnel-borne WS connections are untrusted (this channel can execute
    # tools): require a verified Supabase identity or the local API key.
    # Direct-loopback connections keep the presence-only boundary.
    from app.api.remote_auth import (
        headers_indicate_tunnel,
        is_instance_owner,
        verify_supabase_token,
    )

    via_tunnel = headers_indicate_tunnel(websocket.headers)
    if via_tunnel:
        verified = await verify_supabase_token(token)
        if verified is None:
            logger.warning("WebSocket rejected - unverified token over tunnel: %s", url)
            await websocket.close(code=1008, reason="Invalid or expired credentials")
            return
        if not await is_instance_owner(verified.user_id):
            logger.warning("WebSocket rejected - non-owner token over tunnel: %s", url)
            await websocket.close(code=1008, reason="Not authorized for this instance")
            return

    # Store token for downstream forwarding.
    websocket.state.user_token = token

    conn = await websocket_manager.connect(websocket, via_tunnel=via_tunnel)
    try:
        while True:
            data = await websocket.receive_text()
            await websocket_manager.handle_tool_message(conn, data)
    except Exception as e:
        # WebSocketDisconnect with code 1012 = "Service Restart" — this is the
        # normal close code sent when the old engine is killed during a restart.
        # Logging it as ERROR creates noise in every update/restart cycle.
        # Any other unexpected exception is a genuine error worth surfacing.
        from starlette.websockets import WebSocketDisconnect

        if isinstance(e, WebSocketDisconnect) and e.code in (1001, 1012):
            # 1001 = Going Away (page unload / app close) — expected, not an error.
            # 1012 = Service Restart — normal on engine restart/update.
            logger.debug(f"WebSocket closed normally ({e.code}): {url}")
        else:
            logger.error(
                f"WebSocket error: {e} | {url} | Headers: {dict(websocket.headers)}"
            )
    finally:
        await websocket_manager.disconnect(websocket)
