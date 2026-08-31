"""The ONE Python-side resolver for "which organization does this call act
in" — mirrors the desktop TS resolver (``desktop/src/lib/org/active-org.ts``)
and the platform-canonical rule (matrx-extend ``src/lib/org/active-org.ts`` /
matrx-frontend ``lib/organizations/resolveActiveOrgContext.ts``).

aidream's AuthMiddleware refuses every authenticated request that names no
organization (400 ``organization_required``) before it routes, and it will
NEVER pick one for the caller — a server that guesses is exactly how work
lands in the wrong tenant. The caller states it; the server only verifies
membership.

Resolution order — deliberately NOT "owner-or-oldest membership" (that WAS
the guess this module replaces):

    1. The user's durable default-organization preference
       (``users.user_preferences`` -> ``organization.defaultOrganizationId``)
       — IF they are still a member.
    2. Exactly ONE active membership -> that organization (there is nothing
       to choose, so choosing it invents nothing).
    3. Otherwise: refuse with ``VaultUnavailable("no_organization", ...)``
       naming the remedy. Never "first", "owner", "oldest", or "most
       recent".

There is deliberately no "this device's stored selection" step here (unlike
the TS resolver): that selection lives in the desktop UI's own local
storage, a browser-only concept this background Python service has no
access to. The default-preference and sole-membership rules above are
shared with every other surface (desktop UI, matrx-extend, matrx-frontend)
because they read the SAME `users.user_preferences` row and the SAME
membership table — so the common case (a user with one org, or a stated
default) resolves identically everywhere. A user with several orgs and no
stated default gets asked to set one via the desktop UI's organization
picker; this module does not pick for them.
"""

from __future__ import annotations

from typing import Any

_org_cache: dict[str, str] = {}


def _jwt_sub(jwt_value: str) -> str | None:
    try:
        import jwt as pyjwt

        claims = pyjwt.decode(jwt_value, options={"verify_signature": False})
        sub = claims.get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None


async def _active_memberships(jwt_value: str) -> list[dict[str, Any]]:
    """Every organization the caller is an active member of, via the
    canonical ``mbr_for_user`` RPC — never re-derived from a junction table."""
    import httpx

    from app.config import (
        SUPABASE_PROFILE_HEADERS,
        SUPABASE_PUBLISHABLE_KEY,
        SUPABASE_URL,
    )

    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/rpc/mbr_for_user"
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as http:
        resp = await http.post(
            url,
            json={"p_container_type": "organization"},
            headers={
                "apikey": SUPABASE_PUBLISHABLE_KEY,
                **SUPABASE_PROFILE_HEADERS,
                "Authorization": f"Bearer {jwt_value}",
                "Content-Type": "application/json",
            },
        )
    resp.raise_for_status()
    rows = resp.json()
    return [
        row
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
        and row.get("status") == "active"
        and isinstance(row.get("container_id"), str)
        and row.get("container_id")
    ]


async def _default_organization_id(jwt_value: str, user_id: str) -> str | None:
    """The user's durable default-organization preference, read straight
    from ``users.user_preferences`` (the same row the web app, the extension,
    and the desktop UI's own resolver write). Never raises — a preference we
    cannot read simply does not participate in resolution."""
    import httpx

    from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL

    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/user_preferences"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as http:
            resp = await http.get(
                url,
                params={"select": "preferences", "user_id": f"eq.{user_id}"},
                headers={
                    "apikey": SUPABASE_PUBLISHABLE_KEY,
                    "Accept-Profile": "users",
                    "Authorization": f"Bearer {jwt_value}",
                },
            )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    preferences = rows[0].get("preferences") if isinstance(rows[0], dict) else None
    if not isinstance(preferences, dict):
        return None
    organization = preferences.get("organization")
    if not isinstance(organization, dict):
        return None
    default_id = organization.get("defaultOrganizationId")
    return default_id if isinstance(default_id, str) and default_id else None


class OrganizationNotResolvedError(Exception):
    """Raised when no organization resolves for this caller — never a guess.

    Carries ``remedy``, a plain-language string a UI can show verbatim.
    """

    def __init__(self, message: str, *, remedy: str) -> None:
        super().__init__(message)
        self.remedy = remedy


async def resolve_active_organization_id(jwt_value: str) -> str:
    """The organization id to send as ``X-Organization-Id``.

    Raises ``OrganizationNotResolvedError`` rather than guess — the caller
    maps that onto whatever refusal shape its own layer already uses (e.g.
    ``credential_vault.client.VaultUnavailable("no_organization", ...)``).
    """
    cache_key = _jwt_sub(jwt_value) or "?"
    cached = _org_cache.get(cache_key)
    if cached:
        return cached

    user_id = _jwt_sub(jwt_value)
    try:
        memberships = await _active_memberships(jwt_value)
    except Exception as exc:  # noqa: BLE001 — mapped to a renderable state
        raise OrganizationNotResolvedError(
            f"Could not resolve your organization membership ({exc}).",
            remedy="Check your connection and try again.",
        ) from exc

    if not memberships:
        raise OrganizationNotResolvedError(
            "Your account has no active organization membership.",
            remedy="You need to be added to an organization before this will work.",
        )

    by_id = {str(row["container_id"]): row for row in memberships}

    if user_id:
        preferred = await _default_organization_id(jwt_value, user_id)
        if preferred and preferred in by_id:
            _org_cache[cache_key] = preferred
            return preferred

    if len(by_id) == 1:
        (only_id,) = by_id.keys()
        _org_cache[cache_key] = only_id
        return only_id

    raise OrganizationNotResolvedError(
        "You belong to more than one organization and haven't set a default.",
        remedy="Choose your organization in the desktop app, then try again.",
    )
