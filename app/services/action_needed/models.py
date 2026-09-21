"""Wire models for a user-fixable blocked operation.

An action-needed item is state, not an exception or a transient toast.  The
source that detected it owns the stable fingerprint and must stop returning it
after a successful retry/recheck.  Clients key by ``fingerprint`` so the same
requirement arriving over REST, WebSocket, or a reconnect snapshot is shown
once.
"""

from __future__ import annotations

from enum import Enum
import time
from typing import Any

from pydantic import BaseModel, Field


class ActionNeededKind(str, Enum):
    OS_PERMISSION = "os_permission"
    FILESYSTEM_ACCESS = "filesystem_access"
    API_KEY = "api_key"
    EXTERNAL_APPROVAL = "external_approval"
    CAPABILITY_INSTALL = "capability_install"
    ORGANIZATION = "organization"


class ActionNeededStatus(str, Enum):
    ACTIVE = "active"
    CHECKING = "checking"
    RESOLVED = "resolved"


class ActionNeededChoice(BaseModel):
    """One option the person can pick to resolve an item — a picker row."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str | None = None


class ActionNeededAction(BaseModel):
    kind: str = Field(description="Stable dispatcher action kind")
    label: str
    permission_key: str | None = None
    provider: str | None = None
    route: str | None = None
    url: str | None = None
    resource_ids: list[str] | None = None
    #: A CHOICE the person makes to resolve the item, carried BY THE PRIMITIVE
    #: so any source can ask one (which organization, which account, which
    #: folder) without a one-off dialog. The desktop renders one button per
    #: choice and PUTs ``{"choice": <id>}`` to ``choice_route`` on the engine;
    #: the source that raised the item owns what happens next and stops
    #: returning the item once the choice took. ``None`` = no choice to make.
    choices: list[ActionNeededChoice] | None = None
    choice_route: str | None = None


class ActionNeeded(BaseModel):
    fingerprint: str = Field(
        min_length=3,
        description="Stable source-owned dedupe key; never a random notification id",
    )
    code: str = Field(min_length=2)
    kind: ActionNeededKind
    feature: str = Field(min_length=1)
    title: str = Field(min_length=1)
    message: str = Field(min_length=1)
    action: ActionNeededAction
    source: str = Field(min_length=1)
    status: ActionNeededStatus = ActionNeededStatus.ACTIVE
    observed_at: float = Field(default_factory=time.time)
    details: dict[str, Any] | None = None


def os_permission_needed(
    *,
    feature: str,
    permission_key: str,
    source: str,
    title: str | None = None,
    message: str | None = None,
    target: str | None = None,
) -> ActionNeeded:
    """Build the canonical request for one concrete operating-system grant.

    Detection stays read-only.  ``request_os_permission`` is deliberately an
    explicit UI action so a tool invocation cannot cause a native prompt or a
    Windows ConsentStore write behind the user's back.
    """

    label = permission_key.replace("_", " ").title()
    details = {"permission_key": permission_key}
    if target:
        details["target"] = target
    return ActionNeeded(
        fingerprint=(
            f"os-permission:{permission_key}:{feature}"
            + (f":{target}" if target else "")
        ),
        code=f"{permission_key}_required",
        kind=ActionNeededKind.OS_PERMISSION,
        feature=feature,
        title=title or f"{label} access is needed",
        message=message or f"Allow {label} access to use {feature}, then retry.",
        action=ActionNeededAction(
            kind="request_os_permission",
            label=f"Allow {label}",
            permission_key=permission_key,
            route=f"/devices?permission={permission_key}",
        ),
        source=source,
        details=details,
    )


def organization_required_needed(
    *, feature: str, source: str, user_id: str | None = None
) -> ActionNeeded:
    """The ask this Mac raises when nothing has chosen an organization yet.

    The sidecar cannot show UI, and it must not guess (Arman, 2026-09-19: a
    saved user-level "default organization" never participates in building a
    request, and the personal organization is not a fallback). So a call that
    finds no SET organization publishes this, the desktop shell turns it into
    the organization picker, the user sets one, and the caller retries with
    the set value.

    Deliberately NOT worded as an error: nothing is broken, the work is
    waiting on one choice. And deliberately never the words "default
    organization" — there is no such thing to set.
    """

    return ActionNeeded(
        fingerprint="organization:required",
        code="organization_required",
        kind=ActionNeededKind.ORGANIZATION,
        feature=feature,
        title="Choose your organization",
        message=(
            "This Mac hasn't been told which organization to work in yet. "
            "Choose one and the work that's waiting will continue."
        ),
        action=ActionNeededAction(
            kind="choose_organization",
            label="Choose organization",
        ),
        source=source,
        details={"user_id": user_id} if user_id else None,
    )


#: The one operation key + fingerprint the coding-session hold reconciles under.
CODING_SESSION_ORGANIZATION_FINGERPRINT = "coding-session:organization:required"
CODING_SESSION_ORGANIZATION_ACTION = "choose_coding_session_organization"
CODING_SESSION_ORGANIZATION_ROUTE = "/coding-session/connection/organization"


def coding_session_organization_needed(
    *,
    source: str,
    organizations: list[dict[str, Any]] | None,
    held_uploads: int | None = None,
) -> ActionNeeded:
    """The SERVER's hold on this account's coding sessions — a different
    question from ``organization_required_needed``.

    That one asks which organization THIS MAC works in. This one asks which
    organization the account's Claude Code / Codex sessions are FILED in on
    AI Matrx: the bridge carries no organization on the wire, so the server
    holds every upload (409 ``organization_required``) until the person sets
    it on the coding-session connection — and the server never picks (Arman,
    2026-09-19). Until 2026-09-20 there was no way to set it: Matrx Local
    retried every upload forever and registered no card (D1).

    ``organizations`` is the membership list the hold itself carried (the ONE
    hold shape: ``{id, name, abbreviation}``) and becomes the card's choices;
    ``None`` means the server could not read the list, and the card then says
    so and offers the retry instead of an empty picker.
    """

    choices = [
        ActionNeededChoice(
            id=str(org["id"]),
            label=str(org.get("name") or org["id"]),
            description=str(org["abbreviation"]) if org.get("abbreviation") else None,
        )
        for org in (organizations or [])
        if isinstance(org, dict) and org.get("id")
    ]
    waiting = (
        f"{held_uploads} upload{'s' if held_uploads != 1 else ''} are waiting"
        if held_uploads
        else "Every upload is waiting"
    )
    if choices:
        message = (
            f"AI Matrx needs to know which organization to file your coding "
            f"sessions in, and it will not guess. {waiting} on this Mac and "
            "nothing is lost. Pick an organization and delivery resumes by itself."
        )
        label = "Choose organization"
    else:
        message = (
            f"AI Matrx needs to know which organization to file your coding "
            f"sessions in, and it will not guess. {waiting} on this Mac and "
            "nothing is lost — but your organizations could not be listed just "
            "now. Retry delivery to ask again."
        )
        label = "Retry delivery"
    return ActionNeeded(
        fingerprint=CODING_SESSION_ORGANIZATION_FINGERPRINT,
        code="organization_required",
        kind=ActionNeededKind.ORGANIZATION,
        feature="Coding sessions",
        title="Your Claude Code sessions are waiting for an organization",
        message=message,
        action=ActionNeededAction(
            kind=CODING_SESSION_ORGANIZATION_ACTION,
            label=label,
            route="/coding-sessions",
            choices=choices or None,
            choice_route=CODING_SESSION_ORGANIZATION_ROUTE if choices else None,
        ),
        source=source,
        details={
            "set_on": "coding_session",
            "held_uploads": held_uploads,
        },
    )


def filesystem_access_needed(
    *, feature: str, path: str, operation: str, source: str
) -> ActionNeeded:
    """Build an evidence-only filesystem ask.

    A bare EACCES/EPERM is not proof that Full Disk Access is the remedy, so
    this deliberately routes to access diagnostics rather than making an FDA
    claim.
    """

    return ActionNeeded(
        fingerprint=f"filesystem:{feature}:{path}:{operation}",
        code="filesystem_access_denied",
        kind=ActionNeededKind.FILESYSTEM_ACCESS,
        feature=feature,
        title="This location needs access",
        message=(
            f"Matrx could not {operation} {path}. Check the location's access "
            "and retry; Full Disk Access is only suggested if diagnostics confirm it."
        ),
        action=ActionNeededAction(
            kind="recheck_filesystem_access",
            label="Check access",
            route="/settings?tab=storage",
        ),
        source=source,
        details={"path": path, "operation": operation},
    )


def capability_install_needed(
    *, feature: str, capability_id: str, source: str, message: str
) -> ActionNeeded:
    """Adapt a missing optional runtime into the one remediation contract."""
    label = capability_id.replace("_", " ").title()
    return ActionNeeded(
        fingerprint=f"capability:{capability_id}:{feature}",
        code="capability_install_required",
        kind=ActionNeededKind.CAPABILITY_INSTALL,
        feature=feature,
        title=f"{label} needs to be installed",
        message=message,
        action=ActionNeededAction(
            kind="install_capability",
            label=f"Install {label}",
            route=(
                "/settings?tab=capabilities&capability="
                f"{capability_id}"
            ),
        ),
        source=source,
        details={"capability_id": capability_id},
    )


def download_resolution_needed(
    resolution: Any,
    *,
    feature: str,
    source: str,
    resource_id: str | None = None,
) -> ActionNeeded:
    """Adapt the established download-resolution taxonomy to this wire model."""
    action_kind = str(resolution.action_kind)
    if action_kind == "settings_api_keys":
        kind = ActionNeededKind.API_KEY
        action = ActionNeededAction(
            kind="settings_api_keys",
            label=resolution.action_label,
            provider=resolution.provider,
            route=(
                f"/settings?tab=api-keys&provider={resolution.provider}"
                if resolution.provider
                else "/settings?tab=api-keys"
            ),
        )
    elif action_kind == "open_url":
        kind = ActionNeededKind.EXTERNAL_APPROVAL
        action = ActionNeededAction(
            kind="open_url",
            label=resolution.action_label,
            url=resolution.action_url,
        )
    else:
        kind = ActionNeededKind.CAPABILITY_INSTALL
        action = ActionNeededAction(
            kind="install_ai_packages",
            label=resolution.action_label,
            route="/media-generation",
        )
    suffix = f":{resource_id}" if resource_id else ""
    details = {"resource_id": resource_id} if resource_id else None
    return ActionNeeded(
        fingerprint=f"{source}:{resolution.code}{suffix}",
        code=resolution.code,
        kind=kind,
        feature=feature,
        title=resolution.title,
        message=resolution.message,
        action=action,
        source=source,
        details=details,
    )
