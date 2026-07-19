"""Auth boundary for the desktop-owned Cloud Chat continuation stream."""

from __future__ import annotations

import uuid

import httpx


def test_delegation_ui_claim_and_release_require_bearer(
    http: httpx.Client,
    http_public: httpx.Client,
) -> None:
    conversation_id = str(uuid.uuid4())
    body = {"conversation_id": conversation_id, "ttl_seconds": 20}

    unauthenticated_claim = http_public.post(
        "/chat/delegation/ui-claim",
        json=body,
    )
    assert unauthenticated_claim.status_code == 401

    claim = http.post("/chat/delegation/ui-claim", json=body)
    assert claim.status_code == 200
    assert claim.json()["claimed"] is True

    unauthenticated_release = http_public.post(
        "/chat/delegation/ui-release",
        json=body,
    )
    assert unauthenticated_release.status_code == 401

    release = http.post("/chat/delegation/ui-release", json=body)
    assert release.status_code == 200
    assert release.json() == {"released": True}
