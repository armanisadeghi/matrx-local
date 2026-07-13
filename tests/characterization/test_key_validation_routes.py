"""Pins the /settings/api-keys validation ROUTE contract.

The load-bearing guarantee here is that a rejected key is an HTTP **200** whose
body says "invalid" — not a 4xx. The desktop engine client (`EngineAPI.request`,
desktop/src/lib/api.ts) throws away the response body on a non-2xx and surfaces
only `"POST <path> failed: 401"`. Answering a bad key with a 4xx would therefore
strip the reason off on its way to the UI and show the user a status code.

A rejected key is a SUCCESSFUL check. Only a bad *request* (unknown provider) is
an error.

Stubs, no network and no SQLite: these assert routing/persistence logic, not the
providers (which tests/characterization/test_key_validation.py covers).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

# app.main first: importing app.api.settings_routes directly trips a pre-existing
# circular import through app.config.
import app.main  # noqa: F401
from app.api import settings_routes
from app.services.ai.key_validation import ValidationResult


class _FakeRepo:
    """Stands in for ApiKeysRepo — records what the route persists."""

    stored: dict[str, str] = {}
    recorded: list[tuple[str, str, str | None]] = []

    async def get_all(self) -> dict[str, str]:
        return dict(self.stored)

    async def record_validation(
        self, provider: str, verdict: str, account: str | None
    ) -> None:
        type(self).recorded.append((provider, verdict, account))


@pytest.fixture(autouse=True)
def _reset() -> Any:
    _FakeRepo.stored = {}
    _FakeRepo.recorded = []
    yield


def _patch(monkeypatch: pytest.MonkeyPatch, result: ValidationResult) -> None:
    monkeypatch.setattr(settings_routes, "ApiKeysRepo", _FakeRepo)

    async def _validate_key(provider: str, key: str) -> ValidationResult:
        return result

    async def _validate_stored_key(provider: str) -> ValidationResult:
        return result

    import app.services.ai.key_validation as kv

    monkeypatch.setattr(kv, "validate_key", _validate_key)
    monkeypatch.setattr(kv, "validate_stored_key", _validate_stored_key)


def test_rejected_key_is_a_200_not_an_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: the UI must receive the REASON, not a bare status code."""
    _patch(
        monkeypatch,
        ValidationResult(
            provider="civitai",
            verdict="invalid",
            message="The provider rejected this key.",
            status_code=401,
        ),
    )
    # No HTTPException raised → FastAPI serialises this as a 200.
    result = asyncio.run(
        settings_routes.validate_api_key("civitai", None)
    )
    assert result.verdict == "invalid"
    assert result.message  # the reason survives to the client


def test_unknown_provider_is_a_422() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(settings_routes.validate_api_key("not-a-provider", None))
    assert exc.value.status_code == 422


def test_verdict_for_the_stored_key_is_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch,
        ValidationResult(
            provider="huggingface", verdict="valid", message="ok", account="armani"
        ),
    )
    asyncio.run(settings_routes.validate_api_key("huggingface", None))
    assert _FakeRepo.recorded == [("huggingface", "valid", "armani")]


def test_verdict_for_an_unsaved_candidate_key_is_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Testing a typed key says nothing about the key that is actually stored."""
    _patch(
        monkeypatch,
        ValidationResult(provider="openai", verdict="valid", message="ok"),
    )
    req = settings_routes.ValidateApiKeyRequest(key="sk-typed-but-never-saved")
    asyncio.run(settings_routes.validate_api_key("openai", req))
    assert _FakeRepo.recorded == []


def test_validate_all_skips_providers_with_no_key_and_no_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_routes, "ApiKeysRepo", _FakeRepo)
    _FakeRepo.stored = {
        "openai": "sk-real",
        "anthropic": "   ",      # blank → nothing to test
        "fastino": "key",        # no free endpoint → untestable
    }

    import app.services.ai.key_validation as kv

    async def _validate_many(keys: dict[str, str]) -> list[ValidationResult]:
        # Only the one provider that has both a key and a spec should arrive.
        assert set(keys) == {"openai"}
        return [
            ValidationResult(provider=p, verdict="valid", message="ok") for p in keys
        ]

    monkeypatch.setattr(kv, "validate_many", _validate_many)

    out = asyncio.run(settings_routes.validate_all_api_keys())
    assert [r.provider for r in out.results] == ["openai"]
    assert _FakeRepo.recorded == [("openai", "valid", None)]
