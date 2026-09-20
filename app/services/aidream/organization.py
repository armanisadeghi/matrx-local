"""The ONE Python-side resolver for "which organization does this call act
in" — mirrors the desktop TS resolver (``desktop/src/lib/org/active-org.ts``)
and the platform-canonical rule (matrx-extend ``src/lib/org/active-org.ts``).

aidream's AuthMiddleware refuses every authenticated request that names no
organization (400 ``organization_required``) before it routes, and it will
NEVER pick one for the caller — a server that guesses is exactly how work
lands in the wrong tenant. The caller states it; the server only verifies
membership.

## The ruling this module enforces (Arman, 2026-09-19)

A "default organization" is at most a per-client DISPLAY preference. Nothing
that builds a request may read the user-level saved preference
(``users.user_preferences -> organization.defaultOrganizationId``), and
nothing may fall back to the personal organization. Both rungs used to live
here and both are gone.

    "one missed org check that should have just failed turns into 50 in a
    month and 5,000 in a year, and suddenly we don't have orgs any more, we
    have a user and a default org, which means we just have user now."

What a client MAY remember is the organization the USER THEMSELVES SET on
this device. That is the little picker's state, not a preference read out of
their account.

## Resolution order

    1. THIS DEVICE'S SET organization — the id the user chose in the desktop
       picker, which the desktop pushes to the engine (``PUT
       /organization/active``) and this module reads back out of the local
       app-settings row. Honoured only when it belongs to the SAME user and
       is still one of their live memberships.
    2. Exactly ONE active membership -> that organization. There is nothing
       to choose, so choosing it invents nothing.
    3. Nothing. The call is HELD, not guessed: this module publishes an
       ``organization_required`` action-needed item, the desktop shell turns
       that into the picker, the user sets one, and the caller retries with
       the set value.

The sidecar cannot show UI itself, which is why step 3 goes through the
action-needed registry (``app/services/action_needed/``) — the same durable
channel every other blocked-on-the-user operation uses. It survives a
desktop reconnect (the registry replays snapshots) and it is what a
background job with no window attached leaves behind instead of guessing.
"""

from __future__ import annotations

from typing import Any

# Cached per JWT subject. Cleared whenever the device's set organization
# changes, so a switch takes effect on the next call rather than the next
# engine restart.
_org_cache: dict[str, str] = {}

# The local app-settings key holding this device's set organization. Local
# SQLite on purpose: this is a per-DEVICE choice, so it must never ride the
# cloud settings sync (that would turn one device's pick into every device's
# pick, which is the "default organization" this ruling abolished).
DEVICE_ORGANIZATION_SETTING = "active_organization"

#: The one operation key the organization hold reconciles under.
_HOLD_OPERATION_KEY = "organization:required"


def _jwt_sub(jwt_value: str) -> str | None:
    try:
        import jwt as pyjwt

        claims = pyjwt.decode(jwt_value, options={"verify_signature": False})
        sub = claims.get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None


def jwt_user_id(jwt_value: str) -> str | None:
    """The caller's user id from an unverified JWT — the public name for
    ``_jwt_sub``, so other services can ask "is this the same caller?" without
    reaching into a private helper."""
    return _jwt_sub(jwt_value)


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


# ----------------------------------------------------------------------
# This device's SET organization
# ----------------------------------------------------------------------


async def get_device_organization(user_id: str | None = None) -> str | None:
    """The organization the user SET on this device, or ``None``.

    Stored with the user id that set it, so a second account signing in on
    the same Mac never inherits the first account's pick. Never raises — a
    store we cannot read simply has nothing set.
    """
    try:
        from app.services.local_db.repositories import AppSettingsRepo

        stored = await AppSettingsRepo().get(DEVICE_ORGANIZATION_SETTING)
    except Exception:
        return None
    if not isinstance(stored, dict):
        return None
    organization_id = stored.get("organization_id")
    if not isinstance(organization_id, str) or not organization_id:
        return None
    owner = stored.get("user_id")
    if user_id is not None and isinstance(owner, str) and owner and owner != user_id:
        return None
    return organization_id


async def set_device_organization(organization_id: str, *, user_id: str) -> None:
    """Record the organization the user SET in the desktop picker.

    The desktop is the only writer: it owns the picker, and this is the value
    every background job on this Mac acts under until the user changes it.
    """
    from app.services.local_db.repositories import AppSettingsRepo

    await AppSettingsRepo().set(
        DEVICE_ORGANIZATION_SETTING,
        {"organization_id": organization_id, "user_id": user_id},
    )
    _org_cache.clear()
    await _clear_hold()


async def clear_device_organization() -> None:
    """Forget this device's pick (sign-out)."""
    from app.services.local_db.repositories import AppSettingsRepo

    await AppSettingsRepo().set(DEVICE_ORGANIZATION_SETTING, None)
    _org_cache.clear()


def invalidate_organization_cache() -> None:
    """Drop the memoised answers — used by tests and by sign-out."""
    _org_cache.clear()


# ----------------------------------------------------------------------
# The hold
# ----------------------------------------------------------------------


async def _raise_hold(user_id: str | None) -> None:
    """Publish the ask, so the desktop shell can show the picker."""
    try:
        from app.services.action_needed.models import organization_required_needed
        from app.services.action_needed.registry import get_action_needed_registry

        await get_action_needed_registry().reconcile_operation(
            _HOLD_OPERATION_KEY,
            organization_required_needed(
                feature="AI Matrx",
                source="aidream.organization",
                user_id=user_id,
            ),
        )
    except Exception:
        # Publishing the ask must never replace the caller's own refusal —
        # the raised OrganizationNotResolvedError still carries the remedy.
        pass


async def _clear_hold() -> None:
    try:
        from app.services.action_needed.registry import get_action_needed_registry

        await get_action_needed_registry().reconcile_operation(_HOLD_OPERATION_KEY, None)
    except Exception:
        pass


class OrganizationNotResolvedError(RuntimeError):
    """Raised when this device has no organization set — never a guess.

    Carries ``remedy``, a plain-language string a UI can show verbatim, and
    ``held``: True when the ask has been published and the operation is
    waiting on the user rather than broken.

    It is a ``RuntimeError`` on purpose. Transport clients used to flatten it
    into a bare ``RuntimeError`` to keep their own callers' ``except``
    clauses working, and flattening threw away exactly the two facts a screen
    needs — ``held`` and ``remedy`` — which is how a held operation ended up
    reading as a generic failure. Sharing the base class means the typed error
    can travel all the way to the surface without anyone losing their
    handler.
    """

    def __init__(self, message: str, *, remedy: str, held: bool = False) -> None:
        super().__init__(message)
        self.remedy = remedy
        self.held = held


#: A held operation is WAITING ON ONE CLICK, not broken. Two codes, because a
#: screen that says the same thing about both teaches the person to distrust
#: the one it can actually fix.
ORGANIZATION_HELD_CODE = "organization_required"
ORGANIZATION_BLOCKED_CODE = "no_organization"

#: The action-needed handler the desktop registers for the picker. A refusal
#: NAMES it rather than leaving each screen to match a code string: the three
#: coding-session surfaces each hardcoded their own ``code ==`` branch, so a
#: new lane's held refusal shipped with no button at all (law 4).
CHOOSE_ORGANIZATION_ACTION = "choose_organization"

_HELD_MESSAGE = (
    "Waiting for you to choose an organization. Nobody has told this Mac "
    "which organization to work in yet, and AI Matrx will not guess. Nothing "
    "is lost — this continues by itself once you choose."
)
_HELD_REMEDY = (
    "Choose your organization in Matrx Local. The work resumes on its own "
    "within a few seconds."
)


def organization_refusal(exc: OrganizationNotResolvedError) -> dict[str, Any]:
    """THE one description of an unresolved organization.

    Every sidecar client renders its refusal from here, so a held operation
    says the same sentence — and offers the same one-click action — whether it
    came from file sync, the scraper, the Vault, delegation or the
    coding-session lanes. A lane that writes its own text drifts out of one of
    the two states, which is the census this helper exists to end.
    """
    if exc.held:
        return {
            "code": ORGANIZATION_HELD_CODE,
            "message": _HELD_MESSAGE,
            "remedy": _HELD_REMEDY,
            "action": CHOOSE_ORGANIZATION_ACTION,
            "held": True,
        }
    return {
        "code": ORGANIZATION_BLOCKED_CODE,
        "message": str(exc),
        "remedy": exc.remedy,
        "action": None,
        "held": False,
    }


def organization_refusal_text(exc: OrganizationNotResolvedError) -> str:
    """The same refusal as one sentence, for transports that carry only text."""
    refusal = organization_refusal(exc)
    return f"{refusal['message']} {refusal['remedy']}"


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

    # READ THIS MAC'S ANSWER FIRST. The membership lookup is a network call,
    # and a question the user has already answered on this device must not be
    # re-asked because Supabase blinked: the SERVER is the referee on
    # membership (it refuses an organization the caller is not in), so sending
    # the set value and letting it verify is both safe and the only behaviour
    # that does not throw a picker at somebody who already chose.
    device_choice = await get_device_organization(user_id)

    try:
        memberships = await _active_memberships(jwt_value)
    except Exception as exc:  # noqa: BLE001 — mapped to a renderable state
        if device_choice:
            _org_cache[cache_key] = device_choice
            await _clear_hold()
            return device_choice
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

    # 1. What the user SET on this device (read above, before the network).
    if device_choice and device_choice in by_id:
        _org_cache[cache_key] = device_choice
        await _clear_hold()
        return device_choice

    # 2. Exactly one membership — nothing to choose.
    if len(by_id) == 1:
        (only_id,) = by_id.keys()
        _org_cache[cache_key] = only_id
        await _clear_hold()
        return only_id

    # 3. Hold. Ask, and let the caller retry once the user has set one.
    await _raise_hold(user_id)
    raise OrganizationNotResolvedError(
        "This Mac has no organization chosen yet.",
        remedy="Choose your organization in Matrx Local, then this will continue.",
        held=True,
    )
