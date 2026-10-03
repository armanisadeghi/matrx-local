"""Direct delivery proves every acknowledgement; the identity walk fails closed."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from matrx_coding_history.bridge import BridgeRequest
from matrx_coding_history.delivery import DirectBridgeDelivery
from matrx_coding_history.envelopes import session_metadata_request, validate_acknowledgement
from matrx_coding_history.identity import IdentityInventoryBlocked, fetch_complete_identity_inventory
from matrx_coding_history.ports import CloudError, CloudOffline
from coding_history_support import FakeServer

CONVERSATION = "4f1c3a52-9a2e-4d8e-b6f1-2c4d5e6f7a82"


def _native(entries: int = 2) -> BridgeRequest:
    return BridgeRequest.model_validate(
        {
            "action": "append_native",
            "provider": "claude_code",
            "provider_session_id": "claude-sdk:x:y",
            "provider_project_key": "p",
            "origin": "matrx_local",
            "conversation": {"conversation_id": CONVERSATION, "is_new": True, "store": True},
            "stream_key": "main",
            "writer_runtime_id": "matrx-local:claude-history:" + "a" * 64,
            "writer_lease_seconds": 300,
            "entries": [
                {"entry_id": f"e{i}", "source_sequence": i, "kind": "user", "payload_sha256": f"{i:064d}", "payload": {}}
                for i in range(entries)
            ],
            "source_metadata": {
                "source_kind": "claude_local_jsonl",
                "provider_native_session_id": "3f1c3a52-9a2e-4d8e-b6f1-2c4d5e6f7a81",
                "provider_account_key": "a" * 64,
                "importer_version": "matrx-local/claude-history-v2",
                "client_version": "2.1.228",
                "transcript_sha256": "d" * 64,
                "transcript_bytes": 1,
                "transcript_entry_count": entries,
                "transcript_mtime_ns": 1,
                "source_complete": True,
            },
        }
    )


def test_direct_delivery_posts_the_outbox_body_and_reports_duplicates() -> None:
    server = FakeServer()
    delivery = DirectBridgeDelivery(server)
    label = session_metadata_request(provider_session_id="claude-sdk:x:y", provider_project_key="p", payload={"title": "t"})
    result = asyncio.run(delivery.enqueue_many([_native(), label], enqueue_origin="explicit_history"))
    assert result["duplicates_by_index"] == [False, False] and result["pending"] == 0
    assert server.posts[0] == _native().model_dump(mode="json", exclude_none=True)
    server.answer = {**_ack(_native()), "accepted": 0, "duplicates": 2}
    again = asyncio.run(delivery.enqueue_many([_native()], enqueue_origin="explicit_history"))
    assert again["duplicates_by_index"] == [True]


def _ack(request: BridgeRequest) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "action": request.action.value,
        "provider": "claude_code",
        "fidelity": "native",
        "session_id": "3f1c3a52-9a2e-4d8e-b6f1-2c4d5e6f7a81",
        "conversation_id": CONVERSATION,
        "accepted": len(request.entries),
        "duplicates": 0,
        "conflicts": 0,
    }


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("not json", "bridge acknowledgement is not a JSON object"),
        ({"accepted": 2}, "bridge acknowledgement schema_version did not match request"),
        ("short", "bridge acknowledgement did not account for every submitted entry"),
        ("bad_fidelity", "bridge acknowledgement has invalid import fidelity"),
    ],
)
def test_a_2xx_that_does_not_account_for_every_entry_is_an_error(answer: Any, message: str) -> None:
    request = _native()
    if answer == "short":
        answer = {**_ack(request), "accepted": 1}
    elif answer == "bad_fidelity":
        answer = {**_ack(request), "fidelity": "summary"}
    server = FakeServer()
    server.answer = answer
    with pytest.raises(CloudError) as excinfo:
        asyncio.run(DirectBridgeDelivery(server).enqueue_many([request], enqueue_origin="explicit_history"))
    assert excinfo.value.status == 502 and str(excinfo.value) == message


def test_the_acknowledgement_check_raises_the_callers_own_error_type() -> None:
    class Mine(Exception):
        def __init__(self, status: int, message: str) -> None:
            super().__init__(message)
            self.status = status

    with pytest.raises(Mine) as excinfo:
        validate_acknowledgement([], _native(), error=Mine)
    assert excinfo.value.status == 502
    validate_acknowledgement(_ack(_native()), _native(), error=Mine)  # a full receipt passes


def test_identity_walk_reads_every_page_or_fails_closed() -> None:
    rows = [{"provider_session_id": f"s{i}", "last_seen_at": None} for i in range(5)]
    server = FakeServer(rows, page_size=2)
    got = asyncio.run(fetch_complete_identity_inventory(http=server, provider="claude_code"))
    assert [r["provider_session_id"] for r in got] == [f"s{i}" for i in range(5)]

    class Offline(FakeServer):
        async def get_json(self, path: str) -> Any:
            raise CloudOffline("down")

    with pytest.raises(IdentityInventoryBlocked) as excinfo:
        asyncio.run(fetch_complete_identity_inventory(http=Offline(), provider="claude_code"))
    assert excinfo.value.reason == "aidream_unreachable"

    class Duplicate(FakeServer):
        async def get_json(self, path: str) -> Any:
            page = await super().get_json(path)
            page["sessions"] = [rows[0], rows[0]]
            page["page_count"] = 2
            return page

    with pytest.raises(IdentityInventoryBlocked) as excinfo:
        asyncio.run(fetch_complete_identity_inventory(http=Duplicate(rows, page_size=2), provider="claude_code"))
    assert excinfo.value.reason == "identity_list_duplicate"
