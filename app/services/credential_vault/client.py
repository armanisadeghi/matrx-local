"""HTTP client for aidream's Credential Vault (``/api/vault/*``).

Four calls, nothing more:

  * ``list_user_items()``        GET  /api/vault/items?principal_type=user
  * ``get_item(item_id)``        GET  /api/vault/items/{id}
  * ``reveal(item, field)``      POST /api/vault/items/{id}/reveal
  * ``resolve(refs)``            POST /api/vault/resolve

Reuses the EXISTING plumbing — no second HTTP client, no second auth path:

  * transport: ``app.services.aidream.client.get_aidream_client()`` (its base
    URL comes from remote app config; ``/api`` prefix is added there)
  * identity: ``app.services.scraper.auth_helper.get_active_user_token()``,
    the repo's one "signed-in user's valid JWT for a background call" helper.
    It lives under ``scraper/`` for historical reasons and has no scraper
    dependency; importing it is deliberate — copying it would create the
    second auth path this module must not have.

Security posture (CLAUDE.md § Security & configuration posture, rule 4):
these calls read the USER's own credentials with the USER's own session.
No dev-owned secret, service-role key, or signing secret is involved, and
none may ever be added here.

``sealed`` fields never cross this boundary — the server refuses them. Do not
add a code path that asks for one.

Unavailability is a STATE, not an error (rule 3). Every function raises
``VaultUnavailable`` with a machine-readable ``state`` so callers can render a
prompt instead of a failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.common.system_logger import get_logger

logger = get_logger()

# ``no_session``  — nobody is signed in (or the stored JWT expired)
# ``unconfigured``— no aidream server URL resolved from app config
# ``offline``     — the server could not be reached
# ``denied``      — the server answered 401/403 for this session
# ``error``       — any other non-2xx
VaultState = Literal["ready", "no_session", "unconfigured", "offline", "denied", "error"]


class VaultUnavailable(Exception):
    """The vault could not answer. Carries the state a UI should render."""

    def __init__(self, state: VaultState, message: str) -> None:
        super().__init__(message)
        self.state: VaultState = state
        self.message = message


@dataclass(frozen=True)
class VaultRef:
    """A stable pointer to one field of one vault item."""

    item_id: str
    field_key: str

    @property
    def resolve_key(self) -> str:
        """The key aidream returns values under: ``"{item_id}/{field_key}"``."""
        return f"{self.item_id}/{self.field_key}"


async def _session_jwt() -> str:
    from app.services.scraper.auth_helper import get_active_user_token

    token = await get_active_user_token()
    if not token:
        raise VaultUnavailable(
            "no_session",
            "Sign in to AI Matrx to use credentials from your Vault.",
        )
    return token


def _client() -> Any:
    from app.services.aidream.client import get_aidream_client

    client = get_aidream_client()
    if client is None:
        raise VaultUnavailable(
            "unconfigured",
            "No AI Matrx server URL is configured — Vault credentials are unavailable.",
        )
    return client


def _translate(exc: Exception) -> VaultUnavailable:
    """Map an aidream-client failure onto a renderable vault state."""
    from app.services.aidream.client import AIDreamError, AIDreamOfflineError

    if isinstance(exc, AIDreamOfflineError):
        return VaultUnavailable(
            "offline",
            "Can't reach AI Matrx — Vault credentials are unavailable right now.",
        )
    if isinstance(exc, AIDreamError):
        if exc.status in (401, 403):
            return VaultUnavailable(
                "denied",
                "Your AI Matrx session is not authorized for this credential. "
                "Sign in again and retry.",
            )
        return VaultUnavailable(
            "error", f"AI Matrx returned HTTP {exc.status} for a Vault request."
        )
    return VaultUnavailable("error", f"Vault request failed: {exc}")


async def list_user_items() -> list[dict[str, Any]]:
    """The signed-in user's own vault items (masked — no plaintext values).

    Organization-owned items are deliberately NOT fetched: matrx-local's key
    store is a personal, per-machine thing, and pulling an org credential onto
    a user's laptop is a sharing decision nobody made here.
    """
    jwt = await _session_jwt()
    client = _client()
    try:
        data = await client.get("/vault/items?principal_type=user", jwt=jwt)
    except VaultUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — mapped to a renderable state
        raise _translate(exc) from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise VaultUnavailable("error", "Vault returned an unexpected item list.")
    return [i for i in items if isinstance(i, dict)]


async def get_item(item_id: str) -> dict[str, Any]:
    """One vault item with its masked fields and the actor's capabilities."""
    jwt = await _session_jwt()
    client = _client()
    try:
        data = await client.get(f"/vault/items/{item_id}", jwt=jwt)
    except VaultUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    if not isinstance(data, dict):
        raise VaultUnavailable("error", "Vault returned an unexpected item.")
    return data


async def reveal(item_id: str, field_key: str) -> str:
    """Explicit reveal of ONE ``revealable`` field.

    Audited server-side and subject to the server's recent-auth policy. Use
    ``resolve`` for bulk/`visible` reads; use this when the user asked for one
    specific secret.
    """
    jwt = await _session_jwt()
    client = _client()
    try:
        data = await client.post(
            f"/vault/items/{item_id}/reveal", {"field_key": field_key}, jwt=jwt, timeout=30.0
        )
    except VaultUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    value = data.get("value") if isinstance(data, dict) else None
    if not isinstance(value, str):
        raise VaultUnavailable("error", "Vault reveal returned no value.")
    return value


async def resolve(refs: list[VaultRef]) -> dict[str, str]:
    """Resolve several fields in ONE round trip.

    Handling-aware on the server: ``visible`` at can_use, ``revealable`` at
    can_reveal (+ recent auth), ``sealed`` refused outright. Returns
    ``{"{item_id}/{field_key}": value}`` — a ref the server declined is simply
    absent from the mapping, never an error.
    """
    if not refs:
        return {}
    jwt = await _session_jwt()
    client = _client()
    payload = {"refs": [{"item_id": r.item_id, "field_key": r.field_key} for r in refs]}
    try:
        data = await client.post("/vault/resolve", payload, jwt=jwt, timeout=30.0)
    except VaultUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict):
        raise VaultUnavailable("error", "Vault resolve returned an unexpected payload.")
    return {k: v for k, v in values.items() if isinstance(k, str) and isinstance(v, str)}
