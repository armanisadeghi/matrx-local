"""POST /auth/token accepts only issuer-verified, user-matching sessions."""

from __future__ import annotations

import logging
from contextlib import contextmanager
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
async def test_invalid_posted_token_leaves_previous_session_untouched(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
) -> None:
    """A rejected NEW token must not erase a different stored session.

    2026-08-25 incident: the UI restored a revoked session and posted it; the
    old behavior cleared the engine's previously-verified token, silently
    killing every engine cloud lane while the UI still looked signed in.
    """
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
    assert repo.cleared == 0
    assert repo.row is not None and repo.row["access_token"] == "previous-token"
    assert outbox.credential_changes == 0


@pytest.mark.anyio
async def test_invalid_posted_token_clears_only_its_own_stored_copy(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
) -> None:
    repo, _outbox = token_route_fakes
    repo.row = {"access_token": "wrong-project", "user_id": "user-1"}

    async def _verify(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("invalid")

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _verify)
    with pytest.raises(HTTPException) as raised:
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="wrong-project", user_id="user-1")
        )

    assert raised.value.status_code == 401
    assert repo.cleared == 1
    assert repo.row is None


@pytest.mark.anyio
async def test_verified_user_mismatch_is_rejected_but_stored_session_survives(
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
    assert repo.cleared == 0
    assert repo.row is not None and repo.row["access_token"] == "previous-token"


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


# ---------------------------------------------------------------------------
# Our API key being rejected is a CONFIGURATION fault, never "your session is
# bad". Measured against https://db.matrxserver.com/auth/v1/user on 2026-09-12:
#   valid apikey + bad bearer -> 403 {"error_code": "bad_jwt", ...}
#   bogus apikey              -> 401 {"message": "Invalid API key", ...}
#   no apikey                 -> 401 {"message": "No API key found in request"}
# Collapsing all of these into "invalid" told every user to sign in again,
# forever, whenever the engine's publishable key was wrong or rotated.
# ---------------------------------------------------------------------------


@contextmanager
def _engine_logs():
    """Capture the engine logger's records (it never propagates to root)."""
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    sink = _Sink(logging.DEBUG)
    engine_logger = logging.getLogger("system_logger")
    previous = engine_logger.level
    engine_logger.addHandler(sink)
    engine_logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        engine_logger.removeHandler(sink)
        engine_logger.setLevel(previous)


def _text(records: list[logging.LogRecord]) -> str:
    return "\n".join(f"{r.levelname} {r.getMessage()}" for r in records)


def _issuer_returning(monkeypatch: pytest.MonkeyPatch, *responses: _Response):
    calls: list[dict[str, Any]] = []
    queue = list(responses)
    remote_auth._verify_cache.clear()
    monkeypatch.setattr(remote_auth, "SUPABASE_URL", "https://configured.example")
    monkeypatch.setattr(remote_auth, "SUPABASE_PUBLISHABLE_KEY", "publishable-key")
    monkeypatch.setattr(
        remote_auth.httpx,
        "AsyncClient",
        lambda **_kwargs: _HTTPClient(queue, calls),
    )
    return calls


@pytest.mark.anyio
async def test_bogus_api_key_is_a_configuration_fault_not_an_invalid_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _issuer_returning(
        monkeypatch,
        _Response(
            401,
            {
                "message": "Invalid API key",
                "hint": "Double check your Supabase `anon` or `service_role` API key.",
            },
        ),
    )

    with _engine_logs() as records:
        result = await remote_auth.verify_supabase_token_result("perfectly-good-token")

    assert result.status == "misconfigured"
    assert result.user is None
    text = _text(records)
    assert "WARNING" in text and "ERROR" in text
    assert "MISCONFIGURATION" in text
    assert "401" in text and "Invalid API key" in text
    assert "SUPABASE_PUBLISHABLE_KEY" in text  # the remedy is named


@pytest.mark.anyio
async def test_missing_api_key_is_a_configuration_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _issuer_returning(
        monkeypatch,
        _Response(401, {"message": "No API key found in request"}),
    )

    result = await remote_auth.verify_supabase_token_result("perfectly-good-token")

    assert result.status == "misconfigured"


@pytest.mark.anyio
async def test_configuration_fault_is_never_cached_against_the_users_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fixed key must work on the very next call — no cached 'invalid'."""
    calls = _issuer_returning(
        monkeypatch,
        _Response(401, {"message": "Invalid API key"}),
        _Response(200, {"id": "project-user", "role": "authenticated"}),
    )

    broken = await remote_auth.verify_supabase_token_result("access-token")
    fixed = await remote_auth.verify_supabase_token_result("access-token")

    assert broken.status == "misconfigured"
    assert fixed.status == "verified"
    assert len(calls) == 2


@pytest.mark.anyio
async def test_bad_user_jwt_is_still_invalid_and_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _issuer_returning(
        monkeypatch,
        _Response(
            403,
            {
                "code": 403,
                "error_code": "bad_jwt",
                "msg": "invalid JWT: unable to parse or verify signature",
            },
        ),
    )

    with _engine_logs() as records:
        result = await remote_auth.verify_supabase_token_result("expired-token")
    logged = _text(records)

    assert result.status == "invalid"
    assert "WARNING" in logged
    assert "MISCONFIGURATION" not in logged
    assert "bad_jwt" in logged and "403" in logged


@pytest.mark.anyio
async def test_token_401_about_the_token_stays_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive evidence only: a 401 that is about the token is not our fault."""
    _issuer_returning(
        monkeypatch,
        _Response(401, {"error_code": "bad_jwt", "msg": "invalid JWT"}),
    )

    result = await remote_auth.verify_supabase_token_result("expired-token")

    assert result.status == "invalid"


@pytest.mark.anyio
async def test_api_key_rejection_keeps_the_stored_session_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
) -> None:
    """save_token must not sign the user out over OUR broken API key."""
    repo, outbox = token_route_fakes
    repo.row = {"access_token": "posted-token", "user_id": "user-1"}
    invalidated: list[str] = []
    monkeypatch.setattr(token_routes, "invalidate_token", invalidated.append)

    async def _verify(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("misconfigured")

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _verify)
    with pytest.raises(HTTPException) as raised:
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="posted-token", user_id="user-1")
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["code"] == "account_service_key_rejected"
    assert (
        "signing in again will not help" in raised.value.detail["message"].lower()
    )
    # Even though the stored copy IS the posted token, it survives: the token
    # was never judged bad.
    assert repo.cleared == 0
    assert repo.row is not None and repo.row["access_token"] == "posted-token"
    assert invalidated == []
    assert outbox.credential_changes == 0


# ---------------------------------------------------------------------------
# A configuration fault the user cannot act on still has to reach a human:
# both our-fault statuses raise the canonical action-needed card on the 503
# body (harvested by desktop/src/lib/api.ts) and register it so it survives a
# reconnect. And "we were never configured at all" is the same class as "our
# key was rejected" — it must never be dressed up as a network problem the
# user should re-authenticate through.
# ---------------------------------------------------------------------------


class _BrokenBodyResponse:
    """An issuer error whose body is not JSON at all (gateway HTML, etc.)."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        raise ValueError("not JSON")


@pytest.fixture
def clean_action_needed_registry():
    from app.services.action_needed.registry import get_action_needed_registry

    registry = get_action_needed_registry()
    yield registry


async def _registered_auth_items(registry) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for snapshot in await registry.snapshots():
        if snapshot.get("type") != "action_needed_snapshot":
            continue
        items.extend(
            item
            for item in snapshot.get("items", [])
            if str(item.get("fingerprint", "")).startswith("auth-config:")
        )
    return items


@pytest.mark.anyio
async def test_unparseable_error_body_stays_an_invalid_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No positive evidence about our key -> never blamed on our configuration."""
    _issuer_returning(monkeypatch)
    monkeypatch.setattr(
        remote_auth.httpx,
        "AsyncClient",
        lambda **_kwargs: _HTTPClient([_BrokenBodyResponse(401)], []),
    )

    result = await remote_auth.verify_supabase_token_result("some-token")

    assert result.status == "invalid"


@pytest.mark.anyio
async def test_missing_account_service_config_is_logged_with_its_remedy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_auth._verify_cache.clear()
    monkeypatch.setattr(remote_auth, "_unconfigured_logged", set())
    monkeypatch.setattr(remote_auth, "SUPABASE_URL", "https://configured.example")
    monkeypatch.setattr(remote_auth, "SUPABASE_PUBLISHABLE_KEY", "")

    with _engine_logs() as records:
        result = await remote_auth.verify_supabase_token_result("any-token")
        repeat = await remote_auth.verify_supabase_token_result("any-token")

    assert result.status == "unconfigured"
    assert repeat.status == "unconfigured"
    errors = [r for r in records if r.levelno >= logging.ERROR]
    # Loud once per process; the persistent card is what keeps saying it.
    assert len(errors) == 1
    text = errors[0].getMessage()
    assert "MISCONFIGURATION" in text
    assert "SUPABASE_PUBLISHABLE_KEY" in text
    assert "SUPABASE_URL" not in text  # only what is ACTUALLY missing
    assert "restart the engine" in text


@pytest.mark.anyio
async def test_unconfigured_engine_is_a_config_fault_not_a_connection_problem(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
    clean_action_needed_registry,
) -> None:
    repo, outbox = token_route_fakes
    invalidated: list[str] = []
    monkeypatch.setattr(token_routes, "invalidate_token", invalidated.append)
    monkeypatch.setattr(
        token_routes, "missing_supabase_config", lambda: ["SUPABASE_PUBLISHABLE_KEY"]
    )

    async def _verify(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("unconfigured")

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _verify)
    with pytest.raises(HTTPException) as raised:
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="previous-token", user_id="user-1")
        )

    detail = raised.value.detail
    assert raised.value.status_code == 503
    assert detail["code"] == "account_service_not_configured"
    assert "signing in again will not help" in detail["message"].lower()
    assert "check the connection" not in detail["message"].lower()
    # The card the desktop harvests off the error body.
    card = detail["action_needed"]
    assert card["kind"] == "api_key"
    assert card["fingerprint"] == "auth-config:account_service_not_configured"
    assert card["action"]["route"] == "/settings?tab=cloud"
    assert card["details"]["missing"] == ["SUPABASE_PUBLISHABLE_KEY"]
    # Stored session untouched even though it IS the posted token.
    assert repo.cleared == 0
    assert repo.row is not None and repo.row["access_token"] == "previous-token"
    assert invalidated == []
    assert outbox.credential_changes == 0

    registered = await _registered_auth_items(clean_action_needed_registry)
    assert [item["code"] for item in registered] == ["account_service_not_configured"]


@pytest.mark.anyio
async def test_real_invalid_api_key_response_reaches_the_user_as_a_card(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
    clean_action_needed_registry,
) -> None:
    """End to end: the issuer's real 401 body drives the whole save_token path.

    Nothing between the HTTP response and the 503 is mocked — this is the seam
    the classifier and the route meet at.
    """
    repo, _outbox = token_route_fakes
    _issuer_returning(
        monkeypatch,
        _Response(
            401,
            {
                "message": "Invalid API key",
                "hint": "Double check your Supabase `anon` or `service_role` API key.",
            },
        ),
    )

    with pytest.raises(HTTPException) as raised:
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="previous-token", user_id="user-1")
        )

    detail = raised.value.detail
    assert raised.value.status_code == 503
    assert detail["code"] == "account_service_key_rejected"
    assert "sign in again" not in detail["message"].lower().replace(
        "signing in again will not help", ""
    )
    card = detail["action_needed"]
    assert card["kind"] == "api_key"
    assert card["code"] == "account_service_key_rejected"
    assert card["action"]["label"] == "Open Cloud & Account"
    assert repo.cleared == 0
    assert repo.row is not None and repo.row["access_token"] == "previous-token"

    registered = await _registered_auth_items(clean_action_needed_registry)
    assert [item["code"] for item in registered] == ["account_service_key_rejected"]


@pytest.mark.anyio
async def test_a_working_sign_in_clears_the_configuration_card(
    monkeypatch: pytest.MonkeyPatch,
    token_route_fakes,
    clean_action_needed_registry,
) -> None:
    """The source owns the requirement through retry success."""
    _repo, _outbox = token_route_fakes

    async def _broken(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult("misconfigured")

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _broken)
    with pytest.raises(HTTPException):
        await token_routes.save_token(
            token_routes.TokenRequest(access_token="posted", user_id="user-1")
        )
    assert await _registered_auth_items(clean_action_needed_registry)

    async def _fixed(_token: str) -> TokenVerificationResult:
        return TokenVerificationResult(
            "verified", VerifiedUser(user_id="user-1", email=None, is_anon=False)
        )

    monkeypatch.setattr(token_routes, "verify_supabase_token_result", _fixed)
    await token_routes.save_token(
        token_routes.TokenRequest(access_token="posted", user_id="user-1")
    )

    assert await _registered_auth_items(clean_action_needed_registry) == []
