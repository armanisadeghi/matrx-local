"""D1 (2026-09-20): the server's coding-session organization hold has a door.

Measured live: aidream answered 409 ``organization_required`` for every
upload; the publisher recognised it as a HOLD but resumed on the DEVICE
organization check (which passed), retried, was held again — 320 uploads at
attempt 16+, 0 sent, and ``/actions/needed`` empty. These tests pin the fix:

* a server hold registers exactly ONE action-needed card, carrying the
  memberships the hold listed as choices;
* a paused publisher asks the SERVER (not this Mac) whether the organization
  is set, and stays paused while it is not;
* choosing an organization PUTs it through the server's door, clears the
  card, and the next tick sends.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.services.action_needed.models import (
    CODING_SESSION_ORGANIZATION_ACTION,
    CODING_SESSION_ORGANIZATION_FINGERPRINT,
    CODING_SESSION_ORGANIZATION_ROUTE,
)
from app.services.action_needed.registry import get_action_needed_registry
from app.services.aidream.client import AIDreamError
from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.local_db.database import LocalDatabase


ORG_A = {
    "id": "11111111-1111-4111-8111-111111111111",
    "name": "All Green",
    "abbreviation": "AG",
}
ORG_B = {
    "id": "22222222-2222-4222-8222-222222222222",
    "name": "AI Matrx",
    "abbreviation": "AM",
}

HOLD_BODY = {
    "error": "organization_required",
    "code": "organization_required",
    "message": (
        "This coding session has not been told which organization it belongs to, "
        "and coding-session transports carry no organization on the wire."
    ),
    "user_message": "Choose the organization this coding session belongs to, then try again.",
    "details": {
        "hold": "organization_required",
        "can_choose": True,
        "set_on": "coding_session",
        "remedy": "Choose the organization this coding session belongs to.",
        "organizations": [ORG_A, ORG_B],
        "memberships_url": "/auth/organizations",
    },
}


class FakeTokenRepo:
    async def get(self) -> dict[str, Any]:
        return {
            "user_id": "00000000-0000-4000-8000-000000000001",
            "access_token": "owner-jwt",
        }

    def is_expired(self, _row: dict[str, Any]) -> bool:
        return False


class HoldingServer:
    """A fake aidream: holds every upload until PUT sets the organization."""

    def __init__(self) -> None:
        self.connection_organization: str | None = None
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []
        self.puts: list[tuple[str, dict[str, Any]]] = []

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        jwt: str | None = None,
        timeout: float = 130.0,
    ) -> dict[str, Any]:
        self.posts.append((path, deepcopy(payload)))
        if self.connection_organization is None:
            import json

            raise AIDreamError(
                409,
                f"[aidream_client] {path} → HTTP 409: {json.dumps(HOLD_BODY)}",
                body=deepcopy(HOLD_BODY),
            )
        return {
            "schema_version": 1,
            "action": payload["action"],
            "provider": payload["provider"],
            "session_id": "11111111-1111-4111-8111-111111111111",
            "conversation_id": "22222222-2222-4222-8222-222222222222",
            "fidelity": "event_mirror",
            "accepted": 1,
            "duplicates": 0,
            "conflicts": 0,
        }

    async def get(
        self, path: str, jwt: str | None = None, **_kw: Any
    ) -> dict[str, Any]:
        self.gets.append(path)
        assert path == "/coding-sessions/connection/organization"
        return {
            "set_on": "coding_session",
            "organization_id": self.connection_organization,
            "organizations": [ORG_A, ORG_B],
            "memberships_url": "/auth/organizations",
        }

    async def put(
        self, path: str, payload: dict[str, Any], *, jwt: str | None = None, **_kw: Any
    ) -> dict[str, Any]:
        self.puts.append((path, deepcopy(payload)))
        assert path == "/coding-sessions/connection/organization"
        chosen = payload["organization_id"]
        if chosen not in {ORG_A["id"], ORG_B["id"]}:
            raise AIDreamError(
                422,
                f"[aidream_client] {path} → HTTP 422",
                body={
                    "error": "not_a_membership",
                    "message": f"Organization {chosen} is not one of your memberships.",
                    "user_message": f"Organization {chosen} is not one of your memberships.",
                },
            )
        self.connection_organization = chosen
        return {
            "set_on": "coding_session",
            "organization_id": chosen,
            "organizations": [ORG_A, ORG_B],
        }


def _hook(*, stable_id: str, provider_session_id: str) -> BridgeRequest:
    return BridgeRequest.model_validate(
        {
            "schema_version": 1,
            "action": "observe_hook",
            "provider": "claude_code",
            "provider_session_id": provider_session_id,
            "origin": "independent_hook",
            "stream_key": "main",
            "hook_event": {
                "name": "UserPromptSubmit",
                "payload": {"prompt": "hello"},
                "stable_event_id": stable_id,
            },
        }
    )


@pytest.fixture
async def bridge_db(tmp_path: Path):
    db = LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture(autouse=True)
async def _clean_registry():
    await get_action_needed_registry().reset()
    yield
    await get_action_needed_registry().reset()


async def _cards() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for snapshot in await get_action_needed_registry().snapshots():
        items.extend(snapshot.get("items") or [])
    return items


@pytest.mark.anyio
async def test_the_servers_hold_registers_one_card_with_the_choices_and_choosing_resumes(
    bridge_db: LocalDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.aidream import organization as organization_module

    # This Mac resolves a device organization (the sole-membership rule) but
    # the user has SET none — so there is nothing to answer the server with,
    # and the card is the honest next step. (The old resume check passed on
    # the resolved value and the publisher looped forever.)
    async def device_org_resolves(_jwt: str) -> str:
        return ORG_A["id"]

    async def nothing_set(_user_id: str | None = None) -> str | None:
        return None

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", device_org_resolves
    )
    monkeypatch.setattr(organization_module, "get_device_organization", nothing_set)

    server = HoldingServer()
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=server,  # type: ignore[arg-type]
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook(stable_id="a-1", provider_session_id="session-a"))
    await service.enqueue(_hook(stable_id="b-1", provider_session_id="session-b"))

    first = await service.sync_pending()
    assert first == {"sent": 0, "failed": 1, "blocked": "organization_required"}
    assert len(server.posts) == 1, "one hold stops cross-lane fan-out"

    # EXACTLY ONE card, carrying the hold's memberships as choices.
    cards = await _cards()
    assert len(cards) == 1
    card = cards[0]
    assert card["fingerprint"] == CODING_SESSION_ORGANIZATION_FINGERPRINT
    assert card["title"] == "Your Claude Code sessions are waiting for an organization"
    assert card["action"]["kind"] == CODING_SESSION_ORGANIZATION_ACTION
    assert card["action"]["choice_route"] == CODING_SESSION_ORGANIZATION_ROUTE
    assert [c["id"] for c in card["action"]["choices"]] == [ORG_A["id"], ORG_B["id"]]
    assert [c["label"] for c in card["action"]["choices"]] == ["All Green", "AI Matrx"]
    assert card["details"]["held_uploads"] == 2

    # The blocker names the remedy, and the action is the SERVER's question,
    # not the device picker.
    blocker = (await service.delivery_status())["publisher"]["blocker"]
    assert blocker["code"] == "organization_required"
    assert blocker["action"] == CODING_SESSION_ORGANIZATION_ACTION
    assert (
        "Your Claude Code sessions are waiting for an organization" in blocker["remedy"]
    )
    assert blocker["organizations"] == [ORG_A, ORG_B]

    # Paused: the next tick asks the SERVER, not this Mac, and does NOT retry
    # the upload while the server still says nothing is set.
    paused = await service.sync_pending()
    assert paused == {"sent": 0, "failed": 0, "blocked": "organization_required"}
    assert len(server.posts) == 1, "a held publisher does not knock on the bridge"
    assert server.gets == ["/coding-sessions/connection/organization"]
    assert len(await _cards()) == 1, "still exactly one card"
    rows = await bridge_db.fetchall(
        "SELECT attempts FROM coding_session_bridge_outbox ORDER BY id"
    )
    assert [int(r["attempts"]) for r in rows] == [0, 0], (
        "no attempt is charged while held"
    )

    # THE DOOR: choosing an organization PUTs it to the server, clears the card...
    outcome = await service.set_connection_organization(ORG_B["id"])
    assert server.puts == [
        ("/coding-sessions/connection/organization", {"organization_id": ORG_B["id"]})
    ]
    assert outcome["organization_id"] == ORG_B["id"]
    assert outcome["blocker"] is None
    assert await _cards() == [], "the card is gone the moment the choice took"

    # ...and the next tick sends everything.
    resumed = await service.sync_pending()
    assert resumed == {"sent": 2, "failed": 0, "blocked": None}
    assert len(server.posts) == 3
    assert (
        int(
            (
                await bridge_db.fetchall(
                    "SELECT COUNT(*) AS n FROM coding_session_bridge_outbox"
                )
            )[0]["n"]
        )
        == 0
    )


@pytest.mark.anyio
async def test_a_refused_choice_keeps_the_card_and_the_pause(
    bridge_db: LocalDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.aidream import organization as organization_module

    async def device_org_resolves(_jwt: str) -> str:
        return ORG_A["id"]

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", device_org_resolves
    )
    server = HoldingServer()
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=server,  # type: ignore[arg-type]
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook(stable_id="a-1", provider_session_id="session-a"))
    await service.sync_pending()
    assert len(await _cards()) == 1

    with pytest.raises(AIDreamError) as refused:
        await service.set_connection_organization(
            "99999999-9999-4999-8999-999999999999"
        )
    assert refused.value.status == 422
    assert "not one of your memberships" in refused.value.body["user_message"]

    assert len(await _cards()) == 1, "a refused choice resolves nothing"
    still = await service.sync_pending()
    assert still == {"sent": 0, "failed": 0, "blocked": "organization_required"}


@pytest.mark.anyio
async def test_a_hold_without_a_membership_list_still_registers_an_honest_card(
    bridge_db: LocalDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`organizations: null` means the server could not read the list — the
    card says so and offers the retry, never an empty picker."""
    from app.services.aidream import organization as organization_module

    async def device_org_resolves(_jwt: str) -> str:
        return ORG_A["id"]

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", device_org_resolves
    )
    server = HoldingServer()
    listless = deepcopy(HOLD_BODY)
    listless["details"]["organizations"] = None

    async def post_without_list(
        path: str, payload: dict[str, Any], **_kw: Any
    ) -> dict[str, Any]:
        raise AIDreamError(
            409,
            f"[aidream_client] {path} → HTTP 409: organization_required",
            body=listless,
        )

    server.post = post_without_list  # type: ignore[method-assign]
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=server,  # type: ignore[arg-type]
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook(stable_id="a-1", provider_session_id="session-a"))
    await service.sync_pending()
    cards = await _cards()
    assert len(cards) == 1
    assert cards[0]["action"].get("choices") is None
    assert cards[0]["action"].get("choice_route") is None
    assert "could not be listed" in cards[0]["message"]
    assert cards[0]["action"]["label"] == "Retry delivery"


@pytest.mark.anyio
async def test_a_mac_with_an_organization_set_answers_the_servers_hold_itself_no_second_card(
    bridge_db: LocalDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ONE organization per Mac (Arman, 2026-09-21).

    The user set an organization in the top bar; the server then held coding
    sessions asking "file them where?". That is the same question, already
    answered — so the publisher answers it from the device value through the
    server's own door, registers NO card, and the next tick sends.
    """
    from app.services.aidream import organization as organization_module

    async def the_one_value(_user_id: str | None = None) -> str | None:
        return ORG_B["id"]

    monkeypatch.setattr(organization_module, "get_device_organization", the_one_value)

    server = HoldingServer()
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=server,  # type: ignore[arg-type]
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook(stable_id="a-1", provider_session_id="session-a"))
    await service.enqueue(_hook(stable_id="b-1", provider_session_id="session-b"))

    first = await service.sync_pending()
    assert first["blocked"] == "organization_required"
    # The hold was answered with THE value this Mac holds — through the
    # server's membership-verified door — and nobody was asked again.
    assert server.puts == [
        ("/coding-sessions/connection/organization", {"organization_id": ORG_B["id"]})
    ]
    assert server.connection_organization == ORG_B["id"]
    assert await _cards() == [], "no second card for a question already answered"
    assert (await service.delivery_status())["publisher"]["blocker"] is None

    resumed = await service.sync_pending()
    assert resumed == {"sent": 2, "failed": 0, "blocked": None}


@pytest.mark.anyio
async def test_a_set_organization_the_server_did_not_offer_falls_back_to_the_card(
    bridge_db: LocalDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The device value answers the hold only when it is one of the hold's own
    choices; otherwise the card asks, rather than the publisher sending an
    organization the server will refuse."""
    from app.services.aidream import organization as organization_module

    async def not_offered(_user_id: str | None = None) -> str | None:
        return "99999999-9999-4999-8999-999999999999"

    monkeypatch.setattr(organization_module, "get_device_organization", not_offered)

    server = HoldingServer()
    service = CodingSessionBridgeOutbox(
        db=bridge_db,
        client=server,  # type: ignore[arg-type]
        token_repo=FakeTokenRepo(),  # type: ignore[arg-type]
        cloud_enabled=True,
    )
    await service.enqueue(_hook(stable_id="a-1", provider_session_id="session-a"))
    await service.sync_pending()
    assert server.puts == []
    cards = await _cards()
    assert [c["fingerprint"] for c in cards] == [CODING_SESSION_ORGANIZATION_FINGERPRINT]
