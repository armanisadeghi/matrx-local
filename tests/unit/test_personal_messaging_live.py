"""Exact selected event contracts; provider/customer live delivery remains separate."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from app.services.personal_messaging import live, beeper
from app.services.personal_messaging.models import GrantUnavailable
from test_personal_messaging_beeper import grant


class Socket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.queue.put_nowait(
            json.dumps({"type": "ready", "version": 1, "chatIDs": []})
        )
        self.sent = []

    async def recv(self):
        return await self.queue.get()

    async def send(self, body):
        command = json.loads(body)
        self.sent.append(command)
        self.queue.put_nowait(
            json.dumps(
                {
                    "type": "subscriptions.updated",
                    "requestID": command["requestID"],
                    "chatIDs": command["chatIDs"],
                }
            )
        )


def socket_fixture(monkeypatch, socket):
    @asynccontextmanager
    async def factory(url, **kwargs):
        assert url == "ws://127.0.0.1:23373/v1/ws"
        assert kwargs["additional_headers"] == {"Authorization": "Bearer fixture-token"}
        assert kwargs["proxy"] is None and kwargs["max_size"] == 1048576
        assert kwargs["logger"].level >= 30
        yield socket

    monkeypatch.setattr(live, "connect", factory)


class Native:
    active = staticmethod(beeper.DesktopApi.active)

    async def verify_chat(self, token, account_id, chat_id):
        assert account_id == "whatsapp" and chat_id == "sandbox"


class Service:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.grant = grant()
        self.native = Native()
        self.ready_checks = 0

    async def _ready(self):
        self.ready_checks += 1
        self.native.active(self.grant)
        return self.grant

    async def fence(self):
        pass

    @asynccontextmanager
    async def _read_boundary(self, snapshot):
        yield
        assert snapshot == self.grant


@pytest.mark.parametrize(
    "kind", ["message.upserted", "message.deleted", "chat.deleted"]
)
def test_only_credential_free_invalidation_not_hydrated_entries(kind):
    result = live.invalidation(
        {
            "type": kind,
            "seq": 8,
            "chatID": "sandbox",
            "ids": ["native-id"],
            "entries": [{"text": "PRIVATE", "access_token": "NEVER"}],
        },
        "sandbox",
        7,
    )
    assert (
        result.type == "changed"
        and "PRIVATE" not in result.model_dump_json()
        and "NEVER" not in result.model_dump_json()
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"chatID": "foreign"},
        {"seq": 7},
        {"seq": 6},
        {"seq": True},
        {"type": "unknown"},
        {"ids": ["x"] * 101},
        {"ids": ["x" * 1025]},
    ],
)
def test_wrong_subscription_or_stale_protocol_refuses(patch):
    with pytest.raises(beeper.BeeperFailure):
        live.invalidation(
            {
                "type": "message.upserted",
                "seq": 8,
                "chatID": "sandbox",
                "ids": ["native-id"],
                **patch,
            },
            "sandbox",
            7,
        )


def test_forward_sequence_gap_requests_reconcile_without_resetting():
    result = live.invalidation(
        {
            "type": "message.upserted",
            "seq": 18,
            "chatID": "sandbox",
            "ids": ["native-id"],
        },
        "sandbox",
        7,
    )
    assert result.type == "changed" and result.reconcile


def test_idle_stream_rechecks_original_authorization_and_does_not_hold_lock(
    monkeypatch,
):
    async def run():
        socket = Socket()
        socket_fixture(monkeypatch, socket)
        monkeypatch.setattr(live, "_HEARTBEAT_SECONDS", 0.001)
        service = Service()
        checks = 0

        async def authorize():
            nonlocal checks
            checks += 1
            if checks >= 3:
                raise GrantUnavailable("Original actor token expired")

        stream = live.live_frames(service, "sandbox", authorize)
        assert json.loads(await anext(stream))["type"] == "ready"
        assert (
            socket.sent[0]["chatIDs"] == ["sandbox"]
            and "*" not in socket.sent[0]["chatIDs"]
        )
        async with asyncio.timeout(0.1):
            async with service.lock:
                pass
        assert json.loads(await anext(stream))["type"] == "access_lost"
        assert checks == 3
        await stream.aclose()

    asyncio.run(run())


def test_native_retirement_during_idle_stream_ends_access(monkeypatch):
    async def run():
        socket = Socket()
        socket_fixture(monkeypatch, socket)
        monkeypatch.setattr(live, "_HEARTBEAT_SECONDS", 0.001)
        service = Service()

        async def authorize():
            pass

        stream = live.live_frames(service, "sandbox", authorize)
        assert json.loads(await anext(stream))["type"] == "ready"
        service.grant = service.grant.model_copy(
            update={"status": "retired", "access_token": None}
        )
        assert json.loads(await anext(stream))["type"] == "access_lost"
        await stream.aclose()

    asyncio.run(run())


def test_each_native_change_revalidates_before_emitting_selected_ids(monkeypatch):
    async def run():
        socket = Socket()
        socket_fixture(monkeypatch, socket)
        service = Service()
        checks = 0

        async def authorize():
            nonlocal checks
            checks += 1

        stream = live.live_frames(service, "sandbox", authorize)
        await anext(stream)
        socket.queue.put_nowait(
            json.dumps(
                {
                    "type": "message.upserted",
                    "seq": 8,
                    "chatID": "sandbox",
                    "ids": ["native-id"],
                    "entries": [{"text": "PRIVATE"}],
                }
            )
        )
        result = json.loads(await anext(stream))
        assert (
            result["ids"] == ["native-id"]
            and checks == 3
            and "PRIVATE" not in str(result)
        )
        await stream.aclose()

    asyncio.run(run())
