"""An agent calling an agent still works on the desktop (aidream runner-boundary wave 4 / wave 5 ruling B).

Since aidream ``fa92935f75`` matrx-ai's ``agent_call`` and agent tools load and run their child only through the
installed runner's ``prepare_own_agent`` / ``run_agent_as_tool`` and refuse when the runner serves neither. Before
it, the desktop loaded the child through its execution source and ran it on this device. The desktop runner must
serve both verbs over that same load and funnel — and the engine must install it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

# The locked matrx-ai (0.5.612) predates the client runner AND the agent-as-tool port: its agent_call still loads
# the child itself. From the first matrx-ai that ships the runner, every test below runs.
pytest.importorskip("matrx_ai.client_host.mandate_runner", reason="matrx-ai predates the client mandate runner")

from app.services.ai import mandate_runner as desktop  # noqa: E402


@pytest.fixture
def runner():
    return desktop.DesktopMandateRunner("https://server.example", lambda: "jwt")


def test_prepare_own_agent_loads_the_named_version_through_the_host_load(runner, monkeypatch) -> None:
    from matrx_ai.agents import definition

    seen: dict = {}

    async def fake_from_agent(agent_id, **kwargs):
        seen.update(agent_id=agent_id, **kwargs)
        return "AGENT"

    monkeypatch.setattr(definition.Agent, "from_agent", staticmethod(fake_from_agent))
    holder = SimpleNamespace(type="agent", id="a-1", version_id="v-9", config_overrides={"temperature": 0})
    agent = asyncio.run(runner.prepare_own_agent(holder, {"x": 1}, who=None, opts=SimpleNamespace(door="agent_call")))
    assert agent == "AGENT"
    assert seen == {"agent_id": "v-9", "is_version": True, "variables": {"x": 1}, "config_overrides": {"temperature": 0}}


def test_run_agent_as_tool_runs_the_child_through_the_execution_funnel(runner, monkeypatch) -> None:
    from matrx_ai.agents import executor

    seen: dict = {}

    async def fake_run_agent(agent, **run):
        seen.update(agent=agent, **run)
        return "RESULT"

    monkeypatch.setattr(executor, "run_agent", fake_run_agent)
    out = asyncio.run(runner.run_agent_as_tool("AGENT", who=None, opts=SimpleNamespace(door="agent_call"), label="child"))
    assert out == "RESULT"
    assert seen == {"agent": "AGENT", "label": "child", "source_feature": "agent_call"}


def test_the_desktop_runner_satisfies_the_agent_as_tool_port_when_the_package_declares_it(runner) -> None:
    from matrx_ai import mandates

    port = getattr(mandates, "AgentAsToolRunnerPort", None)
    if port is not None:
        assert isinstance(runner, port)
    assert callable(runner.prepare_own_agent) and callable(runner.run_agent_as_tool)


def test_install_wins_over_configure_and_never_overwrites_a_host_runner(monkeypatch) -> None:
    from matrx_ai import mandates

    monkeypatch.setattr(mandates, "_MANDATE_RUNNER", None)
    desktop.install_desktop_mandate_runner("https://server.example", lambda: "jwt")
    installed = mandates._MANDATE_RUNNER
    assert isinstance(installed, desktop.DesktopMandateRunner)
    desktop.install_desktop_mandate_runner("https://other.example", lambda: "jwt")
    assert mandates._MANDATE_RUNNER is installed


def test_the_engine_installs_the_desktop_runner() -> None:
    import inspect

    from app.services.ai import engine

    source = inspect.getsource(engine)
    assert "install_desktop_mandate_runner(server_url, _get_jwt)" in source
    assert source.index("install_desktop_mandate_runner(server_url") < source.index("matrx_ai.configure(**configure_kwargs)")
