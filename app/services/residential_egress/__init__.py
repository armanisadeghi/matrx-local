"""Residential egress — this computer as the internet exit, when we get blocked.

Contract (the ONE source of truth for all five repos):
``common-docs/systems/platform/residential-egress/FEATURE.md``.
"""

from app.services.residential_egress.supervisor import (
    ResidentialEgressSupervisor,
    get_egress_supervisor,
)

__all__ = ["ResidentialEgressSupervisor", "get_egress_supervisor"]
