"""Forcing checks for the profile authorization rules behind final macOS signing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "verify-apple-provisioning-profile.py"
SPEC = importlib.util.spec_from_file_location("verify_apple_provisioning_profile", MODULE_PATH)
assert SPEC and SPEC.loader
profile_verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile_verifier)


def signed_provider() -> dict[str, object]:
    _, expected, _ = profile_verifier.expected_contract("provider")
    return dict(expected)


def provider_profile() -> dict[str, object]:
    return {
        profile_verifier.APPLICATION_IDENTIFIER: f"{profile_verifier.TEAM_ID}.*",
        profile_verifier.TEAM_ENTITLEMENT: profile_verifier.TEAM_ID,
        "com.apple.developer.authentication-services.autofill-credential-provider": True,
        "com.apple.security.application-groups": [profile_verifier.STATUS_GROUP, "group.com.aimatrx.unrelated"],
        "keychain-access-groups": [f"{profile_verifier.TEAM_ID}.*"],
    }


def test_profile_authorizes_exact_signed_provider_groups_with_extra_and_trailing_wildcard_grants() -> None:
    """A profile with normal Mac default grants must authorize, not replace, the least-privilege signed set."""
    signed = signed_provider()
    profile_verifier.assert_signed_contract("provider", signed)
    profile_verifier.assert_profile_authorizes("provider", provider_profile(), signed)


@pytest.mark.parametrize(
    ("grant", "requested"),
    [
        ("JH83UH9P4D.*", "JH83UH9P4D.com.aimatrx.desktop.vault-provider"),
        ("group.com.aimatrx.*", profile_verifier.STATUS_GROUP),
    ],
)
def test_profile_authorizes_only_documented_trailing_wildcards(grant: str, requested: str) -> None:
    assert profile_verifier.profile_value_authorizes(grant, requested)


@pytest.mark.parametrize("grant", ["*", "JH83*UH9P4D.*", "JH83UH9P4D.*.provider"])
def test_profile_rejects_nontrailing_or_unscoped_wildcards(grant: str) -> None:
    assert not profile_verifier.profile_value_authorizes(
        grant, "JH83UH9P4D.com.aimatrx.desktop.vault-provider"
    )


def test_profile_rejects_provider_group_outside_its_authorized_prefix() -> None:
    profile = provider_profile()
    profile["keychain-access-groups"] = ["JH83UH9P4D.com.aimatrx.desktop.other"]
    with pytest.raises(ValueError, match="keychain-access-groups"):
        profile_verifier.assert_profile_authorizes("provider", profile, signed_provider())


def test_host_rejects_provider_keychain_group_even_when_profile_can_grant_extra_defaults() -> None:
    _, expected_host, _ = profile_verifier.expected_contract("host")
    signed_host = dict(expected_host)
    signed_host["keychain-access-groups"] = [profile_verifier.PROVIDER_KEYCHAIN_GROUP]
    with pytest.raises(ValueError, match="never include a Keychain access group"):
        profile_verifier.assert_signed_contract("host", signed_host)


def test_host_rejects_wildcard_keychain_grant_even_when_it_can_match_the_provider_group() -> None:
    _, expected_host, _ = profile_verifier.expected_contract("host")
    signed_host = dict(expected_host)
    signed_host["keychain-access-groups"] = [f"{profile_verifier.TEAM_ID}.*"]
    with pytest.raises(ValueError, match="never include a Keychain access group"):
        profile_verifier.assert_signed_contract("host", signed_host)


def test_host_rejects_team_wildcard_instead_of_its_exact_signed_team() -> None:
    _, expected_host, _ = profile_verifier.expected_contract("host")
    signed_host = dict(expected_host)
    signed_host[profile_verifier.TEAM_ENTITLEMENT] = f"{profile_verifier.TEAM_ID}*"
    with pytest.raises(ValueError, match="com.apple.developer.team-identifier"):
        profile_verifier.assert_signed_contract("host", signed_host)


def test_provider_rejects_unreviewed_healthkit_claim_even_when_profile_grants_it() -> None:
    signed = signed_provider()
    signed["com.apple.developer.healthkit"] = True
    profile = provider_profile()
    profile["com.apple.developer.healthkit"] = True
    with pytest.raises(ValueError, match="com.apple.developer.healthkit.*outside the reviewed native contract"):
        profile_verifier.assert_signed_contract("provider", signed)


def test_sandbox_and_network_are_signed_requirements_not_profile_requirements() -> None:
    """Unrestricted sandbox/network claims remain exact in signed code without demanding fake profile entries."""
    profile = provider_profile()
    signed = signed_provider()
    profile_verifier.assert_signed_contract("provider", signed)
    profile_verifier.assert_profile_authorizes("provider", profile, signed)
