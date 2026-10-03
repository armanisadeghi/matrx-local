"""The bridge envelope rules every Claude history client shares.

Moved here from Matrx Local (``coding_sessions/service.py`` and ``title_sync.py``) so the
importer and every app that delivers its envelopes apply ONE rule: the metadata-plane event name,
the label observation, and the proof that a 2xx answer really accepted what was sent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from matrx_coding_history.bridge import BridgeRequest

# The metadata-plane hook name. It carries provider-authored session labels
# (title, workspace, branch, worktree, archived) rather than transcript
# content, so the server applies it to an EXISTING binding of either fidelity
# and settles an unbound session with accepted=0 instead of minting one.
SESSION_METADATA_EVENT = "SessionMetadata"

#: The server door every envelope is delivered to.
BRIDGE_PATH = "/coding-sessions/bridge"


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def session_metadata_request(
    *,
    provider_session_id: str,
    provider_project_key: str | None,
    payload: dict[str, Any],
) -> BridgeRequest:
    """One metadata-plane observation for an existing Claude binding."""
    envelope: dict[str, Any] = {
        "action": "observe_hook",
        "provider": "claude_code",
        "provider_session_id": provider_session_id,
        "origin": "independent_hook",
        "hook_event": {
            "name": SESSION_METADATA_EVENT,
            "stable_event_id": f"session-metadata:{payload_digest(payload)[:48]}",
            "payload": payload,
        },
    }
    if provider_project_key:
        envelope["provider_project_key"] = provider_project_key
    return BridgeRequest.model_validate(envelope)


def validate_acknowledgement(
    response: Any,
    request: BridgeRequest,
    *,
    error: Callable[[int, str], Exception],
) -> None:
    """Prove a 2xx body durably accepted exactly this one hook event.

    A reverse proxy, stale server, or accidentally remounted route can return
    JSON with HTTP 2xx without committing the bridge entry. Deleting the local
    outbox row on that weak signal would turn a deployment mistake into data
    loss, so the response must satisfy the frozen BridgeResponse v1 receipt.

    ``error(status, message)`` builds what is raised, so each caller keeps its
    own exception type (Matrx Local's outbox raises ``AIDreamError(502, ...)``).
    """

    def _count(name: str) -> int:
        value = response.get(name) if isinstance(response, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise error(502, f"bridge acknowledgement has invalid {name}")
        return value

    if not isinstance(response, dict):
        raise error(502, "bridge acknowledgement is not a JSON object")
    expected = {
        "schema_version": 1,
        "action": request.action.value,
        "provider": request.provider.value,
    }
    hook_event = request.hook_event
    is_session_metadata = (
        request.action.value == "observe_hook"
        and hook_event is not None
        and hook_event.name == SESSION_METADATA_EVENT
    )
    if is_session_metadata:
        # A label update lands on an existing binding of EITHER fidelity, and
        # an unmirrored local session settles with accepted=0 and no session
        # identity — that is a durable "nothing to update here", not a failure
        # to retry forever.
        for field, value in expected.items():
            if response.get(field) != value:
                raise error(
                    502,
                    f"bridge acknowledgement {field} did not match request",
                )
        accepted = _count("accepted")
        duplicates = _count("duplicates")
        if _count("conflicts") != 0:
            raise error(
                502,
                "bridge acknowledgement did not account for every submitted entry",
            )
        if accepted == 0 and duplicates == 0:
            return
        if accepted + duplicates != 1:
            raise error(
                502,
                "bridge acknowledgement did not account for every submitted entry",
            )
        if response.get("fidelity") not in {"native", "event_mirror"}:
            raise error(502, "bridge acknowledgement has invalid fidelity")
        for field in ("session_id", "conversation_id"):
            try:
                UUID(str(response.get(field)))
            except (TypeError, ValueError, AttributeError) as exc:
                raise error(
                    502,
                    f"bridge acknowledgement has invalid {field}",
                ) from exc
        return
    if request.action.value == "observe_hook":
        expected["fidelity"] = "event_mirror"
    for field, value in expected.items():
        if response.get(field) != value:
            raise error(
                502,
                f"bridge acknowledgement {field} did not match request",
            )
    for field in ("session_id", "conversation_id"):
        try:
            UUID(str(response.get(field)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise error(
                502,
                f"bridge acknowledgement has invalid {field}",
            ) from exc
    accepted = _count("accepted")
    duplicates = _count("duplicates")
    conflicts = _count("conflicts")
    expected_count = (
        1 if request.action.value == "observe_hook" else len(request.entries)
    )
    if request.action.value == "append_native" and response.get("fidelity") not in {
        "native",
        "event_mirror",
    }:
        raise error(502, "bridge acknowledgement has invalid import fidelity")
    if conflicts != 0 or accepted + duplicates != expected_count:
        raise error(
            502,
            "bridge acknowledgement did not account for every submitted entry",
        )


__all__ = [
    "BRIDGE_PATH",
    "SESSION_METADATA_EVENT",
    "payload_digest",
    "session_metadata_request",
    "validate_acknowledgement",
]
