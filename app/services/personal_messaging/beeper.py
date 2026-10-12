"""Official fixed-origin Desktop API adapter, with account/chat binding.

Never consumes platform Vault tokens or forwards arbitrary caller URLs. Native
provider errors are replaced by safe status codes, without response envelopes.
"""

from __future__ import annotations
import json
from datetime import datetime, UTC
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote
import httpx
from pydantic import BaseModel, Field, SecretStr, ConfigDict, model_validator
from .media import project_message
from .models import DeviceGrant

ORIGIN = "http://127.0.0.1:23373"


class BeeperFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class IssuedCredentials(BaseModel):
    access_token: SecretStr = Field(repr=False)
    other_credentials: dict[str, SecretStr] = Field(default_factory=dict, repr=False)


class UnverifiedTokenIssued(BeeperFailure):
    """Caller must seal this token in inactive cleanup custody before returning."""

    def __init__(self, credentials: IssuedCredentials):
        super().__init__(
            "grant_mismatch",
            "Beeper issued an unexpected grant. Disconnect cleanup is required.",
        )
        self.credentials = credentials


@dataclass(frozen=True)
class NativeLimits:
    timeout_seconds: float = 15
    max_response_bytes: int = 4 * 1024 * 1024


class Bridge(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["whatsapp"]
    provider: Literal["cloud", "local"]


class NativeUser(BaseModel):
    id: str = Field(min_length=1, max_length=1024)
    displayText: str | None = None
    phoneNumber: str | None = None


class OAuthClient(BaseModel):
    client_id: str
    redirect_uri: str
    engine_port: int = Field(ge=1, le=65535)
    client_secret: SecretStr | None = Field(default=None, repr=False)
    registration_access_token: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def exact_engine_callback(self):
        if (
            self.redirect_uri
            != f"http://127.0.0.1:{self.engine_port}/personal-messaging/beeper/callback"
        ):
            raise ValueError(
                "OAuth callback must match the discovered local engine port"
            )
        return self


class NativeAccount(BaseModel):
    accountID: str = Field(min_length=1, max_length=1024)
    status: str
    bridge: Bridge
    user: NativeUser


class NativeChat(BaseModel):
    id: str = Field(min_length=1, max_length=1024)
    accountID: str = Field(min_length=1, max_length=1024)
    title: str


class NativeChatPage(BaseModel):
    items: list[NativeChat]
    hasMore: bool
    oldestCursor: str
    newestCursor: str


class NativeMessage(BaseModel):
    id: str = Field(min_length=1, max_length=1024)
    accountID: str
    chatID: str
    timestamp: datetime
    senderID: str
    senderName: str | None = None
    editedTimestamp: datetime | None = None
    isSender: bool | None = None
    text: str | None = None
    type: str | None = None
    isDeleted: bool = False
    isHidden: bool = False
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class NativePage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[NativeMessage]
    hasMore: bool
    oldestCursor: str
    newestCursor: str


class DesktopApi:
    def __init__(self, limits: NativeLimits | None = None):
        self.limits = limits or NativeLimits()

    async def _call(
        self,
        method: str,
        path: str,
        *,
        token: SecretStr | None = None,
        params: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        empty_ok: bool = False,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=self.limits.timeout_seconds,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    method,
                    ORIGIN + path,
                    params=params,
                    data=form,
                    json=payload,
                    headers={"Authorization": "Bearer " + token.get_secret_value()}
                    if token
                    else None,
                ) as response:
                    if response.status_code == 401:
                        raise BeeperFailure(
                            "reauthorization_required",
                            "Beeper access expired or was revoked. Reconnect.",
                        )
                    if response.status_code == 404:
                        raise BeeperFailure(
                            "native_not_found",
                            "This native account or conversation is no longer available.",
                        )
                    if response.status_code == 403:
                        raise BeeperFailure(
                            "permission_required",
                            "Beeper did not allow this operation.",
                        )
                    if response.status_code >= 300:
                        raise BeeperFailure(
                            "provider_refused",
                            "Beeper could not complete this operation.",
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.limits.max_response_bytes:
                            raise BeeperFailure(
                                "response_too_large",
                                "Beeper returned too much data. Narrow the selection.",
                            )
                    if not body and empty_ok:
                        return None
                    return json.loads(body)
        except httpx.TimeoutException:
            raise BeeperFailure(
                "device_timeout",
                "Beeper did not respond. Keep the desktop app open and retry.",
            ) from None
        except httpx.RequestError:
            raise BeeperFailure(
                "device_offline",
                "Open Beeper Desktop on this computer to use WhatsApp.",
            ) from None
        except (ValueError, UnicodeError):
            raise BeeperFailure(
                "invalid_response", "Beeper returned an unreadable response."
            ) from None

    async def metadata(self) -> dict:
        result = await self._call("GET", "/.well-known/oauth-authorization-server")
        required = {
            "issuer": ORIGIN,
            "authorization_endpoint": ORIGIN + "/oauth/authorize",
            "token_endpoint": ORIGIN + "/oauth/token",
            "registration_endpoint": ORIGIN + "/oauth/register",
            "revocation_endpoint": ORIGIN + "/oauth/revoke",
        }
        if (
            not isinstance(result, dict)
            or any(result.get(k) != v for k, v in required.items())
            or (
                "S256" not in result.get("code_challenge_methods_supported", [])
                or "read" not in result.get("scopes_supported", [])
                or result.get("grant_types_supported") != ["authorization_code"]
                or result.get("token_endpoint_auth_methods_supported") != ["none"]
            )
        ):
            raise BeeperFailure(
                "unsupported_auth",
                "This Beeper version cannot provide the required customer OAuth flow.",
            )
        return {**required, "refresh_supported": False, "renewal": "reauthorization"}

    async def register_client(
        self,
        redirect_uri: str,
        *,
        engine_port: int,
        client_label: str = "AI Matrx personal messaging",
    ) -> OAuthClient:
        if (
            not 1 <= engine_port <= 65535
            or redirect_uri
            != f"http://127.0.0.1:{engine_port}/personal-messaging/beeper/callback"
        ):
            raise BeeperFailure(
                "invalid_callback",
                "Messaging callback must belong to this local engine.",
            )
        if not client_label.startswith("AI Matrx") or not 1 <= len(client_label) <= 100:
            raise BeeperFailure("invalid_client", "Native client label is invalid.")
        await self.metadata()
        result = await self._call(
            "POST",
            "/oauth/register",
            payload={
                "client_name": client_label,
                "client_uri": "https://www.aimatrx.com",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": "read",
                "token_endpoint_auth_method": "none",
            },
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("client_id"), str)
            or not result["client_id"]
            or result.get("client_name") != client_label
            or result.get("redirect_uris") != [redirect_uri]
            or result.get("scope") != "read"
            or result.get("token_endpoint_auth_method") != "none"
        ):
            raise BeeperFailure(
                "registration_mismatch",
                "Beeper did not register the approved read-only client.",
            )
        try:
            return OAuthClient(
                client_id=result["client_id"],
                redirect_uri=redirect_uri,
                engine_port=engine_port,
                client_secret=result.get("client_secret"),
                registration_access_token=result.get("registration_access_token"),
            )
        except ValueError:
            raise BeeperFailure(
                "registration_mismatch",
                "Beeper returned invalid client custody metadata.",
            ) from None

    async def exchange(
        self, *, client: OAuthClient, code: SecretStr, verifier: SecretStr
    ) -> SecretStr:
        result = await self._call(
            "POST",
            "/oauth/token",
            form={
                "grant_type": "authorization_code",
                "code": code.get_secret_value(),
                "code_verifier": verifier.get_secret_value(),
                "client_id": client.client_id,
                "redirect_uri": client.redirect_uri,
            },
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("access_token"), str)
            or not result["access_token"]
        ):
            raise BeeperFailure(
                "grant_mismatch", "Beeper did not return the approved read-only token."
            )
        token = SecretStr(result["access_token"])
        extras = {
            k: SecretStr(v)
            for k, v in result.items()
            if k != "access_token"
            and (k.endswith("_token") or k == "client_secret")
            and isinstance(v, str)
            and v
        }
        if (
            result.get("scope") != "read"
            or str(result.get("token_type", "")).lower() != "bearer"
            or extras
        ):
            raise UnverifiedTokenIssued(
                IssuedCredentials(access_token=token, other_credentials=extras)
            )
        # The callback seals pending custody before subsequent native probes.
        return token

    async def introspect(self, token: SecretStr) -> dict:
        result = await self._call(
            "POST",
            "/oauth/introspect",
            form={"token": token.get_secret_value(), "token_type_hint": "access_token"},
        )
        if not isinstance(result, dict) or result.get("active") is not True:
            raise BeeperFailure(
                "reauthorization_required",
                "Beeper access expired or was revoked. Reconnect.",
            )
        exp = result.get("exp")
        scopes = result.get("scope")
        if (
            not isinstance(exp, int)
            or isinstance(exp, bool)
            or exp <= datetime.now(UTC).timestamp()
            or not isinstance(scopes, str)
            or set(scopes.split()) != {"read"}
        ):
            raise BeeperFailure(
                "grant_mismatch", "Beeper did not return the approved read-only grant."
            )
        return {"expires_at": datetime.fromtimestamp(exp, UTC), "scopes": ["read"]}

    async def accounts(self, token: SecretStr) -> list[NativeAccount]:
        result = await self._call("GET", "/v1/accounts", token=token)
        if not isinstance(result, list):
            raise BeeperFailure("invalid_response", "Beeper account discovery failed.")
        try:
            accounts = [
                NativeAccount.model_validate(row)
                for row in result
                if isinstance(row, dict)
                and isinstance(row.get("bridge"), dict)
                and row["bridge"].get("type") == "whatsapp"
            ]
        except ValueError:
            raise BeeperFailure(
                "invalid_response", "Beeper returned an invalid WhatsApp account."
            ) from None
        return accounts

    @staticmethod
    def active(grant: DeviceGrant) -> SecretStr:
        if (
            grant.status != "active"
            or not grant.access_token
            or not grant.expires_at
            or grant.expires_at <= datetime.now(UTC)
            or grant.scopes != ["read"]
            or grant.resources_generation != grant.generation
            or grant.registration_pending
            or grant.exchange_pending
            or grant.cleanup_credentials
            or grant.cleanup_confirmation_generation == grant.generation
        ):
            raise BeeperFailure(
                "reauthorization_required", "Reconnect your personal WhatsApp account."
            )
        return grant.access_token

    async def account(self, grant: DeviceGrant) -> NativeAccount:
        accounts = await self.accounts(self.active(grant))
        account = next(
            (
                a
                for a in accounts
                if a.accountID == grant.account_ref
                and a.user.id == grant.native_user_ref
            ),
            None,
        )
        if (
            account is None
            or account.status != "connected"
            or account.user.id != grant.native_user_ref
            or account.bridge.provider != grant.bridge_provider
        ):
            raise BeeperFailure(
                "account_disconnected",
                "The selected WhatsApp account is disconnected in Beeper.",
            )
        return account

    async def verify_chat(
        self, token: SecretStr, account_ref: str, chat_id: str
    ) -> NativeChat:
        if not chat_id or len(chat_id) > 1024:
            raise BeeperFailure("invalid_chat", "Conversation selection is invalid.")
        result = await self._call(
            "GET", "/v1/chats/" + quote(chat_id, safe=""), token=token
        )
        try:
            chat = NativeChat.model_validate(result)
        except ValueError:
            raise BeeperFailure(
                "invalid_response", "Beeper returned an invalid conversation."
            ) from None
        if chat.accountID != account_ref or chat.id != chat_id:
            raise BeeperFailure(
                "chat_mismatch", "Beeper returned a different WhatsApp conversation."
            )
        return chat

    async def chats(
        self,
        grant: DeviceGrant,
        *,
        cursor: str | None = None,
        direction: Literal["before", "after"] = "before",
        limit: int = 25,
    ) -> NativeChatPage:
        await self.account(grant)
        if not 1 <= limit <= 200:
            raise BeeperFailure("invalid_limit", "Conversation page size is invalid.")
        params = {
            "accountIDs": str(grant.account_ref),
            "direction": direction,
            "limit": str(limit),
        }
        if cursor is not None:
            if not cursor or len(cursor) > 8192:
                raise BeeperFailure("invalid_cursor", "Conversation cursor is invalid.")
            params["cursor"] = cursor
        result = await self._call(
            "GET", "/v1/chats", token=self.active(grant), params=params
        )
        try:
            page = NativeChatPage.model_validate(result)
        except ValueError:
            raise BeeperFailure(
                "invalid_response", "Beeper returned an invalid conversation page."
            ) from None
        if any(chat.accountID != grant.account_ref for chat in page.items):
            raise BeeperFailure(
                "chat_mismatch",
                "Beeper returned conversations from a different account.",
            )
        return page

    async def chat(self, grant: DeviceGrant, chat_id: str) -> NativeChat:
        await self.account(grant)
        if chat_id not in grant.selected_chat_ids:
            raise BeeperFailure(
                "chat_not_selected",
                "Select this WhatsApp conversation before opening it.",
            )
        return await self.verify_chat(
            self.active(grant), str(grant.account_ref), chat_id
        )

    async def messages(
        self,
        grant: DeviceGrant,
        chat_id: str,
        *,
        cursor: str | None = None,
        direction: Literal["before", "after"] = "before",
    ) -> dict:
        await self.chat(grant, chat_id)
        params = {"direction": direction}
        if cursor is not None:
            if not cursor or len(cursor) > 8192:
                raise BeeperFailure("invalid_cursor", "Conversation cursor is invalid.")
            params["cursor"] = cursor
        result = await self._call(
            "GET",
            "/v1/chats/" + quote(chat_id, safe="") + "/messages",
            token=self.active(grant),
            params=params,
        )
        try:
            page = NativePage.model_validate(result)
        except ValueError:
            raise BeeperFailure(
                "invalid_response", "Beeper returned an invalid message page."
            ) from None
        messages = []
        for message in page.items:
            projected = project_message(message, grant, chat_id)
            if projected is not None:
                messages.append(projected)
        return {
            "items": messages,
            "has_more": page.hasMore,
            "oldest_cursor": page.oldestCursor,
            "newest_cursor": page.newestCursor,
            "history_coverage": "available_in_beeper",
            "refresh_supported": False,
            "renewal": "reauthorization",
        }

    async def revoke(
        self,
        token: SecretStr,
        *,
        token_type_hint: Literal["access_token", "refresh_token"] = "access_token",
    ) -> None:
        await self._call(
            "POST",
            "/oauth/revoke",
            form={
                "token": token.get_secret_value(),
                "token_type_hint": token_type_hint,
            },
            empty_ok=True,
        )
        await self.verify_inactive(token, token_type_hint=token_type_hint)

    async def verify_inactive(
        self,
        token: SecretStr,
        *,
        token_type_hint: Literal["access_token", "refresh_token"] = "access_token",
    ) -> None:
        # Introspection is a fresh provider proof, rather than inferring from 200.
        result = await self._call(
            "POST",
            "/oauth/introspect",
            form={
                "token": token.get_secret_value(),
                "token_type_hint": token_type_hint,
            },
        )
        if not isinstance(result, dict) or result.get("active") is not False:
            raise BeeperFailure(
                "revocation_unconfirmed",
                "Beeper has not confirmed disconnection. Cleanup will retry.",
            )

    async def revoke_credentials(self, credentials: IssuedCredentials) -> None:
        """All issued values must confirm inactive before the caller wipes custody."""
        for name, token in {
            "access_token": credentials.access_token,
            **credentials.other_credentials,
        }.items():
            await self.revoke(
                token,
                token_type_hint="refresh_token"
                if name == "refresh_token"
                else "access_token",
            )
