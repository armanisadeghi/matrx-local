"""HTTP contract forcing checks; not provider or customer lifecycle evidence."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from types import SimpleNamespace
import httpx
import pytest
from pydantic import SecretStr
from app.services.personal_messaging.models import (
    DeviceGrant,
    GrantConflict,
    GrantUnavailable,
)

from app.services.personal_messaging import cloud


def grant():
    return DeviceGrant(
        id=uuid4(),
        actor_id=uuid4(),
        app_instance_id=uuid4(),
        generation=1,
        status="pending_credential",
        client_id="public",
        redirect_uri="http://127.0.0.1:22240/personal-messaging/beeper/callback",
        access_token=SecretStr("native-never-cloud"),
        account_ref="whatsapp",
        native_user_ref="native-person",
        scopes=["read"],
        expires_at=datetime.now(UTC) + timedelta(hours=23),
        bridge_provider="cloud",
    )


def receipt(g, client, revision=1, status="needs_attention"):
    return dict(
        id=str(g.connection_id or uuid4()),
        owner_type="user",
        owner_user_id=str(g.actor_id),
        organization_id=None,
        provider="beeper_whatsapp",
        provider_subject=client._subject(g),
        scopes=["read"],
        status=status,
        updated_at=datetime.now(UTC).isoformat(),
        deleted_at=None,
        metadata=client._metadata(g, revision).model_dump(mode="json"),
    )


def harness(monkeypatch, g, handler, owner=None):
    async def access():
        return ("actor-session", str(owner or g.actor_id))

    monkeypatch.setattr(
        cloud, "get_sync_client", lambda: SimpleNamespace(access_grant=access)
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        cloud.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    return cloud.NativeConnectionClient(g.actor_id, g.app_instance_id)


def test_post_uses_real_authenticated_column_contract_no_native_credentials(
    monkeypatch,
):
    g = grant()
    calls = []

    def handler(r):
        calls.append(r)
        assert r.headers["authorization"] == "Bearer actor-session"
        assert r.headers["accept-profile"] == r.headers["content-profile"] == "users"
        assert (
            b"native-never-cloud" not in r.content and b"native-person" not in r.content
        )
        if r.method == "GET":
            assert (
                r.url.params["select"]
                == "id,owner_type,owner_user_id,organization_id,provider,provider_subject,scopes,status,updated_at,deleted_at,metadata"
            )
            return httpx.Response(200, json=[])
        assert (
            r.method == "POST"
            and r.url.path == "/rest/v1/rpc/personal_native_device_connection"
        )
        p = json.loads(r.content)
        assert set(p) == {"p_operation", "p_binding", "p_previous"}
        assert p["p_operation"] == "create" and p["p_previous"] is None
        row = receipt(g, c)
        row["metadata"] = p["p_binding"]
        return httpx.Response(200, json=[row])

    c = harness(monkeypatch, g, handler)
    saved = asyncio.run(c.register_pending(g))
    assert saved.status == "needs_attention" and len(calls) == 2


def test_wrong_daemon_owner_cannot_send_any_cloud_request(monkeypatch):
    g = grant()
    calls = []
    c = harness(monkeypatch, g, lambda r: calls.append(r), owner=uuid4())
    with pytest.raises(GrantUnavailable):
        asyncio.run(c.find(g))
    assert not calls


def test_patch_requires_both_revision_and_timestamp_and_owner(monkeypatch):
    g = grant()

    def handler(r):
        assert (
            r.method == "POST"
            and r.url.path == "/rest/v1/rpc/personal_native_device_connection"
        )
        p = json.loads(r.content)
        assert p["p_operation"] == "update"
        assert p["p_previous"] == {
            "id": str(current.id),
            "revision": 1,
            "updated_at": current.updated_at.isoformat(),
            "status": "connected",
        }
        assert "owner_user_id" not in p
        row = current.model_dump(mode="json")
        row.update(
            status=p["p_previous"]["status"],
            metadata=p["p_binding"],
            updated_at=datetime.now(UTC).isoformat(),
        )
        return httpx.Response(200, json=[row])

    c = harness(monkeypatch, g, handler)
    current = cloud.CloudReceipt.model_validate(receipt(g, c))
    g.connection_id = current.id
    activated = asyncio.run(c.activate(g, current))
    assert (
        activated.status == "connected"
        and activated.metadata.native_grant_revision == 2
    )


def test_empty_patch_conflict_never_retries_stale_update(monkeypatch):
    g = grant()
    calls = []
    c = harness(
        monkeypatch, g, lambda r: (calls.append(r), httpx.Response(200, json=[]))[1]
    )
    current = cloud.CloudReceipt.model_validate(receipt(g, c))
    g.connection_id = current.id
    with pytest.raises(GrantConflict):
        asyncio.run(c.activate(g, current))
    assert len(calls) == 1 and calls[0].method == "POST"


def test_creation_conflict_cannot_adopt_another_grant(monkeypatch):
    g = grant()
    calls = []

    def handler(r):
        calls.append(r)
        if r.method == "POST":
            return httpx.Response(409)
        if len(calls) == 1:
            return httpx.Response(200, json=[])
        row = receipt(g, c)
        row["metadata"]["native_grant_id"] = str(uuid4())
        return httpx.Response(200, json=[row])

    c = harness(monkeypatch, g, handler)
    with pytest.raises(GrantConflict):
        asyncio.run(c.register_pending(g))
    assert [r.method for r in calls] == ["GET", "POST", "GET"]


def test_retired_generation_cannot_register_again(monkeypatch):
    g = grant()
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json=[receipt(g, c, status="disconnected")])

    c = harness(monkeypatch, g, handler)
    with pytest.raises(GrantConflict):
        asyncio.run(c.register_pending(g))
    assert [r.method for r in calls] == ["GET"]


def test_retire_requires_local_denial_first(monkeypatch):
    g = grant()
    calls = []
    c = harness(monkeypatch, g, lambda r: calls.append(r))
    with pytest.raises(GrantUnavailable):
        asyncio.run(c.retire(g))
    assert not calls


def test_switched_native_account_cannot_adopt_saved_receipt(monkeypatch):
    g = grant()

    def handler(r):
        row = receipt(g, c)
        row["metadata"]["account_ref"] = "another-account"
        return httpx.Response(200, json=[row])

    c = harness(monkeypatch, g, handler)
    with pytest.raises(GrantConflict):
        asyncio.run(c.find(g))


def test_missing_cloud_retirement_is_not_reported_complete(monkeypatch):
    g = grant()
    g.status = "revoking"
    g.connection_id = uuid4()
    c = harness(monkeypatch, g, lambda r: httpx.Response(200, json=[]))
    with pytest.raises(GrantConflict):
        asyncio.run(c.retire(g))


def test_cloud_error_never_echoes_native_or_session_credentials(monkeypatch):
    g = grant()
    c = harness(
        monkeypatch,
        g,
        lambda r: httpx.Response(
            500, json={"error": "actor-session native-never-cloud"}
        ),
    )
    with pytest.raises(GrantUnavailable) as e:
        asyncio.run(c.find(g))
    assert "actor-session" not in str(e.value) and "native-never-cloud" not in str(
        e.value
    )


def test_previously_connected_receipt_still_requires_activation_cas(monkeypatch):
    g = grant()
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json=[])

    c = harness(monkeypatch, g, handler)
    current = cloud.CloudReceipt.model_validate(receipt(g, c, status="connected"))
    g.connection_id = current.id
    with pytest.raises(GrantConflict):
        asyncio.run(c.activate(g, current))
    assert len(calls) == 1 and calls[0].method == "POST"


def test_only_device_selected_chat_can_be_saved(monkeypatch):
    g = grant()
    g.status = "active"
    g.connection_id = uuid4()
    g.selected_chat_ids = ["sandbox"]
    calls = []

    def handler(r):
        calls.append(r)
        p = json.loads(r.content)
        assert (
            r.method == "POST"
            and r.url.path == "/rest/v1/rpc/personal_native_device_connection"
        )
        assert (
            p["p_operation"] == "resource"
            and p["p_previous"]["resource_ref"] == "sandbox"
        )
        assert p["p_previous"]["revision"] == 1 and p["p_previous"]["id"] == str(
            current.id
        )
        meta = {
            k: v
            for k, v in p["p_binding"].items()
            if k
            in (
                "app_instance_id",
                "native_grant_id",
                "native_grant_generation",
                "native_user_subject",
                "account_ref",
            )
        }
        meta["selected"] = True
        return httpx.Response(
            200,
            json=[
                {
                    "id": str(uuid4()),
                    "connection_id": str(current.id),
                    "resource_type": "beeper_whatsapp_chat",
                    "resource_ref": "sandbox",
                    "display_name": "Sandbox",
                    "permission_level": "read",
                    "metadata": meta,
                }
            ],
        )

    c = harness(monkeypatch, g, handler)
    current = cloud.CloudReceipt.model_validate(receipt(g, c, status="connected"))
    with pytest.raises(GrantUnavailable):
        asyncio.run(
            c.select_resource(
                g,
                current,
                resource_type="beeper_whatsapp_chat",
                resource_ref="private-unselected",
                display_name="Unselected",
                selected=True,
            )
        )
    assert not calls
    saved = asyncio.run(
        c.select_resource(
            g,
            current,
            resource_type="beeper_whatsapp_chat",
            resource_ref="sandbox",
            display_name="Sandbox",
            selected=True,
        )
    )
    assert saved.metadata.selected and len(calls) == 1


def test_resource_receipt_cannot_switch_consent_generation(monkeypatch):
    g = grant()
    g.status = "active"
    g.connection_id = uuid4()
    g.selected_chat_ids = ["sandbox"]

    def handler(r):
        meta = {
            k: v
            for k, v in current.metadata.model_dump(mode="json").items()
            if k
            in (
                "app_instance_id",
                "native_grant_id",
                "native_grant_generation",
                "native_user_subject",
                "account_ref",
            )
        }
        meta.update(selected=True, native_grant_generation=2)
        return httpx.Response(
            200,
            json=[
                {
                    "id": str(uuid4()),
                    "connection_id": str(current.id),
                    "resource_type": "beeper_whatsapp_chat",
                    "resource_ref": "sandbox",
                    "display_name": "Sandbox",
                    "permission_level": "read",
                    "metadata": meta,
                }
            ],
        )

    c = harness(monkeypatch, g, handler)
    current = cloud.CloudReceipt.model_validate(receipt(g, c, status="connected"))
    with pytest.raises(GrantConflict):
        asyncio.run(
            c.select_resource(
                g,
                current,
                resource_type="beeper_whatsapp_chat",
                resource_ref="sandbox",
                display_name="Sandbox",
                selected=True,
            )
        )
