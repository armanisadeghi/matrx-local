"""A local request reads ``context_picks`` beside the legacy ``scope_ids``, by the server's rules.

Contract: aidream common-docs/systems/data/scopes-context/projects/any-table-context/CONTRACT.md §2.
The cases are a COPY of aidream packages/matrx-connect/tests/fixtures/context_picks_cases.json (the
canonical twin's fixture), so this repo's narrow reading (app/services/ai/context_picks.py) is held
to the same answers as matrx_connect.context.context_picks until that ships to this repo's lock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.ai_routes import (
    LocalAgentStartRequest,
    LocalChatRequest,
    LocalConversationContinueRequest,
)
from app.services.ai.context_picks import (
    ContextPick,
    canonical_picks,
    fold_legacy_selection,
    legacy_selection,
)

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "context_picks_cases.json").read_text()
)
CASES = _FIXTURE["cases"]
REFUSED = _FIXTURE["refused"]

ORG = "0a000000-0000-4000-8000-000000000001"
TABLE = "7a000000-0000-4000-8000-000000000001"
RECORD = "1e000000-0000-4000-8000-000000000001"
LEGACY = "1e000000-0000-4000-8000-000000000009"


def _json(picks: list[ContextPick]) -> list[dict]:
    return [p.model_dump(mode="json") for p in picks]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_each_case_folds_and_reads_back_as_the_server_does(case: dict) -> None:
    picks = fold_legacy_selection(
        case["scope_ids"], case["active_scope_type_ids"], case["picks"]
    )
    assert _json(picks) == case["canonical"]
    legacy = legacy_selection(case["canonical"])
    assert {
        "scope_ids": legacy.scope_ids,
        "active_scope_type_ids": legacy.active_scope_type_ids,
        "built_in": _json(legacy.built_in),
    } == case["legacy"]
    assert _json(canonical_picks(case["canonical"])) == case["canonical"]


@pytest.mark.parametrize("case", REFUSED, ids=[c["name"] for c in REFUSED])
def test_a_malformed_pick_is_refused(case: dict) -> None:
    with pytest.raises(ValidationError):
        ContextPick.model_validate(case["pick"])


def _start(**body: object) -> LocalAgentStartRequest:
    return LocalAgentStartRequest(
        conversation_id="c1", is_new=True, store=False, **body
    )


def test_a_pick_only_body_fills_the_legacy_scope_ids() -> None:
    pick = {"organization_id": ORG, "table": TABLE, "record_ids": [RECORD]}
    for request in (
        _start(context_picks=[pick]),
        LocalConversationContinueRequest(context_picks=[pick]),
        LocalChatRequest(
            ai_model_id="m",
            messages=[],
            conversation_id="c1",
            is_new=True,
            store=False,
            context_picks=[pick],
        ),
    ):
        assert request.scope_ids == [RECORD]
        assert _json(request.context_picks or []) == _json(canonical_picks([pick]))


def test_a_legacy_body_keeps_its_scope_ids_and_gains_the_folded_picks() -> None:
    request = _start(scope_ids=[LEGACY, "not-a-uuid"])
    assert request.scope_ids == [LEGACY, "not-a-uuid"]
    assert _json(request.context_picks or []) == [
        {
            "organization_id": None,
            "table": None,
            "view": None,
            "record_ids": [LEGACY],
            "fields": [],
        }
    ]
    assert "context_picks" not in request.model_dump(exclude_unset=True)


def test_a_built_in_pick_never_enters_scope_ids() -> None:
    request = _start(
        context_picks=[
            {"organization_id": ORG, "table": "entity:party", "record_ids": [RECORD]}
        ]
    )
    assert request.scope_ids is None
    assert (
        request.context_picks is not None
        and request.context_picks[0].table == "entity:party"
    )


def test_nothing_sent_carries_nothing() -> None:
    request = _start()
    assert request.scope_ids is None
    assert request.context_picks is None


def test_the_turn_records_the_picks_beside_scope_ids() -> None:
    from types import SimpleNamespace

    from matrx_connect.context.app_context import AppContext

    from app.services.ai.local_ai_task import _apply_request_scope

    ctx = AppContext(emitter=SimpleNamespace(), user_id="user-1", token="jwt-1")
    pick = {"organization_id": ORG, "table": "entity:party", "record_ids": [RECORD]}
    scoped = _apply_request_scope(ctx, _start(context_picks=[pick]))
    assert scoped.metadata["context_picks"] == _json(canonical_picks([pick]))
    assert "scope_ids" not in scoped.metadata
