"""THE USER-INPUT LAW on the local /ai/chat door (mirror of aidream PB-03).

The Builder sends one ``messages[]``: the agent's authored messages, then prior turns, then the
person's typed turn. Only the leading ``template_message_count`` authored messages (and the
system instruction) are a template; typed text and carried history stay exactly as written.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from matrx_connect.context.app_context import AppContext

from app.api.ai_routes import LocalChatRequest
from app.services.ai import local_ai_task

VARIABLES = {"x": "FILLED-X"}
TYPED = "quote this back: {{x}}"
PRIOR_REPLY = "Earlier I wrote {{x}}."


def _request(template_message_count: int | None) -> LocalChatRequest:
    body: dict[str, Any] = {
        "ai_model_id": "m",
        "messages": [
            {"role": "system", "content": "You serve {{x}}."},
            {"role": "user", "content": "Describe {{x}}."},
            {"role": "assistant", "content": PRIOR_REPLY},
            {"role": "user", "content": TYPED},
        ],
        "conversation_id": "c1",
        "is_new": True,
        "store": False,
        "variables": VARIABLES,
    }
    if template_message_count is not None:
        body["template_message_count"] = template_message_count
    return LocalChatRequest.model_validate(body)


async def _prepare(monkeypatch: pytest.MonkeyPatch, request: LocalChatRequest) -> Any:
    async def gate(*_a: Any, **_k: Any) -> tuple[str, bool]:
        return "c1", True

    async def tools(_config: Any, ctx: Any, *_a: Any, **_k: Any) -> Any:
        return ctx

    monkeypatch.setattr(local_ai_task, "resolve_conversation_gate", gate)
    monkeypatch.setattr(local_ai_task, "apply_request_tools", tools)
    monkeypatch.setattr(local_ai_task, "bind_active_local_model", lambda _c: "m")
    monkeypatch.setattr(local_ai_task, "set_app_context", lambda _c: None)
    ctx = AppContext(emitter=SimpleNamespace(), user_id="user-1", token="jwt-1")
    _, config, _, _ = await local_ai_task.prepare_chat(request, ctx)
    return config


def _texts(config: Any) -> list[str]:
    return ["".join(c.text for c in m.content) for m in config.messages]


def test_only_the_authored_messages_are_filled(monkeypatch: pytest.MonkeyPatch) -> None:
    config = asyncio.run(_prepare(monkeypatch, _request(2)))
    authored, prior, typed = _texts(config)
    assert authored == "Describe FILLED-X."
    assert prior == PRIOR_REPLY
    assert typed == TYPED
    assert config.system_instruction.base_instruction == "You serve FILLED-X."


def test_an_older_client_never_has_typed_text_or_history_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = asyncio.run(_prepare(monkeypatch, _request(None)))
    authored, prior, typed = _texts(config)
    assert (authored, prior, typed) == ("Describe {{x}}.", PRIOR_REPLY, TYPED)
    assert config.system_instruction.base_instruction == "You serve FILLED-X."
