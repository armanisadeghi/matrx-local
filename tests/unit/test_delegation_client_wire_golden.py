"""Golden: DelegationApiClient puts exactly these identity headers, method, path, query and body on the wire.

Pinned against the hand-built httpx transport BEFORE it moved onto ``matrx_api`` and kept green after, so the switch
is provably a no-op on the wire. X-Request-Id is the transport's own and never an identity.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.delegation.client import DelegationApiClient, DelegationApiError

ORG = "3f2b8c1e-0a4d-4c55-9e0f-1a2b3c4d5e6f"
JWT = "user-jwt"
CONV = "conv-1"
INSTANCE = "11111111-2222-4333-8444-555555555555"
IDENTITY = {"authorization", "x-organization-id"}


def _identity(request: httpx.Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k in IDENTITY}


@pytest.fixture
def org(monkeypatch):
    from app.services.aidream import organization as org_module

    async def _org(_jwt):
        return ORG

    monkeypatch.setattr(org_module, "resolve_active_organization_id", _org)


def _client(handler) -> DelegationApiClient:
    return DelegationApiClient(
        "https://aidream.test/", transport=httpx.MockTransport(handler), instance_id_provider=lambda: INSTANCE
    )


@pytest.mark.anyio
async def test_pending_calls_is_a_get_with_instance_query_and_identity(org):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    assert await _client(handler).list_pending_calls(JWT) == []
    req = seen[0]
    assert req.method == "GET"
    assert req.url.path == "/ai/user/pending_calls" and req.url.host == "aidream.test"
    assert dict(req.url.params) == {"instance_id": INSTANCE}
    assert _identity(req) == {"authorization": f"Bearer {JWT}", "x-organization-id": ORG}
    assert req.headers["accept"].startswith("application/json")


@pytest.mark.anyio
async def test_tool_results_post_body_and_identity(org):
    seen: list[httpx.Request] = []
    results = [{"call_id": "c1", "output": "ok", "is_error": False}]

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={"resolved": ["c1"], "already_resolved": [], "not_found": [], "continuation_needed": False,
                  "user_request_id": "r", "conversation_id": CONV},
        )

    await _client(handler).post_tool_results(JWT, CONV, results)
    req = seen[0]
    assert (req.method, req.url.path) == ("POST", f"/ai/conversations/{CONV}/tool_results")
    assert json.loads(req.content) == {"results": results, "instance_id": INSTANCE}
    assert _identity(req) == {"authorization": f"Bearer {JWT}", "x-organization-id": ORG}


@pytest.mark.anyio
async def test_resume_streams_with_identity_and_409_is_a_conflict_outcome(org):
    seen: list[httpx.Request] = []

    def ok(request):
        seen.append(request)
        return httpx.Response(200, content=b'{"event":"end","data":{"reason":"complete"}}\n')

    outcome = await _client(ok).resume(JWT, CONV, {"a": 1})
    assert outcome.status == "streamed" and outcome.events_seen == 1
    req = seen[0]
    assert (req.method, req.url.path) == ("POST", f"/ai/conversations/{CONV}/resume")
    assert json.loads(req.content) == {"a": 1}
    assert _identity(req) == {"authorization": f"Bearer {JWT}", "x-organization-id": ORG}

    def conflict(request):
        return httpx.Response(409, json={"error": "resume_conflict", "message": "x", "details": {"retryable": True}})

    out = await _client(conflict).resume(JWT, CONV, {})
    assert out.status == "conflict" and out.conflict.code == "resume_conflict" and out.conflict.retryable is True


@pytest.mark.anyio
async def test_http_errors_become_delegation_api_errors_with_status(org):
    def handler(request):
        return httpx.Response(500, text="boom")

    with pytest.raises(DelegationApiError) as excinfo:
        await _client(handler).list_pending_calls(JWT)
    assert excinfo.value.status == 500 and "500" in str(excinfo.value)
    with pytest.raises(DelegationApiError) as excinfo:
        await _client(handler).resume(JWT, CONV, {})
    assert excinfo.value.status == 500
