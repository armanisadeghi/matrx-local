"""Loopback routes for user-review delegated calls, against a REAL engine.

`google_email_send` has no server executor anywhere in the platform — that
absence is the Gmail authorization boundary. On this surface the engine parks
the call and the desktop card is the authorization, so these routes are the
only way a decision can exist. They are behind the same auth gate as the rest
of the delegation surface: loopback is not a credential.
"""

from __future__ import annotations

import uuid

import httpx


def test_reviews_and_decision_require_bearer(
    http: httpx.Client, http_public: httpx.Client
) -> None:
    assert http_public.get("/chat/delegation/reviews").status_code == 401
    assert (
        http_public.post(
            "/chat/delegation/review-decision",
            json={"call_id": str(uuid.uuid4()), "outcome": "declined"},
        ).status_code
        == 401
    )

    listed = http.get("/chat/delegation/reviews")
    assert listed.status_code == 200
    body = listed.json()
    assert isinstance(body["reviews"], list)
    assert body["count"] == len(body["reviews"])


def test_unknown_call_id_cannot_be_resolved(http: httpx.Client) -> None:
    """No fabricated decision can ever produce a tool result."""
    response = http.post(
        "/chat/delegation/review-decision",
        json={"call_id": str(uuid.uuid4()), "outcome": "sent", "message_id": "x"},
    )
    assert response.status_code == 404


def test_unknown_outcome_is_rejected(http: httpx.Client) -> None:
    response = http.post(
        "/chat/delegation/review-decision",
        json={"call_id": str(uuid.uuid4()), "outcome": "user_confirmed"},
    )
    assert response.status_code == 422


def test_delegation_status_reports_the_review_queue(http: httpx.Client) -> None:
    counts = http.get("/chat/delegation/status").json()["counts"]
    assert counts["awaiting_user_review"] == 0
