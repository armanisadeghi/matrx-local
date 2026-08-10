"""Real-engine hook-ingress smoke tests for the loopback/tunnel boundary."""

from __future__ import annotations

import uuid

import httpx


def _envelope() -> dict:
    return {
        "schema_version": 1,
        "action": "observe_hook",
        "provider": "codex",
        "provider_session_id": f"smoke-{uuid.uuid4()}",
        "origin": "independent_hook",
        "stream_key": "main",
        "hook_event": {
            "name": "Stop",
            "payload": {"last_assistant_message": "smoke"},
        },
    }


def test_loopback_hook_is_acknowledged_only_as_durably_persisted(
    http_public: httpx.Client,
) -> None:
    response = http_public.post("/coding-session/hooks", json=_envelope())
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["schema_version"] == 1
    assert body["accepted"] is True
    assert body["persisted"] is True
    assert isinstance(body["receipt_id"], int) and body["receipt_id"] > 0
    assert body["pending"] >= 1


def test_bridge_publisher_is_launcher_managed(http_public: httpx.Client) -> None:
    response = http_public.get("/admin/status")
    assert response.status_code == 200, response.text
    service = response.json()["services"]["coding_session_bridge"]
    assert service["state"] == "ready"
    assert service["metadata"]["ingress"] == "/coding-session/hooks"
    assert service["metadata"]["upstream"] == "/api/coding-sessions/bridge"


def test_hook_ingress_hard_rejects_tunnel_even_without_auth(
    http_public: httpx.Client,
) -> None:
    response = http_public.post(
        "/coding-session/hooks",
        json=_envelope(),
        headers={"Cf-Ray": "test-ray", "Cf-Connecting-Ip": "203.0.113.7"},
    )
    assert response.status_code == 403, response.text


def test_hook_ingress_rejects_non_hook_actions(http_public: httpx.Client) -> None:
    payload = _envelope()
    payload.pop("hook_event")
    payload["action"] = "health"
    payload["provider_session_id"] = None
    response = http_public.post("/coding-session/hooks", json=payload)
    assert response.status_code == 422, response.text


def test_hook_ingress_rejects_unknown_fields(http_public: httpx.Client) -> None:
    payload = _envelope()
    payload["user_id"] = "caller-cannot-assert-owner"
    response = http_public.post("/coding-session/hooks", json=payload)
    assert response.status_code == 422, response.text
