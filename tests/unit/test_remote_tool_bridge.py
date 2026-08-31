from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from matrx_ai.tools.models import ToolContext
from matrx_connect.context.app_context import AppContext

from app.services.ai.remote_tool_bridge import (
    REMOTE_TOOL_CONTEXT_KEY,
    RemoteToolBridge,
    build_local_context_definitions,
)
from app.services.aidream.client import AIDreamClient



@pytest.fixture(autouse=True)
def _organization_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every authenticated call now names its organization at the TRANSPORT
    (2026-08-30 admission gate). Resolving it is a Supabase round trip, so
    these transport tests stub the lookup — never the header assembly, which
    has its own guards in test_aidream_transport_organization_header.py."""
    from app.services.aidream import organization as organization_module

    async def _resolve(_jwt: str) -> str:
        return "11111111-2222-4333-8444-555555555555"

    monkeypatch.setattr(
        organization_module, "resolve_active_organization_id", _resolve
    )


class _FakeRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.ids: dict[str, str] = {}

    def get(self, name: str):
        return self.tools.get(self.ids.get(name, name))

    def load_from_definitions(self, definitions):
        for definition in definitions:
            self.tools[definition.name] = definition
            if definition.tool_id:
                self.ids[definition.tool_id] = definition.name
        return len(definitions)

    def unregister(self, name: str) -> bool:
        definition = self.tools.pop(name, None)
        if definition is not None and definition.tool_id:
            self.ids.pop(definition.tool_id, None)
        return definition is not None


class _FakeHandlers:
    def __init__(self) -> None:
        self._tool_handlers: dict[str, object] = {}


class _FakeClient:
    def __init__(self, *, rows=None, response=None) -> None:
        self.rows = rows or []
        self.response = response
        self.posts: list[tuple] = []

    async def fetch_tools(self):
        return self.rows

    async def post(self, path, payload, *, jwt, headers=None, timeout=130.0):
        self.posts.append((path, payload, jwt, headers, timeout))
        return self.response


@pytest.mark.anyio
async def test_aidream_post_uses_api_prefix_bearer_and_json() -> None:
    captured: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"call_id": "call-1", "ok": True, "output": "done"},
        )

    client = AIDreamClient(
        "https://aidream.test",
        transport=httpx.MockTransport(handle),
    )
    response = await client.post(
        "/ai/tools/execute",
        {"tool_name": "fs_read"},
        jwt="jwt-1",
    )

    assert response["ok"] is True
    assert captured[0].url.path == "/api/ai/tools/execute"
    assert captured[0].headers["authorization"] == "Bearer jwt-1"
    assert captured[0].headers["content-type"] == "application/json"
    assert captured[0].read() == b'{"tool_name":"fs_read"}'


@pytest.mark.anyio
async def test_refresh_registers_remote_definitions_without_shadowing_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import remote_tool_bridge as bridge_module
    from app.tools import catalog as catalog_module
    from matrx_ai.tools import external_handlers, registry
    from matrx_ai.tools.models import ToolDefinition, ToolType

    fake_client = _FakeClient(
        rows=[
            {
                "id": "remote-id",
                "name": "fs_read",
                "parameters": {},
                "timeout_seconds": 45,
                "admin_only": True,
            },
            {"id": "local-id", "name": "local_file", "parameters": {}},
            {
                "id": "discovery-id",
                "name": "load_desktop_tools",
                "parameters": {},
            },
            {"id": "inactive-id", "name": "inactive_tool", "is_active": False},
            {"id": "bundle-id", "name": "bundle:list_example"},
        ]
    )
    fake_registry = _FakeRegistry()
    discovery = ToolDefinition(
        name="load_desktop_tools",
        tool_id="local-discovery-id",
        tool_type=ToolType.LOCAL,
        function_path=(
            "matrx_ai.tools.implementations.desktop_discovery.load_desktop_tools"
        ),
    )
    fake_registry.load_from_definitions([discovery])
    fake_handlers = _FakeHandlers()
    monkeypatch.setattr(bridge_module, "get_aidream_client", lambda: fake_client)
    monkeypatch.setattr(
        catalog_module,
        "get_catalog",
        lambda: (SimpleNamespace(cloud_name="local_file"),),
    )
    monkeypatch.setattr(
        registry.ToolRegistry,
        "get_instance",
        staticmethod(lambda: fake_registry),
    )
    monkeypatch.setattr(
        external_handlers.ExternalHandlerRegistry,
        "get_instance",
        staticmethod(lambda: fake_handlers),
    )

    count = await RemoteToolBridge().refresh()

    assert count == 1
    assert set(fake_registry.tools) == {"fs_read", "load_desktop_tools"}
    assert set(fake_handlers._tool_handlers) == {"fs_read"}
    hydrated_discovery = fake_registry.tools["load_desktop_tools"]
    assert hydrated_discovery.tool_id == "discovery-id"
    assert hydrated_discovery._callable is not None
    assert fake_registry.tools["fs_read"].tool_id == "remote-id"
    assert fake_registry.tools["fs_read"].admin_only is False
    assert fake_registry.tools["fs_read"].timeout_seconds == 75


@pytest.mark.anyio
async def test_refresh_accepts_mcp_object_shaped_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dict-shaped (MCP-style) ``annotations`` row must not abort refresh.

    2026-08-30: one registry row carrying ``{"title": ..., "destructiveHint":
    false}`` failed ToolDefinition validation (list expected) and the WHOLE
    remote-tool catalog vanished at startup. The bridge normalizes the object
    form into the list shape matrx-ai declares.
    """
    from app.services.ai import remote_tool_bridge as bridge_module
    from app.tools import catalog as catalog_module
    from matrx_ai.tools import external_handlers, registry

    fake_client = _FakeClient(
        rows=[
            {
                "id": "annotated-id",
                "name": "ask_question",
                "parameters": {},
                "annotations": {"title": "Ask a question", "destructiveHint": False},
            },
        ]
    )
    fake_registry = _FakeRegistry()
    fake_handlers = _FakeHandlers()
    monkeypatch.setattr(bridge_module, "get_aidream_client", lambda: fake_client)
    monkeypatch.setattr(catalog_module, "get_catalog", lambda: ())
    monkeypatch.setattr(
        registry.ToolRegistry,
        "get_instance",
        staticmethod(lambda: fake_registry),
    )
    monkeypatch.setattr(
        external_handlers.ExternalHandlerRegistry,
        "get_instance",
        staticmethod(lambda: fake_handlers),
    )

    count = await RemoteToolBridge().refresh()

    assert count == 1
    assert fake_registry.tools["ask_question"].annotations == [
        {"title": "Ask a question", "destructiveHint": False}
    ]


@pytest.mark.anyio
async def test_remote_execution_forwards_identity_and_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import remote_tool_bridge as bridge_module
    from matrx_connect.context import app_context as context_module

    fake_client = _FakeClient(
        response={"call_id": "call-1", "ok": True, "output": "server result"}
    )
    monkeypatch.setattr(bridge_module, "get_aidream_client", lambda: fake_client)

    async def _stored_token():
        return "jwt-1"

    monkeypatch.setattr(bridge_module, "_stored_jwt", _stored_token)
    app_ctx = AppContext(
        emitter=SimpleNamespace(),
        user_id="user-1",
        token="jwt-1",
        request_id="request-1",
        conversation_id="conversation-1",
        agent_id="agent-1",
        metadata={
            REMOTE_TOOL_CONTEXT_KEY: {
                "tools": [],
                "tools_replace": [{"kind": "registered", "name": "fs_read"}],
                "client": {"surface": "matrx-user/chat"},
                "scope_ids": ["scope-1"],
            }
        },
        organization_id="org-1",
        project_id="project-1",
        task_id="task-1",
        source_app="matrx_local",
    )
    monkeypatch.setattr(context_module, "get_app_context", lambda: app_ctx)

    result = await RemoteToolBridge().execute(
        {"path": "/tmp/example.txt"},
        ToolContext(call_id="call-1", tool_name="fs_read"),
    )

    assert result.success is True
    assert result.output == "server result"
    path, payload, jwt, headers, timeout = fake_client.posts[0]
    assert path == "/ai/tools/execute"
    assert jwt == "jwt-1"
    assert headers == {"X-Organization-Id": "org-1"}
    assert timeout == 135.0
    assert payload["agent_id"] == "agent-1"
    assert payload["conversation_id"] == "conversation-1"
    assert payload["tools_replace"][0]["name"] == "fs_read"
    assert payload["organization_id"] == "org-1"
    assert payload["project_id"] == "project-1"
    assert payload["task_id"] == "task-1"
    assert payload["scope_ids"] == ["scope-1"]
    assert payload["source_app"] == "matrx-local"
    assert payload["store"] is False


@pytest.mark.anyio
async def test_remote_execution_refuses_when_organization_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """aidream's AuthMiddleware refuses every authenticated request that
    names no organization (400 organization_required) before it routes.
    Round-tripping such a request is a guaranteed, wasted failure — the
    bridge must refuse locally instead, and it must NEVER invent a fallback
    organization to smooth over one that's missing."""
    from app.services.ai import remote_tool_bridge as bridge_module
    from matrx_connect.context import app_context as context_module

    fake_client = _FakeClient(
        response={"call_id": "call-1", "ok": True, "output": "server result"}
    )
    monkeypatch.setattr(bridge_module, "get_aidream_client", lambda: fake_client)

    async def _stored_token():
        return "jwt-1"

    monkeypatch.setattr(bridge_module, "_stored_jwt", _stored_token)
    app_ctx = AppContext(
        emitter=SimpleNamespace(),
        user_id="user-1",
        token="jwt-1",
        request_id="request-1",
        conversation_id="conversation-1",
        agent_id="agent-1",
        metadata={
            REMOTE_TOOL_CONTEXT_KEY: {
                "tools": [],
                "tools_replace": [{"kind": "registered", "name": "fs_read"}],
                "client": {"surface": "matrx-user/chat"},
                "scope_ids": ["scope-1"],
            }
        },
        organization_id=None,
        project_id="project-1",
        task_id="task-1",
        source_app="matrx_local",
    )
    monkeypatch.setattr(context_module, "get_app_context", lambda: app_ctx)

    result = await RemoteToolBridge().execute(
        {"path": "/tmp/example.txt"},
        ToolContext(call_id="call-1", tool_name="fs_read"),
    )

    assert result.success is False
    assert result.error.error_type == "organization_required"
    assert fake_client.posts == []


@pytest.mark.anyio
async def test_refresh_runs_context_mutating_discovery_inside_local_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import remote_tool_bridge as bridge_module
    from app.tools import catalog as catalog_module
    from matrx_ai.tools import external_handlers, registry
    fake_registry = _FakeRegistry()
    fake_registry.load_from_definitions(build_local_context_definitions())
    fake_handlers = _FakeHandlers()
    fake_client = _FakeClient(
        rows=[
            {
                "id": "discovery-id",
                "name": "load_desktop_tools",
                "description": "Canonical server description",
                "parameters": {
                    "type": "object",
                    "properties": {"category": {"type": "string"}},
                    "required": ["category"],
                },
            }
        ]
    )
    monkeypatch.setattr(bridge_module, "get_aidream_client", lambda: fake_client)
    monkeypatch.setattr(catalog_module, "get_catalog", lambda: ())
    monkeypatch.setattr(
        registry.ToolRegistry, "get_instance", staticmethod(lambda: fake_registry)
    )
    monkeypatch.setattr(
        external_handlers.ExternalHandlerRegistry,
        "get_instance",
        staticmethod(lambda: fake_handlers),
    )

    count = await RemoteToolBridge().refresh()

    assert count == 0
    hydrated = fake_registry.get("load_desktop_tools")
    assert hydrated is fake_registry.get("discovery-id")
    assert hydrated.tool_id == "discovery-id"
    assert hydrated._callable is not None
    assert hydrated.description == "Canonical server description"
    assert hydrated.parameters["category"]["type"] == "string"
    assert hydrated.required_params == ["category"]
    assert hydrated.to_anthropic_format()["input_schema"]["required"] == ["category"]
    assert fake_handlers._tool_handlers == {}


@pytest.mark.anyio
async def test_refresh_removes_stale_remote_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import remote_tool_bridge as bridge_module
    from app.tools import catalog as catalog_module
    from matrx_ai.tools import external_handlers, registry
    from matrx_ai.tools.models import ToolDefinition, ToolType

    fake_client = _FakeClient(rows=[])
    fake_registry = _FakeRegistry()
    fake_registry.load_from_definitions(
        [
            ToolDefinition(
                name="stale_remote",
                tool_id="stale-id",
                tool_type=ToolType.EXTERNAL_HANDLER,
            )
        ]
    )
    fake_handlers = _FakeHandlers()
    fake_handlers._tool_handlers["stale_remote"] = object()
    monkeypatch.setattr(bridge_module, "get_aidream_client", lambda: fake_client)
    monkeypatch.setattr(catalog_module, "get_catalog", lambda: ())
    monkeypatch.setattr(
        registry.ToolRegistry, "get_instance", staticmethod(lambda: fake_registry)
    )
    monkeypatch.setattr(
        external_handlers.ExternalHandlerRegistry,
        "get_instance",
        staticmethod(lambda: fake_handlers),
    )
    bridge = RemoteToolBridge()
    bridge._loaded_names = {"stale_remote"}

    await bridge.refresh()

    assert "stale_remote" not in fake_registry.tools
    assert "stale_remote" not in fake_handlers._tool_handlers


@pytest.mark.anyio
async def test_remote_execution_returns_tool_error_without_agent_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from matrx_connect.context import app_context as context_module

    monkeypatch.setattr(
        context_module,
        "get_app_context",
        lambda: AppContext(emitter=SimpleNamespace(), user_id="user-1", token="jwt-1"),
    )

    result = await RemoteToolBridge().execute(
        {}, ToolContext(call_id="call-1", tool_name="fs_read")
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.error_type == "remote_context_missing"


def test_bundled_context_tool_is_executable_without_server_catalog() -> None:
    definitions = build_local_context_definitions()

    assert [definition.name for definition in definitions] == ["load_desktop_tools"]
    definition = definitions[0]
    assert definition._callable is not None
    assert definition.function_path.endswith(".load_desktop_tools")
    assert definition.parameters["category"]["type"] == "string"
    assert "category" in definition.required_params


@pytest.mark.anyio
async def test_engine_bootstrap_registers_context_tool_while_server_is_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai import engine as engine_module
    from app.services.ai import local_tool_bridge, remote_tool_bridge
    from matrx_ai.tools import handle_tool_calls, registry

    fake_registry = _FakeRegistry()

    async def no_server_catalog() -> int:
        return 0

    class OfflineBridge:
        async def refresh(self) -> int:
            raise RuntimeError("offline")

    monkeypatch.setattr(engine_module, "_ai_initialized", True)
    monkeypatch.setattr(engine_module, "_tools_loaded", False)
    monkeypatch.setattr(engine_module, "_registered_tool_count", 0)
    monkeypatch.setattr(handle_tool_calls, "initialize_tool_system", no_server_catalog)
    monkeypatch.setattr(local_tool_bridge, "register_local_tools", lambda: 1)
    monkeypatch.setattr(local_tool_bridge, "build_local_tool_definitions", list)
    monkeypatch.setattr(
        remote_tool_bridge,
        "get_remote_tool_bridge",
        lambda: OfflineBridge(),
    )
    monkeypatch.setattr(
        registry.ToolRegistry,
        "get_instance",
        staticmethod(lambda: fake_registry),
    )

    assert await engine_module.load_tools_and_register() == 1
    discovery = fake_registry.get("load_desktop_tools")
    assert discovery is not None
    assert discovery._callable is not None
