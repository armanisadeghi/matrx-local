"""THE VARIABLE RULE on the local doors (agent door + /ai/chat), through matrx-ai's shared functions.

Declared ``{{name}}`` fills once on turn 1 (client value > default > ""), ``{{!x}}`` becomes
``{{x}}``, sections evaluate, declared prompt blocks resolve or refuse by name, undeclared client
keys never fill, a cleared value stays empty, and a continuation never refills.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from matrx_ai.agents.variables import AgentVariable
from matrx_ai.config import UnifiedConfig
from matrx_connect.context.app_context import AppContext

from app.api.ai_routes import LocalAgentStartRequest, LocalChatRequest
from app.services.ai import local_ai_task, prompt_blocks_client
from app.services.aidream.client import AIDreamError

AGENT_ID = "11111111-1111-4111-8111-111111111111"
CONV_ID = "22222222-2222-4222-8222-222222222222"
SYSTEM = "City {{city}}. Keep {{!literal}}. {{#vip}}VIP{{/vip}}{{^vip}}STD{{/vip}}. {{other}}"
DEFAULTS = {
    "city": AgentVariable(name="city", default_value="Paris"),
    "vip": AgentVariable(name="vip", default_value=""),
}


def _ctx() -> AppContext:
    return AppContext(emitter=SimpleNamespace(), user_id="user-1", token="jwt-1", organization_id="org-1")


def _agent_config(system: str = SYSTEM, blocks: list[str] | None = None) -> Any:
    data: dict[str, Any] = {
        "model": "m",
        "system_instruction": system,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    if blocks:
        data["prompt_blocks"] = blocks
    return SimpleNamespace(
        config=UnifiedConfig.from_dict(data),
        name="agent",
        matrx_actions=None,
        excluded_tools=[],
        variable_defaults=DEFAULTS,
    )


def _agent_request(variables: dict[str, Any] | None) -> LocalAgentStartRequest:
    body: dict[str, Any] = {"conversation_id": CONV_ID, "is_new": True, "store": False}
    if variables is not None:
        body["variables"] = variables
    return LocalAgentStartRequest.model_validate(body)


async def _prepare_agent(
    monkeypatch: pytest.MonkeyPatch, agent_config: Any, variables: dict[str, Any] | None
) -> UnifiedConfig:
    import matrx_ai.db.agx_manager as agx_module

    async def load(_id: str, is_version: bool = False) -> Any:
        return agent_config

    async def gate(*_a: Any, **_k: Any) -> tuple[str, bool]:
        return CONV_ID, True

    async def tools(_config: Any, ctx: Any, *_a: Any, **_k: Any) -> Any:
        return ctx

    monkeypatch.setattr(agx_module.agx, "load_for_execution", load)
    monkeypatch.setattr("app.services.ai.engine.supports_agent_execution", lambda: True)
    monkeypatch.setattr(local_ai_task, "resolve_conversation_gate", gate)
    monkeypatch.setattr(local_ai_task, "apply_request_tools", tools)
    monkeypatch.setattr(local_ai_task, "bind_active_local_model", lambda _c: "m")
    monkeypatch.setattr(local_ai_task, "set_app_context", lambda _c: None)
    _, config, _, _ = await local_ai_task.prepare_agent_start(
        AGENT_ID, _agent_request(variables), _ctx()
    )
    return config


def _texts(config: UnifiedConfig) -> list[str]:
    return ["".join(c.text for c in m.content) for m in config.messages]


def _system(config: UnifiedConfig) -> str:
    return config.system_instruction.base_instruction


def test_no_values_sent_defaults_fill_escape_stripped_sections_evaluated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = asyncio.run(_prepare_agent(monkeypatch, _agent_config(), None))
    assert _system(config) == "City Paris. Keep {{literal}}. STD. {{other}}"


def test_undeclared_client_keys_never_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    config = asyncio.run(
        _prepare_agent(monkeypatch, _agent_config(), {"city": "Rome", "other": "LEAK"})
    )
    assert _system(config) == "City Rome. Keep {{literal}}. STD. {{other}}"


def test_a_cleared_value_is_honoured_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    config = asyncio.run(_prepare_agent(monkeypatch, _agent_config(), {"city": ""}))
    assert _system(config).startswith("City . Keep")


def test_a_set_toggle_keeps_its_section(monkeypatch: pytest.MonkeyPatch) -> None:
    config = asyncio.run(_prepare_agent(monkeypatch, _agent_config(), {"vip": "yes"}))
    assert "VIP" in _system(config) and "STD" not in _system(config)


def _serve_blocks(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[str]:
    paths: list[str] = []

    class Client:
        async def get(self, path: str, jwt: str | None = None, *, headers: Any = None) -> Any:
            paths.append(path)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(prompt_blocks_client, "get_aidream_client", lambda: Client())
    return paths


def test_declared_blocks_resolve_with_and_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for variables in (None, {"city": "Rome"}):
        paths = _serve_blocks(
            monkeypatch, {"blocks": {"house": "RULES"}, "report": [], "tool_ids": []}
        )
        config = asyncio.run(
            _prepare_agent(monkeypatch, _agent_config("{{<house>}} {{city}}", ["house"]), variables)
        )
        assert _system(config) == f"<house>\nRULES\n</house> {variables['city'] if variables else 'Paris'}"
        assert "names=house" in paths[0]


def test_a_missing_block_refuses_the_run_naming_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_blocks(
        monkeypatch,
        AIDreamError(
            422, "x", body={"message": "Declared prompt block(s) not found at any level: house"}
        ),
    )
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_prepare_agent(monkeypatch, _agent_config("{{<house>}}", ["house"]), None))
    assert caught.value.detail["code"] == "prompt_block_missing"
    assert caught.value.detail["blocks"] == ["house"]
    assert "house" in caught.value.detail["message"]


def test_an_unreachable_block_source_refuses_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_blocks_client, "get_aidream_client", lambda: None)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(_prepare_agent(monkeypatch, _agent_config("{{<house>}}", ["house"]), None))
    assert caught.value.detail["blocks"] == ["house"]
    assert caught.value.status_code == 503


# ── /ai/chat: a continuation keeps turn 1's frozen system prompt ─────────────────────────────


def _chat_request(is_new: bool) -> LocalChatRequest:
    return LocalChatRequest.model_validate(
        {
            "ai_model_id": "m",
            "messages": [
                {"role": "system", "content": "You serve {{x}}. {{!y}}"},
                {"role": "user", "content": "typed"},
            ],
            "conversation_id": CONV_ID,
            "is_new": is_new,
            "store": True,
            "variables": {"x": "FILLED"},
        }
    )


async def _prepare_chat(
    monkeypatch: pytest.MonkeyPatch, request: LocalChatRequest, frozen: UnifiedConfig | None
) -> UnifiedConfig:
    async def gate(*_a: Any, **_k: Any) -> tuple[str, bool]:
        return CONV_ID, False

    async def tools(_config: Any, ctx: Any, *_a: Any, **_k: Any) -> Any:
        return ctx

    async def exists(_id: str) -> bool:
        return not request.is_new

    async def load(_id: str) -> UnifiedConfig:
        assert frozen is not None
        return frozen

    monkeypatch.setattr(local_ai_task, "resolve_conversation_gate", gate)
    monkeypatch.setattr(local_ai_task, "apply_request_tools", tools)
    monkeypatch.setattr(local_ai_task, "conversation_exists", exists)
    monkeypatch.setattr("matrx_ai.agents.resolver._load_unified_config", load)
    monkeypatch.setattr(local_ai_task, "bind_active_local_model", lambda _c: "m")
    monkeypatch.setattr(local_ai_task, "set_app_context", lambda _c: None)
    _, config, _, _ = await local_ai_task.prepare_chat(request, _ctx())
    return config


def test_chat_turn_one_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    config = asyncio.run(_prepare_chat(monkeypatch, _chat_request(True), None))
    assert _system(config) == "You serve FILLED. {{y}}"


def test_chat_continuation_keeps_the_frozen_prompt_and_never_refills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = UnifiedConfig.from_dict(
        {"model": "m", "system_instruction": "You serve FROZEN. {{y}}", "messages": []}
    )
    frozen.system_prompt_frozen = True
    config = asyncio.run(_prepare_chat(monkeypatch, _chat_request(False), frozen))
    assert _system(config) == "You serve FROZEN. {{y}}"
    assert config.system_prompt_frozen is True


def test_chat_continuation_frozen_with_no_system_prompt_stays_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = UnifiedConfig.from_dict({"model": "m", "messages": []})
    frozen.system_prompt_frozen = True
    config = asyncio.run(_prepare_chat(monkeypatch, _chat_request(False), frozen))
    assert config.system_instruction is None or not _system(config)


# ── agent continuation: turn 2 keeps turn 1's frozen system prompt ───────────────────────────


async def _prepare_agent_continue(
    monkeypatch: pytest.MonkeyPatch, persisted: dict[str, Any]
) -> UnifiedConfig:
    import matrx_ai.db.agx_manager as agx_module

    from app.services.ai import conversation_handler
    from app.services.local_db import repositories

    async def load(_id: str, is_version: bool = False) -> Any:
        return _agent_config()

    async def exists(_id: str) -> bool:
        return True

    async def tools(_config: Any, ctx: Any, *_a: Any, **_k: Any) -> Any:
        return ctx

    class Repo:
        async def get(self, _id: str) -> dict[str, Any]:
            return {"agent_id": AGENT_ID}

    class Store:
        async def get_conversation_config(self, _id: str) -> dict[str, Any]:
            return persisted

    monkeypatch.setattr(agx_module.agx, "load_for_execution", load)
    monkeypatch.setattr(local_ai_task, "conversation_exists", exists)
    monkeypatch.setattr(local_ai_task, "apply_request_tools", tools)
    monkeypatch.setattr(local_ai_task, "bind_active_local_model", lambda _c: "m")
    monkeypatch.setattr(local_ai_task, "set_app_context", lambda _c: None)
    monkeypatch.setattr(repositories, "ConversationsRepo", Repo)
    monkeypatch.setattr(conversation_handler, "get_conversation_store", lambda: Store())
    request = SimpleNamespace(
        user_input="again", retry=False, config_overrides=None, debug=False, stream=True,
        store=True, tools=[], tools_replace=None, client=None, max_iterations=5,
        max_retries_per_iteration=1, organization_id=None,
    )
    _, config, _, _ = await local_ai_task.prepare_conversation_continue(CONV_ID, request, _ctx())
    return config


def test_agent_continuation_uses_the_frozen_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted = {
        "model": "m",
        "system_instruction": "City Rome. Keep {{literal}}. STD. {{other}}",
        "system_prompt_frozen": True,
        "messages": [
            {"role": "user", "content": "Hi Rome"},
            {"role": "assistant", "content": "Reply"},
        ],
    }
    config = asyncio.run(_prepare_agent_continue(monkeypatch, persisted))
    assert _system(config) == "City Rome. Keep {{literal}}. STD. {{other}}"
    assert config.system_prompt_frozen is True
    # What the model receives on turn 2: stored history (turn 1's FILLED authored opening turn
    # and the reply), then the new typed message. The definition's raw copy is not sent too.
    assert _texts(config) == ["Hi Rome", "Reply", "again"]


def test_agent_continuation_of_an_unfrozen_conversation_is_filled_not_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = asyncio.run(
        _prepare_agent_continue(monkeypatch, {"model": "m", "messages": []})
    )
    assert _system(config) == "City Paris. Keep {{literal}}. STD. {{other}}"


@pytest.mark.anyio
async def test_the_local_store_freezes_turn_ones_system_prompt_and_hands_it_back(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.ai.conversation_handler import SQLiteConversationStore
    from app.services.local_db import database

    db = database.LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    monkeypatch.setattr(database, "_instance", db)
    try:
        store = SQLiteConversationStore()
        await store.ensure_conversation_exists(CONV_ID, "user-1")
        before = await store.get_conversation_config(CONV_ID)
        assert "system_instruction" not in before and not before.get("system_prompt_frozen")

        completed = {
            "conversation_id": CONV_ID,
            "user_request_id": "33333333-3333-4333-8333-333333333333",
            "messages": [{"role": "user", "content": "Describe Rome."}],
            "conversation": {"system_instruction": "City Rome."},
        }
        await store.create_pending_user_request(
            completed["user_request_id"], CONV_ID, "user-1"
        )
        await store.persist_completed_request(completed, CONV_ID)
        # Turn 2 re-persists the history with a different (refilled) prompt: the first one wins.
        completed["conversation"] = {"system_instruction": "City Paris."}
        await store.persist_completed_request(completed, CONV_ID)

        after = await store.get_conversation_config(CONV_ID)
        assert after["system_instruction"] == "City Rome."
        assert after["system_prompt_frozen"] is True
    finally:
        await db.close()


@pytest.mark.anyio
async def test_a_real_completed_request_lands_its_filled_system_prompt_in_the_conversation_row(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store write driven by the object the engine passes (matrx-ai's own CompletedRequest,
    built through its own constructors with a real filled SystemInstruction), not a dict."""
    from matrx_ai.orchestrator.requests import AIMatrixRequest, CompletedRequest
    from matrx_connect.context.app_context import set_app_context

    from app.services.ai.conversation_handler import SQLiteConversationStore
    from app.services.local_db import database

    config = UnifiedConfig.from_dict(
        {
            "model": "local/test",
            "system_instruction": SYSTEM,
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Reply"},
            ],
        }
    )
    config.replace_variables({"city": "Rome", "vip": ""})
    filled = "City Rome. Keep {{literal}}. STD. {{other}}"
    assert _system(config) == filled
    completed = CompletedRequest(
        request=AIMatrixRequest(
            conversation_id=CONV_ID,
            config=config,
            request_id="44444444-4444-4444-8444-444444444444",
        ),
        iterations=1,
        final_response=SimpleNamespace(),  # type: ignore[arg-type]
    )

    db = database.LocalDatabase(tmp_path / "matrx.db")
    await db.connect()
    monkeypatch.setattr(database, "_instance", db)
    from unittest.mock import AsyncMock

    set_app_context(
        AppContext(emitter=AsyncMock(), user_id="user-1", token="jwt-1", organization_id="org-1")
    )
    try:
        store = SQLiteConversationStore()
        await store.ensure_conversation_exists(CONV_ID, "user-1")
        await store.create_pending_user_request(
            "44444444-4444-4444-8444-444444444444", CONV_ID, "user-1"
        )
        await store.persist_completed_request(completed, CONV_ID)

        row = await db.fetchone(
            "SELECT system_instruction FROM chat.conversation WHERE id = ?", (CONV_ID,)
        )
        assert row is not None and filled in (row["system_instruction"] or "")
        again = await store.get_conversation_config(CONV_ID)
        assert again["system_prompt_frozen"] is True
        assert filled in again["system_instruction"]
        assert "{{city}}" not in again["system_instruction"]
    finally:
        await db.close()
