from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.tools.actions import ACTION_GROUPS, make_group_handler
from app.services.ai.local_tool_bridge import build_local_tool_definitions
from app.tools.session import ToolSession
from app.tools.types import ToolResult
from scripts.check_tool_db_drift import (
    DbTool,
    LocalTool,
    RegistryState,
    SURFACE_BINDING_ALLOWLIST,
    compare_tool,
    compute_drift,
    fetch_registry_state,
    load_local_tools,
)


def _local(
    name: str,
    parameters: dict[str, Any] | None = None,
    *,
    tier: str | None = None,
    category: str | None = "desktop",
    admin_only: bool = False,
) -> LocalTool:
    return LocalTool(
        name=name,
        parameters=parameters or {},
        tier=tier,
        category=category,
        admin_only=admin_only,
    )


def _db(
    name: str,
    parameters: dict[str, Any] | None = None,
    *,
    tool_id: str | None = None,
    tier: str | None = None,
    category: str | None = "desktop",
    admin_only: bool = False,
    is_active: bool = True,
) -> DbTool:
    return DbTool(
        id=tool_id or f"id-{name}",
        name=name,
        parameters=parameters or {},
        tier=tier,
        category=category,
        admin_only=admin_only,
        is_active=is_active,
    )


def _registry(
    tools: tuple[DbTool, ...],
    *,
    always_include: frozenset[str] = frozenset(),
    surface_active: bool = True,
) -> RegistryState:
    return RegistryState(
        tools=tools,
        active_binding_ids=frozenset(tool.id for tool in tools),
        active_binding_count=len(tools),
        always_include_tools=always_include,
        surface_active=surface_active,
    )


def test_live_local_contract_is_exactly_the_19_advertised_mega_tools() -> None:
    tools = load_local_tools()

    assert len(tools) == 19
    assert {tool.name for tool in tools} == {
        group.cloud_name for group in ACTION_GROUPS.values()
    }
    assert all("action" in tool.parameters for tool in tools)
    assert all(tool.parameters["action"].get("required") is True for tool in tools)


def test_startup_fallback_definitions_carry_drift_checked_metadata() -> None:
    definitions = build_local_tool_definitions()

    assert len(definitions) == 19
    by_name = {definition.name: definition for definition in definitions}
    for tool in load_local_tools():
        definition = by_name[tool.name]
        assert definition.tier == tool.tier
        assert definition.category == tool.category
        assert definition.admin_only == tool.admin_only


def test_compare_tool_checks_shared_structural_fields() -> None:
    local = _local(
        "local_demo",
        {
            "action": {
                "type": "string",
                "required": True,
                "enum": ["read", "write"],
                "default": "read",
            },
            "count": {"type": "integer"},
            "$variants": {"ignored": {"not": "a top-level argument"}},
        },
        tier="action",
        category="desktop",
        admin_only=False,
    )
    db = _db(
        "local_demo",
        {
            "action": {
                "type": "number",
                "enum": ["read", "delete"],
                "default": "write",
            },
            "extra": {"type": "string", "required": True},
            "$variants": {"different": "metadata is intentionally ignored"},
        },
        tier="read",
        category="desktop-web",
        admin_only=True,
    )

    issues = compare_tool(local, db)

    assert "tier differs (local='action', db='read')" in issues
    assert "admin_only differs (local=False, db=True)" in issues
    assert "category differs (local='desktop', db='desktop-web')" in issues
    assert "fields only in local: count" in issues
    assert "fields only in DB: extra" in issues
    assert "required only in local: action" in issues
    assert "required only in DB: extra" in issues
    assert any(issue.startswith("action: type differs") for issue in issues)
    assert any(issue.startswith("action: enum drift") for issue in issues)
    assert "action: default differs (local='read', db='write')" in issues


def test_compute_drift_covers_bindings_and_surface_allowlist() -> None:
    local_tools = (_local("local_file"), _local("local_only"))
    registry = _registry(
        (_db("local_file"), _db("db_only")),
        always_include=frozenset(
            {"load_desktop_tools", "local_file", "surface_only"}
        ),
    )

    report = compute_drift(local_tools, registry)

    assert report.bound_not_implemented == ["db_only"]
    assert report.implemented_not_bound == ["local_only"]
    assert report.surface_without_binding == ["surface_only"]
    assert SURFACE_BINDING_ALLOWLIST == frozenset({"load_desktop_tools"})


def test_fetch_registry_targets_tool_schema_and_active_executor_tree(
    monkeypatch: Any,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/binding"):
            return httpx.Response(
                200,
                json=[
                    {
                        "tool_id": "tool-1",
                        "executor_name": "matrx-local",
                        "is_active": True,
                    }
                ],
            )
        if request.url.path.endswith("/definition"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "tool-1",
                        "name": "local_file",
                        "parameters": {},
                        "tier": None,
                        "category": "desktop",
                        "admin_only": False,
                        "is_active": True,
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "surface_name": "matrx-local/desktop",
                    "always_include_tools": ["load_desktop_tools", "local_file"],
                    "never_include_tools": [],
                    "is_active": True,
                }
            ],
        )

    monkeypatch.setattr(
        "scripts.check_tool_db_drift.SUPABASE_URL", "https://example.supabase.co"
    )
    monkeypatch.setattr(
        "scripts.check_tool_db_drift.SUPABASE_PUBLISHABLE_KEY", "publishable-test"
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        state = fetch_registry_state(client)

    assert state.active_binding_count == 1
    assert {tool.name for tool in state.tools} == {"local_file"}
    assert all(request.headers["accept-profile"] == "tool" for request in requests)
    binding_query = requests[0].url.params
    assert binding_query["is_active"] == "eq.true"
    assert "executor_name.eq.matrx-local" in binding_query["or"]
    assert "executor_name.like.matrx-local.*" in binding_query["or"]


def test_mega_tool_handler_runs_the_underlying_pydantic_validator() -> None:
    handler = make_group_handler(ACTION_GROUPS["Clipboard"])

    result = asyncio.run(handler(ToolSession(), action="write"))

    assert result.type.value == "error"
    assert "Invalid parameters for local_clipboard" in result.output
    assert "content" in result.output


def test_model_less_action_is_still_enforced_by_the_real_handler_signature() -> None:
    handler = make_group_handler(ACTION_GROUPS["Web"])

    result = asyncio.run(handler(ToolSession(), action="download"))

    assert result.type.value == "error"
    assert "Invalid parameters for DownloadFile" in result.output
    assert "url" in result.output


def test_mega_tool_handler_applies_model_defaults_before_dispatch(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_dispatch(
        tool_name: str, tool_input: dict[str, Any], session: ToolSession
    ) -> ToolResult:
        captured.update(tool_name=tool_name, tool_input=tool_input, session=session)
        return ToolResult(output="ok")

    monkeypatch.setattr("app.tools.dispatcher.dispatch", fake_dispatch)
    session = ToolSession()
    handler = make_group_handler(ACTION_GROUPS["Browser"])

    result = asyncio.run(handler(session, action="tabs"))

    assert result.output == "ok"
    assert captured == {
        "tool_name": "BrowserTabs",
        "tool_input": {"action": "list"},
        "session": session,
    }
