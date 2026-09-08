"""Owner-scoped coding-session routes never wait on an organization choice."""

from __future__ import annotations

import pytest

from app.services.aidream import client as client_module
from app.services.aidream.client import AIDreamClient, _is_organization_self_resolved


def test_only_the_owner_scoped_coding_session_routes_are_self_resolved() -> None:
    assert _is_organization_self_resolved("/coding-sessions/bridge")
    assert _is_organization_self_resolved("/coding-sessions/sessions?provider=claude_code&limit=200")
    assert _is_organization_self_resolved("/coding-sessions/sessions/")
    assert not _is_organization_self_resolved("/coding-sessions-archive")
    assert not _is_organization_self_resolved("/agents")
    assert not _is_organization_self_resolved("")


@pytest.mark.anyio
async def test_bridge_headers_are_built_without_resolving_an_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-09-08 stall: a Mac with several memberships and no default
    could not SEND a bridge envelope because the transport refused to build
    headers. The server resolves the organization itself for these routes."""
    from app.services.aidream import organization as organization_module

    async def refuse(_jwt: str) -> str:
        raise organization_module.OrganizationNotResolvedError(
            "several memberships", remedy="choose one"
        )

    monkeypatch.setattr(organization_module, "resolve_active_organization_id", refuse)
    client = AIDreamClient("https://example.test")

    headers = await client._build_headers({}, "jwt", None, path="/coding-sessions/bridge")
    assert headers["Authorization"] == "Bearer jwt"
    assert "X-Organization-Id" not in headers

    # A caller that states an organization still wins, unchanged.
    stated = await client._build_headers(
        {}, "jwt", {"X-Organization-Id": "org-1"}, path="/coding-sessions/bridge"
    )
    assert stated["X-Organization-Id"] == "org-1"

    # Every other route keeps the refusal: the server would 400 without it.
    with pytest.raises(client_module.AIDreamError) as excinfo:
        await client._build_headers({}, "jwt", None, path="/agents")
    assert "Cannot name an organization" in str(excinfo.value)
