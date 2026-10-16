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
