"""Selected-chat native events. Only credential-free invalidations cross the device."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4
from typing import TYPE_CHECKING

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .beeper import BeeperFailure
from .models import DeviceGrant, GrantConflict, GrantUnavailable
from pydantic import SecretStr
from .public import PersonalLiveFrame

if TYPE_CHECKING:
    from .coordinator import PersonalMessagingCoordinator

_WS_LOG = logging.getLogger("app.services.personal_messaging.private_websocket")
_WS_LOG.setLevel(logging.WARNING)  # Library DEBUG handshake logs contain Authorization.
_HEARTBEAT_SECONDS = 5.0


class LiveActorUnverified(RuntimeError):
    """Original bearer expired/unavailable; retry with current caller credentials."""


@asynccontextmanager
async def selected_subscription(token: SecretStr, chat_id: str):
    try:
        async with connect(
            "ws://127.0.0.1:23373/v1/ws",
            additional_headers={"Authorization": "Bearer " + token.get_secret_value()},
            proxy=None,
            max_size=1048576,
            max_queue=16,
            open_timeout=10,
            close_timeout=1,
            logger=_WS_LOG,
        ) as socket:
            ready = json.loads(await asyncio.wait_for(socket.recv(), 10))
            if (
                not isinstance(ready, dict)
                or ready.get("type") != "ready"
                or ready.get("version") != 1
            ):
                raise BeeperFailure(
                    "live_protocol_changed", "WhatsApp updates need reconnecting."
                )
            request_id = str(uuid4())
            await socket.send(
                json.dumps(
                    {
                        "type": "subscriptions.set",
                        "requestID": request_id,
                        "chatIDs": [chat_id],
                    }
                )
            )
            ack = json.loads(await asyncio.wait_for(socket.recv(), 10))
            if (
                not isinstance(ack, dict)
                or ack.get("type") != "subscriptions.updated"
                or ack.get("requestID") != request_id
                or ack.get("chatIDs") != [chat_id]
            ):
                raise BeeperFailure(
                    "live_protocol_changed", "WhatsApp updates need reconnecting."
                )
            yield socket
    except (WebSocketException, OSError, TimeoutError, ValueError):
        raise BeeperFailure(
            "live_unavailable", "WhatsApp updates are reconnecting."
        ) from None


def invalidation(
    raw: object, chat_id: str, previous_seq: int | None
) -> PersonalLiveFrame | None:
    if not isinstance(raw, dict):
        raise BeeperFailure(
            "live_protocol_changed", "WhatsApp updates need reconnecting."
        )
    kind = raw.get("type")
    if kind not in (
        "message.upserted",
        "message.deleted",
        "chat.deleted",
        "chat.upserted",
    ):
        raise BeeperFailure(
            "live_protocol_changed", "WhatsApp updates need reconnecting."
        )
    seq = raw.get("seq")
    if (
        isinstance(seq, bool)
        or not isinstance(seq, int)
        or seq < 0
        or (previous_seq is not None and seq <= previous_seq)
    ):
        raise BeeperFailure(
            "live_protocol_changed", "WhatsApp updates need reconnecting."
        )
    if raw.get("chatID") != chat_id:
        raise BeeperFailure(
            "live_protocol_changed", "WhatsApp updates need reconnecting."
        )
    ids = raw.get("ids", [])
    if (
        not isinstance(ids, list)
        or len(ids) > 100
        or any(not isinstance(i, str) or not 1 <= len(i) <= 1024 for i in ids)
    ):
        raise BeeperFailure(
            "live_protocol_changed", "WhatsApp updates need reconnecting."
        )
    # Provider entries can contain private content; never project or log them.
    change = (
        "upsert"
        if kind == "message.upserted"
        else "deleted"
        if kind == "message.deleted"
        else "chat_deleted"
        if kind == "chat.deleted"
        else "reconcile"
    )
    return PersonalLiveFrame(
        type="changed",
        chat_id=chat_id,
        seq=seq,
        ids=ids if kind.startswith("message.") else [],
        change=change,
        reconcile=previous_seq is not None and seq > previous_seq + 1,
    )


async def live_frames(
    service: PersonalMessagingCoordinator,
    chat_id: str,
    authorize: Callable[[], Awaitable[None]],
    initial_snapshot: DeviceGrant | None = None,
) -> AsyncIterator[bytes]:
    snapshot = initial_snapshot
    last_check = 0.0

    async def validate():
        nonlocal snapshot, last_check
        last_check = time.monotonic()
        async with asyncio.timeout(4):
            await authorize()
            async with service.lock:
                grant = await service._ready()
                if chat_id not in grant.selected_chat_ids:
                    raise GrantUnavailable("Select this WhatsApp conversation first.")
                if snapshot and (grant.id, grant.generation, grant.revision) != (
                    snapshot.id,
                    snapshot.generation,
                    snapshot.revision,
                ):
                    raise GrantConflict("WhatsApp consent changed during updates.")
                async with service._read_boundary(grant):
                    await service.native.verify_chat(
                        service.native.active(grant), str(grant.account_ref), chat_id
                    )
                    await service.fence()
                if snapshot is None:
                    snapshot = grant
                return grant

    def encode(frame: PersonalLiveFrame) -> bytes:
        return (frame.model_dump_json(exclude_none=True) + "\n").encode()

    receive = None
    try:
        grant = await validate()
        async with selected_subscription(
            service.native.active(grant), chat_id
        ) as socket:
            await validate()
            yield encode(PersonalLiveFrame(type="ready", chat_id=chat_id))
            previous_seq = None
            receive = asyncio.create_task(socket.recv())
            while True:
                done, _ = await asyncio.wait(
                    {receive},
                    timeout=max(
                        0, _HEARTBEAT_SECONDS - (time.monotonic() - last_check)
                    ),
                )
                if not done:
                    await validate()
                    yield encode(PersonalLiveFrame(type="heartbeat", chat_id=chat_id))
                    continue
                frame = invalidation(
                    json.loads(receive.result()), chat_id, previous_seq
                )
                await validate()
                if frame is not None:
                    previous_seq = frame.seq
                    yield encode(frame)
                    if frame.change == "chat_deleted":
                        return
                receive = asyncio.create_task(socket.recv())
    except LiveActorUnverified:
        yield encode(PersonalLiveFrame(type="reconnect_required", chat_id=chat_id, reason="actor_unverified"))
    except (GrantUnavailable, GrantConflict):
        yield encode(
            PersonalLiveFrame(
                type="access_lost", chat_id=chat_id, reason="access_changed"
            )
        )
    except BeeperFailure as error:
        lost = error.code in {
            "reauthorization_required",
            "grant_mismatch",
            "account_disconnected",
            "permission_required",
            "native_not_found",
        }
        yield encode(
            PersonalLiveFrame(
                type="access_lost" if lost else "reconnect_required",
                chat_id=chat_id,
                reason="access_changed" if lost else "provider_unavailable",
            )
        )
    except (WebSocketException, OSError, TimeoutError, ValueError):
        yield encode(
            PersonalLiveFrame(
                type="reconnect_required",
                chat_id=chat_id,
                reason="provider_unavailable",
            )
        )
    finally:
        if receive is not None:
            if not receive.done():
                receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)
