"""Every API-key consumer must derive from the canonical provider catalog."""

from app.services.ai.key_manager import PROVIDER_ENV_MAP
from app.services.ai.provider_grants import (
    CHAT_PROVIDERS,
    ENDPOINT_TO_PROVIDER,
    PROVIDER_GRANTS,
    VALID_PROVIDERS,
)

def test_provider_catalog_is_the_single_key_authority() -> None:
    assert VALID_PROVIDERS == frozenset(PROVIDER_ENV_MAP)
    assert "brave" in VALID_PROVIDERS
    for key, spec in PROVIDER_GRANTS.items():
        assert spec.key == key
        assert spec.label and spec.description and spec.env_vars
        assert PROVIDER_ENV_MAP[key] == list(spec.env_vars)


def test_chat_endpoints_are_derived_from_chat_providers() -> None:
    assert CHAT_PROVIDERS
    assert set(ENDPOINT_TO_PROVIDER.values()) == set(CHAT_PROVIDERS)
