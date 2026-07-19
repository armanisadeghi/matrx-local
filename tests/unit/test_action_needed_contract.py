"""The canonical user-remediation payload must survive every tool envelope."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.api import extension_handlers, tool_routes
from app.api.tool_routes import ToolResponse
from app.services.action_needed import (
    ActionNeeded,
    ActionNeededAction,
    ActionNeededKind,
)
from app.services.action_needed.registry import ActionNeededRegistry
from app.services.delegation.engine import DelegationEngine
from app.tools.session import ToolSession
from app.websocket_manager import WebSocketManager
from app.tools.types import ToolResult, ToolResultType


def _requirement() -> ActionNeeded:
    return ActionNeeded(
        fingerprint="api-key:huggingface",
        code="api_key_missing",
        kind=ActionNeededKind.API_KEY,
        feature="local-models",
        title="Add your Hugging Face token",
        message="This model needs a token.",
        action=ActionNeededAction(
            kind="settings_api_keys",
            label="Add token",
            provider="huggingface",
            route="/settings?tab=api-keys&provider=huggingface",
        ),
        source="test",
        observed_at=123.0,
    )


def test_action_needed_round_trips_tool_and_http_envelopes() -> None:
    tool = ToolResult(
        type=ToolResultType.ERROR,
        output="token required",
        action_needed=_requirement(),
    )
    payload = tool.model_dump(mode="json", exclude_none=True)
    rebuilt = ToolResult.model_validate(payload)
    assert rebuilt.action_needed == _requirement()

    http = ToolResponse(
        type=rebuilt.type.value,
        output=rebuilt.output,
        action_needed=rebuilt.action_needed.model_dump(mode="json", exclude_none=True),
    )
    assert http.model_dump(mode="json", exclude_none=True)["action_needed"] == payload["action_needed"]


def test_action_needed_null_is_explicit_in_full_tool_dump() -> None:
    payload = ToolResult(output="recovered", action_needed=None).model_dump(mode="json")
    assert "action_needed" in payload
    assert payload["action_needed"] is None


def test_registry_reconciles_exact_invocation_and_emits_snapshots() -> None:
    async def exercise() -> None:
        registry = ActionNeededRegistry()
        emitted: list[dict[str, object]] = []

        async def listener(snapshot: dict[str, object]) -> None:
            emitted.append(snapshot)

        registry.subscribe(listener)
        await registry.reconcile_invocation("ReadFile", {"path": "/a"}, _requirement())
        snapshots = await registry.snapshots()
        assert snapshots[0]["type"] == "action_needed_epoch"
        assert isinstance(snapshots[0]["epoch"], str)
        assert snapshots[0]["epoch"]
        assert snapshots[1]["version"] == 1
        assert snapshots[1]["items"]

        # A successful retry for a different invocation cannot clear /a.
        await registry.reconcile_invocation("ReadFile", {"path": "/b"}, None)
        assert (await registry.snapshots())[1]["items"]

        await registry.reconcile_invocation("ReadFile", {"path": "/a"}, None)
        assert (await registry.snapshots())[1]["items"] == []
        assert emitted[-1]["version"] == 2

    asyncio.run(exercise())


def test_registry_keeps_shared_requirement_until_every_invocation_recovers() -> None:
    async def exercise() -> None:
        registry = ActionNeededRegistry()
        await registry.reconcile_invocation("Search", {"q": "a"}, _requirement())
        await registry.reconcile_invocation("Search", {"q": "b"}, _requirement())
        await registry.reconcile_invocation("Search", {"q": "a"}, None)
        assert (await registry.snapshots())[1]["items"]
        await registry.reconcile_invocation("Search", {"q": "b"}, None)
        assert (await registry.snapshots())[1]["items"] == []

    asyncio.run(exercise())


def test_registry_owns_non_tool_operations_and_resolves_provider_grants() -> None:
    async def exercise() -> None:
        registry = ActionNeededRegistry()
        await registry.reconcile_operation("image.download:model-a", _requirement())
        assert (await registry.snapshots())[1]["items"]

        # An unrelated successful operation cannot clear this one.
        await registry.reconcile_operation("image.download:model-b", None)
        assert (await registry.snapshots())[1]["items"]

        await registry.resolve_matching(
            lambda item: item.action.provider == "huggingface"
        )
        assert (await registry.snapshots())[1]["items"] == []

    asyncio.run(exercise())


def test_delegation_execute_preserves_machine_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_dispatch(*_args: object, **_kwargs: object) -> ToolResult:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="token required",
            action_needed=_requirement(),
        )

    async def fake_credentials() -> str:
        return "test-token"

    monkeypatch.setattr("app.tools.dispatcher.dispatch", fake_dispatch)
    engine = object.__new__(DelegationEngine)
    engine._get_credentials = fake_credentials  # type: ignore[method-assign]
    engine._event = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    payload = asyncio.run(
        engine._execute(
            SimpleNamespace(dispatcher_name="Search"),
            "local_search",
            "call-1",
            {"query": "test"},
        )
    )

    assert payload["is_error"] is True
    assert payload["output"]["action_needed"]["fingerprint"] == "api-key:huggingface"


def test_rest_websocket_and_extension_carry_identical_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = ToolResult(
        type=ToolResultType.ERROR,
        output="token required",
        action_needed=_requirement(),
    )

    async def fake_dispatch(*_args: object, **_kwargs: object) -> ToolResult:
        return result

    async def exercise() -> None:
        monkeypatch.setattr(tool_routes, "dispatch", fake_dispatch)
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/tools/invoke",
                "headers": [],
                "client": ("127.0.0.1", 1),
            }
        )
        rest = await tool_routes.invoke_tool(
            tool_routes.ToolRequest(tool="Search", input={"query": "x"}),
            request,
        )

        monkeypatch.setattr(extension_handlers, "dispatch", fake_dispatch)
        extension = await extension_handlers.handle_tool(
            {"tool_name": "Search", "tool_input": {"query": "x"}},
            None,
        )

        import app.websocket_manager as ws_module

        monkeypatch.setattr(ws_module, "dispatch", fake_dispatch)
        manager = WebSocketManager()
        sent: list[dict[str, object]] = []

        async def capture(_conn: object, payload: dict[str, object]) -> None:
            sent.append(payload)

        manager._send = capture  # type: ignore[method-assign]
        await manager._run_tool(
            SimpleNamespace(session=ToolSession()),
            "request-1",
            "Search",
            {"query": "x"},
        )

        expected = _requirement().model_dump(mode="json", exclude_none=True)
        assert rest.action_needed is not None
        assert rest.action_needed.model_dump(mode="json", exclude_none=True) == expected
        assert extension["result"]["action_needed"] == expected
        assert sent[0]["action_needed"] == expected

    asyncio.run(exercise())
