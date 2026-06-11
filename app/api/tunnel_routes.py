"""Tunnel management API routes.

Exposes start/stop/status endpoints for the Cloudflare tunnel that allows
remote devices (mobile, web) to connect to this local engine from anywhere.

GET  /tunnel/status  — public, returns current tunnel state
POST /tunnel/start   — starts the tunnel subprocess
POST /tunnel/stop    — stops the tunnel subprocess
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.tunnel.manager import get_tunnel_manager
from app.common.system_logger import get_logger

logger = get_logger()
router = APIRouter(prefix="/tunnel", tags=["tunnel"])


class TunnelStatus(BaseModel):
    running: bool
    url: str | None
    ws_url: str | None
    uptime_seconds: float
    port: int
    mode: str  # "quick" or "named"


class TunnelStartRequest(BaseModel):
    port: int | None = None  # override the engine port if needed


@router.get("/status", response_model=TunnelStatus)
async def tunnel_status() -> TunnelStatus:
    """Return current tunnel state. Public — no auth required."""
    tm = get_tunnel_manager()
    status = tm.get_status()
    return TunnelStatus(**status)


@router.post("/start", response_model=TunnelStatus)
async def tunnel_start(body: TunnelStartRequest | None = None) -> TunnelStatus:
    """Start the Cloudflare tunnel. Returns the assigned public URL."""
    tm = get_tunnel_manager()

    if tm.running:
        logger.info("[tunnel] Already running at %s", tm.url)
        return TunnelStatus(**tm.get_status())

    # Determine the engine port — request override, then the stored port from
    # a previous start, then the ACTUAL bound port from the discovery file.
    # Hardcoding 22140 pointed the tunnel at a dead port whenever the engine
    # bound a fallback port (22141-22159).
    discovered_port = None
    try:
        from app.preflight import read_discovery_file
        discovered_port = (read_discovery_file() or {}).get("port")
    except Exception:
        pass
    port = (
        (body.port if body and body.port else None)
        or tm._port
        or discovered_port
        or 22140
    )

    logger.info("[tunnel] Starting tunnel on port %d", port)
    url = await tm.start(port)

    if not url:
        detail = (
            "Failed to start tunnel — cloudflared exited before producing a URL. "
            "Check logs for details."
            if not tm.running
            else "cloudflared is running but did not produce a tunnel URL within 30s. "
            "Check firewall or network settings."
        )
        raise HTTPException(status_code=503, detail=detail)

    # Persist the user's preference so it auto-starts on next engine boot.
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync
        get_settings_sync().set("tunnel_enabled", True)
    except Exception:
        logger.debug("[tunnel] Could not persist tunnel_enabled=True", exc_info=True)

    # Push the new URL to Supabase asynchronously (best-effort)
    try:
        from app.services.cloud_sync.instance_manager import get_instance_manager
        mgr = get_instance_manager()
        await mgr.update_tunnel_url(url, active=True)
    except Exception:
        logger.debug("[tunnel] Could not push tunnel URL to Supabase", exc_info=True)

    # Update the discovery file through the central atomic writer (the old
    # direct write_text bypassed preflight's tmp+replace and never updated
    # the schema-2 services map).
    try:
        import sys as _sys
        _run_mod = _sys.modules.get("run") or _sys.modules.get("__main__")
        if _run_mod is not None and hasattr(_run_mod, "update_discovery_tunnel"):
            _run_mod.update_discovery_tunnel(url)
    except Exception:
        logger.debug("[tunnel] Could not update discovery file", exc_info=True)

    # Keep the runtime singleton in sync — desktop UI / extension polls
    # ``GET /extension/tunnel/status`` instead of re-reading the disk file.
    try:
        from app.api.tunnel_state import mark_tunnel_inactive, set_tunnel_url
        if url:
            set_tunnel_url(
                url,
                mode="named" if tm._token else "quick",
                uptime_seconds=tm.uptime_seconds,
            )
        else:
            mark_tunnel_inactive()
    except Exception:
        logger.debug("[tunnel] Could not update tunnel state singleton", exc_info=True)

    return TunnelStatus(**tm.get_status())


@router.post("/stop", response_model=TunnelStatus)
async def tunnel_stop() -> TunnelStatus:
    """Stop the Cloudflare tunnel."""
    tm = get_tunnel_manager()

    if not tm.running:
        return TunnelStatus(**tm.get_status())

    await tm.stop()

    # Persist the user's preference so it stays off on next engine boot.
    try:
        from app.services.cloud_sync.settings_sync import get_settings_sync
        get_settings_sync().set("tunnel_enabled", False)
    except Exception:
        logger.debug("[tunnel] Could not persist tunnel_enabled=False", exc_info=True)

    # Clear tunnel URL in Supabase (best-effort)
    try:
        from app.services.cloud_sync.instance_manager import get_instance_manager
        mgr = get_instance_manager()
        await mgr.update_tunnel_url(None, active=False)
    except Exception:
        logger.debug("[tunnel] Could not clear tunnel URL in Supabase", exc_info=True)

    # Clear from discovery file (atomic, schema-2 aware)
    try:
        import sys as _sys
        _run_mod = _sys.modules.get("run") or _sys.modules.get("__main__")
        if _run_mod is not None and hasattr(_run_mod, "update_discovery_tunnel"):
            _run_mod.update_discovery_tunnel(None)
    except Exception:
        logger.debug("[tunnel] Could not update discovery file", exc_info=True)

    # Mirror the inactive state into the runtime singleton.
    try:
        from app.api.tunnel_state import mark_tunnel_inactive
        mark_tunnel_inactive()
    except Exception:
        logger.debug("[tunnel] Could not update tunnel state singleton", exc_info=True)

    return TunnelStatus(**tm.get_status())
