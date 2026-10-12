"""Actual native HTTP adapter with a documented provider boundary, not fake delivery."""
import asyncio
from datetime import UTC,datetime
import httpx
import pytest
from app.services.personal_messaging import beeper
from test_personal_messaging_beeper import grant,native_client

def writable():
    return grant().model_copy(update={"request_profile":"read_write","scopes":["read","write"]})

def direct(**changes):
    return {"id":"sandbox","accountID":"whatsapp","title":"Sandbox","type":"single","isReadOnly":False,"capabilities":{"maxTextLength":12},**changes}

def test_read_grant_cannot_dispatch_or_claim_write_token(monkeypatch):
    calls=[];native_client(monkeypatch,lambda r:calls.append(r))
    with pytest.raises(beeper.BeeperFailure) as err:asyncio.run(beeper.DesktopApi().send_text(grant(),"sandbox","hello"))
    assert err.value.code=="write_consent_required" and not calls

@pytest.mark.parametrize("change", [{"merge":{"chatIDs":["sandbox","other"]}},{"isReadOnly":True},{"accountID":"other"},{"id":"other"},{"capabilities":{"maxTextLength":0}}])
def test_predispatch_writable_target_and_capability_fail_closed(monkeypatch,change):
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(200,json=direct(**change))
    native_client(monkeypatch,handler)
    g=writable();g.selected_chat_ids=["sandbox"]
    with pytest.raises(beeper.BeeperFailure):asyncio.run(beeper.DesktopApi().writable_chat(g,"sandbox","hello"))
    assert calls and all(r.method=="GET" for r in calls)

def test_alias_resolves_only_stored_target_outbound_not_body_equivalence(monkeypatch):
    def handler(request):
        if request.url.path=="/v1/accounts":return httpx.Response(200,json=[{"accountID":"whatsapp","status":"connected","user":{"id":"native-owner"},"bridge":{"id":"wa","type":"whatsapp","provider":"cloud"}}])
        if request.url.path.endswith("/messages/pending-native-id"):
            return httpx.Response(200,json={"id":"final-native-id","accountID":"whatsapp","chatID":"sandbox","senderID":"different-provider-namespace","isSender":True,"timestamp":datetime.now(UTC).isoformat(),"text":"normalized rich text","sendStatus":{"status":"SUCCESS","timestamp":datetime.now(UTC).isoformat()}})
        return httpx.Response(200,json=direct())
    native_client(monkeypatch,handler)
    result=asyncio.run(beeper.DesktopApi().resolve_sent(writable(),"sandbox","pending-native-id"))
    assert result and result.id=="final-native-id" and result.sendStatus.status=="SUCCESS"

def test_scope_comparison_is_set_semantics_without_unrequested_permissions():
    assert beeper.returned_scope_matches("write read",("read","write"))
    assert not beeper.returned_scope_matches("read write",("read",))
    assert not beeper.returned_scope_matches("read write manage",("read","write"))
