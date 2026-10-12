"""Real sealed store/cloud/native coordinator chain at the irreversible send boundary."""
import asyncio
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
import httpx
import pytest
from pydantic import SecretStr
from test_personal_messaging_coordinator import scenario, connect_selected
from app.services.personal_messaging.beeper import BeeperFailure
from app.services.personal_messaging.models import GrantConflict

async def setup(service, remote):
    original = remote.handle
    remote.sent = 0
    remote.lose_send = False
    remote.reject_send = 0
    def handle(request):
        path = request.url.path
        if request.url.host == "127.0.0.1" and path == "/v1/chats/sandbox/messages" and request.method == "POST":
            remote.sent += 1
            if remote.reject_send: return httpx.Response(remote.reject_send)
            if remote.lose_send: raise httpx.ReadError("response lost", request=request)
            return httpx.Response(200, json={"chatID":"sandbox","pendingMessageID":"pending-alias"})
        if request.url.host == "127.0.0.1" and path == "/v1/chats/sandbox/messages/pending-alias":
            return httpx.Response(200,json={"id":"final-id","accountID":"whatsapp","chatID":"sandbox","timestamp":datetime.now(UTC).isoformat(),"senderID":"different-namespace","isSender":True,"text":"sandbox fixture","sendStatus":{"status":"SUCCESS","timestamp":datetime.now(UTC).isoformat()}})
        response=original(request)
        if response.status_code < 300 and response.content:
            data=response.json()
            if path in ("/oauth/token","/oauth/introspect"): data["scope"]="write read"
            if path=="/v1/chats/sandbox":data.update(type="single",isReadOnly=False,capabilities={"maxTextLength":65536})
            if path=="/rest/v1/rpc/personal_native_device_connection":
                body=json.loads(request.content)
                if body["p_operation"] in ("create","update"):
                    remote.row["scopes"]=body["p_previous"]["scopes"];data=[remote.row]
                else:
                    for item in data:item["permission_level"]="read_write"
                    remote.resources[(body["p_previous"]["resource_type"],body["p_previous"]["resource_ref"])]["permission_level"]="read_write"
            return httpx.Response(response.status_code,json=data)
        return response
    remote.handle=handle
    started=await service.start(request_profile="read_write")
    args=parse_qs(urlsplit(started["authorization_url"]).query)
    assert args["scope"]==["read write"]
    await service.callback(state=SecretStr(args["state"][0]),code=SecretStr("native-code"))
    await service.select_account("whatsapp");await service.select_chat("sandbox")


def test_same_uuid_dispatches_once_and_pending_alias_is_real_final_receipt(tmp_path,monkeypatch):
    async def run(service,repo,remote):
        await setup(service,remote);service_grant=await service._load();key=uuid4()
        first=await service.send("sandbox",key,"sandbox fixture",service_grant.id,service_grant.generation)
        again=await service.send("sandbox",key,"sandbox fixture",service_grant.id,service_grant.generation)
        assert first==again and remote.sent==1 and first["state"]=="accepted"
        assert first["delivered"] is None and first["network_status"] is None
        receipt=(await service.outbox_status("sandbox"))[0]
        assert receipt["pending_message_id"]=="pending-alias" and receipt["final_message_id"]=="final-id"
        assert receipt["network_status"]=="SUCCESS" and receipt["delivered"] is None and receipt["seen"] is None
    asyncio.run(scenario(tmp_path,monkeypatch,run))


def test_response_loss_new_uuid_and_reconnect_never_send_again(tmp_path,monkeypatch):
    async def run(service,repo,remote):
        await setup(service,remote);service_grant=await service._load();remote.lose_send=True
        with pytest.raises(BeeperFailure):await service.send("sandbox",uuid4(),"sandbox fixture",service_grant.id,service_grant.generation)
        receipt=(await service.outbox_status("sandbox"))[0]
        assert receipt["state"]=="unknown" and remote.sent==1
        with pytest.raises(BeeperFailure) as error:await service.send("sandbox",uuid4(),"sandbox fixture",service_grant.id,service_grant.generation)
        assert error.value.code=="send_unresolved" and remote.sent==1
        retired=await service.disconnect();assert retired["status"]=="retired"
        with pytest.raises(BeeperFailure):await service.send("sandbox",uuid4(),"sandbox fixture",service_grant.id,service_grant.generation)
        assert remote.sent==1
    asyncio.run(scenario(tmp_path,monkeypatch,run))


def test_cancel_tombstone_wins_late_send_and_generation_change_refuses(tmp_path,monkeypatch):
    async def run(service,repo,remote):
        await setup(service,remote);g=await service._load();key=uuid4()
        tombstone=await service.cancel_unsubmitted("sandbox",key,g.id,g.generation)
        assert tombstone["state"]=="cancelled"
        with pytest.raises(GrantConflict,match="another draft"):
            await service.send("sandbox",key,"sandbox fixture",g.id,g.generation)
        with pytest.raises(GrantConflict,match="earlier WhatsApp connection"):
            await service.send("sandbox",uuid4(),"sandbox fixture",g.id,g.generation+1)
        assert remote.sent==0
    asyncio.run(scenario(tmp_path,monkeypatch,run))


@pytest.mark.parametrize("status",[400,401,403,404,429])
def test_definite_refusal_is_failed_not_unknown(tmp_path,monkeypatch,status):
    async def run(service,repo,remote):
        await setup(service,remote);g=await service._load();key=uuid4();remote.reject_send=status
        with pytest.raises(BeeperFailure):await service.send("sandbox",key,"sandbox fixture",g.id,g.generation)
        entry=await service.outbox.get(g,"sandbox",key)
        assert entry.state=="failed" and remote.sent==1
        assert (await service._load()).status == ("needs_attention" if status in (401,403,404) else "active")
    asyncio.run(scenario(tmp_path,monkeypatch,run))


def test_cancel_never_clears_unknown_dispatch(tmp_path,monkeypatch):
    async def run(service,repo,remote):
        await setup(service,remote);g=await service._load();key=uuid4();remote.lose_send=True
        with pytest.raises(BeeperFailure):await service.send("sandbox",key,"sandbox fixture",g.id,g.generation)
        result=await service.cancel_unsubmitted("sandbox",key,g.id,g.generation)
        assert result["state"]=="unknown" and remote.sent==1
    asyncio.run(scenario(tmp_path,monkeypatch,run))


def test_read_grant_cannot_create_cancel_tombstone(tmp_path,monkeypatch):
    async def run(service,repo,remote):
        await connect_selected(service);g=await service._load();key=uuid4()
        with pytest.raises(BeeperFailure) as error:await service.cancel_unsubmitted("sandbox",key,g.id,g.generation)
        assert error.value.code=="write_consent_required"
        assert await service.outbox.get(g,"sandbox",key) is None
    asyncio.run(scenario(tmp_path,monkeypatch,run))
