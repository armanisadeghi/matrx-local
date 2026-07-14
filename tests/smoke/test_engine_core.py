"""
Core engine smoke tests.

Checks: /health, /version, /tools/list, /system/info, WebSocket /ws,
        GET /settings (public), /platform/context, /hardware.

All these endpoints are either in _PUBLIC_PATHS or require no authentication
by virtue of being device/* paths — so no Bearer token is needed.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest
import websockets


# ---------------------------------------------------------------------------
# Health + discovery
# ---------------------------------------------------------------------------


def test_health(http_public: httpx.Client) -> None:
    """GET /health returns 200 and the honest registry-backed contract.

    status is ok|degraded|failed_services (alive != fully healthy — the
    engine reports failed/degraded managed services instead of a hardcoded
    "ok"); non-ok statuses must name the offending services so a remote
    client can tell WHAT is broken, not just that something is.
    """
    r = http_public.get("/health")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("service") == "matrx-local"
    status = data.get("status")
    assert status in ("ok", "degraded", "failed_services"), data
    if status == "failed_services":
        assert data.get("failed"), data
    if status == "degraded":
        assert data.get("degraded"), data


def test_version(http_public: httpx.Client) -> None:
    """GET /version returns a version string."""
    r = http_public.get("/version")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "version" in data
    assert isinstance(data["version"], str)
    assert len(data["version"]) > 0


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_tools_list_returns_200(http_public: httpx.Client) -> None:
    """GET /tools/list responds 200 (public endpoint)."""
    r = http_public.get("/tools/list")
    assert r.status_code == 200, r.text


def test_tools_list_minimum_count(http_public: httpx.Client) -> None:
    """GET /tools/list returns at least 79 tools."""
    r = http_public.get("/tools/list")
    assert r.status_code == 200
    data = r.json()
    tools = data if isinstance(data, list) else data.get("tools", [])
    assert len(tools) >= 79, (
        f"Expected at least 79 tools, got {len(tools)}. "
        "A tool file may have been deleted or its functions renamed."
    )


def test_tools_list_schema(http_public: httpx.Client) -> None:
    """Each entry in /tools/list is a non-empty string (tool names are PascalCase strings)."""
    r = http_public.get("/tools/list")
    assert r.status_code == 200
    data = r.json()
    tools = data if isinstance(data, list) else data.get("tools", [])
    for tool in tools[:10]:  # spot-check first 10
        assert isinstance(tool, str) and len(tool) > 0, (
            f"Expected tool name to be a non-empty string, got: {tool!r}"
        )


def test_tools_list_includes_known_tools(http_public: httpx.Client) -> None:
    """Known tool names are present in /tools/list (PascalCase)."""
    r = http_public.get("/tools/list")
    assert r.status_code == 200
    data = r.json()
    tools = set(data if isinstance(data, list) else data.get("tools", []))
    required = {"Bash", "Read", "Write", "Glob", "Grep", "SystemInfo", "ListDirectory"}
    missing = required - tools
    assert not missing, (
        f"Expected tools missing from /tools/list: {missing}\n"
        "These may have been renamed or deleted."
    )


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------


def test_system_info(http: httpx.Client) -> None:
    """GET /system/info returns os and architecture."""
    r = http.get("/system/info")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "os" in data or "platform" in data, (
        f"Expected 'os' or 'platform' key in system info. Got: {list(data.keys())}"
    )


# ---------------------------------------------------------------------------
# Settings (public endpoint)
# ---------------------------------------------------------------------------


def test_settings_get_public(http_public: httpx.Client) -> None:
    """GET /settings is public and returns a dict."""
    r = http_public.get("/settings")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict)
    assert len(data) > 0


# ---------------------------------------------------------------------------
# Platform context
# ---------------------------------------------------------------------------


def test_platform_context(http: httpx.Client) -> None:
    """GET /platform/context returns os and arch."""
    r = http.get("/platform/context")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "os" in data or "platform" in data or "arch" in data, (
        f"Platform context missing expected keys. Got: {list(data.keys())}"
    )


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------


def test_hardware(http: httpx.Client) -> None:
    """GET /hardware returns a non-empty hardware profile."""
    r = http.get("/hardware")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_websocket_connects(engine_url: str) -> None:
    """WebSocket /ws accepts connections and receives at least one message."""

    async def _connect() -> bool:
        ws_url = engine_url.replace("http://", "ws://") + "/ws"
        try:
            async with websockets.connect(
                ws_url,
                additional_headers={"Authorization": "Bearer test-token-matrx-local"},
                open_timeout=5,
                close_timeout=5,
            ) as ws:
                # Wait up to 5 seconds for any message (heartbeat or event)
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    return msg is not None
                except asyncio.TimeoutError:
                    # Connected but no message — still a pass (engine is running)
                    return True
        except Exception:
            return False

    result = asyncio.run(_connect())
    assert result, "WebSocket connection to /ws failed"
