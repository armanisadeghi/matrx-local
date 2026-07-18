"""Canonical filesystem access-health authority.

Public facade — everything outside this package imports from here:

    from app.services.access_health import (
        AccessDeniedError, Capability, Provenance, get_access_health,
    )
"""

from app.services.access_health.models import (
    AccessDeniedError,
    Capability,
    Observation,
    Provenance,
    ResourceHealth,
    is_permission_error,
)
from app.services.access_health.probes import (
    capability_probe,
    diagnose_fda,
    fda_hint,
)
from app.services.access_health.service import (
    AccessHealthService,
    get_access_health,
)

__all__ = [
    "AccessDeniedError",
    "AccessHealthService",
    "Capability",
    "Observation",
    "Provenance",
    "ResourceHealth",
    "capability_probe",
    "diagnose_fda",
    "fda_hint",
    "get_access_health",
    "is_permission_error",
]
