"""A local-model turn carries NO context — and says so (nothing fails silently).

Contract: common-docs/systems/scopes-context/context-delivery/RULES.md. The
local runtime has no context gate, no saved-rule read and no receipt, so a
request's ``context`` is never delivered here. Before 2026-09-30 the request
models dropped it through pydantic's ignore-extras with no trace; now the field
is declared, the drop is logged, recorded on the turn's AppContext, and streamed
as a ``context_not_delivered`` warning the desktop shows.
"""

from __future__ import annotations

from types import SimpleNamespace

from matrx_connect.context.app_context import AppContext

from app.api.ai_routes import (
    LocalAgentStartRequest,
    LocalChatRequest,
    LocalConversationContinueRequest,
)
from app.services.ai.local_ai_task import (
    CONTEXT_NOT_DELIVERED_METADATA_KEY,
    _apply_request_scope,
    context_keys_not_delivered,
    context_not_delivered_warning,
)


def _ctx() -> AppContext:
    return AppContext(emitter=SimpleNamespace(), user_id="user-1", token="jwt-1")


def test_every_local_request_model_declares_context() -> None:
    context = {"page_full_content": {"content": "x"}, "__google_files": ["f1"]}
    start = LocalAgentStartRequest(
        conversation_id="c1", is_new=True, store=False, context=context
    )
    cont = LocalConversationContinueRequest(context=context)
    chat = LocalChatRequest(
        ai_model_id="m",
        messages=[],
        conversation_id="c1",
        is_new=True,
        store=False,
        context=context,
    )
    for request in (start, cont, chat):
        assert request.context == context


def test_dropped_keys_are_recorded_on_the_turn() -> None:
    request = LocalConversationContinueRequest(
        context={"page_full_content": "body", "__google_files": ["f1"]},
        scope_ids=["s1"],
    )
    ctx = _apply_request_scope(_ctx(), request)
    assert ctx.metadata[CONTEXT_NOT_DELIVERED_METADATA_KEY] == [
        "__google_files",
        "page_full_content",
    ]
    # The existing scope metadata still rides alongside.
    assert ctx.metadata["scope_ids"] == ["s1"]


def test_no_context_records_nothing() -> None:
    for context in (None, {}):
        request = LocalConversationContinueRequest(context=context)
        ctx = _apply_request_scope(_ctx(), request)
        assert CONTEXT_NOT_DELIVERED_METADATA_KEY not in ctx.metadata
    assert context_keys_not_delivered(SimpleNamespace()) == []


def test_warning_names_the_keys() -> None:
    warning = context_not_delivered_warning(["page_full_content"])
    assert warning.code == "context_not_delivered"
    assert warning.metadata == {"keys": ["page_full_content"]}
    assert "page_full_content" in warning.system_message
    assert warning.user_message and len(warning.user_message) <= 140
