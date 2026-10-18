"""Prompt blocks on the CLIENT host: the one way matrx-local resolves an agent's DECLARED blocks.

The server resolves blocks from tables this host does not have (personal, then the run's
organization, then the agent's own organization when the agent is named, then system; the shared ``matrx_ai.prompt_blocks.resolve_declared_blocks``). The
client host reaches that answer through the API the server provides for it, ``GET
/prompt-blocks/resolve``, and puts it on the config exactly where the shared resolver puts it
(``prompt_block_texts`` / ``prompt_block_report`` / ``prompt_block_tool_ids``), so the shared
first-turn fill (``UnifiedConfig.replace_variables``) finds every declared block resolved.

A block that cannot be resolved refuses the run, naming the block. Never skipped, never raw.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, status
from matrx_ai.prompt_blocks import PromptBlocksMissing, add_block_tools, declared_names

from app.services.aidream.client import AIDreamError, AIDreamOfflineError, get_aidream_client


def _refusal(code: str, message: str, names: list[str], http_status: int) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message, "blocks": names},
    )


def _missing_names(error: AIDreamError, declared: list[str]) -> list[str]:
    """The block names the server said it could not find; every declared name if it did not say."""
    body = error.body if isinstance(error.body, dict) else {}
    text = " ".join(str(body.get(k) or "") for k in ("message", "detail", "user_message"))
    match = re.search(r"not found[^:]*:\s*(.+)$", text) or re.search(r"block\(s\)[^:]*:\s*(.+)$", text)
    if match:
        named = [n.strip().rstrip(".") for n in match.group(1).split(",")]
        found = [n for n in declared if n in named]
        if found:
            return found
    return list(declared)


async def resolve_declared_prompt_blocks(
    config: Any, ctx: Any, *, agent_id: str | None = None, agent_is_version: bool = False
) -> None:
    """Resolve ``config``'s declared prompt blocks once (idempotent), or refuse the run by name.

    ``agent_id`` names the agent (or version) whose blocks these are: the server looks up that
    definition's owning organization itself, so a block the agent's own organization holds is
    found when this device runs in another organization (run org > agent org > system)."""
    names = declared_names(getattr(config, "prompt_blocks", None))
    if getattr(config, "prompt_block_texts", None) is not None:
        return
    if not names:
        config.prompt_block_texts = {}
        return

    if not config.prompt_block_creator:
        config.prompt_block_creator = getattr(ctx, "user_id", None)

    tools = [t for t in (config.tools or []) if isinstance(t, str)]
    reasoning_raw = getattr(config, "reasoning_effort", None)
    reasoning = None if reasoning_raw is None else str(getattr(reasoning_raw, "value", reasoning_raw))
    query: list[tuple[str, str]] = [("names", n) for n in names]
    query.append(("model", str(config.model or "")))
    if reasoning:
        query.append(("reasoning", reasoning))
    query.extend(("tools", t) for t in tools)
    if config.prompt_block_creator:
        query.append(("creator", str(config.prompt_block_creator)))
    if agent_id:
        query.append(("agent_id", str(agent_id)))
        if agent_is_version:
            query.append(("agent_is_version", "true"))

    unavailable = ", ".join(names)
    client = get_aidream_client()
    jwt = getattr(ctx, "token", None)
    if client is None or not jwt:
        raise _refusal(
            "prompt_block_source_unavailable",
            f"Prompt block(s) {unavailable} cannot be resolved: the server is not reachable or "
            "this device is not signed in.",
            names,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    org = getattr(ctx, "organization_id", None)
    try:
        resolved = await client.get(
            f"/prompt-blocks/resolve?{urlencode(query)}",
            jwt=jwt,
            headers={"X-Organization-Id": str(org)} if org else None,
        )
    except AIDreamError as exc:
        if exc.status == 422:
            missing = _missing_names(exc, names)
            error = PromptBlocksMissing(missing)
            raise _refusal(
                error.error_info.error_type,
                error.error_info.user_message,
                missing,
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            ) from exc
        raise _refusal(
            "prompt_block_source_unavailable",
            f"Prompt block(s) {unavailable} could not be resolved (server answered {exc.status}).",
            names,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc
    except AIDreamOfflineError as exc:
        raise _refusal(
            "prompt_block_source_unavailable",
            f"Prompt block(s) {unavailable} could not be resolved: the server is unreachable.",
            names,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    blocks = resolved.get("blocks") if isinstance(resolved, dict) else None
    blocks = blocks if isinstance(blocks, dict) else {}
    missing = [n for n in names if n not in blocks]
    if missing:
        error = PromptBlocksMissing(missing)
        raise _refusal(
            error.error_info.error_type,
            error.error_info.user_message,
            missing,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    config.prompt_block_texts = {n: str(blocks[n]) for n in names}
    config.prompt_block_report = list(resolved.get("report") or [])
    config.prompt_block_tool_ids = [str(t) for t in resolved.get("tool_ids") or []]
    if config.prompt_block_tool_ids:
        add_block_tools(config)
