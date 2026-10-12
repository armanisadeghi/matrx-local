"""Native HTTP contract fixtures; these do not establish customer/provider proof."""

import asyncio
import json
from datetime import datetime, UTC, timedelta
from uuid import uuid4
import httpx
from pydantic import SecretStr
import pytest
from app.services.personal_messaging import beeper
from app.services.personal_messaging.store import DeviceGrant


def grant():
    return DeviceGrant(
        id=uuid4(),
        actor_id=uuid4(),
        app_instance_id=uuid4(),
        generation=1,
        status="active",
        client_id="public",
        redirect_uri="http://127.0.0.1:22140/personal-messaging/beeper/callback",
        access_token=SecretStr("fixture-token"),
        account_ref="whatsapp",
        native_user_ref="native-owner",
        selected_chat_ids=["sandbox"],
        scopes=["read"],
        expires_at=datetime.now(UTC) + timedelta(days=1),
        connection_id=uuid4(),
    )


def native_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        beeper.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_selected_account_message_and_opaque_cursor(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert str(request.url).startswith(beeper.ORIGIN)
        assert request.headers["authorization"] == "Bearer fixture-token"
        path = request.url.path
        if path == "/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    {
                        "accountID": "whatsapp",
                        "status": "connected",
                        "user": {"id": "native-owner"},
                        "bridge": {"id": "wa", "type": "whatsapp", "provider": "cloud"},
                    }
                ],
            )
        if path == "/v1/chats/sandbox":
            return httpx.Response(
                200, json={"id": "sandbox", "accountID": "whatsapp", "title": "Sandbox"}
            )
        assert path == "/v1/chats/sandbox/messages"
        assert request.url.params["cursor"] == "opaque/+?=cursor"
        assert request.url.params["direction"] == "after"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "native-stable-id",
                        "accountID": "whatsapp",
                        "chatID": "sandbox",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "senderID": "native-sender",
                        "text": "sandbox text",
                        "isSender": False,
                    }
                ],
                "hasMore": True,
                "oldestCursor": "old/opaque",
                "newestCursor": "new/opaque",
            },
        )

    native_client(monkeypatch, handler)
    result = asyncio.run(
        beeper.DesktopApi().messages(
            grant(), "sandbox", cursor="opaque/+?=cursor", direction="after"
        )
    )
    assert result["items"][0]["id"] == "native-stable-id"
    assert result["items"][0]["content"]["parts"][0]["text"] == "sandbox text"
    assert result["newest_cursor"] == "new/opaque" and result["has_more"]
    assert len(calls) == 3 and result["history_coverage"] == "available_in_beeper"


@pytest.mark.parametrize("wrong", ["chat", "message", "account", "owner"])
def test_foreign_account_or_chat_never_returned(monkeypatch, wrong):
    def handler(request):
        if request.url.path == "/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    {
                        "accountID": "foreign" if wrong == "account" else "whatsapp",
                        "status": "connected",
                        "user": {
                            "id": "foreign-owner"
                            if wrong == "owner"
                            else "native-owner"
                        },
                        "bridge": {"id": "wa", "type": "whatsapp", "provider": "cloud"},
                    }
                ],
            )
        if request.url.path == "/v1/chats/sandbox":
            return httpx.Response(
                200,
                json={
                    "id": "sandbox",
                    "accountID": "foreign" if wrong == "chat" else "whatsapp",
                    "title": "Sandbox",
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "native",
                        "accountID": "foreign",
                        "chatID": "sandbox",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "senderID": "sender",
                        "text": "must not return",
                    }
                ],
                "hasMore": False,
                "oldestCursor": "old",
                "newestCursor": "new",
            },
        )

    native_client(monkeypatch, handler)
    with pytest.raises(beeper.BeeperFailure):
        asyncio.run(beeper.DesktopApi().messages(grant(), "sandbox"))


def test_unselected_and_expired_never_read_messages(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json=[
                {
                    "accountID": "whatsapp",
                    "status": "connected",
                    "user": {"id": "native-owner"},
                    "bridge": {"id": "wa", "type": "whatsapp", "provider": "local"},
                }
            ],
        )

    native_client(monkeypatch, handler)
    with pytest.raises(beeper.BeeperFailure):
        asyncio.run(beeper.DesktopApi().messages(grant(), "unselected"))
    assert all("/chats/" not in path for path in calls)
    calls.clear()
    expired = grant().model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    with pytest.raises(beeper.BeeperFailure):
        asyncio.run(beeper.DesktopApi().messages(expired, "sandbox"))
    assert calls == []


@pytest.mark.parametrize(
    "failure", ["offline", "timeout", "revoked", "invalid", "large"]
)
def test_safe_failure_never_exposes_provider_or_token(monkeypatch, failure):
    def handler(request):
        if failure == "offline":
            raise httpx.ConnectError(
                "fixture-token provider diagnostic", request=request
            )
        if failure == "timeout":
            raise httpx.ReadTimeout(
                "fixture-token provider diagnostic", request=request
            )
        if failure == "revoked":
            return httpx.Response(401, json={"token": "fixture-token"})
        if failure == "large":
            return httpx.Response(200, content=b"x" * 65)
        return httpx.Response(200, content=b"not json fixture-token")

    native_client(monkeypatch, handler)
    with pytest.raises(beeper.BeeperFailure) as error:
        asyncio.run(
            beeper.DesktopApi(beeper.NativeLimits(max_response_bytes=64)).accounts(
                SecretStr("fixture-token")
            )
        )
    assert "fixture-token" not in str(error.value)


@pytest.mark.parametrize("active", [False, True])
def test_revoke_empty_response_requires_actual_inactive_introspection(
    monkeypatch, active
):
    def handler(request):
        if request.url.path == "/oauth/revoke":
            return httpx.Response(200, content=b"")
        assert request.url.path == "/oauth/introspect"
        return httpx.Response(200, json={"active": active})

    native_client(monkeypatch, handler)
    if active:
        with pytest.raises(beeper.BeeperFailure):
            asyncio.run(beeper.DesktopApi().revoke(SecretStr("fixture-token")))
    else:
        asyncio.run(beeper.DesktopApi().revoke(SecretStr("fixture-token")))


@pytest.mark.parametrize(
    "callback",
    [
        "https://external.example/callback",
        "http://127.0.0.1:22140/wrong",
        "http://name:22140/personal-messaging/beeper/callback",
    ],
)
def test_dcr_refuses_external_or_wrong_callback_before_http(monkeypatch, callback):
    def handler(request):
        raise AssertionError("Invalid callback reached provider")

    native_client(monkeypatch, handler)
    with pytest.raises(beeper.BeeperFailure):
        asyncio.run(beeper.DesktopApi().register_client(callback, engine_port=22140))


def test_dcr_exact_local_callback_and_public_read_client(monkeypatch):
    callback = "http://127.0.0.1:22140/personal-messaging/beeper/callback"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": beeper.ORIGIN,
                    "authorization_endpoint": beeper.ORIGIN + "/oauth/authorize",
                    "token_endpoint": beeper.ORIGIN + "/oauth/token",
                    "registration_endpoint": beeper.ORIGIN + "/oauth/register",
                    "revocation_endpoint": beeper.ORIGIN + "/oauth/revoke",
                    "code_challenge_methods_supported": ["S256"],
                    "scopes_supported": ["read", "write"],
                    "grant_types_supported": ["authorization_code"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        assert request.url.path == "/oauth/register"
        body = json.loads(request.content)
        assert (
            body["redirect_uris"] == [callback]
            and body["scope"] == "read"
            and body["token_endpoint_auth_method"] == "none"
        )
        return httpx.Response(
            200,
            json={
                "client_id": "public-native-client",
                "redirect_uris": [callback],
                "scope": "read",
                "token_endpoint_auth_method": "none",
            },
        )

    native_client(monkeypatch, handler)
    result = asyncio.run(
        beeper.DesktopApi().register_client(callback, engine_port=22140)
    )
    assert result.client_id == "public-native-client" and len(calls) == 2


@pytest.mark.parametrize("scope", ["read", "read write"])
def test_exchange_retains_token_for_custody_before_native_probe(monkeypatch, scope):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.url.path == "/oauth/token"
        body = request.content.decode()
        assert "code_verifier=" in body and "authorization_code" in body
        from urllib.parse import parse_qs

        assert parse_qs(body)["redirect_uri"] == [
            "http://127.0.0.1:22140/personal-messaging/beeper/callback"
        ]
        return httpx.Response(
            200,
            json={
                "access_token": "issued-fixture-token",
                "token_type": "Bearer",
                "scope": scope,
            },
        )

    native_client(monkeypatch, handler)
    if scope == "read":
        token = asyncio.run(
            beeper.DesktopApi().exchange(
                client=beeper.OAuthClient(
                    client_id="public",
                    redirect_uri="http://127.0.0.1:22140/personal-messaging/beeper/callback",
                    engine_port=22140,
                ),
                code=SecretStr("code"),
                verifier=SecretStr("verifier"),
            )
        )
        assert token.get_secret_value() == "issued-fixture-token"
    else:
        with pytest.raises(beeper.UnverifiedTokenIssued) as error:
            asyncio.run(
                beeper.DesktopApi().exchange(
                    client=beeper.OAuthClient(
                        client_id="public",
                        redirect_uri="http://127.0.0.1:22140/personal-messaging/beeper/callback",
                        engine_port=22140,
                    ),
                    code=SecretStr("code"),
                    verifier=SecretStr("verifier"),
                )
            )
        assert (
            error.value.credentials.access_token.get_secret_value()
            == "issued-fixture-token"
            and "issued-fixture-token" not in str(error.value)
        )
    assert calls == ["/oauth/token"]


def test_duplicate_account_ids_use_native_user_composite(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/accounts"
        return httpx.Response(
            200,
            json=[
                {
                    "accountID": "whatsapp",
                    "status": "connected",
                    "user": {"id": user},
                    "bridge": {"id": "wa", "type": "whatsapp", "provider": "cloud"},
                }
                for user in ["other-user", "native-owner"]
            ],
        )

    native_client(monkeypatch, handler)
    account = asyncio.run(beeper.DesktopApi().account(grant()))
    assert account.user.id == "native-owner"


def test_every_unexpected_issued_token_is_retained_and_masked(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "access_token": "private-issued-access",
                "refresh_token": "private-issued-refresh",
                "id_token": "private-issued-id",
                "scope": "read",
                "token_type": "Bearer",
            },
        )

    native_client(monkeypatch, handler)
    client = beeper.OAuthClient(
        client_id="public",
        redirect_uri="http://127.0.0.1:22140/personal-messaging/beeper/callback",
        engine_port=22140,
    )
    with pytest.raises(beeper.UnverifiedTokenIssued) as error:
        asyncio.run(
            beeper.DesktopApi().exchange(
                client=client,
                code=SecretStr("private-code"),
                verifier=SecretStr("private-verifier"),
            )
        )
    values = error.value.credentials
    assert values.access_token.get_secret_value() == "private-issued-access"
    assert (
        values.other_credentials["refresh_token"].get_secret_value()
        == "private-issued-refresh"
    )
    assert (
        values.other_credentials["id_token"].get_secret_value() == "private-issued-id"
    )
    for secret in [
        "private-issued-access",
        "private-issued-refresh",
        "private-issued-id",
    ]:
        assert secret not in str(error.value) and secret not in repr(error.value)
        assert secret not in repr(values) and secret not in values.model_dump_json()


def test_cleanup_revokes_every_issued_credential_before_success(monkeypatch):
    from urllib.parse import parse_qs

    revoked = []

    def handler(request):
        body = parse_qs(request.content.decode())
        token = body["token"][0]
        assert body["token_type_hint"] == [
            "refresh_token" if token == "refresh-secret" else "access_token"
        ]
        if request.url.path == "/oauth/revoke":
            revoked.append(token)
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json={"active": token not in revoked})

    native_client(monkeypatch, handler)
    credentials = beeper.IssuedCredentials(
        access_token=SecretStr("access-secret"),
        other_credentials={
            "refresh_token": SecretStr("refresh-secret"),
            "id_token": SecretStr("id-secret"),
        },
    )
    asyncio.run(beeper.DesktopApi().revoke_credentials(credentials))
    assert revoked == ["access-secret", "refresh-secret", "id-secret"]
