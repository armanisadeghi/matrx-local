"""Cloud tool-call delegation client (suspend/resume, headless).

See FEATURE.md in this directory for the doctrine. Public surface:

    from app.services.delegation import get_delegation_engine
"""

from app.services.delegation.engine import DelegationEngine, get_delegation_engine

__all__ = ["DelegationEngine", "get_delegation_engine"]
