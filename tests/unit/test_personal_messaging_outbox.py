"""Real SQLite journal, actual encryption/CAS, no provider send mocks."""
import asyncio
from uuid import uuid4
from cryptography.fernet import Fernet
import pytest
from app.services.local_db import secret_store
from app.services.local_db.database import LocalDatabase
from app.services.personal_messaging import outbox
from app.services.personal_messaging.beeper import BeeperFailure
from app.services.personal_messaging.models import GrantConflict
from test_personal_messaging_beeper import grant

async def scenario(tmp_path, monkeypatch, run):
    monkeypatch.setattr(secret_store, "_fernet", Fernet(Fernet.generate_key()))
    db = LocalDatabase(tmp_path / "private.db")
    await db.connect()
    try:
        await run(outbox.NativeOutboxRepo(db), db)
    finally:
        await db.close()


def test_same_uuid_replays_one_sealed_draft_and_changed_payload_refuses(tmp_path, monkeypatch):
    async def run(repo, db):
        g=grant(); key=uuid4()
        entry,created=await repo.reserve(g,"sandbox",key,"non-sensitive fixture")
        assert created and entry.state=="pending"
        again,created=await repo.reserve(g,"sandbox",key,"non-sensitive fixture")
        assert not created and again.client_id==key
        with pytest.raises(GrantConflict):await repo.reserve(g,"sandbox",key,"other text")
        rows=await db.fetchall("SELECT * FROM personal_message_outbox")
        assert len(rows)==1 and "non-sensitive fixture" not in str(dict(rows[0]))
        assert rows[0]["payload_cipher"].startswith("enc:v1:")
        assert "non-sensitive fixture" not in repr(entry)
    asyncio.run(scenario(tmp_path,monkeypatch,run))


def test_response_loss_restart_new_uuid_and_reconnect_never_resume_send(tmp_path, monkeypatch):
    async def run(repo, db):
        g=grant();key=uuid4();await repo.reserve(g,"sandbox",key,"sandbox text")
        await repo.transition(g,"sandbox",key,expected=("pending",),state="dispatching")
        restarted=outbox.NativeOutboxRepo(db)
        rows=await restarted.recent(g,"sandbox")
        assert len(rows)==1 and rows[0].state=="unknown"
        for current in (g,g.model_copy(update={"generation":g.generation+1})):
            with pytest.raises(BeeperFailure) as err:await restarted.reserve(current,"sandbox",uuid4(),"sandbox text")
            assert err.value.code=="send_unresolved"
        assert (await restarted.get(g,"sandbox",key)).state=="unknown"
    asyncio.run(scenario(tmp_path,monkeypatch,run))


def test_retirement_cancels_only_undispatched_and_keeps_ambiguous_work(tmp_path, monkeypatch):
    async def run(repo, db):
        g=grant();a,b=uuid4(),uuid4()
        await repo.reserve(g,"first",a,"first text")
        await repo.reserve(g,"second",b,"second text")
        await repo.transition(g,"second",b,expected=("pending",),state="dispatching")
        await repo.retire(g)
        assert (await repo.get(g,"first",a)).state=="cancelled"
        assert (await repo.get(g,"second",b)).state=="dispatching"
        other=g.model_copy(update={"actor_id":uuid4()})
        assert await repo.get(other,"second",b) is None
        assert await repo.recent(g,"unselected-other-chat")==[]
    asyncio.run(scenario(tmp_path,monkeypatch,run))


def test_duplicate_dispatch_transition_fails_before_any_second_transport(tmp_path, monkeypatch):
    async def run(repo, db):
        g=grant();key=uuid4();await repo.reserve(g,"sandbox",key,"sandbox text")
        await repo.transition(g,"sandbox",key,expected=("pending",),state="dispatching")
        with pytest.raises(GrantConflict):await repo.transition(g,"sandbox",key,expected=("pending",),state="dispatching")
        saved=await repo.transition(g,"sandbox",key,expected=("dispatching",),state="accepted",pending_message_id="pending-receipt")
        assert saved.state=="accepted" and saved.network_status is None and saved.delivered is None and saved.seen is None
    asyncio.run(scenario(tmp_path,monkeypatch,run))
