"""
Tool invocation smoke tests.

Tests real tool calls via POST /tools/invoke.
Tools use PascalCase names (e.g. "Bash", "Read", "Glob") and the request
body uses the "input" field (not "params").

Tools are chosen to be safe (no side effects that could break the dev environment)
and cross-platform where possible.

Each test:
  1. Invokes the tool through the real engine endpoint.
  2. Asserts the response type is not 'error'.
  3. Checks basic structural validity of the result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest


def invoke(http: httpx.Client, tool_name: str, input_params: dict) -> dict:
    """Call POST /tools/invoke and return the parsed response."""
    r = http.post(
        "/tools/invoke",
        json={"tool": tool_name, "input": input_params},
    )
    assert r.status_code == 200, (
        f"POST /tools/invoke({tool_name}) returned {r.status_code}: {r.text}"
    )
    data = r.json()
    assert data.get("type") != "error", (
        f"Tool {tool_name} returned error: {data.get('output', data)}"
    )
    return data


# ---------------------------------------------------------------------------
# Filesystem tools (always safe, cross-platform)
# ---------------------------------------------------------------------------


def test_tool_read(http: httpx.Client) -> None:
    """Read tool reads the project pyproject.toml."""
    project_root = Path(__file__).parent.parent.parent
    target = str(project_root / "pyproject.toml")
    result = invoke(http, "Read", {"file_path": target})
    assert result.get("output"), f"Read returned no content: {result}"
    assert "matrx" in result["output"].lower(), (
        f"Read output doesn't look like pyproject.toml: {result['output'][:200]}"
    )


def test_tool_list_directory(http: httpx.Client) -> None:
    """ListDirectory lists the project root directory."""
    project_root = str(Path(__file__).parent.parent.parent)
    result = invoke(http, "ListDirectory", {"path": project_root})
    content = result.get("output", "")
    assert "app" in content.lower() or "desktop" in content.lower(), (
        f"ListDirectory didn't return expected files: {content[:300]}"
    )


def test_tool_glob(http: httpx.Client) -> None:
    """Glob finds Python files in the app/ directory."""
    project_root = str(Path(__file__).parent.parent.parent)
    result = invoke(http, "Glob", {
        "pattern": "*.py",
        "path": project_root + "/app",
    })
    content = result.get("output", "")
    assert ".py" in content, f"Glob returned no .py files: {content[:300]}"


def test_tool_grep(http: httpx.Client) -> None:
    """Grep searches for 'FastAPI' in the app directory."""
    project_root = str(Path(__file__).parent.parent.parent)
    result = invoke(http, "Grep", {
        "pattern": "FastAPI",
        "path": project_root + "/app",
    })
    content = result.get("output", "")
    assert "FastAPI" in content or len(content) > 2, (
        f"Grep returned unexpected result: {content[:300]}"
    )


# ---------------------------------------------------------------------------
# System tools
# ---------------------------------------------------------------------------


def test_tool_system_info(http: httpx.Client) -> None:
    """SystemInfo returns OS-level information."""
    result = invoke(http, "SystemInfo", {})
    content = result.get("output", "").lower()
    assert any(
        keyword in content
        for keyword in ("darwin", "windows", "linux", "mac", "os", "platform", "cpu")
    ), f"SystemInfo returned unexpected result: {content[:300]}"


def test_tool_system_resources(http: httpx.Client) -> None:
    """SystemResources returns CPU and memory usage."""
    result = invoke(http, "SystemResources", {})
    content = result.get("output", "").lower()
    assert any(
        keyword in content
        for keyword in ("cpu", "memory", "ram", "percent")
    ), f"SystemResources missing expected fields: {content[:300]}"


def test_tool_disk_usage(http: httpx.Client) -> None:
    """DiskUsage returns disk info for the root path."""
    result = invoke(http, "DiskUsage", {"path": "/"})
    content = result.get("output", "").lower()
    assert any(
        keyword in content
        for keyword in ("total", "free", "used", "percent", "gb", "bytes", "disk")
    ), f"DiskUsage missing expected fields: {content[:300]}"


def test_tool_list_processes(http: httpx.Client) -> None:
    """ListProcesses returns a list of running processes."""
    result = invoke(http, "ListProcesses", {})
    content = result.get("output", "").lower()
    assert "python" in content or len(content) > 50, (
        f"ListProcesses returned unexpected result: {content[:300]}"
    )


def test_tool_list_ports(http: httpx.Client) -> None:
    """ListPorts returns currently listening ports."""
    result = invoke(http, "ListPorts", {})
    content = result.get("output", "")
    # Engine's own test port should appear
    assert "22399" in content or len(content) > 10, (
        f"ListPorts unexpected result: {content[:300]}"
    )


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Bash uses Unix shell")
def test_tool_bash(http: httpx.Client) -> None:
    """Bash executes a safe echo command."""
    result = invoke(http, "Bash", {"command": "echo 'matrx-test-ok'"})
    content = result.get("output", "")
    assert "matrx-test-ok" in content, (
        f"Bash did not return expected output: {content[:300]}"
    )


@pytest.mark.windows_only
def test_tool_bash_windows(http: httpx.Client) -> None:
    """Bash echoes on Windows."""
    result = invoke(http, "Bash", {"command": "echo matrx-test-ok"})
    assert "matrx-test-ok" in result.get("output", "")
