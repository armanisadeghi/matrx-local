"""POST /auth/token accepts only issuer-verified, user-matching sessions."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.api import remote_auth
from app.api import token_routes
from app.api.remote_auth import TokenVerificationResult, VerifiedUser


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _HTTPClient:
    def __init__(self, responses: list[_Response], calls: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str]) -> _Response:
        self._calls.append({"url": url, "headers": headers})
        return self._responses.pop(0)


class _Repo:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.saved: dict[str, Any] | None = None
        self.cleared = 0

    async def get(self) -> dict[str, Any] | None:
        return self.row

    async def save(self, **kwargs: Any) -> None:
        self.saved = kwargs
        self.row = dict(kwargs)

    async def clear(self) -> None:
        self.cleared += 1
        self.row = None


class _Outbox:
    def __init__(self) -> None:
        self.credential_changes = 0

    async def credentials_changed(self) -> None:
        self.credential_changes += 1


@pytest.fixture
def token_route_fakes(monkeypatch: pytest.MonkeyPatch):
    repo = _Repo(
        {
            "access_token": "previous-token",
            "refresh_token": "previous-refresh",
            "user_id": "previous-user",
        }
    )
    outbox = _Outbox()
    monkeypatch.setattr(token_routes, "TokenRepo", lambda: repo)
    monkeypatch.setattr(token_routes, "_broadcast_enabled", lambda: False)
    monkeypatch.setattr(token_routes, "set_jwt_cache", lambda _token: None)
    monkeypatch.setattr(token_routes, "clear_jwt_cache", lambda: None)
    monkeypatch.setattr(token_routes, "invalidate_token", lambda _token: None)

    def _discard_background(coro, *, name: str) -> None:  # noqa: ARG001
        coro.close()

    monkeypatch.setattr(token_routes, "fire_and_forget", _discard_background)

    from app.services import coding_sessions
    from app.services.ai import key_manager

    monkeypatch.setattr(
        coding_sessions,
        "get_coding_session_bridge_outbox",
        lambda: outbox,
    )
    monkeypatch.setattr(key_manager, "clear_vault_keys", lambda: None)
    return repo, outbox


@pytest.mark.anyio
async def test_verified_matching_token_is_saved_and_wakes_publisher(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
) -> None:
    repo, outbox = token_route_fakes
    verified = VerifiedUser(user_id="user-1", email=None, is_anon=False)

    async def _verify(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("verified", verified)

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _verify)
    result = await token_routes.save_token(
        token_routes.TokenRequest(
            access_token="verified-token",
            refresh_token="refresh-token",
            user_id="user-1",
            expires_in=60,
        )
    )

    assert result == {"status": "ok", "user_id": "user-1"}
    assert repo.saved is not None
    assert repo.saved["access_token"] == "verified-token"
    assert repo.saved["user_id"] == "user-1"
    assert outbox.credential_changes == 1


@pytest.mark.anyio
async def test_invalid_token_clears_incompatible_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
) -> None:
    repo, outbox = token_route_fakes

    async def _verify(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("invalid")

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _verify)
    with pytest.raises(HTTPException) as raised:
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="wrong-project", user_id="user-1")
        )

    assert raised.value.status_code == 401
    assert raised.value.detail["code"] == "invalid_supabase_session"
    assert repo.cleared == 1
    assert repo.row is None
    assert outbox.credential_changes == 1


@pytest.mark.anyio
async def test_verified_user_mismatch_is_rejected_and_cleared(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
) -> None:
    repo, _outbox = token_route_fakes
    verified = VerifiedUser(user_id="other-user", email=None, is_anon=False)

    async def _verify(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("verified", verified)

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _verify)
    with pytest.raises(HTTPException) as raised:
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="valid-token", user_id="user-1")
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "session_user_mismatch"
    assert repo.cleared == 1


@pytest.mark.anyio
async def test_verification_outage_preserves_previous_session(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
) -> None:
    repo, outbox = token_route_fakes

    async def _verify(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("unavailable")

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _verify)
    with pytest.raises(HTTPException) as raised:
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="unverified", user_id="user-1")
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "session_verification_unavailable"
    assert repo.cleared == 0
    assert repo.row is not None
    assert repo.row["access_token"] == "previous-token"
    assert outbox.credential_changes == 0


@pytest.mark.anyio
async def test_canonical_verifier_uses_configured_project_auth_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    responses = [
        _Response(
            200,
            {
                "id": "project-user",
                "email": "person@example.com",
                "role": "authenticated",
            },
        )
    ]
    remote_auth._verify_cache.clear()
    monkeypatch.setattr(remote_auth, "SUPABASE_URL", "https://configured.example/")
    monkeypatch.setattr(remote_auth, "SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    monkeypatch.setattr(
        remote_auth.httpx,
        "AsyncClient",
        lambda **_kwargs: _HTTPClient(responses, calls),
    )

    result = await remote_auth.verify_supabase_token_result("access-token")

    assert result.status == "verified"
    assert result.user is not None and result.user.user_id == "project-user"
    assert calls == [
        {
            "url": "https://configured.example/auth/v1/user",
            "headers": {
                "apikey": "publishable-key",
                "Authorization": "Bearer access-token",
                "Accept": "application/json",
            },
        }
    ]


@pytest.mark.anyio
async def test_verifier_does_not_cache_transient_issuer_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    responses = [
        _Response(503),
        _Response(200, {"id": "project-user", "role": "authenticated"}),
    ]
    remote_auth._verify_cache.clear()
    monkeypatch.setattr(remote_auth, "SUPABASE_URL", "https://configured.example")
    monkeypatch.setattr(remote_auth, "SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    monkeypatch.setattr(
        remote_auth.httpx,
        "AsyncClient",
        lambda **_kwargs: _HTTPClient(responses, calls),
    )

    unavailable = await remote_auth.verify_supabase_token_result("access-token")
    verified = await remote_auth.verify_supabase_token_result("access-token")

    assert unavailable.status == "unavailable"
    assert verified.status == "verified"
    assert len(calls) == 2
