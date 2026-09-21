"""Canonical user-remediation contract.

Feature modules remain the authority for detecting a blocked operation; this
package only standardises how that requirement crosses process boundaries.
"""

from .models import (
    ActionNeeded,
    ActionNeededAction,
    ActionNeededChoice,
    ActionNeededKind,
    ActionNeededStatus,
    capability_install_needed,
    coding_session_organization_needed,
    filesystem_access_needed,
    download_resolution_needed,
    organization_required_needed,
    os_permission_needed,
)

__all__ = [
    "ActionNeeded",
    "ActionNeededAction",
    "ActionNeededChoice",
    "ActionNeededKind",
    "ActionNeededStatus",
    "capability_install_needed",
    "coding_session_organization_needed",
    "filesystem_access_needed",
    "download_resolution_needed",
    "organization_required_needed",
    "os_permission_needed",
]
