"""The Credential Vault key tier must fill gaps and never shadow a local key.

Covers the two things that would break the promise "a key saved once is
available everywhere":
  1. matching vault items to local providers (provider_key AND env_key alias),
  2. the resolution order — local first, Vault second, always.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.ai import key_manager
from app.services.credential_vault import provider_keys


def _item(
    item_id: str,
    *,
    provider_key: str | None = None,
    display_name: str = "Item",
    fields: list[dict[str, Any]] | None = None,
    can_reveal: bool = True,
    updated_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "display_name": display_name,
        "provider_key": provider_key,
        "updated_at": updated_at,
        "capabilities": {"can_use": True, "can_reveal": can_reveal},
        "fields": fields
        or [
            {
                "field_key": "api_key",
                "env_key": None,
                "handling": "revealable",
                "is_active": True,
            }
        ],
    }


# ── Matching ──────────────────────────────────────────────────────────────


def test_provider_key_identifies_the_local_provider() -> None:
    candidates = provider_keys.build_candidates(
        [_item("i1", provider_key="anthropic", display_name="My Claude key")]
    )
    assert [(c.provider, c.item_name, c.field_key) for c in candidates] == [
        ("anthropic", "My Claude key", "api_key")
    ]


def test_env_key_alias_identifies_a_custom_item_with_no_provider_key() -> None:
    candidates = provider_keys.build_candidates(
        [
            _item(
                "i2",
                display_name="Scratch env",
                fields=[
                    {
                        "field_key": "value",
                        "env_key": "hugging_face_hub_token",
                        "handling": "revealable",
                        "is_active": True,
                    }
                ],
            )
        ]
    )
    assert [(c.provider, c.field_key) for c in candidates] == [
        ("huggingface", "value")
    ]


def test_the_api_key_provider_catalog_supplies_the_wider_alias_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same alias table the .env bulk import uses — one list, both paths.
    A vault field named BRAVE_SEARCH_API_KEY must resolve like a pasted one."""

    class _Entry:
        key = "brave"
        payload = {
            "names": ["brave", "brave_search"],
            "env_var_names": ["BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY"],
            "label": "Brave Search",
        }

    monkeypatch.setattr(
        "app.services.catalogs.get_catalog",
        lambda _kind: [_Entry()],
    )
    candidates = provider_keys.build_candidates(
        [
            _item(
                "b",
                display_name="Brave search key",
                fields=[
                    {
                        "field_key": "value",
                        "env_key": "BRAVE_SEARCH_API_KEY",
                        "handling": "revealable",
                        "is_active": True,
                    }
                ],
            )
        ]
    )
    assert [(c.provider, c.field_key) for c in candidates] == [("brave", "value")]


def test_a_missing_alias_catalog_never_breaks_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_kind: str) -> list[object]:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr("app.services.catalogs.get_catalog", _boom)
    candidates = provider_keys.build_candidates(
        [_item("i", provider_key="anthropic")]
    )
    assert [c.provider for c in candidates] == ["anthropic"]


def test_items_for_providers_this_app_does_not_know_are_ignored() -> None:
    assert provider_keys.build_candidates([_item("i3", provider_key="stripe")]) == []


def test_the_key_field_of_a_multi_field_item_is_the_env_aliased_one() -> None:
    candidates = provider_keys.build_candidates(
        [
            _item(
                "i4",
                provider_key="openai",
                fields=[
                    {
                        "field_key": "org_id",
                        "env_key": "OPENAI_ORG_ID",
                        "handling": "visible",
                        "is_active": True,
                    },
                    {
                        "field_key": "secret",
                        "env_key": "OPENAI_API_KEY",
                        "handling": "revealable",
                        "is_active": True,
                    },
                ],
            )
        ]
    )
    assert [c.field_key for c in candidates] == ["secret"]


def test_a_same_provider_item_holding_a_different_secret_is_not_used() -> None:
    """The vault groups unrelated credentials under one provider slug: a
    Google Analytics service-account JSON is also ``provider_key=google``.
    Handing that to matrx-ai as GEMINI_API_KEY would fail inexplicably."""
    assert (
        provider_keys.build_candidates(
            [
                _item(
                    "ga",
                    provider_key="google",
                    display_name="GA4 service account",
                    fields=[
                        {
                            "field_key": "json_document",
                            "env_key": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                            "handling": "revealable",
                            "is_active": True,
                        },
                        {
                            "field_key": "property_id",
                            "env_key": "GA4_PROPERTY_ID",
                            "handling": "visible",
                            "is_active": True,
                        },
                    ],
                )
            ]
        )
        == []
    )


def test_a_lone_field_with_a_foreign_env_alias_is_not_used() -> None:
    assert (
        provider_keys.build_candidates(
            [
                _item(
                    "ga1",
                    provider_key="google",
                    fields=[
                        {
                            "field_key": "json_document",
                            "env_key": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                            "handling": "revealable",
                            "is_active": True,
                        }
                    ],
                )
            ]
        )
        == []
    )


def test_the_most_recently_updated_item_wins_a_duplicate_provider() -> None:
    candidates = provider_keys.build_candidates(
        [
            _item(
                "old",
                provider_key="groq",
                display_name="Old",
                updated_at="2025-01-01T00:00:00Z",
            ),
            _item(
                "new",
                provider_key="groq",
                display_name="New",
                updated_at="2026-06-01T00:00:00Z",
            ),
        ]
    )
    assert [(c.item_id, c.item_name) for c in candidates] == [("new", "New")]


def test_sealed_and_unrevealable_values_are_never_asked_for() -> None:
    candidates = provider_keys.build_candidates(
        [
            _item(
                "s",
                provider_key="openai",
                fields=[
                    {
                        "field_key": "api_key",
                        "handling": "sealed",
                        "is_active": True,
                    }
                ],
            ),
            _item("u", provider_key="groq", can_reveal=False),
            _item(
                "v",
                provider_key="xai",
                fields=[
                    {
                        "field_key": "api_key",
                        "handling": "visible",
                        "is_active": True,
                    }
                ],
                can_reveal=False,
            ),
        ]
    )
    by_provider = {c.provider: c for c in candidates}
    assert by_provider["openai"].resolvable is False  # sealed never crosses
    assert by_provider["groq"].resolvable is False  # revealable, no can_reveal
    # `visible` needs only can_use, so it stays resolvable without can_reveal.
    assert by_provider["xai"].resolvable is True


# ── Resolution order ──────────────────────────────────────────────────────


def _reset(monkeypatch: pytest.MonkeyPatch, local: dict[str, str]) -> None:
    monkeypatch.setattr(key_manager, "_user_keys", dict(local))
    monkeypatch.setattr(key_manager, "_user_keys_loaded", True)
    monkeypatch.setattr(key_manager, "_vault_keys", {})
    monkeypatch.setattr(key_manager, "_vault_origins", {})
    monkeypatch.setattr(key_manager, "_vault_refresh_lock", asyncio.Lock())
    monkeypatch.setattr(key_manager, "_inject", lambda _provider, _key: None)
    monkeypatch.setattr(key_manager, "_erase", lambda _provider: None)


def _stub_snapshot(
    monkeypatch: pytest.MonkeyPatch, snapshot: provider_keys.VaultProviderSnapshot
) -> list[set[str] | None]:
    """Capture which providers the refresh asked the vault to resolve."""
    asked: list[set[str] | None] = []

    async def _fetch(
        *, resolve_providers: set[str] | None = None
    ) -> provider_keys.VaultProviderSnapshot:
        asked.append(resolve_providers)
        return snapshot

    monkeypatch.setattr(provider_keys, "fetch_provider_snapshot", _fetch)
    return asked


def test_a_local_key_is_never_shadowed_by_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch, {"openai": "local-openai"})
    candidate = provider_keys.build_candidates(
        [_item("i", provider_key="anthropic", display_name="Team Claude")]
    )[0]
    asked = _stub_snapshot(
        monkeypatch,
        provider_keys.VaultProviderSnapshot(
            state="ready",
            candidates=(candidate,),
            values={"anthropic": "vault-anthropic"},
        ),
    )

    asyncio.run(key_manager.refresh_vault_keys())

    # The provider the desktop already owns is never fetched a second time.
    assert asked and "openai" not in (asked[0] or set())
    assert key_manager.get_cached_user_keys() == {
        "openai": "local-openai",
        "anthropic": "vault-anthropic",
    }
    assert key_manager.get_local_user_keys() == {"openai": "local-openai"}
    assert key_manager.get_vault_key_origins() == {"anthropic": "Team Claude"}
    resolver = key_manager.get_key_resolver()
    assert resolver("OPENAI_API_KEY") == "local-openai"
    assert resolver("ANTHROPIC_API_KEY") == "vault-anthropic"


def test_an_unavailable_vault_leaves_local_keys_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch, {"openai": "local-openai"})
    _stub_snapshot(
        monkeypatch,
        provider_keys.VaultProviderSnapshot(
            state="no_session", message="Sign in to AI Matrx"
        ),
    )

    snapshot = asyncio.run(key_manager.refresh_vault_keys())

    assert snapshot.ok is False
    assert key_manager.get_cached_user_keys() == {"openai": "local-openai"}
    assert key_manager.get_vault_keys() == {}


def test_sign_out_drops_vault_keys_but_keeps_local_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch, {"openai": "local-openai"})
    key_manager._vault_keys["groq"] = "vault-groq"
    key_manager._vault_origins["groq"] = "Shared Groq"

    key_manager.clear_vault_keys()

    assert key_manager.get_vault_keys() == {}
    assert key_manager.get_cached_user_keys() == {"openai": "local-openai"}
