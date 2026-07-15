"""Fetch-through canonical agent source for matrx-ai client-host mode."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from matrx_ai.client_host.agent_source import (
    ExecutionAgentDefinition,
    InvalidExecutionAgentDefinition,
    validate_execution_agent_definition,
)

from app.services.aidream.client import (
    AIDreamError,
    AIDreamOfflineError,
    get_aidream_client,
)
from app.services.local_db.repositories import (
    AgentExecutionDefinitionsRepo,
    TokenRepo,
)

_CACHE_TTL = timedelta(minutes=5)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class LocalExecutionAgentSource:
    """Serve fresh canonical definitions; never execute listing metadata."""

    def __init__(
        self,
        repo: AgentExecutionDefinitionsRepo | None = None,
        token_repo: TokenRepo | None = None,
    ) -> None:
        self._repo = repo or AgentExecutionDefinitionsRepo()
        self._token_repo = token_repo or TokenRepo()

    async def load_for_execution(
        self,
        agent_id: str,
        *,
        is_version: bool = False,
    ) -> ExecutionAgentDefinition:
        stale_definition: ExecutionAgentDefinition | None = None
        cached = await self._repo.get(agent_id, is_version=is_version)
        if cached is not None:
            try:
                definition = validate_execution_agent_definition(
                    cached.get("definition_json")
                )
                fetched_at = _parse_timestamp(cached.get("fetched_at"))
                if (
                    fetched_at is not None
                    and datetime.now(timezone.utc) - fetched_at < _CACHE_TTL
                    and definition.definition_hash == definition.content_hash()
                ):
                    return definition
                if definition.definition_hash == definition.content_hash():
                    stale_definition = definition
            except (ValidationError, InvalidExecutionAgentDefinition):
                pass
            if stale_definition is None:
                # Corrupt, incomplete, or hash-mismatched: it may not be
                # executed and must not survive the authoritative refetch.
                await self._repo.delete(agent_id, is_version=is_version)

        token = await self._token_repo.get()
        if not token or self._token_repo.is_expired(token):
            if stale_definition is not None:
                return stale_definition
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "authentication_required",
                    "message": (
                        "Saved-agent execution requires a current signed-in "
                        "session so Matrx Local can load the authoritative "
                        "agent definition."
                    ),
                },
            )
        jwt = token.get("access_token")
        if not isinstance(jwt, str) or not jwt:
            if stale_definition is not None:
                return stale_definition
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "authentication_required",
                    "message": (
                        "Saved-agent execution requires a current AIDream "
                        "access token."
                    ),
                },
            )

        client = get_aidream_client()
        if client is None:
            if stale_definition is not None:
                return stale_definition
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "agent_execution_unavailable",
                    "message": (
                        "Saved-agent execution is unavailable because the "
                        "AIDream server URL is not configured."
                    ),
                },
            )
        try:
            raw = await client.fetch_agent_execution_definition(
                agent_id,
                is_version=is_version,
                jwt=jwt,
            )
        except AIDreamOfflineError as exc:
            if stale_definition is not None:
                return stale_definition
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "agent_definition_unavailable",
                    "message": (
                        "The canonical saved-agent definition is not cached and "
                        "AIDream is currently unreachable."
                    ),
                },
            ) from exc
        except AIDreamError as exc:
            if exc.status == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "agent_not_found",
                        "message": f"Agent not found: {agent_id}",
                    },
                ) from exc
            if exc.status in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ):
                raise HTTPException(
                    status_code=exc.status,
                    detail={
                        "code": "agent_definition_access_denied",
                        "message": (
                            "The signed-in user is not authorized to load this "
                            "saved agent's executable definition."
                        ),
                    },
                ) from exc
            if exc.status == status.HTTP_422_UNPROCESSABLE_CONTENT:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "agent_definition_invalid",
                        "message": (
                            "AIDream refused the saved agent because its "
                            "executable definition is incomplete or invalid."
                        ),
                    },
                ) from exc
            raise
        try:
            definition = validate_execution_agent_definition(raw)
        except InvalidExecutionAgentDefinition as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "agent_definition_invalid",
                    "message": str(exc),
                },
            ) from exc
        if definition.definition_id != agent_id or definition.is_version != is_version:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "agent_definition_mismatch",
                    "message": (
                        "AIDream returned a mismatched executable-agent "
                        "definition; refusing to cache or execute it."
                    ),
                },
            )
        if definition.definition_hash != definition.content_hash():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "agent_definition_invalid",
                    "message": (
                        "AIDream returned an executable-agent definition with "
                        "an invalid content hash; refusing execution."
                    ),
                },
            )
        await self._repo.upsert(definition.model_dump(mode="json"))
        return definition


_instance: LocalExecutionAgentSource | None = None


def get_execution_agent_source() -> LocalExecutionAgentSource:
    global _instance
    if _instance is None:
        _instance = LocalExecutionAgentSource()
    return _instance
