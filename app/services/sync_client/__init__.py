"""The engine's client for the sync daemon's control API (FS-C5, SPEC-CUSTODY §6).

The Python engine is a **token consumer**, never a token holder. It asks `matrx-syncd` for a
short-lived access token and holds it in memory only; there is no refresh token anywhere in
Python, which is what makes the MXL-D-046 class — a UI-pushed token nothing headless can renew —
structurally impossible rather than merely fixed.

Public surface:

* :func:`get_sync_client` — the process-wide client.
* :class:`SyncDaemonClient.access_token` — a JWT, or ``None`` with a state.
* :class:`SyncDaemonClient.session` — the honest session state, for a surface to render.

Nothing here raises when the device is signed out, offline or the daemon is down. A missing
session is a **state with a remedy**, never an error (matrx-local's states-not-errors doctrine,
SPEC-CUSTODY S13).
"""

from app.services.sync_client.client import (
    SessionSnapshot,
    SyncDaemonClient,
    get_sync_client,
    reset_sync_client,
)

__all__ = [
    "SessionSnapshot",
    "SyncDaemonClient",
    "get_sync_client",
    "reset_sync_client",
]
