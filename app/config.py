import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from app.common.platform_ctx import PLATFORM as _PLATFORM_CTX

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

APP_NAME = "MatrxLocal"
APP_NAME_SLUG = "matrx-local"  # lowercase-hyphen form for Linux paths
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1")
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")

# ---------------------------------------------------------------------------
# CORS — allowed origins
#
# Exact origins are listed here.  Wildcard subdomains (*.aimatrx.com, etc.)
# are handled by ALLOWED_ORIGIN_REGEX in main.py so Starlette can match them
# against the incoming Origin header at runtime.
#
# To add a new exact origin: append to _DEFAULT_ORIGINS.
# To allow a new subdomain pattern: extend ALLOWED_ORIGIN_REGEX in main.py.
# ---------------------------------------------------------------------------

_DEFAULT_ORIGINS = ",".join(
    [
        # Production domains (exact)
        "https://aimatrx.com",
        "https://www.aimatrx.com",
        "https://appmatrx.com",
        "https://www.appmatrx.com",
        "https://mymatrx.com",
        "https://www.mymatrx.com",
        "https://codematrx.com",
        "https://www.codematrx.com",
        "https://matrxserver.com",
        "https://www.matrxserver.com",
        # Known Vercel deployment
        "https://ai-matrx-admin.vercel.app",
        # Chrome extension OAuth callback
        "https://ccmjgggbdngllppncmidllcjablcdepl.chromiumapp.org",
        # Local development
        "http://localhost:1420",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:5173",
        "http://127.0.0.1:1420",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:5173",
        # Tauri desktop app
        "tauri://localhost",  # macOS WebKit
        "http://tauri.localhost",  # Windows WebView2 / Linux WebKitGTK
    ]
)
# Tauri origins are always required — they cannot be removed via env var because
# the desktop app (WebView2 on Windows, WebKit on macOS) needs them to reach the
# local engine. Any additional origins from the env var are appended.
_TAURI_ORIGINS = {
    "tauri://localhost",  # macOS WebKit
    "http://tauri.localhost",  # Windows WebView2 / Linux WebKitGTK
}
_env_origins = os.getenv("ALLOWED_ORIGINS", "")
_extra = (
    [o.strip() for o in _env_origins.split(",") if o.strip()] if _env_origins else []
)
ALLOWED_ORIGINS: list[str] = list(
    dict.fromkeys(  # preserve order, deduplicate
        _DEFAULT_ORIGINS.split(",") + _extra + list(_TAURI_ORIGINS)
    )
)

# Regex covers wildcard subdomains that can't be listed exhaustively:
#   *.aimatrx.com, *.appmatrx.com, *.mymatrx.com, *.codematrx.com,
#   *.matrxserver.com, *-armani-sadeghis-projects.vercel.app
#   *.trycloudflare.com — Cloudflare quick-tunnel URLs (remote access)
# This is used by CORSMiddleware's allow_origin_regex parameter.
ALLOWED_ORIGIN_REGEX = (
    r"https://(.*\.)?(aimatrx|appmatrx|mymatrx|codematrx|matrxserver)\.com"
    r"|https://.*-armani-sadeghis-projects\.vercel\.app"
    r"|https://.*\.vercel\.app"  # covers preview deployments
    r"|https://.*\.trycloudflare\.com"  # Cloudflare quick tunnels (remote access dev/testing)
    r"|chrome-extension://.*"  # Chrome extensions
)

# ---------------------------------------------------------------------------
# Cloudflare Tunnel — remote access
#
# Default (all users): quick tunnel — cloudflared spawns as a subprocess and
# Cloudflare assigns a unique random *.trycloudflare.com URL per installation.
# No token, no account, no setup required. URL changes each restart but is
# immediately pushed to Supabase so remote devices discover it automatically.
#
# Optional: set CLOUDFLARE_TUNNEL_TOKEN to use a named tunnel with a stable URL.
# TUNNEL_ENABLED controls auto-start on engine boot.
# ---------------------------------------------------------------------------
CLOUDFLARE_TUNNEL_TOKEN = os.getenv("CLOUDFLARE_TUNNEL_TOKEN", "")
TUNNEL_ENABLED = os.getenv("TUNNEL_ENABLED", "False").lower() in ("true", "1")

# Remote scraper server — all DB access goes through this API, never directly.
# Authenticated users' Supabase JWTs are accepted by the server — no API key needed for users.
# SCRAPER_API_KEY is for server-to-server calls only (your own .env, never shipped to users).
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
SCRAPER_SERVER_URL = os.getenv(
    "SCRAPER_SERVER_URL", "https://scraper.app.matrxserver.com"
)

# ---------------------------------------------------------------------------
# AIDream Server — REST API for shared data (models, prompts, tools, cx data)
#
# The active URL is read from AIDREAM_SERVER_URL_LIVE.  All four variants are
# loaded so the UI can present a server-picker in debug/dev mode.
#
# NEVER fall back to a hardcoded URL here — if the env var is absent, sync is
# simply disabled (logged as a warning).  This prevents stale hardcoded URLs
# from shipping in production builds.
# ---------------------------------------------------------------------------
AIDREAM_SERVER_URL_LIVE = os.getenv(
    "AIDREAM_SERVER_URL_LIVE", "https://server.app.matrxserver.com"
)
AIDREAM_SERVER_URL_PRODUCTION = os.getenv(
    "AIDREAM_SERVER_URL_PRODUCTION", "https://server.app.matrxserver.com"
)
AIDREAM_SERVER_URL_DEV = os.getenv(
    "AIDREAM_SERVER_URL_DEV", "https://dev.server.app.matrxserver.com"
)
AIDREAM_SERVER_URL_LOCAL = os.getenv(
    "AIDREAM_SERVER_URL_LOCAL", "http://localhost:8000"
)

# Active URL — everything in the codebase that needs the AIDream server reads this.
AIDREAM_SERVER_URL = AIDREAM_SERVER_URL_LIVE

# Matrx Files — the standalone file microservice (EC2/Cloudflare, launched
# 2026-07-13). ALL desktop file-sync traffic (change feed, uploads, patches,
# tombstones, URL minting) goes here — never through aidream, never a
# hand-constructed S3/CDN URL. The env override exists for local development
# against a locally-run matrx-files instance.
MATRX_FILES_URL = os.getenv("MATRX_FILES_URL", "https://files.matrxserver.com")

# Supabase (for document sync — uses PostgREST API with user JWTs)
# These are publishable values — safe to embed in the binary (RLS enforces security).
# The env var overrides are for local dev; shipped users get the baked-in defaults.
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://db.matrxserver.com")
SUPABASE_PUBLISHABLE_KEY = os.getenv(
    "SUPABASE_PUBLISHABLE_KEY",
    "sb_publishable_4pvkRT-9-_dB0PWqF1sp1w_W9leRIoW",
)

# ---------------------------------------------------------------------------
# Platform-aware storage roots
#
# Development (running from source):
#   All paths fall back to  <project_root>/system/...  so nothing changes for
#   your local workflow — the "system" folder keeps working exactly as before.
#
# Installed / frozen app (PyInstaller, Tauri sidecar):
#   Paths follow OS conventions so the app behaves like a proper desktop app
#   and doesn't write user data next to its binaries.
#
#   Windows  → %APPDATA%\MatrxLocal\          (Roaming — per-user, synced)
#              %LOCALAPPDATA%\MatrxLocal\      (Local  — per-user, not synced, for cache/temp/logs)
#   macOS    → ~/Library/Application Support/MatrxLocal/
#              ~/Library/Logs/MatrxLocal/
#              ~/Library/Caches/MatrxLocal/
#   Linux    → ~/.local/share/matrx-local/
#              ~/.cache/matrx-local/
#
# Every path can be overridden by an env var for power users / CI.
# ---------------------------------------------------------------------------

_is_frozen = getattr(sys, "frozen", False)  # True when running as PyInstaller bundle
_system = _PLATFORM_CTX["system"]


def _platform_data_dir() -> Path:
    """Persistent user data — settings, DB, manifests."""
    if _system == "Windows":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME
    if _system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux / BSD
    xdg = os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(xdg) / APP_NAME_SLUG


def _platform_cache_dir() -> Path:
    """Ephemeral cache / temp — screenshots, audio, extracted files, etc."""
    if _system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_NAME / "cache"
    if _system == "Darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME
    xdg = os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    return Path(xdg) / APP_NAME_SLUG


def _platform_log_dir() -> Path:
    """Application log files."""
    if _system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_NAME / "logs"
    if _system == "Darwin":
        return Path.home() / "Library" / "Logs" / APP_NAME
    xdg = os.getenv("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(xdg) / APP_NAME_SLUG / "logs"


# In development (not frozen) keep everything inside the repo so nothing moves.
# In a frozen/installed build use the OS-appropriate locations.
_dev_system = BASE_DIR / "system"

if _is_frozen:
    _data_root = _platform_data_dir()
    _cache_root = _platform_cache_dir()
    _log_root = _platform_log_dir()
else:
    _data_root = _dev_system / "data"
    _cache_root = _dev_system / "temp"
    _log_root = _dev_system / "logs"

# Env-var overrides (always respected regardless of frozen/dev)
TEMP_DIR = Path(os.getenv("MATRX_TEMP_DIR", str(_cache_root)))
DATA_DIR = Path(os.getenv("MATRX_DATA_DIR", str(_data_root)))
CONFIG_DIR = Path(os.getenv("MATRX_CONFIG_DIR", str(_data_root / "config")))
LOCAL_LOG_DIR = Path(os.getenv("MATRX_LOG_DIR", str(_log_root)))
CODE_SAVES_DIR = TEMP_DIR / "code_saves"

# ---------------------------------------------------------------------------
# ~/.matrx  — discovery file, settings, instance ID, engine internals
#
# This is intentionally always in the user's home regardless of platform.
# It is a small, well-known location that lets multiple tools (web, mobile,
# CLI) discover the running engine without platform-specific logic on their end.
# ---------------------------------------------------------------------------
MATRX_HOME_DIR = Path(os.getenv("MATRX_HOME_DIR", str(Path.home() / ".matrx")))

# ---------------------------------------------------------------------------
# User-visible storage — lives inside the OS-native Documents folder
#
# All platforms use "Documents" as the standard folder name, so this is safe
# cross-platform.  Subfolders follow the architecture in docs/local-storage-architecture.md.
#
#   Windows:  C:\Users\<user>\Documents\Matrx\
#   macOS:    /Users/<user>/Documents/Matrx/
#   Linux:    ~/Documents/Matrx/   (XDG_DOCUMENTS_DIR or ~/Documents fallback)
#
# Every path can be overridden via environment variable for power users / CI.
# ---------------------------------------------------------------------------


def _os_documents_dir() -> Path:
    """Return the OS-native Documents folder."""
    if _system == "Windows":
        # USERPROFILE\Documents is the Windows standard
        return Path(os.getenv("USERPROFILE", str(Path.home()))) / "Documents"
    if _system == "Darwin":
        return Path.home() / "Documents"
    # Linux: respect XDG_DOCUMENTS_DIR if set, otherwise ~/Documents
    xdg_docs = os.getenv("XDG_DOCUMENTS_DIR")
    if xdg_docs:
        return Path(xdg_docs)
    return Path.home() / "Documents"


# Root of all user-visible Matrx content
MATRX_USER_DIR = Path(os.getenv("MATRX_USER_DIR", str(_os_documents_dir() / "Matrx")))

# Notes: .md and .txt files — local is source of truth, Supabase is sync target
MATRX_NOTES_DIR = Path(os.getenv("MATRX_NOTES_DIR", str(MATRX_USER_DIR / "Notes")))

# Files: binary files (PDF, DOCX, XLSX, images, audio, video) — S3 sync on demand
MATRX_FILES_DIR = Path(os.getenv("MATRX_FILES_DIR", str(MATRX_USER_DIR / "Files")))

# Code: user's git repos (visible to user — agent uses these for their projects)
MATRX_CODE_DIR = Path(os.getenv("MATRX_CODE_DIR", str(MATRX_USER_DIR / "Code")))

# Workspaces: agent working copies of repos — hidden from user, under ~/.matrx
MATRX_WORKSPACES_DIR = Path(
    os.getenv("MATRX_WORKSPACES_DIR", str(MATRX_HOME_DIR / "workspaces"))
)

# Internal structured data: prompts, agent defs, tool configs (hidden from user)
MATRX_DATA_DIR = Path(os.getenv("MATRX_AGENT_DATA_DIR", str(MATRX_HOME_DIR / "data")))

# Deprecated alias — kept temporarily so existing code doesn't break during migration
# TODO: remove after all references are updated to MATRX_NOTES_DIR
DOCUMENTS_BASE_DIR = MATRX_NOTES_DIR

# ---------------------------------------------------------------------------
# Local SQLite database — offline-first data store
#
# Always lives under MATRX_HOME_DIR (user's home) so that data survives
# app reinstalls and updates.  Never stored inside the application folder.
#
#   All platforms: ~/.matrx/matrx.db
#
# The engine reads all runtime data (models, agents, conversations, tools)
# from this database.  Cloud data (Supabase) is synced in the background.
# ---------------------------------------------------------------------------
LOCAL_DB_PATH = Path(os.getenv("MATRX_LOCAL_DB", str(MATRX_HOME_DIR / "matrx.db")))

LOG_VCPRINT = True

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
LOG_DIR = Path(os.getenv("LOG_DIR", str(LOCAL_LOG_DIR)))
MAX_LOG_FILE_SIZE = int(os.getenv("MAX_LOG_FILE_SIZE", 10 * 1024 * 1024))
BACKUP_COUNT = int(os.getenv("BACKUP_COUNT", 5))

# When True: console logs omit timestamp and logger name (clean terminal output).
# File logs always include full timestamp for server diagnostics.
# Set LOCAL_DEV=False in production/Coolify to get full timestamps everywhere.
LOCAL_DEV = os.getenv("LOCAL_DEV", "True").lower() in ("true", "1")
