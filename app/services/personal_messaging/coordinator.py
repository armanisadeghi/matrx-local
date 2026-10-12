"""Same-device read consent, reconciliation and retirement for personal WhatsApp."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

from pydantic import SecretStr

from .beeper import (
    ORIGIN,
    BeeperFailure,
    DesktopApi,
    IssuedCredentials,
    NativeAccount,
    OAuthClient,
    UnverifiedTokenIssued,
)
from .cloud import NativeConnectionClient
from .media import (
    NativeAttachment,
    attachment_revision,
    read_attachment,
    retrieve_message,
    project_message,
)
from .models import DeviceGrant, GrantConflict, GrantUnavailable
from .store import PersonalMessagingGrantRepo


class PersonalMessagingCoordinator:
    _locks: dict[tuple[UUID, UUID], asyncio.Lock] = {}

    def __init__(
        self,
        *,
        actor_id: UUID,
        app_instance_id: UUID,
        engine_port: int,
        fence: Callable[[], Awaitable[None]],
        repo: PersonalMessagingGrantRepo | None = None,
        native: DesktopApi | None = None,
        cloud: NativeConnectionClient | None = None,
    ):
        self.actor_id = actor_id
        self.app_instance_id = app_instance_id
        self.engine_port = engine_port
        self.fence = fence
        self.repo = repo or PersonalMessagingGrantRepo()
        self.native = native or DesktopApi()
        self.cloud = cloud or NativeConnectionClient(actor_id, app_instance_id)
        self.lock = self._locks.setdefault((actor_id, app_instance_id), asyncio.Lock())

    async def _load(self) -> DeviceGrant | None:
        await self.fence()
        grants = await self.repo.list(
            actor_id=self.actor_id, app_instance_id=self.app_instance_id
        )
        if len(grants) > 1:
            raise GrantConflict(
                "Multiple native grants require explicit reconciliation."
            )
        return grants[0] if grants else None

    async def _save(self, grant: DeviceGrant, **updates) -> DeviceGrant:
        return await self.repo.save(
            DeviceGrant.model_validate({**grant.model_dump(), **updates}),
            expected_revision=grant.revision,
        )

    @staticmethod
    def _token(grant: DeviceGrant) -> SecretStr:
        if (
            grant.status not in ("pending_credential", "active", "needs_attention")
            or not grant.access_token
            or grant.exchange_pending
            or grant.registration_pending
            or grant.cleanup_credentials
        ):
            raise GrantUnavailable(
                "Native consent is not ready. Reconnect or reconcile."
            )
        return grant.access_token

    async def status(self) -> dict:
        async with self.lock:
            grant = await self._load()
            return (
                grant.public_status()
                if grant
                else {
                    "status": "not_connected",
                    "refresh_supported": False,
                    "renewal": "reauthorization",
                }
            )

    async def start(self) -> dict:
        async with self.lock:
            previous = await self._load()
            if previous and previous.status != "retired":
                raise GrantUnavailable(
                    "Finish disconnecting the previous consent before reconnecting."
                )
            if previous and (
                previous.provider_revoke_pending or previous.cloud_retirement_pending
            ):
                raise GrantUnavailable("Previous consent cleanup is incomplete.")
            redirect = f"http://127.0.0.1:{self.engine_port}/personal-messaging/beeper/callback"
            grant_id = previous.id if previous else uuid4()
            state = str(grant_id) + "." + secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            # Public client can be reused only at its exact registered callback.
            reuse = (
                previous is not None
                and previous.redirect_uri == redirect
                and bool(previous.client_id)
            )
            grant = DeviceGrant(
                id=grant_id,
                actor_id=self.actor_id,
                app_instance_id=self.app_instance_id,
                generation=previous.generation + 1 if previous else 1,
                status="pending_local",
                client_id=previous.client_id if reuse and previous else "",
                redirect_uri=redirect,
                client_label=previous.client_label
                if reuse and previous
                else "AI Matrx WhatsApp " + str(grant_id),
                registration_pending=not reuse,
                oauth_state_hash=hashlib.sha256(state.encode()).hexdigest(),
                oauth_verifier=SecretStr(verifier),
                oauth_expires_at=datetime.now(UTC) + timedelta(minutes=10),
                account_ref=previous.account_ref if previous else None,
                native_user_ref=previous.native_user_ref if previous else None,
                connection_id=previous.connection_id if previous else None,
                selected_chat_ids=previous.selected_chat_ids if previous else [],
                cleanup_confirmation=previous.cleanup_confirmation
                if previous
                else None,
                cleanup_confirmation_generation=previous.cleanup_confirmation_generation
                if previous
                else None,
            )
            # Encryption/CAS must complete before a remote client or token can be issued.
            grant = await self.repo.save(
                grant, expected_revision=previous.revision if previous else None
            )
            if not reuse:
                await self.fence()
                client = await self.native.register_client(
                    redirect,
                    engine_port=self.engine_port,
                    client_label=grant.client_label,
                )
                grant = await self._save(
                    grant,
                    client_id=client.client_id,
                    client_secret=client.client_secret,
                    registration_access_token=client.registration_access_token,
                    registration_pending=False,
                )
                if client.client_secret or client.registration_access_token:
                    raise GrantUnavailable(
                        "Unexpected registration credentials require native cleanup."
                    )
            await self.fence()
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            return {
                "authorization_url": ORIGIN
                + "/oauth/authorize?"
                + urlencode(
                    {
                        "response_type": "code",
                        "client_id": grant.client_id,
                        "redirect_uri": redirect,
                        "scope": "read",
                        "state": state,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                    }
                ),
                "grant": grant.public_status(),
                "required_duration": "one_day",
            }

    async def callback(self, *, state: SecretStr, code: SecretStr) -> None:
        async with self.lock:
            grant = await self._load()
            if (
                grant is None
                or grant.status != "pending_local"
                or grant.registration_pending
                or grant.exchange_pending
                or grant.oauth_claimed
                or not grant.oauth_state_hash
                or not grant.oauth_verifier
                or not grant.oauth_expires_at
                or grant.oauth_expires_at <= datetime.now(UTC)
                or not hmac.compare_digest(
                    hashlib.sha256(state.get_secret_value().encode()).hexdigest(),
                    grant.oauth_state_hash,
                )
                or not code.get_secret_value()
                or len(code.get_secret_value()) > 8192
            ):
                raise GrantUnavailable(
                    "The consent callback expired or was already used. Reconnect."
                )
            verifier = grant.oauth_verifier
            # A crash after this durable claim quarantines issuance; it never remints.
            grant = await self._save(grant, oauth_claimed=True, exchange_pending=True)
            client = OAuthClient(
                client_id=grant.client_id,
                redirect_uri=grant.redirect_uri,
                engine_port=self.engine_port,
                client_secret=grant.client_secret,
                registration_access_token=grant.registration_access_token,
            )
            extras: dict[str, SecretStr] = {}
            try:
                token = await self.native.exchange(
                    client=client, code=code, verifier=verifier
                )
            except UnverifiedTokenIssued as issued:
                token = issued.credentials.access_token
                extras = issued.credentials.other_credentials
            try:
                grant = await self._save(
                    grant,
                    status="pending_credential",
                    access_token=token,
                    cleanup_credentials=extras,
                    exchange_pending=False,
                    oauth_state_hash=None,
                    oauth_verifier=None,
                    oauth_expires_at=None,
                )
            except Exception:
                # Compensation cannot claim atomic crash recovery. The persisted issuance marker stays quarantined.
                await self.native.revoke_credentials(
                    IssuedCredentials(access_token=token, other_credentials=extras)
                )
                raise
            await self.fence()
            if extras:
                await self._save(grant, status="needs_attention")
                raise GrantUnavailable(
                    "Unexpected consent is held for disconnect cleanup."
                )
            await self._verify_consent(grant)

    async def _verify_consent(self, grant: DeviceGrant) -> DeviceGrant:
        try:
            info = await self.native.introspect(self._token(grant))
        except BeeperFailure:
            if grant.status in ("active", "pending_credential"):
                await self._save(grant, status="needs_attention")
            raise
        if info["expires_at"] > datetime.now(UTC) + timedelta(days=1):
            await self._save(grant, status="needs_attention")
            raise GrantUnavailable("Approve a one-day read-only grant, then reconnect.")
        if grant.expires_at and (
            grant.expires_at != info["expires_at"] or grant.scopes != info["scopes"]
        ):
            raise GrantConflict("Native consent expiry or scope changed unexpectedly.")
        return (
            await self._save(
                grant, expires_at=info["expires_at"], scopes=info["scopes"]
            )
            if not grant.expires_at
            else grant
        )

    async def accounts(self) -> list[dict]:
        async with self.lock:
            grant = await self._load()
            if grant is None:
                raise GrantUnavailable("Connect personal WhatsApp first.")
            grant = await self._verify_consent(grant)
            accounts = await self.native.accounts(self._token(grant))
            return [
                {
                    "account_ref": a.accountID,
                    "name": a.user.displayText or a.accountID,
                    "status": a.status,
                    "bridge_provider": a.bridge.provider,
                }
                for a in accounts
                if not grant.native_user_ref
                or (
                    a.accountID == grant.account_ref
                    and a.user.id == grant.native_user_ref
                )
            ]

    async def _bound_account(self, grant: DeviceGrant) -> NativeAccount:
        accounts = await self.native.accounts(self._token(grant))
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
            or (
                grant.bridge_provider
                and account.bridge.provider != grant.bridge_provider
            )
        ):
            raise BeeperFailure(
                "account_disconnected",
                "The selected WhatsApp account is disconnected in Beeper.",
            )
        return account

    async def select_account(self, account_ref: str) -> dict:
        async with self.lock:
            grant = await self._load()
            if not grant or grant.status != "pending_credential":
                raise GrantUnavailable(
                    "Complete native read consent before selecting an account."
                )
            grant = await self._verify_consent(grant)
            account = next(
                (
                    a
                    for a in await self.native.accounts(self._token(grant))
                    if a.accountID == account_ref
                ),
                None,
            )
            if account is None or account.status != "connected":
                raise GrantUnavailable("Select a connected native WhatsApp account.")
            if grant.native_user_ref and (
                account.user.id != grant.native_user_ref
                or account.accountID != grant.account_ref
            ):
                raise GrantConflict(
                    "Reconnect the originally selected native WhatsApp identity."
                )
            grant = await self._save(
                grant,
                account_ref=account.accountID,
                native_user_ref=account.user.id,
                bridge_provider=account.bridge.provider,
            )
            return await self._activate(grant)

    async def _activate(self, grant: DeviceGrant) -> dict:
        account = await self._bound_account(grant)
        chats = []
        removed = []
        for chat_id in grant.selected_chat_ids:
            try:
                chats.append(
                    await self.native.verify_chat(
                        self._token(grant), str(grant.account_ref), chat_id
                    )
                )
            except BeeperFailure as error:
                if error.code != "native_not_found":
                    raise
                removed.append(chat_id)
        if removed:
            grant = await self._save(grant, selected_chat_ids=[c.id for c in chats])
        await self.fence()
        receipt = await self.cloud.register_pending(grant)
        grant = await self._save(grant, connection_id=receipt.id)
        await self.cloud.select_resource(
            grant,
            receipt,
            resource_type="beeper_whatsapp_account",
            resource_ref=account.accountID,
            display_name=account.user.displayText or "WhatsApp",
            selected=True,
        )
        for chat in chats:
            await self.cloud.select_resource(
                grant,
                receipt,
                resource_type="beeper_whatsapp_chat",
                resource_ref=chat.id,
                display_name=chat.title,
                selected=True,
            )
        await self.cloud.clear_unselected_resources(grant, receipt)
        receipt = await self.cloud.activate(grant, receipt)
        await self.cloud.verify_resources(grant, receipt)
        await self.fence()
        grant = await self._save(
            grant, resources_generation=grant.generation, status="active"
        )
        return {"grant": grant.public_status(), "removed_chat_ids": removed}

    async def _ready(self) -> DeviceGrant:
        grant = await self._load()
        if grant is None:
            raise GrantUnavailable("Connect personal WhatsApp first.")
        try:
            self.native.active(grant)
            await self._verify_consent(grant)
            await self._bound_account(grant)
            current = await self.cloud.find(grant)
            if current is None:
                raise GrantUnavailable("Native consent metadata is missing. Reconcile.")
            await self.cloud.verify_resources(grant, current)
            await self.fence()
            return grant
        except (BeeperFailure, GrantUnavailable, GrantConflict):
            await self._mark_attention(grant)
            raise

    async def _mark_attention(self, grant: DeviceGrant) -> None:
        fresh = await self._load()
        if (
            fresh
            and fresh.id == grant.id
            and fresh.generation == grant.generation
            and fresh.status == "active"
        ):
            await self._save(fresh, status="needs_attention")

    @asynccontextmanager
    async def _read_boundary(self, grant: DeviceGrant) -> AsyncIterator[None]:
        try:
            yield
            fresh = await self._load()
            if not fresh or (fresh.id, fresh.generation, fresh.revision) != (
                grant.id,
                grant.generation,
                grant.revision,
            ):
                raise GrantConflict("Native consent changed during the read. Retry.")
            self.native.active(fresh)
            await self._bound_account(fresh)
            current = await self.cloud.find(fresh)
            if current is None:
                raise GrantUnavailable("Native consent metadata is missing. Reconcile.")
            await self.cloud.verify_resources(fresh, current)
        except (BeeperFailure, GrantUnavailable, GrantConflict):
            await self._mark_attention(grant)
            raise

    async def chats(self, *, cursor: str | None = None) -> dict:
        async with self.lock:
            grant = await self._ready()
            async with self._read_boundary(grant):
                page = await self.native.chats(grant, cursor=cursor)
                await self.fence()
                return {
                    "items": [
                        {
                            "id": c.id,
                            "title": c.title,
                            "selected": c.id in grant.selected_chat_ids,
                        }
                        for c in page.items
                    ],
                    "has_more": page.hasMore,
                    "oldest_cursor": page.oldestCursor,
                    "newest_cursor": page.newestCursor,
                }

    async def select_chat(self, chat_id: str, *, selected: bool = True) -> dict:
        async with self.lock:
            grant = await self._ready()
            async with self._read_boundary(grant):
                chat = await self.native.verify_chat(
                    self.native.active(grant), str(grant.account_ref), chat_id
                )
            ids = list(grant.selected_chat_ids)
            if selected and chat_id not in ids:
                ids.append(chat_id)
            if not selected:
                ids = [i for i in ids if i != chat_id]
            # Saved selection is the local authority. A hard crash before cloud receipt
            # cannot read a newly selected chat: _ready requires every exact receipt.
            grant = await self._save(grant, selected_chat_ids=ids)
            current = await self.cloud.find(grant)
            if current is None:
                await self._save(grant, status="needs_attention")
                raise GrantUnavailable("Native consent metadata is missing. Reconcile.")
            try:
                await self.cloud.select_resource(
                    grant,
                    current,
                    resource_type="beeper_whatsapp_chat",
                    resource_ref=chat_id,
                    display_name=chat.title,
                    selected=selected,
                )
                await self.cloud.verify_resources(grant, current)
                await self.fence()
            except Exception:
                await self._save(grant, status="needs_attention")
                raise
            return grant.public_status()

    async def reconcile(self) -> dict:
        async with self.lock:
            grant = await self._load()
            if grant is None:
                raise GrantUnavailable("Connect personal WhatsApp first.")
            if grant.status == "active":
                try:
                    await self._ready()
                    return {"grant": grant.public_status(), "removed_chat_ids": []}
                except (GrantUnavailable, GrantConflict):
                    grant = await self._load()
                    if grant is None:
                        raise GrantUnavailable("Connect personal WhatsApp first.")
            if grant.status == "needs_attention":
                # Repair metadata only with the exact unexpired sealed consent.
                # Expired/revoked consent must take the full retirement/reauthorization path.
                grant = await self._verify_consent(grant)
                if not grant.expires_at or grant.expires_at <= datetime.now(UTC):
                    raise GrantUnavailable(
                        "Disconnect expired consent before reconnecting."
                    )
                grant = await self._save(grant, status="pending_credential")
            if grant.status != "pending_credential" or not grant.native_user_ref:
                raise GrantUnavailable(
                    "Select the native account before reconciliation."
                )
            grant = await self._verify_consent(grant)
            return await self._activate(grant)

    async def messages(self, chat_id: str, *, cursor: str | None = None) -> dict:
        async with self.lock:
            grant = await self._ready()
            async with self._read_boundary(grant):
                result = await self.native.messages(grant, chat_id, cursor=cursor)
                await self.fence()
                return result

    async def message(self, chat_id: str, message_id: str) -> dict:
        async with self.lock:
            grant = await self._ready()
            async with self._read_boundary(grant):
                message = await retrieve_message(
                    self.native, grant, chat_id, message_id
                )
                await self.fence()
                return {
                    "message": project_message(message, grant, chat_id)
                    if message
                    else None
                }

    async def attachment(
        self, chat_id: str, message_id: str, index: int, revision: str
    ) -> NativeAttachment:
        async with self.lock:
            grant = await self._ready()
            message = await retrieve_message(self.native, grant, chat_id, message_id)
            if (
                message is None
                or not 0 <= index < len(message.attachments)
                or attachment_revision(message, index) != revision
            ):
                raise GrantConflict(
                    "This attachment changed. Refresh the conversation."
                )
        # Never hold the owner lock while downloading: Disconnect must win.
        asset = await read_attachment(self.native, grant, message, index)
        async with self.lock:
            async with self._read_boundary(grant):
                current = await retrieve_message(
                    self.native, grant, chat_id, message_id
                )
                if (
                    current is None
                    or not 0 <= index < len(current.attachments)
                    or attachment_revision(current, index) != revision
                ):
                    raise GrantConflict(
                        "This attachment changed. Refresh the conversation."
                    )
                await self.fence()
        return asset

    async def refresh(self) -> dict:
        async with self.lock:
            grant = await self._ready()
            return {
                **grant.public_status(),
                "refresh_supported": False,
                "renewal": "reauthorization",
            }

    async def confirm_native_cleanup(
        self, *, expected_revision: int, client_label: str
    ) -> dict:
        """Owner attests removing the exact approval in Beeper; never a provider verification claim."""
        async with self.lock:
            grant = await self._load()
            if (
                not grant
                or grant.revision != expected_revision
                or not client_label
                or client_label != grant.client_label
            ):
                raise GrantConflict("Native cleanup confirmation changed. Reload.")
            if not (
                grant.registration_pending
                or grant.exchange_pending
                or grant.client_secret
                or grant.registration_access_token
            ):
                raise GrantUnavailable(
                    "This consent does not require manual native cleanup."
                )
            if grant.status != "revoking":
                grant = await self._save(
                    grant,
                    status="revoking",
                    provider_revoke_pending=True,
                    cloud_retirement_pending=bool(
                        grant.account_ref and grant.native_user_ref and grant.expires_at
                    ),
                )
            # Known issued tokens need actual provider proof even when the UI entry was removed.
            for name, token in (
                {"access_token": grant.access_token, **grant.cleanup_credentials}
            ).items():
                if token:
                    await self.native.verify_inactive(
                        token,
                        token_type_hint="refresh_token"
                        if name == "refresh_token"
                        else "access_token",
                    )
            await self.fence()
            confirmed = DeviceGrant.model_validate(
                {
                    **grant.model_dump(),
                    "access_token": None,
                    "client_secret": None,
                    "registration_access_token": None,
                    "cleanup_credentials": {},
                    "registration_pending": False,
                    "exchange_pending": False,
                    "provider_revoke_pending": False,
                    "oauth_verifier": None,
                    "oauth_state_hash": None,
                    "oauth_expires_at": None,
                    "cleanup_confirmation": "owner_attested",
                    "cleanup_confirmation_generation": grant.generation,
                }
            )
            grant = await self.repo.save(
                confirmed,
                expected_revision=grant.revision,
                cleanup_client_label=client_label,
            )
            if grant.cloud_retirement_pending:
                await self.cloud.retire(grant)
                grant = await self._save(grant, cloud_retirement_pending=False)
            grant = await self._save(grant, status="retired")
            return grant.public_status()

    async def disconnect(self) -> dict:
        async with self.lock:
            grant = await self._load()
            if grant is None:
                return {"status": "not_connected"}
            if grant.status == "retired":
                return grant.public_status()
            if grant.registration_pending or grant.exchange_pending:
                raise GrantUnavailable(
                    "Interrupted native issuance requires observed cleanup in Beeper Authorized Connections."
                )
            if grant.client_secret or grant.registration_access_token:
                raise GrantUnavailable(
                    "Unexpected registration credentials require observed native cleanup."
                )
            if grant.status != "revoking":
                grant = await self._save(
                    grant,
                    status="revoking",
                    provider_revoke_pending=bool(grant.access_token),
                    cloud_retirement_pending=bool(
                        grant.account_ref and grant.native_user_ref and grant.expires_at
                    ),
                )
            if grant.provider_revoke_pending and grant.access_token:
                await self.native.revoke_credentials(
                    IssuedCredentials(
                        access_token=grant.access_token,
                        other_credentials=grant.cleanup_credentials,
                    )
                )
                grant = await self._save(grant, provider_revoke_pending=False)
            if grant.cloud_retirement_pending:
                await self.cloud.retire(grant)
                grant = await self._save(grant, cloud_retirement_pending=False)
            await self.fence()
            grant = await self._save(
                grant,
                status="retired",
                access_token=None,
                cleanup_credentials={},
                oauth_verifier=None,
                oauth_state_hash=None,
                oauth_expires_at=None,
            )
            return grant.public_status()
