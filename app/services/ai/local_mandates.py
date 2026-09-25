"""Local-model Mandates — the platform's answer, kept for offline runs.

Every on-device intelligence point in the desktop (AI Polish, the text
pipeline, Confidential Chat and its prompt library, Tools and Raw JSON modes)
runs through a Mandate: the PLATFORM decides which agent holds the job — its
instructions, variables, sampling and output schema — and this device only
supplies the compute (common-docs/systems/intelligence/mandates/STATE.md §9).

"Offline" is a data LOCATION, never a different intelligence. This module is
the ONE door the desktop asks: it resolves the mandate against AIDream as the
signed-in person in their active organization, stores that answer verbatim
(SQLite ``mandate_resolutions``), and loads the Holder's execution definition
through the existing fetch-through cache (``LocalExecutionAgentSource`` over
``agent_execution_definitions``). With the network down it serves the LAST
answer the platform gave, flagged stale with the reason — never a prompt that
lives in code, and never a silent substitute.

What it refuses (the platform's word is final when it answers):
  * AIDream answers 401/403/404/409/422 for the mandate → that refusal is
    passed through; a cached answer is NOT served over it.
  * Nothing was ever resolved on this device and the platform is unreachable
    → 503 naming the mandate and what to do.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, status

from app.services.ai.agent_source import get_execution_agent_source
from app.services.aidream.client import (
    AIDreamError,
    AIDreamOfflineError,
    get_aidream_client,
)
from app.services.aidream.organization import (
    OrganizationNotResolvedError,
    get_device_organization,
    resolve_active_organization_id,
)
from app.services.local_db.repositories import MandateResolutionsRepo, TokenRepo

logger = logging.getLogger(__name__)

#: AIDream statuses that are the platform's own answer about this mandate for
#: this caller. Serving a cached resolution over one of these would run an
#: agent the platform just refused.
_AUTHORITATIVE_REFUSALS = frozenset({401, 403, 404, 409, 422})


def _resolution_path(mandate_key: str) -> str:
    return f"/mandates/{quote(mandate_key, safe='')}/resolution"


def _checked_resolution(mandate_key: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "mandate_resolution_invalid",
                "message": f"AI Matrx answered {mandate_key} with a non-object resolution.",
            },
        )
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "mandate_resolution_invalid",
                "message": f"AI Matrx's resolution of {mandate_key} names no agent.",
            },
        )
    if not isinstance(payload.get("is_version"), bool):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "mandate_resolution_invalid",
                "message": f"AI Matrx's resolution of {mandate_key} has no is_version flag.",
            },
        )
    return payload


class LocalMandateResolver:
    def __init__(
        self,
        repo: MandateResolutionsRepo | None = None,
        token_repo: TokenRepo | None = None,
    ) -> None:
        self._repo = repo or MandateResolutionsRepo()
        self._token_repo = token_repo or TokenRepo()

    async def _fresh(
        self, mandate_key: str, jwt: str, user_id: str
    ) -> tuple[dict[str, Any], str, str]:
        """Ask AIDream. Returns (resolution, organization_id, fetched_at)."""
        client = get_aidream_client()
        if client is None:
            raise AIDreamOfflineError("the AI Matrx server URL is not configured")
        organization_id = await resolve_active_organization_id(jwt)
        payload = await client.get(
            _resolution_path(mandate_key),
            jwt=jwt,
            headers={"X-Organization-Id": organization_id},
        )
        resolution = _checked_resolution(mandate_key, payload)
        fetched_at = await self._repo.upsert(
            mandate_key,
            user_id=user_id,
            organization_id=organization_id,
            resolution=resolution,
        )
        return resolution, organization_id, fetched_at

    async def resolve(self, mandate_key: str) -> dict[str, Any]:
        token = await self._token_repo.get()
        jwt: str | None = None
        if token and not self._token_repo.is_expired(token):
            raw = token.get("access_token")
            jwt = raw if isinstance(raw, str) and raw else None
        user_id = (token or {}).get("user_id") or await self._token_repo.get_owner_user_id()
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "authentication_required",
                    "message": (
                        f"Sign in to AI Matrx — {mandate_key}'s instructions come from "
                        "your account, and this device has never been signed in."
                    ),
                },
            )

        why_stale: str | None = None
        if jwt is None:
            why_stale = "you are signed out or your session has expired"
        else:
            try:
                resolution, organization_id, fetched_at = await self._fresh(
                    mandate_key, jwt, str(user_id)
                )
            except AIDreamOfflineError as exc:
                why_stale = f"AI Matrx is unreachable ({exc})"
            except OrganizationNotResolvedError as exc:
                why_stale = f"your organization could not be confirmed ({exc})"
            except AIDreamError as exc:
                if exc.status in _AUTHORITATIVE_REFUSALS:
                    raise HTTPException(
                        status_code=exc.status,
                        detail={
                            "code": "mandate_refused",
                            "message": f"AI Matrx refused {mandate_key}: {exc}",
                        },
                    ) from exc
                why_stale = f"AI Matrx answered HTTP {exc.status}"
            else:
                return await self._with_definition(
                    mandate_key, resolution, fetched_at, stale_reason=None
                )

        organization_id = await get_device_organization(str(user_id))
        cached = (
            await self._repo.get(
                mandate_key, user_id=str(user_id), organization_id=organization_id
            )
            if organization_id
            else None
        )
        if cached is None or not isinstance(cached.get("resolution_json"), dict):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "mandate_unavailable_offline",
                    "message": (
                        f"{mandate_key} has never been resolved on this device, and {why_stale}. "
                        "Connect to AI Matrx once so this device learns which instructions to run; "
                        "after that it keeps working offline."
                    ),
                },
            )
        logger.warning(
            "[local_mandates] serving the cached resolution of %s from %s — %s",
            mandate_key,
            cached.get("fetched_at"),
            why_stale,
        )
        return await self._with_definition(
            mandate_key,
            cached["resolution_json"],
            str(cached.get("fetched_at") or ""),
            stale_reason=why_stale,
        )

    async def _with_definition(
        self,
        mandate_key: str,
        resolution: dict[str, Any],
        fetched_at: str,
        *,
        stale_reason: str | None,
    ) -> dict[str, Any]:
        definition = await get_execution_agent_source().load_for_execution(
            str(resolution["agent_id"]),
            is_version=bool(resolution["is_version"]),
        )
        return {
            "mandate_key": mandate_key,
            "resolution": resolution,
            "definition": definition.model_dump(mode="json"),
            "resolved_at": fetched_at,
            "stale": stale_reason is not None,
            "stale_reason": stale_reason,
        }


_instance: LocalMandateResolver | None = None


def get_local_mandate_resolver() -> LocalMandateResolver:
    global _instance
    if _instance is None:
        _instance = LocalMandateResolver()
    return _instance


__all__ = ["LocalMandateResolver", "get_local_mandate_resolver"]
