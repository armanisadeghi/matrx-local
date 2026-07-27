"""
API keys smoke tests.

Covers:
- GET  /settings/api-keys  — list all providers, schema validation
- PUT  /settings/api-keys/{provider}  — store a key, get it back as configured
- DELETE /settings/api-keys/{provider}  — remove a key, confirm unconfigured
- POST /settings/api-keys/bulk  — store multiple keys atomically
- GET  /settings/api-keys/huggingface/value  — 404 when no HF token set

Tests use a synthetic key value that looks realistic but is safe for
ephemeral test storage (cleared by the delete round-trip).
"""

from __future__ import annotations

import httpx
import pytest


# A fake but correctly-prefixed key — safe to store and immediately delete
_FAKE_OPENAI_KEY = "sk-test-matrxlocal-testing-only-not-real-0000000001"
_FAKE_GROQ_KEY = "gsk_test_matrxlocal_testing_only_not_real_000000001"


def test_api_keys_list_schema(http: httpx.Client) -> None:
    """GET /settings/api-keys returns a list of providers with expected fields."""
    r = http.get("/settings/api-keys")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    data = r.json()
    assert "providers" in data, f"Response missing 'providers' key: {data}"

    providers = data["providers"]
    assert isinstance(providers, list) and len(providers) >= 7, (
        f"Expected at least 7 providers, got {len(providers)}: {providers}"
    )

    required_fields = {"provider", "label", "description", "configured"}
    for entry in providers:
        missing = required_fields - set(entry.keys())
        assert not missing, (
            f"Provider entry missing fields {missing}: {entry}"
        )
        assert isinstance(entry["configured"], bool), (
            f"'configured' should be bool, got {type(entry['configured'])}: {entry}"
        )


def test_api_keys_contains_known_providers(http: httpx.Client) -> None:
    """Known providers (including huggingface) are in the list."""
    r = http.get("/settings/api-keys")
    assert r.status_code == 200

    provider_ids = {p["provider"] for p in r.json()["providers"]}
    expected = {"openai", "anthropic", "google", "groq", "huggingface"}

    missing = expected - provider_ids
    assert not missing, (
        f"Expected providers not in /settings/api-keys list: {missing}\n"
        f"Got: {sorted(provider_ids)}"
    )


def test_api_key_set_and_delete_roundtrip(http: httpx.Client) -> None:
    """PUT then DELETE a test key — confirm configured flips to True then back to False."""
    provider = "openai"

    # Store the fake key
    r_put = http.put(
        f"/settings/api-keys/{provider}",
        json={"key": _FAKE_OPENAI_KEY},
    )
    assert r_put.status_code == 200, (
        f"PUT /settings/api-keys/{provider} failed: {r_put.status_code} {r_put.text}"
    )
    put_data = r_put.json()
    assert put_data.get("configured") is True, (
        f"After PUT, 'configured' should be True: {put_data}"
    )
    assert put_data.get("provider") == provider

    # Verify it shows up in the list
    r_list = http.get("/settings/api-keys")
    configured_map = {
        p["provider"]: p["configured"] for p in r_list.json()["providers"]
    }
    assert configured_map.get(provider) is True, (
        f"After PUT, openai should show as configured in list: {configured_map}"
    )

    # Delete it
    r_del = http.delete(f"/settings/api-keys/{provider}")
    assert r_del.status_code == 200, (
        f"DELETE /settings/api-keys/{provider} failed: {r_del.status_code} {r_del.text}"
    )
    del_data = r_del.json()
    assert del_data.get("configured") is False, (
        f"After DELETE, 'configured' should be False: {del_data}"
    )

    # Confirm cleaned up
    r_list2 = http.get("/settings/api-keys")
    configured_map2 = {
        p["provider"]: p["configured"] for p in r_list2.json()["providers"]
    }
    assert configured_map2.get(provider) is False, (
        f"After DELETE, openai should show as unconfigured in list: {configured_map2}"
    )


def test_api_key_invalid_provider_rejected(http: httpx.Client) -> None:
    """PUT with an unknown provider returns 422."""
    r = http.put(
        "/settings/api-keys/not_a_real_provider",
        json={"key": "sk-test-fake"},
    )
    assert r.status_code == 422, (
        f"Expected 422 for unknown provider, got {r.status_code}: {r.text}"
    )


def test_api_keys_bulk_set(http: httpx.Client) -> None:
    """POST /settings/api-keys/bulk stores multiple keys, cleans up after."""
    payload = {
        "keys": [
            {"provider": "openai", "key": _FAKE_OPENAI_KEY},
            {"provider": "groq", "key": _FAKE_GROQ_KEY},
        ]
    }
    r = http.post("/settings/api-keys/bulk", json=payload)
    assert r.status_code == 200, (
        f"POST /settings/api-keys/bulk failed: {r.status_code} {r.text}"
    )

    data = r.json()
    assert "saved" in data and "skipped" in data and "errors" in data, (
        f"Bulk response missing expected fields: {data}"
    )

    saved = set(data["saved"])
    assert "openai" in saved, f"'openai' not in saved list: {data}"
    assert "groq" in saved, f"'groq' not in saved list: {data}"
    assert not data["errors"], f"Unexpected errors in bulk save: {data['errors']}"

    # Cleanup — delete both
    http.delete("/settings/api-keys/openai")
    http.delete("/settings/api-keys/groq")


def test_api_keys_bulk_skips_invalid_provider(http: httpx.Client) -> None:
    """POST /settings/api-keys/bulk skips unknown providers gracefully."""
    payload = {
        "keys": [
            {"provider": "not_a_real_provider", "key": "sk-test-fake-key-abc"},
        ]
    }
    r = http.post("/settings/api-keys/bulk", json=payload)
    assert r.status_code == 200, (
        f"Bulk with invalid provider should return 200 with skipped list: {r.text}"
    )

    data = r.json()
    assert "not_a_real_provider" in data.get("skipped", []), (
        f"Invalid provider should appear in 'skipped': {data}"
    )


def test_huggingface_value_endpoint_404_when_not_set(http: httpx.Client) -> None:
    """GET /settings/api-keys/huggingface/value returns 404 when token not configured."""
    # Ensure HF key is not set first
    http.delete("/settings/api-keys/huggingface")

    r = http.get("/settings/api-keys/huggingface/value")
    assert r.status_code == 404, (
        f"Expected 404 when HF token not set, got {r.status_code}: {r.text}"
    )


# ── Credential Vault as a second key source ──────────────────────────────────
#
# The vault tier is ADDITIVE: these tests assert the surface answers with a
# STATE (never an error) and that it never leaks a plaintext value. They pass
# whether or not the test engine has a signed-in session.

_VAULT_STATES = {
    "ready",
    "no_session",
    "unconfigured",
    "offline",
    "denied",
    "error",
}


def test_api_key_status_declares_its_source(http: httpx.Client) -> None:
    """Every provider says where the key a request would use comes from."""
    r = http.get("/settings/api-keys")
    assert r.status_code == 200

    for entry in r.json()["providers"]:
        assert entry["source"] in {"local", "vault", "none"}, entry
        # `configured` keeps its original meaning: saved on THIS machine.
        if entry["configured"]:
            assert entry["source"] == "local", entry
        if entry["source"] == "none":
            assert entry["vault_item_name"] is None, entry


def test_vault_source_is_a_state_never_an_error(http: httpx.Client) -> None:
    """No session / offline must render as a prompt, not a failed request."""
    r = http.get("/settings/api-keys/vault")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    data = r.json()
    assert data["state"] in _VAULT_STATES, data
    assert isinstance(data["entries"], list)
    if data["state"] != "ready":
        assert data["message"], "a non-ready state must explain itself"
        assert data["entries"] == []


def test_vault_source_never_returns_a_plaintext_value(http: httpx.Client) -> None:
    """The listing is metadata only — values are resolved server-side."""
    r = http.get("/settings/api-keys/vault")
    assert r.status_code == 200

    allowed = {
        "provider",
        "label",
        "item_id",
        "item_name",
        "field_key",
        "handling",
        "available",
        "local_key_present",
        "in_use",
    }
    for entry in r.json()["entries"]:
        extra = set(entry.keys()) - allowed
        assert not extra, f"unexpected field(s) in a Vault entry: {extra}"


def test_vault_import_rejects_an_unknown_provider(http: httpx.Client) -> None:
    r = http.post(
        "/settings/api-keys/vault/import", json={"provider": "not-a-provider"}
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
