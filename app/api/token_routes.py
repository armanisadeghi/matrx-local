"""Token sync routes — React pushes the Supabase JWT to Python for persistence.

Endpoints:
  POST /auth/token   — store a new JWT (called after login / token refresh)
  GET  /auth/token   — retrieve the current stored token (for Python-internal use)
  DELETE /auth/token — clear the stored token (called on logout)

These endpoints are intentionally listed in _PUBLIC_PATHS in auth.py because they
bootstrap the auth state — the JWT is the credential being *given* to Python, not
one it can validate beforehand.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.common.background_tasks import fire_and_forget
from app.common.system_logger import get_logger
from app.api.remote_auth import (
    invalidate_token,
    verify_supabase_token_result,
)
from app.services.local_db.repositories import TokenRepo
from app.services.ai.engine import clear_jwt_cache, set_jwt_cache

logger = get_logger()
router = APIRouter(prefix="/auth", tags=["auth-token"])


def _broadcast_enabled() -> bool:
    """Gate for the cross-component broadcast plumb.

    Delegates to the canonical `extension_broadcast_enabled` user-setting
    gate (default ON) so login/logout hooks, Phase 7 startup, and the
    publish path in extension_broadcast.py can never drift apart. The old
    MATRX_BRIDGE_BROADCAST_ENABLED env var was removed — gating on it here
    kept this path permanently dead.
    """
    from app.api.extension_broadcast import is_broadcast_enabled

    return is_broadcast_enabled()


class TokenRequest(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    user_id: str
    expires_in: Optional[float] = None


class TokenResponse(BaseModel):
    access_token: str
    user_id: str
    expires_at: Optional[int] = None
    is_expired: bool = False


@router.post("/token")
async def save_token(req: TokenRequest) -> dict[str, Any]:
    """Store the user's JWT so Python can use it across restarts.

    Called by the React frontend after every successful auth (login, token refresh,
    initial session restore).  Python reads it on startup and whenever it needs to
    make authenticated API calls (e.g. SyncEngine fetching user prompts).

    After saving, triggers a background sync of user-specific data (agents/prompts)
    so the local SQLite cache is populated immediately rather than waiting for the
    next scheduled sync interval.
    """
    verification = await verify_supabase_token_result(req.access_token)
    if verification.status in {"unavailable", "unconfigured"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "session_verification_unavailable",
                "message": (
                    "AI Matrx could not verify this session against the configured "
                    "account service. Check the connection and try signing in again."
                ),
            },
        )

    verified_user = verification.user
    if verification.status != "verified" or verified_user is None:
        invalidate_token(req.access_token)
        await clear_token()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "invalid_supabase_session",
                "message": (
                    "This session was not accepted by the configured AI Matrx "
                    "account service. Sign in again."
                ),
            },
        )
    if verified_user.user_id != req.user_id:
        invalidate_token(req.access_token)
        await clear_token()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "session_user_mismatch",
                "message": (
                    "The verified session belongs to a different user than the "
                    "desktop session. Sign out, then sign in again."
                ),
            },
        )

    expires_at: Optional[int] = None
    if req.expires_in:
        expires_at = int(time.time()) + int(req.expires_in)

    repo = TokenRepo()
    await repo.save(
        access_token=req.access_token,
        user_id=req.user_id,
        refresh_token=req.refresh_token,
        expires_at=expires_at,
    )
    # Keep the in-memory cache hot so matrx-ai picks up the new token immediately.
    set_jwt_cache(req.access_token)
    logger.info(
        "[token_routes] JWT saved for user_id=%s expires_at=%s — triggering background agent sync",
        req.user_id,
        expires_at,
    )

    # A prior cloud 401 pauses the coding-session publisher to prevent one bad
    # credential from being sprayed across every delivery lane. A verified new
    # session is the explicit unblock signal.
    try:
        from app.services.coding_sessions import get_coding_session_bridge_outbox

        await get_coding_session_bridge_outbox().credentials_changed()
    except Exception:
        logger.debug(
            "[token_routes] coding-session publisher wake failed",
            exc_info=True,
        )

    # Cross-component broadcast subscribe — Case B of the lifecycle wiring.
    # Phase 7 startup in app/main.py handles the resume-from-persisted-session
    # path; this branch handles fresh sign-ins arriving after the engine
    # is already running. Idempotent for the same user_id (the helper
    # short-circuits when the user is already connected). For account
    # switches the old subscription is dropped explicitly so a stale
    # channel for a previous user_id never lingers. Gated on the
    # `extension_broadcast_enabled` user setting so the plumb stays opt-out-able.
    if _broadcast_enabled():
        try:
            from app.api.extension_broadcast import (
                connect_broadcast,
                disconnect_broadcast,
                _channels,
            )

            # Drop any subscription bound to a different user_id (account switch).
            for prev_user_id in list(_channels.keys()):
                if prev_user_id != req.user_id:
                    try:
                        await disconnect_broadcast(prev_user_id)
                    except Exception:
                        logger.debug(
                            "[token_routes] stale broadcast disconnect failed user_id=%s",
                            prev_user_id,
                            exc_info=True,
                        )

            await connect_broadcast(req.user_id)
        except Exception as exc:
            logger.warning(
                "[token_routes] cross-component broadcast subscribe failed (non-fatal): %s",
                exc,
            )

    # Trigger an immediate background sync of user-specific data (agents/prompts).
    # The startup sync_all() runs before the JWT is available, so user prompts are
    # never fetched on startup. We kick a sync here so the UI gets user agents
    # as soon as the token is delivered — no 10-minute wait.
    async def _sync_after_token() -> None:
        try:
            from app.services.local_db.sync_engine import get_sync_engine

            engine = get_sync_engine()
            await engine.sync_agents()
            logger.info(
                "[token_routes] Post-login agent sync complete for user_id=%s",
                req.user_id,
            )
        except Exception as exc:
            logger.warning("[token_routes] Post-login agent sync failed: %s", exc)

        # Credential Vault — tier 2 of the provider-key resolution order
        # (app/services/ai/key_manager.py). A key the user saved once in the
        # web app or the extension becomes usable here the moment they sign
        # in. Never blocks or fails login: an unreachable vault leaves the
        # local key store exactly as it was.
        try:
            from app.services.ai.key_manager import refresh_vault_keys

            snapshot = await refresh_vault_keys()
            if not snapshot.ok:
                logger.info(
                    "[token_routes] Credential Vault unavailable after sign-in (%s)",
                    snapshot.state,
                )
        except Exception as exc:
            logger.warning("[token_routes] Credential Vault refresh failed: %s", exc)

        # Scrapes saved while signed out are queued, not failed — signing in is
        # the blocker clearing, so drain the backlog now instead of leaving the
        # user to wonder why their scrapes aren't in the web app.
        try:
            from app.services.scraper.scrape_store import sync_after_sign_in

            result = await sync_after_sign_in()
            if result["pushed"] or result["revived"]:
                logger.info(
                    "[token_routes] Post-login scrape sync: revived=%d pushed=%d "
                    "deferred=%d failed=%d",
                    result["revived"],
                    result["pushed"],
                    result["deferred"],
                    result["failed"],
                )
        except Exception as exc:
            logger.warning("[token_routes] Post-login scrape sync failed: %s", exc)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            fire_and_forget(_sync_after_token(), name="post-token-sync")
    except Exception:
        pass

    return {"status": "ok", "user_id": req.user_id}


@router.get("/token")
async def get_token() -> dict[str, Any]:
    """Return the currently stored token.

    Used internally by the sync engine and any Python service that needs the
    current user JWT.  React should never call this — it has its own Supabase
    session.  Returns 404-style empty dict if no token is stored.
    """
    repo = TokenRepo()
    row = await repo.get()
    if not row:
        return {"present": False}

    is_expired = repo.is_expired(row)
    return {
        "present": True,
        "user_id": row.get("user_id"),
        "expires_at": row.get("expires_at"),
        "is_expired": is_expired,
        "access_token": row.get("access_token"),
    }


@router.delete("/token")
async def clear_token() -> dict[str, Any]:
    """Clear the stored JWT on logout."""
    repo = TokenRepo()

    # Capture the outgoing user_id BEFORE we wipe the row so we can tear
    # down the matching cross-component broadcast subscription. Best-effort:
    # missing row / read failure must not block logout.
    outgoing_user_id: Optional[str] = None
    row: dict[str, Any] | None = None
    try:
        row = await repo.get()
        if row:
            outgoing_user_id = row.get("user_id")
    except Exception:
        logger.debug(
            "[token_routes] could not read outgoing user_id on logout", exc_info=True
        )

    await repo.clear()
    if row and isinstance(row.get("access_token"), str):
        invalidate_token(row["access_token"])
    clear_jwt_cache()
    # Drop every Credential-Vault-supplied provider key with the session that
    # authorized it. Keys the user saved on THIS machine survive sign-out.
    from app.services.ai.key_manager import clear_vault_keys

    clear_vault_keys()
    logger.info("[token_routes] JWT cleared (logout) user_id=%s", outgoing_user_id)

    try:
        from app.services.coding_sessions import get_coding_session_bridge_outbox

        await get_coding_session_bridge_outbox().credentials_changed()
    except Exception:
        logger.debug(
            "[token_routes] coding-session publisher wake failed on logout",
            exc_info=True,
        )

    # Cross-component broadcast disconnect — Case B of the lifecycle wiring.
    # Mirrors the connect in POST /auth/token. Gated on the
    # `extension_broadcast_enabled` user setting to match the startup wiring.
    if _broadcast_enabled() and outgoing_user_id:
        try:
            from app.api.extension_broadcast import disconnect_broadcast

            await disconnect_broadcast(outgoing_user_id)
        except Exception as exc:
            logger.warning(
                "[token_routes] cross-component broadcast disconnect failed (non-fatal): %s",
                exc,
            )

    return {"status": "ok"}
