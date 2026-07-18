"""Canonical user-remediation contract.

Feature modules remain the authority for detecting a blocked operation; this
package only standardises how that requirement crosses process boundaries.
"""

from .models import (
    ActionNeeded,
    ActionNeededAction,
    ActionNeededKind,
    ActionNeededStatus,
    filesystem_access_needed,
    download_resolution_needed,
    os_permission_needed,
)

__all__ = [
    "ActionNeeded",
    "ActionNeededAction",
    "ActionNeededKind",
    "ActionNeededStatus",
    "filesystem_access_needed",
    "download_resolution_needed",
    "os_permission_needed",
]
