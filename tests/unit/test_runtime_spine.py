"""aidream's AuthMiddleware refuses every authenticated request that names no
organization (400 organization_required) before it routes. The runtime spine
(open/heartbeat/settle) is best-effort, offline-safe cloud execution
tracking — a missing organization must degrade the SAME way a missing JWT
already does (skip cloud tracking for this run), never invent one, and never
send a header-less request that is a guaranteed 400.
"""

from __future__ import annotations

import pytest

from app.services.ai import runtime_spine


class _FakeClient:
    def __init__(self, *, response: dict | None = None) -> None:
        self.response = response or {"execution_id": "exec-1"}
        self.posts: list[tuple] = []

    async def post(self, path, payload, *, jwt, timeout, headers=None):
        self.posts.append((path, payload, jwt, timeout, headers))
        return self.response


@pytest.mark.anyio
async def test_open_runtime_execution_sends_organization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(
        response={
            "execution_id": "exec-1",
            "root_execution_id": "exec-1",
            "lease_holder": "holder-1",
            "lease_seconds": 90,
        }
    )
    monkeypatch.setattr(runtime_spine, "_get_client", lambda: fake_client)

    lease = await runtime_spine.open_runtime_execution(
        jwt="jwt-1",
        organization_id="org-1",
        conversation_id="conversation-1",
    )

    assert lease is not None
    assert lease.organization_id == "org-1"
    path, payload, jwt, timeout, headers = fake_client.posts[0]
    assert path == "/v2/runtime/open"
    assert jwt == "jwt-1"
    assert headers == {"X-Organization-Id": "org-1"}


@pytest.mark.anyio
async def test_open_runtime_execution_skips_tracking_without_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No fallback organization is ever chosen — a missing org means the run
    is simply not cloud-tracked, exactly like a missing JWT, and the client
    is never even reached (no wasted, guaranteed-400 round trip)."""
    fake_client = _FakeClient()
    monkeypatch.setattr(runtime_spine, "_get_client", lambda: fake_client)

    lease = await runtime_spine.open_runtime_execution(
        jwt="jwt-1",
        organization_id=None,
        conversation_id="conversation-1",
    )

    assert lease is None
    assert fake_client.posts == []


@pytest.mark.anyio
async def test_settle_runtime_execution_sends_organization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeClient(response={})
    monkeypatch.setattr(runtime_spine, "_get_client", lambda: fake_client)

    lease = runtime_spine.RuntimeLease(
        execution_id="exec-1",
        root_execution_id="exec-1",
        lease_holder="holder-1",
        lease_seconds=90,
        jwt="jwt-1",
        organization_id="org-1",
    )

    await runtime_spine.settle_runtime_execution(lease, status="completed")

    path, payload, jwt, timeout, headers = fake_client.posts[0]
    assert path == "/v2/runtime/settle"
    assert headers == {"X-Organization-Id": "org-1"}
