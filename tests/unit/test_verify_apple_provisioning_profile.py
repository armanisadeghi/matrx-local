"""Forcing checks for the profile authorization rules behind final macOS signing."""

from __future__ import annotations

import importlib.util
import plistlib
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "verify-apple-provisioning-profile.py"
SPEC = importlib.util.spec_from_file_location("verify_apple_provisioning_profile", MODULE_PATH)
assert SPEC and SPEC.loader
profile_verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile_verifier)


AUTOFILL = "com.apple.developer.authentication-services.autofill-credential-provider"


def host_source_entitlements() -> dict[str, object]:
    path = MODULE_PATH.parents[1] / "desktop/src-tauri/Entitlements.vault.plist"
    return plistlib.loads(path.read_bytes())


def test_host_source_declares_autofill_without_provider_keychain_access() -> None:
    signed = host_source_entitlements()
    assert signed[AUTOFILL] is True
    assert "keychain-access-groups" not in signed
    profile_verifier.assert_signed_contract("host", signed)
    profile_verifier.assert_profile_authorizes("host", provider_profile(), signed)


@pytest.mark.parametrize("replacement", [None, False, 1, "true"])
def test_host_rejects_missing_or_mistyped_autofill_permission(replacement: object) -> None:
    signed = host_source_entitlements()
    if replacement is None:
        signed.pop(AUTOFILL, None)
    else:
        signed[AUTOFILL] = replacement
    with pytest.raises(ValueError, match="autofill-credential-provider"):
        profile_verifier.assert_signed_contract("host", signed)


def test_host_profile_must_authorize_autofill() -> None:
    profile = provider_profile()
    profile.pop(AUTOFILL)
    with pytest.raises(ValueError, match="autofill-credential-provider"):
        profile_verifier.assert_profile_authorizes("host", profile, host_source_entitlements())


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


def test_host_requires_type_exact_values_for_its_intended_claims() -> None:
    _, expected_host, _ = profile_verifier.expected_contract("host")
    signed_host = dict(expected_host)
    signed_host["com.apple.security.application-groups"] = (profile_verifier.STATUS_GROUP,)

    with pytest.raises(ValueError, match="must equal"):
        profile_verifier.assert_signed_contract("host", signed_host)


def test_host_allows_nonrestricted_runtime_claims_outside_its_intended_contract() -> None:
    _, expected_host, _ = profile_verifier.expected_contract("host")
    signed_host = dict(expected_host)
    signed_host["com.apple.security.network.client"] = True

    profile_verifier.assert_signed_contract("host", signed_host)


def test_provider_rejects_unreviewed_healthkit_claim_even_when_profile_grants_it() -> None:
    signed = signed_provider()
    signed["com.apple.developer.healthkit"] = True
    profile = provider_profile()
    profile["com.apple.developer.healthkit"] = True
    with pytest.raises(ValueError, match="exactly match"):
        profile_verifier.assert_signed_contract("provider", signed)


@pytest.mark.parametrize(
    "extra_claim",
    [
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.network.server",
        "com.apple.security.device.camera",
    ],
)
def test_provider_rejects_each_runtime_privilege_outside_its_exact_signed_contract(
    extra_claim: str,
) -> None:
    signed = signed_provider()
    signed[extra_claim] = True

    with pytest.raises(ValueError, match="exactly match"):
        profile_verifier.assert_signed_contract("provider", signed)


@pytest.mark.parametrize(
    ("claim", "replacement"),
    [
        ("com.apple.security.app-sandbox", 1),
        ("com.apple.security.network.client", 1),
        ("com.apple.security.application-groups", (profile_verifier.STATUS_GROUP,)),
    ],
)
def test_provider_requires_type_exact_signed_claim_values(claim: str, replacement: object) -> None:
    signed = signed_provider()
    signed[claim] = replacement

    with pytest.raises(ValueError, match="exactly match"):
        profile_verifier.assert_signed_contract("provider", signed)


def test_provider_accepts_the_complete_exact_seven_claim_contract() -> None:
    profile_verifier.assert_signed_contract("provider", signed_provider())


def test_sandbox_and_network_are_signed_requirements_not_profile_requirements() -> None:
    """Unrestricted sandbox/network claims remain exact in signed code without demanding fake profile entries."""
    profile = provider_profile()
    signed = signed_provider()
    profile_verifier.assert_signed_contract("provider", signed)
    profile_verifier.assert_profile_authorizes("provider", profile, signed)
