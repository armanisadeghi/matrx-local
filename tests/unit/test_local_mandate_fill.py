"""THE VARIABLE RULE on the local-model mandate door (``POST /local-mandates/{key}/fill``).

The desktop's local-model jobs (AI Polish, the text pipeline, Confidential Chat, Tools and Raw
modes, Cloud Chat on a local model) run a mandate's Holder on llama-server. Its authored messages
are filled by matrx-ai's ONE first-turn pass on the engine — never by a TypeScript copy:
declared ``{{name}}`` only (client value > default > ""), the ``{{!name}}`` escape for any
name, sections, declared prompt blocks resolved through the server or the run refused by name.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.services.ai import local_mandates, prompt_blocks_client
from app.services.aidream.client import AIDreamError

AGENT = "33333333-3333-4333-8333-333333333333"


def _definition(system: str, user: str = "Text: {{text}}", blocks: list[str] | None = None) -> dict[str, Any]:
    return {
        "definition_id": AGENT,
        "agent_id": AGENT,
        "name": "holder",
        "model_id": "local-model",
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": system}]},
            {"role": "user", "content": user},
        ],
        "variable_definitions": [
            {"name": "text", "defaultValue": "DEFAULT"},
            {"name": "vip", "defaultValue": ""},
            {"name": "empty"},
        ],
        "prompt_blocks": blocks or [],
    }


def _ctx() -> Any:
    return SimpleNamespace(user_id="user-1", token="jwt-1", organization_id="org-1")


def _fill(definition: dict[str, Any], variables: dict[str, Any] | None) -> list[dict[str, str]]:
    return asyncio.run(local_mandates.fill_holder_messages(definition, variables or {}, _ctx()))


def test_undeclared_keys_never_fill() -> None:
    out = _fill(_definition("S {{other}} {{text}}"), {"other": "LEAK", "text": "hello"})
    assert out[0] == {"role": "system", "content": "S {{other}} hello"}
    assert out[1] == {"role": "user", "content": "Text: hello"}


def test_empty_declared_value_becomes_empty_string() -> None:
    out = _fill(_definition("A[{{empty}}] B[{{vip}}]"), {"vip": ""})
    assert out[0]["content"] == "A[] B[]"


def test_default_fills_when_no_value_is_sent() -> None:
    out = _fill(_definition("S"), None)
    assert out[1]["content"] == "Text: DEFAULT"


def test_escape_applies_to_any_name() -> None:
    # Rule 6 (Arman, 2026-10-25): the escape is for names that are NOT variables, so it never depends
    # on what is declared. Needs the matrx-ai that ships it (escape_pattern(()) is a pattern, not None).
    from matrx_ai.config import template_substitution

    if template_substitution.escape_pattern(()) is None:
        pytest.skip("installed matrx-ai still escapes declared names only; lands with the next publish")
    out = _fill(_definition("Keep {{!text}} and {{!todo}}"), {"text": "x"})
    assert out[0]["content"] == "Keep {{text}} and {{todo}}"


def test_sections_evaluate() -> None:
    template = "{{#vip}}VIP{{/vip}}{{^vip}}STD{{/vip}}"
    assert _fill(_definition(template), {"vip": "yes"})[0]["content"] == "VIP"
    assert _fill(_definition(template), {})[0]["content"] == "STD"


def _serve_blocks(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[str]:
    paths: list[str] = []

    class Client:
        async def get(self, path: str, jwt: str | None = None, *, headers: Any = None) -> Any:
            paths.append(path)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(prompt_blocks_client, "get_aidream_client", lambda: Client())
    return paths


def test_declared_block_resolves_through_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _serve_blocks(monkeypatch, {"blocks": {"house": "RULES"}, "report": [], "tool_ids": []})
    out = _fill(_definition("Pre {{<house>}} {{<other>}}", blocks=["house"]), {})
    assert paths and paths[0].startswith("/prompt-blocks/resolve?")
    assert out[0]["content"] == "Pre <house>\nRULES\n</house> {{<other>}}"


def test_missing_block_refuses_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_blocks(monkeypatch, AIDreamError(422, {"message": "Prompt block(s) not found: house"}))
    with pytest.raises(HTTPException) as caught:
        _fill(_definition("Pre {{<house>}}", blocks=["house"]), {})
    assert caught.value.detail["blocks"] == ["house"]


def test_route_fills_through_the_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import local_mandate_routes

    resolver = local_mandates.LocalMandateResolver.__new__(local_mandates.LocalMandateResolver)

    async def resolve(key: str) -> dict[str, Any]:
        return {
            "mandate_key": key,
            "resolution": {"agent_id": AGENT, "is_version": False},
            "definition": _definition("S {{text}} {{other}}"),
            "resolved_at": "t",
            "stale": True,
            "stale_reason": "offline",
        }

    async def block_context() -> Any:
        return _ctx()

    resolver.resolve = resolve  # type: ignore[method-assign]
    resolver._block_context = block_context  # type: ignore[method-assign]
    monkeypatch.setattr(local_mandate_routes, "get_local_mandate_resolver", lambda: resolver)

    app = FastAPI()
    app.include_router(local_mandate_routes.router)
    resp = TestClient(app).post(
        "/local-mandates/local.summarize_text/fill",
        json={"variables": {"text": "T", "other": "LEAK"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["messages"] == [
        {"role": "system", "content": "S T {{other}}"},
        {"role": "user", "content": "Text: T"},
    ]
    assert body["stale"] is True and body["stale_reason"] == "offline"
    assert resp.headers["X-Matrx-Mandate-Stale"] == "true"


def test_the_resolve_call_names_the_agent_so_its_own_organizations_blocks_are_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defect 39, desktop sibling: the server looks up the named agent's owning organization
    (run org > agent org > system). The device sends the agent id, never an organization."""
    from urllib.parse import parse_qs, urlsplit

    paths = _serve_blocks(monkeypatch, {"blocks": {"house": "RULES"}, "report": [], "tool_ids": []})
    _fill(_definition("Pre {{<house>}}", blocks=["house"]), {})
    query = parse_qs(urlsplit(paths[0]).query)
    assert query.get("agent_id") == [AGENT]
    assert "agent_is_version" not in query
    assert not any("organization" in key for key in query)
