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


class ActionNeededStatus(str, Enum):
    ACTIVE = "active"
    CHECKING = "checking"
    RESOLVED = "resolved"


class ActionNeededAction(BaseModel):
    kind: str = Field(description="Stable dispatcher action kind")
    label: str
    permission_key: str | None = None
    provider: str | None = None
    route: str | None = None
    url: str | None = None
    resource_ids: list[str] | None = None


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
