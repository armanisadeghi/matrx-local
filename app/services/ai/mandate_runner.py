"""The desktop's mandate runner: matrx-ai's client runner plus the agent-as-tool verbs.

matrx-ai's ``ServerMandateRunner`` (``matrx_ai.client_host.mandate_runner``) serves ``prepare`` / ``finish`` /
``resolve`` / ``run`` for a client host. Since runner-boundary wave 4 (aidream ``fa92935f75``) an agent calling an
agent (``agent_call``, an agent projected as a tool) no longer loads and runs its child itself: it asks the
installed runner for ``prepare_own_agent`` and ``run_agent_as_tool``
(``matrx_ai.mandates.get_agent_as_tool_runner``), and refuses out loud when the runner serves neither.

Before that change the desktop ran agent calls like this: the child agent was loaded through the host's
``ExecutionAgentSource`` (``LocalExecutionAgentSource`` — AIDream's server API behind the local fetch-through
cache) and run on this device through matrx-ai's execution funnel. This runner keeps exactly that: the same load
(``Agent.from_agent``, which reads the configured execution source) and the same funnel
(``matrx_ai.agents.executor.run_agent``), now reached through the runner's verbs. Nothing a person sees changes.
The server's twin is ``aidream.services.mandate_runner`` ``prepare_own_agent`` / ``run_agent_as_tool``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from matrx_ai.client_host.mandate_runner import ServerMandateRunner


class DesktopMandateRunner(ServerMandateRunner):
    """``ServerMandateRunner`` plus the two agent-as-tool verbs, served on this device."""

    async def prepare_own_agent(self, holder: Any, variables: Mapping[str, Any], *, who: Any, opts: Any) -> Any:
        from matrx_ai.agents.definition import Agent

        if getattr(holder, "type", "agent") != "agent":
            raise ValueError(f"prepare_own_agent() loads an agent Holder, got {holder.type!r}")
        overrides = getattr(holder, "config_overrides", None)
        return await Agent.from_agent(
            holder.version_id or holder.id,
            is_version=bool(holder.version_id),
            variables=dict(variables),
            config_overrides=dict(overrides) if overrides is not None else None,
        )

    async def run_agent_as_tool(self, agent: Any, *, who: Any, opts: Any, **run: Any) -> Any:
        from matrx_ai.agents import executor

        run.setdefault("source_feature", getattr(opts, "door", None) or "agent_as_tool")
        return await executor.run_agent(agent, **run)


def install_desktop_mandate_runner(server_url: str, get_jwt: Any) -> None:
    """Install the desktop runner BEFORE ``matrx_ai.configure`` (a host-installed runner is never overwritten)."""
    from matrx_ai import mandates

    if mandates._MANDATE_RUNNER is None:
        mandates.set_mandate_runner(DesktopMandateRunner(server_url, get_jwt))


__all__ = ["DesktopMandateRunner", "install_desktop_mandate_runner"]
