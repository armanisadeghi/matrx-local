"""Real coordinator/store/native/cloud chain; only remote HTTP/daemon owner are simulated.
Use case: admin's approved read-only WhatsApp sandbox; no private chat content is recorded.
These guards do not claim actual customer/provider completion.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.services.local_db.database import LocalDatabase
from app.services.local_db import secret_store
from app.services.personal_messaging import beeper, cloud, coordinator, store
from app.services.personal_messaging.models import GrantUnavailable, GrantConflict


class Remote:
    def __init__(self, actor):
        self.actor = actor
        self.expiry = int((datetime.now(UTC) + timedelta(hours=23)).timestamp())
        self.exchanges = 0
        self.registrations = 0
        self.revoked = set()
        self.row = None
        self.resources = {}
        self.messages_read = 0
        self.fail_cloud = False
        self.fail_resource_once = False
        self.fail_revoke = False
        self.fail_exchange = False
        self.swap_user = False
        self.drop_chat = False
        self.dcr_name = ""
        self.registration_redirect = ""
        self.verifier = ""
        self.before_issuance = None

    def handle(self, request):
        path = request.url.path
        if request.url.host == "127.0.0.1":
            if path == "/.well-known/oauth-authorization-server":
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
            if path == "/oauth/register":
                self.registrations += 1
                body = json.loads(request.content)
                self.dcr_name = body["client_name"]
                self.registration_redirect = body["redirect_uris"][0]
                return httpx.Response(
                    201, json={**body, "client_id": "public-device-client"}
                )
            if path == "/oauth/token":
                self.exchanges += 1
                self.before_issuance()
                if self.fail_exchange:
                    raise httpx.ConnectError("Interrupted issuance", request=request)
                form = parse_qs(request.content.decode())
                self.verifier = form["code_verifier"][0]
                assert form["redirect_uri"][0] == self.registration_redirect
                return httpx.Response(
                    200,
                    json={
                        "access_token": "device-read-" + str(self.exchanges),
                        "token_type": "Bearer",
                        "scope": "read",
                        "expires_in": 82800,
                    },
                )
            if path == "/oauth/introspect":
                form = parse_qs(request.content.decode())
                token = form["token"][0]
                return httpx.Response(
                    200,
                    json={
                        "active": token not in self.revoked,
                        "scope": "read",
                        "exp": self.expiry,
                    },
                )
            if path == "/oauth/revoke":
                if self.fail_revoke:
                    return httpx.Response(503)
                self.revoked.add(parse_qs(request.content.decode())["token"][0])
                return httpx.Response(200)
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token in self.revoked:
                return httpx.Response(401)
            if path == "/v1/accounts":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "accountID": "whatsapp",
                            "status": "connected",
                            "user": {
                                "id": "other-native-owner"
                                if self.swap_user
                                else "native-sandbox-owner",
                                "displayText": "WhatsApp sandbox",
                            },
                            "bridge": {
                                "id": "wa",
                                "type": "whatsapp",
                                "provider": "cloud",
                            },
                        }
                    ],
                )
            if path == "/v1/chats":
                assert request.url.params["accountIDs"] == "whatsapp"
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "sandbox",
                                "accountID": "whatsapp",
                                "title": "WhatsApp sandbox",
                            }
                        ],
                        "hasMore": False,
                        "oldestCursor": "opaque-before",
                        "newestCursor": "opaque-after",
                    },
                )
            if path == "/v1/chats/sandbox":
                if self.drop_chat:
                    return httpx.Response(404)
                return httpx.Response(
                    200,
                    json={
                        "id": "sandbox",
                        "accountID": "whatsapp",
                        "title": "WhatsApp sandbox",
                    },
                )
            if path == "/v1/chats/sandbox/messages":
                self.messages_read += 1
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "id": "sandbox-message",
                                "accountID": "whatsapp",
                                "chatID": "sandbox",
                                "timestamp": datetime.now(UTC).isoformat(),
                                "senderID": "native-sandbox-sender",
                                "text": "Sandbox receive proof",
                                "isSender": False,
                            }
                        ],
                        "hasMore": False,
                        "oldestCursor": "opaque-before",
                        "newestCursor": "opaque-after",
                    },
                )
            raise AssertionError("Unexpected native operation " + path)
        assert str(request.url).startswith(cloud.SUPABASE_URL)
        if self.fail_cloud:
            return httpx.Response(503)
        if path == "/rest/v1/integration_connections":
            return httpx.Response(200, json=[self.row] if self.row else [])
        if path == "/rest/v1/integration_connection_resources":
            generation = int(
                request.url.params["metadata->>native_grant_generation"].removeprefix(
                    "eq."
                )
            )
            return httpx.Response(
                200,
                json=[
                    r
                    for r in self.resources.values()
                    if r["metadata"]["selected"]
                    and r["metadata"]["native_grant_generation"] == generation
                ],
            )
        assert path == "/rest/v1/rpc/personal_native_device_connection"
        body = json.loads(request.content)
        meta = body["p_binding"]
        previous = body["p_previous"]
        if body["p_operation"] == "create":
            assert self.row is None
            self.row = {
                "id": str(uuid4()),
                "owner_type": "user",
                "owner_user_id": str(self.actor),
                "organization_id": None,
                "provider": "beeper_whatsapp",
                "provider_subject": meta["native_user_subject"],
                "scopes": ["read"],
                "status": "needs_attention",
                "updated_at": datetime.now(UTC).isoformat(),
                "deleted_at": None,
                "metadata": meta,
            }
        elif body["p_operation"] == "update":
            if (
                previous["revision"] != self.row["metadata"]["native_grant_revision"]
                or previous["updated_at"] != self.row["updated_at"]
            ):
                return httpx.Response(200, json=[])
            self.row = {
                **self.row,
                "metadata": meta,
                "status": previous["status"],
                "updated_at": datetime.now(UTC).isoformat(),
            }
        elif body["p_operation"] == "resource":
            if self.fail_resource_once:
                self.fail_resource_once = False
                return httpx.Response(503)
            if previous["revision"] != self.row["metadata"]["native_grant_revision"]:
                return httpx.Response(200, json=[])
            key = (previous["resource_type"], previous["resource_ref"])
            resource = {
                "id": self.resources.get(key, {}).get("id", str(uuid4())),
                "connection_id": self.row["id"],
                "resource_type": previous["resource_type"],
                "resource_ref": previous["resource_ref"],
                "display_name": previous["display_name"],
                "permission_level": "read",
                "metadata": {
                    k: meta[k]
                    for k in (
                        "app_instance_id",
                        "native_grant_id",
                        "native_grant_generation",
                        "native_user_subject",
                        "account_ref",
                    )
                },
            }
            resource["metadata"]["selected"] = previous["selected"]
            self.resources[key] = resource
            return httpx.Response(200, json=[resource])
        else:
            raise AssertionError("Unknown metadata operation")
        return httpx.Response(200, json=[self.row])


async def scenario(tmp_path, monkeypatch, run):
    monkeypatch.setattr(secret_store, "_fernet", Fernet(Fernet.generate_key()))
    db = LocalDatabase(tmp_path / "personal.db")
    await db.connect()
    actor, device = uuid4(), uuid4()
    repo = store.PersonalMessagingGrantRepo(db)
    remote = Remote(actor)
    allowed = True

    async def fence():
        if not allowed:
            raise GrantUnavailable("Device owner changed")

    async def access():
        return ("owner-jwt", str(actor))

    monkeypatch.setattr(
        cloud, "get_sync_client", lambda: SimpleNamespace(access_grant=access)
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(
            **{"transport": httpx.MockTransport(remote.handle), **kwargs}
        ),
    )
    service = coordinator.PersonalMessagingCoordinator(
        actor_id=actor,
        app_instance_id=device,
        engine_port=22240,
        fence=fence,
        repo=repo,
    )

    def check_sealed_marker():
        # Synchronous test observes only in-memory issuance inputs; SQLite custody is asserted after callback.
        assert remote.dcr_name.startswith("AI Matrx WhatsApp ")

    remote.before_issuance = check_sealed_marker
    try:
        await run(service, repo, remote)
    finally:
        await db.close()


async def authorize(service):
    started = await service.start()
    args = parse_qs(urlsplit(started["authorization_url"]).query)
    await service.callback(
        state=SecretStr(args["state"][0]), code=SecretStr("native-code")
    )
    return args


async def connect_selected(service):
    args = await authorize(service)
    await service.select_account("whatsapp")
    await service.select_chat("sandbox")
    return args


def test_reconnect_revalidates_resource_generation_before_post_reconnect_read(
    tmp_path, monkeypatch
):
    async def run(service, repo, remote):
        await connect_selected(service)
        first = await service.messages("sandbox")
        assert (
            first["items"][0]["content"]["parts"][0]["text"] == "Sandbox receive proof"
        )
        assert (await service.refresh())["renewal"] == "reauthorization"
        retired = await service.disconnect()
        assert retired["status"] == "retired" and remote.revoked == {"device-read-1"}
        with pytest.raises(beeper.BeeperFailure, match="Reconnect"):
            await service.messages("sandbox")
        await authorize(service)
        with pytest.raises(beeper.BeeperFailure, match="Reconnect"):
            await service.messages("sandbox")
        result = await service.select_account("whatsapp")
        assert (
            result["grant"]["generation"]
            == result["grant"]["resources_generation"]
            == 2
        )
        assert remote.registrations == 1 and remote.exchanges == 2
        assert (
            remote.resources[("beeper_whatsapp_chat", "sandbox")]["metadata"][
                "native_grant_generation"
            ]
            == 2
        )
        assert (await service.messages("sandbox"))["items"][0]["direction"] == "inbound"
        persisted = str(await repo.settings.get_all())
        assert "device-read-" not in persisted and "native-code" not in persisted
        assert remote.messages_read == 2

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_callback_is_one_use_across_repository_restart(tmp_path, monkeypatch):
    async def run(service, repo, remote):
        args = await authorize(service)
        restarted = coordinator.PersonalMessagingCoordinator(
            actor_id=service.actor_id,
            app_instance_id=service.app_instance_id,
            engine_port=22240,
            fence=service.fence,
            repo=store.PersonalMessagingGrantRepo(repo.settings._db),
        )
        with pytest.raises(GrantUnavailable, match="already used"):
            await restarted.callback(
                state=SecretStr(args["state"][0]), code=SecretStr("native-code")
            )
        assert remote.exchanges == 1

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_interrupted_issuance_is_quarantined_without_remint(tmp_path, monkeypatch):
    async def run(service, repo, remote):
        started = await service.start()
        state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
        remote.fail_exchange = True
        with pytest.raises(beeper.BeeperFailure, match="Open Beeper"):
            await service.callback(
                state=SecretStr(state), code=SecretStr("native-code")
            )
        status = await service.status()
        assert status["recovery_required"] is True
        with pytest.raises(GrantUnavailable, match="observed cleanup"):
            await service.disconnect()
        with pytest.raises(GrantUnavailable, match="previous consent"):
            await service.start()
        assert remote.exchanges == 1

    asyncio.run(scenario(tmp_path, monkeypatch, run))


@pytest.mark.parametrize("failure", ["provider", "cloud"])
def test_cleanup_outage_keeps_reads_denied_and_reconnect_refused(
    tmp_path, monkeypatch, failure
):
    async def run(service, repo, remote):
        await connect_selected(service)
        remote.fail_revoke = failure == "provider"
        remote.fail_cloud = failure == "cloud"
        with pytest.raises((beeper.BeeperFailure, GrantUnavailable)):
            await service.disconnect()
        assert (await service.status())["status"] == "revoking"
        with pytest.raises(beeper.BeeperFailure, match="Reconnect"):
            await service.messages("sandbox")
        with pytest.raises(GrantUnavailable, match="previous consent"):
            await service.start()
        remote.fail_revoke = remote.fail_cloud = False
        assert (await service.disconnect())["status"] == "retired"

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_same_account_id_cannot_swap_native_whatsapp_user(tmp_path, monkeypatch):
    async def run(service, repo, remote):
        await connect_selected(service)
        remote.swap_user = True
        with pytest.raises(beeper.BeeperFailure, match="disconnected"):
            await service.messages("sandbox")
        assert remote.messages_read == 0

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_old_cloud_resource_receipt_never_authorizes_current_chat(
    tmp_path, monkeypatch
):
    async def run(service, repo, remote):
        await connect_selected(service)
        remote.resources[("beeper_whatsapp_chat", "sandbox")]["metadata"][
            "native_grant_generation"
        ] = 0
        with pytest.raises(GrantUnavailable, match="receipts are missing"):
            await service.messages("sandbox")
        assert remote.messages_read == 0

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_longer_than_approved_grant_stays_in_cleanup_custody(tmp_path, monkeypatch):
    async def run(service, repo, remote):
        remote.expiry = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        with pytest.raises(GrantUnavailable, match="one-day"):
            await authorize(service)
        status = await service.status()
        assert status["status"] == "needs_attention"
        grant = (
            await repo.list(
                actor_id=service.actor_id, app_instance_id=service.app_instance_id
            )
        )[0]
        assert grant.access_token.get_secret_value() == "device-read-1"
        assert (await service.disconnect())["status"] == "retired"
        assert remote.revoked == {"device-read-1"}

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_bad_callback_state_never_exchanges_the_native_code(tmp_path, monkeypatch):
    async def run(service, repo, remote):
        await service.start()
        with pytest.raises(GrantUnavailable, match="expired or was already used"):
            await service.callback(
                state=SecretStr("unrelated-state"), code=SecretStr("native-code")
            )
        assert remote.exchanges == 0

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_reconnect_clears_missing_chat_with_explicit_result(tmp_path, monkeypatch):
    async def run(service, repo, remote):
        await connect_selected(service)
        await service.disconnect()
        await authorize(service)
        remote.drop_chat = True
        result = await service.select_account("whatsapp")
        assert (
            result["removed_chat_ids"] == ["sandbox"]
            and result["grant"]["selected_chat_ids"] == []
        )
        with pytest.raises(beeper.BeeperFailure, match="Select this"):
            await service.messages("sandbox")
        assert remote.messages_read == 0

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_deselect_denies_old_chat_without_unlinking_native_account(
    tmp_path, monkeypatch
):
    async def run(service, repo, remote):
        await connect_selected(service)
        await service.select_chat("sandbox", selected=False)
        with pytest.raises(beeper.BeeperFailure, match="Select this"):
            await service.messages("sandbox")
        assert (
            remote.resources[("beeper_whatsapp_chat", "sandbox")]["metadata"][
                "selected"
            ]
            is False
        )
        assert remote.revoked == set() and remote.messages_read == 0

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_quarantined_exchange_recovers_only_after_exact_owner_cleanup_confirmation(
    tmp_path, monkeypatch
):
    async def run(service, repo, remote):
        started = await service.start()
        state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
        remote.fail_exchange = True
        with pytest.raises(beeper.BeeperFailure):
            await service.callback(
                state=SecretStr(state), code=SecretStr("native-code")
            )
        grant = (
            await repo.list(
                actor_id=service.actor_id, app_instance_id=service.app_instance_id
            )
        )[0]
        with pytest.raises(GrantConflict, match="confirmation changed"):
            await service.confirm_native_cleanup(
                expected_revision=grant.revision, client_label="different client"
            )
        retired = await service.confirm_native_cleanup(
            expected_revision=grant.revision, client_label=grant.client_label
        )
        assert (
            retired["status"] == "retired"
            and retired["cleanup_confirmation"] == "owner_attested"
        )
        assert (
            retired["cleanup_confirmation_generation"] == 1 and remote.revoked == set()
        )
        remote.fail_exchange = False
        await authorize(service)
        connected = await service.select_account("whatsapp")
        assert connected["grant"]["generation"] == 2
        assert connected["grant"]["cleanup_confirmation_generation"] == 1

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_failed_deselect_reconciles_extra_current_generation_receipt(
    tmp_path, monkeypatch
):
    async def run(service, repo, remote):
        await connect_selected(service)
        remote.fail_resource_once = True
        with pytest.raises(GrantUnavailable):
            await service.select_chat("sandbox", selected=False)
        assert (
            remote.resources[("beeper_whatsapp_chat", "sandbox")]["metadata"][
                "selected"
            ]
            is True
        )
        try:
            result = await service.reconcile()
        except GrantUnavailable:
            assert remote.resources[("beeper_whatsapp_chat", "sandbox")]["metadata"]["selected"] is False, "Reconciliation left a stale selected receipt"
            raise
        assert (
            result["grant"]["status"] == "active"
            and result["grant"]["selected_chat_ids"] == []
        )
        assert (
            remote.resources[("beeper_whatsapp_chat", "sandbox")]["metadata"][
                "selected"
            ]
            is False
        )
        with pytest.raises(beeper.BeeperFailure, match="Select this"):
            await service.messages("sandbox")

    asyncio.run(scenario(tmp_path, monkeypatch, run))


@pytest.mark.parametrize("loss", ["expiry", "account"])
def test_readiness_loss_persists_needs_attention_before_refusing_read(
    tmp_path, monkeypatch, loss
):
    async def run(service, repo, remote):
        await connect_selected(service)
        if loss == "account":
            remote.swap_user = True
        else:
            remote.expiry = int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())
        with pytest.raises(beeper.BeeperFailure):
            await service.messages("sandbox")
        persisted = (
            await repo.list(
                actor_id=service.actor_id, app_instance_id=service.app_instance_id
            )
        )[0]
        assert persisted.status == "needs_attention" and remote.messages_read == 0
        with pytest.raises(GrantUnavailable, match="previous consent"):
            await service.start()
        assert (await service.disconnect())["status"] == "retired"

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_extra_current_generation_selection_refuses_native_read(tmp_path, monkeypatch):
    async def run(service, repo, remote):
        await connect_selected(service)
        existing = remote.resources[("beeper_whatsapp_chat", "sandbox")]
        remote.resources[("beeper_whatsapp_chat", "unselected-chat")] = {
            **existing,
            "id": str(uuid4()),
            "resource_ref": "unselected-chat",
            "display_name": "Unselected conversation",
        }
        with pytest.raises(GrantUnavailable, match="inconsistent"):
            await service.messages("sandbox")
        assert remote.messages_read == 0

    asyncio.run(scenario(tmp_path, monkeypatch, run))


@pytest.mark.parametrize("loss", ["provider", "account", "cloud", "revision"])
def test_mid_read_loss_refuses_private_payload_and_demotes_current_grant(tmp_path, monkeypatch, loss):
    async def run(service, repo, remote):
        await connect_selected(service)
        native_read = service.native.messages

        async def changed(grant, chat_id, **kwargs):
            value = await native_read(grant, chat_id, **kwargs)
            if loss == "provider":
                raise beeper.BeeperFailure("reauthorize", "Native access ended during read")
            if loss == "account":
                remote.swap_user = True
            elif loss == "cloud":
                remote.fail_cloud = True
            elif loss == "revision":
                await service._save(grant, selected_chat_ids=[])
            return value

        monkeypatch.setattr(service.native, "messages", changed)
        with pytest.raises((beeper.BeeperFailure, GrantUnavailable, GrantConflict)):
            await service.messages("sandbox")
        persisted = (await repo.list(actor_id=service.actor_id, app_instance_id=service.app_instance_id))[0]
        assert persisted.status == "needs_attention" and remote.messages_read == 1
    asyncio.run(scenario(tmp_path, monkeypatch, run))
