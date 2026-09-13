"""Organization-aware server tool source for the desktop client host.

``matrx_ai`` can derive a ``ServerToolSource`` from ``get_jwt`` and a server
URL.  That source can attach a Bearer token, but it has no organization
resolver, while AIDream correctly refuses every authenticated request that
doesn't name an organization.  Client hosts must therefore use this explicit
source so tool discovery has the same identity contract as every other
AIDream transport in this app.
"""

from __future__ import annotations

from typing import Any, Callable

from app.services.aidream.client import AIDreamClient


class OrganizationAwareToolSource:
    """Fetch active tool definitions under the user's resolved organization."""

    def __init__(
        self,
        *,
        server_url: str,
        source_app: str,
        get_jwt: Callable[[], str | None],
        client_factory: Callable[[str], AIDreamClient] = AIDreamClient,
    ) -> None:
        self._server_url = server_url
        self._source_app = source_app
        self._get_jwt = get_jwt
        self._client_factory = client_factory
        self._rows: list[dict[str, Any]] = []
        self._fetched = False
        self._executor_name = source_app.replace("_", "-")

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return server definitions, or no rows while this host is anonymous.

        An unauthenticated engine startup is normal: React has not handed over
        a session yet.  Treating that as a public discovery request would
        either leak an unnecessary request or make the server log a guaranteed
        context failure.  ``refresh_server_tool_definitions`` runs after the
        verified token is installed.
        """
        jwt = self._get_jwt()
        if not jwt:
            self._rows = []
            self._fetched = True
            return []

        response = await self._client_factory(self._server_url).get(
            f"/ai-tools/app/{self._source_app}/all", jwt=jwt
        )
        tools = response.get("tools") if isinstance(response, dict) else None
        if not isinstance(tools, list):
            raise RuntimeError("tool registry response did not contain a tools list")
        self._rows = [row for row in tools if isinstance(row, dict)]
        executor_name = response.get("executor_name") if isinstance(response, dict) else None
        if isinstance(executor_name, str) and executor_name.strip():
            self._executor_name = executor_name.strip()
        self._fetched = True
        return self._rows

    async def list_bindings(self) -> list[dict[str, str]]:
        if not self._fetched:
            await self.list_tools()
        return [
            {"tool_id": str(row["id"]), "executor_name": self._executor_name}
            for row in self._rows
            if row.get("id")
        ]

    async def list_executors(self) -> list[dict[str, Any]]:
        if not self._fetched:
            await self.list_tools()
        return [
            {
                "name": self._executor_name,
                "parent_executor_name": None,
                "is_active": True,
            }
        ]
