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
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.common.background_tasks import fire_and_forget
from app.common.system_logger import get_logger
from app.api.remote_auth import (
    invalidate_token,
    missing_supabase_config,
    verify_supabase_token_result,
)
from app.services.action_needed import (
    ActionNeeded,
    ActionNeededAction,
    ActionNeededKind,
)
from app.services.action_needed.registry import get_action_needed_registry
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


# A configuration fault that no user can act on still has to reach a human.
# The canonical way to do that is an action-needed item: the desktop harvests
# `detail.action_needed` off any non-OK response body and renders a persistent,
# remedy-bearing card (desktop/src/lib/api.ts), and the registry keeps it in the
# reconnect snapshot until this same operation succeeds. Both are existing
# platform primitives — no client code exists for this case.
_SESSION_VERIFICATION_OPERATION = "auth:session-verification"


def _config_fault_action_needed(
    *, code: str, message: str, details: dict[str, Any]
) -> ActionNeeded:
    """Build the one card for 'the account service will not talk to this app'."""
    return ActionNeeded(
        # Source-owned and stable: the same fault arriving over another lane or
        # after a reconnect is shown once, not once per sign-in attempt.
        fingerprint=f"auth-config:{code}",
        code=code,
        kind=ActionNeededKind.API_KEY,
        feature="Sign-in",
        title="This app cannot reach your AI Matrx account",
        message=message,
        action=ActionNeededAction(
            kind="settings_cloud_account",
            label="Open Cloud & Account",
            route="/settings?tab=cloud",
        ),
        source="auth",
        details=details,
    )


async def _raise_config_fault(
    *, code: str, message: str, details: dict[str, Any]
) -> None:
    item = _config_fault_action_needed(code=code, message=message, details=details)
    await get_action_needed_registry().reconcile_operation(
        _SESSION_VERIFICATION_OPERATION, item
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": code,
            "message": message,
            "action_needed": item.model_dump(mode="json", exclude_none=True),
        },
    )


class TokenRequest(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    user_id: str
    expires_in: Optional[float] = None
    expected_generation: UUID | None = None
    expected_credential_revision: int | None = Field(
        default=None, ge=0, le=9_007_199_254_740_991
    )


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
    # Two OUR-CONFIGURATION faults, neither of them the user's session. Telling
    # them to sign in again would be a lie they could never act on, and clearing
    # the stored token would destroy a session that is very probably fine. Both
    # keep the stored session, leave the verification cache alone, and raise a
    # persistent action-needed card instead of a message only the log file sees.
    if verification.status == "misconfigured":
        logger.error(
            "[token_routes] session verification blocked by a configuration "
            "fault: the account service rejected this engine's API key. "
            "Stored session kept; user_id=%s was NOT signed out. "
            "Remedy: fix the engine's Supabase publishable key.",
            req.user_id,
        )
        await _raise_config_fault(
            code="account_service_key_rejected",
            message=(
                "The AI Matrx account service rejected this app's API key, so "
                "your sign-in could not be verified. This is a problem with the "
                "app's configuration, not with your account — signing in again "
                "will not help, and your saved session was left untouched. "
                "Update AI Matrx, or contact support if it is already current."
            ),
            details={"reason": "api_key_rejected"},
        )
    if verification.status == "unconfigured":
        missing = missing_supabase_config()
        logger.error(
            "[token_routes] session verification blocked by a configuration "
            "fault: this engine has no account-service configuration (%s). "
            "Stored session kept; user_id=%s was NOT signed out. "
            "Remedy: ship/restore that configuration and restart the engine.",
            ", ".join(missing) or "unknown",
            req.user_id,
        )
        await _raise_config_fault(
            code="account_service_not_configured",
            message=(
                "This copy of AI Matrx has no account-service settings, so it "
                "cannot verify any sign-in. This is a problem with the app's "
                "configuration, not with your account — signing in again will "
                "not help, and your saved session was left untouched. Update "
                "AI Matrx, or contact support if it is already current."
            ),
            details={"reason": "not_configured", "missing": missing},
        )
    if verification.status == "unavailable":
        # Genuinely a network/issuer outage — the one case where "check the
        # connection and try again" is honest advice.
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
        # Reject the POSTED token, but never wipe a previously-stored session
        # over it (remote_auth's own doctrine). The 2026-08-25 incident: the UI
        # restored a revoked session, posted it, and this handler cleared the
        # engine's good token — leaving the UI "signed in" while every engine
        # cloud lane (bridge outbox, sync, the browser runtime trigger) died.
        # Only a stored copy of the SAME bad token is cleared.
        await _clear_stored_token_if_matches(req.access_token, str(req.expected_generation) if req.expected_generation else None, req.expected_credential_revision)
        logger.warning(
            "[token_routes] rejected posted session for user_id=%s "
            "(verification=%s); stored session left untouched unless identical",
            req.user_id,
            verification.status,
        )
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
        await _clear_stored_token_if_matches(req.access_token, str(req.expected_generation) if req.expected_generation else None, req.expected_credential_revision)
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

    from app.services.auth_session import get_auth_session, SessionFenceError
    try:
        receipt = await get_auth_session().install(generation=str(req.expected_generation) if req.expected_generation else None, revision=req.expected_credential_revision, subject=req.user_id, access_token=req.access_token, refresh_token=req.refresh_token, expires_at=expires_at, allow_initialize=True)
    except SessionFenceError as error:
        raise HTTPException(status_code=409, detail={"code": error.code})
    repo = TokenRepo()
    # Keep the in-memory cache hot so matrx-ai picks up the new token immediately.
    set_jwt_cache(req.access_token)
    # Startup may have happened before the desktop transferred a session. In
    # that state local OS tools are present but the server registry deliberately
    # has no identity/org context yet. Refresh it only after this verified
    # hand-off, so remote tools and bindings do not remain permanently empty.
    try:
        from app.services.ai.engine import refresh_server_tool_definitions

        await refresh_server_tool_definitions()
    except Exception:
        # The helper contains its own containment; retain this guard so token
        # custody itself can never fail because a best-effort catalog refresh
        # has an unexpected import/runtime issue.
        logger.warning(
            "[token_routes] authenticated tool-registry refresh failed",
            exc_info=True,
        )
    # The source owns the requirement through retry success: a verification that
    # worked is the proof the configuration fault is gone, so the card clears.
    await get_action_needed_registry().reconcile_operation(
        _SESSION_VERIFICATION_OPERATION, None
    )
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

    # The gap every cloud lane was reporting is over the moment a session
    # lands: close the refresh window here rather than letting it time out.
    from app.services.session_freshness import session_restored

    session_restored()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            fire_and_forget(_sync_after_token(), name="post-token-sync")
    except Exception:
        pass

    return {"status": "ok", "user_id": req.user_id, "generation": receipt.generation, "credential_revision": receipt.credential_revision}


@router.get("/session-state")
async def session_state() -> dict[str, Any]:
    from app.services.auth_session import get_auth_session
    state = await get_auth_session().snapshot()
    return {"generation": state.generation, "credential_revision": state.credential_revision, "subject": state.subject, "cleanup": None if state.cleanup is None else {"retired_generation": state.cleanup.retired_generation, "status": state.cleanup.status}}


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


async def _teardown_cleared_session(result: Any) -> None:
    """Required logout teardown; failure keeps this exact cycle fenced."""
    from app.services.auth_session import get_auth_session
    row = result.row; outgoing = row.get("user_id") if row else None
    try:
        if row and isinstance(row.get("access_token"), str): invalidate_token(row["access_token"])
        clear_jwt_cache()
        from app.services.ai.key_manager import clear_vault_keys
        from app.services.cloud_sync.settings_sync import get_settings_sync
        clear_vault_keys(); get_settings_sync().clear_credentials()
        from app.services.coding_sessions import get_coding_session_bridge_outbox
        await get_coding_session_bridge_outbox().credentials_changed()
        if _broadcast_enabled() and outgoing:
            from app.api.extension_broadcast import disconnect_broadcast
            await disconnect_broadcast(outgoing)
    except Exception:
        await get_auth_session().finish_cleanup(retired_generation=result.retired_generation, new_generation=result.snapshot.generation, attempt_id=result.attempt_id, success=False)
        raise
    await get_auth_session().finish_cleanup(retired_generation=result.retired_generation, new_generation=result.snapshot.generation, attempt_id=result.attempt_id, success=True)

async def _clear_stored_token_if_matches(posted_token: str, generation: str | None, revision: int | None) -> None:
    """Retire only an exact rejected stored token under its captured fence."""
    from app.services.auth_session import get_auth_session, SessionFenceError
    try:
        result = await get_auth_session().clear(generation=generation, revision=revision, token=posted_token)
    except SessionFenceError:
        return
    if result is not None:
        try:
            await _teardown_cleared_session(result)
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "session_cleanup_failed",
                    "message": "Engine credential cleanup did not finish. Retry sign-out.",
                },
            ) from error

@router.delete("/token")
async def clear_token(expected_generation: UUID | None = None, expected_credential_revision: int | None = None) -> dict[str, Any]:
    """Compare-and-rotate the exact observed JWT; never clear unqualified state."""
    from app.services.auth_session import get_auth_session, SessionFenceError
    try:
        result = await get_auth_session().clear(generation=str(expected_generation) if expected_generation else None, revision=expected_credential_revision)
        if result is None: raise SessionFenceError()
        await _teardown_cleared_session(result)
    except SessionFenceError as error:
        raise HTTPException(status_code=409, detail={"code": error.code})
    except Exception as error:
        raise HTTPException(status_code=503, detail={"code": "session_cleanup_failed", "message": "Engine credential cleanup did not finish. Retry sign-out."}) from error
    return {"status": "ok", "generation": result.snapshot.generation, "credential_revision": result.snapshot.credential_revision}
