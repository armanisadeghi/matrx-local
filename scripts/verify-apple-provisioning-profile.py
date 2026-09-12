#!/usr/bin/env python3
"""Verify a signed macOS code object against its Apple provisioning profile.

Plist decoding alone does not establish that a profile is authentic. This
helper asks macOS Security to verify the CMS envelope, then checks the current
profile expiry, Apple-team/bundle authorization, the certificate actually used
by codesign, and the capability values embedded in the signed code.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path


TEAM_ID = "JH83UH9P4D"
STATUS_GROUP = "group.com.aimatrx.desktop.vault-status"
PROVIDER_BUNDLE_ID = "com.aimatrx.desktop.vault-provider"
HOST_BUNDLE_ID = "com.aimatrx.desktop"
PROVIDER_KEYCHAIN_GROUP = f"{TEAM_ID}.{PROVIDER_BUNDLE_ID}"


def fail(message: str) -> None:
    raise ValueError(message)


def run(*command: str, stdout: bool = False) -> str:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE if stdout else None,
        stderr=subprocess.PIPE,
        check=False,
        text=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        fail(f"{' '.join(command[:2])} failed: {detail or 'non-zero exit'}")
    return completed.stdout.decode("utf-8", "replace") if stdout else ""


def diagnostic_output(*command: str) -> str:
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        fail(f"{' '.join(command[:2])} failed: {detail or 'non-zero exit'}")
    return (completed.stdout + completed.stderr).decode("utf-8", "replace")


def codesign_metadata(code: Path) -> tuple[str, str, dict[object, object], bytes]:
    details = diagnostic_output("codesign", "--display", "--verbose=4", str(code))
    identifier = next((line[11:] for line in details.splitlines() if line.startswith("Identifier=")), "")
    team = next((line[15:] for line in details.splitlines() if line.startswith("TeamIdentifier=")), "")
    if not identifier or not team:
        fail(f"{code} is missing a Developer ID identifier or team identifier")

    entitlements_xml = run(
        "codesign", "--display", "--entitlements", "-", "--xml", str(code), stdout=True
    ).encode()
    try:
        entitlements = plistlib.loads(entitlements_xml)
    except (plistlib.InvalidFileException, ValueError) as error:
        fail(f"{code} has no readable signed entitlements: {error}")
    if not isinstance(entitlements, dict):
        fail(f"{code} signed entitlements are not a plist dictionary")

    with tempfile.TemporaryDirectory(prefix="matrx-profile-cert-") as workdir:
        certificate_prefix = Path(workdir) / "signer"
        run("codesign", "--display", "--extract-certificates", str(certificate_prefix), str(code))
        certificate = Path(f"{certificate_prefix}0")
        if not certificate.is_file():
            fail(f"{code} did not expose its leaf signing certificate")
        # This is independent system trust validation for the certificate that
        # sealed the code, not merely a plist claim from the profile.
        run("security", "verify-cert", "-p", "codeSign", "-c", str(certificate), "-q")
        certificate_bytes = certificate.read_bytes()
    return identifier, team, entitlements, certificate_bytes


def expected_contract(kind: str) -> tuple[str, dict[str, object], set[str]]:
    if kind == "provider":
        return (
            PROVIDER_BUNDLE_ID,
            {
                "com.apple.app-sandbox": True,
                "com.apple.developer.authentication-services.autofill-credential-provider": True,
                "com.apple.security.application-groups": [STATUS_GROUP],
                "keychain-access-groups": [PROVIDER_KEYCHAIN_GROUP],
                "com.apple.security.network.client": True,
            },
            set(),
        )
    return (
        HOST_BUNDLE_ID,
        {"com.apple.security.application-groups": [STATUS_GROUP]},
        {"keychain-access-groups", "com.apple.security.keychain-access-groups"},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--code", required=True, type=Path)
    parser.add_argument("--kind", required=True, choices=("host", "provider"))
    args = parser.parse_args()

    if not args.profile.is_file():
        fail(f"provisioning profile is missing: {args.profile}")
    if not args.code.exists():
        fail(f"signed code object is missing: {args.code}")

    expected_bundle_id, expected_signed, forbidden = expected_contract(args.kind)
    with tempfile.TemporaryDirectory(prefix="matrx-profile-cms-") as workdir:
        decoded_profile = Path(workdir) / "profile.plist"
        # `-u 9` is macOS Security's protected-object-signer trust policy. It
        # rejects arbitrary CMS/plist bytes instead of treating decode success
        # as proof that Apple issued the profile.
        run(
            "security", "cms", "-D", "-u", "9", "-i", str(args.profile), "-o", str(decoded_profile)
        )
        try:
            profile = plistlib.loads(decoded_profile.read_bytes())
        except (FileNotFoundError, plistlib.InvalidFileException, ValueError) as error:
            fail(f"Apple CMS verification produced no readable profile: {error}")

    if not isinstance(profile, dict):
        fail("decoded provisioning profile is not a plist dictionary")
    expires = profile.get("ExpirationDate")
    if not isinstance(expires, dt.datetime):
        fail("provisioning profile has no valid ExpirationDate")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.timezone.utc)
    if expires <= dt.datetime.now(dt.timezone.utc):
        fail(f"provisioning profile expired at {expires.isoformat()}")

    teams = profile.get("TeamIdentifier")
    if teams != [TEAM_ID]:
        fail(f"profile TeamIdentifier must be [{TEAM_ID!r}], got {teams!r}")
    profile_entitlements = profile.get("Entitlements")
    if not isinstance(profile_entitlements, dict):
        fail("provisioning profile has no Entitlements dictionary")
    if profile_entitlements.get("application-identifier") != f"{TEAM_ID}.{expected_bundle_id}":
        fail("profile application-identifier does not authorize the expected bundle")
    if profile_entitlements.get("com.apple.developer.team-identifier") != TEAM_ID:
        fail("profile team entitlement does not match the expected team")

    identifier, code_team, signed_entitlements, signing_certificate = codesign_metadata(args.code)
    if identifier != expected_bundle_id:
        fail(f"signed code identifier must be {expected_bundle_id}, got {identifier}")
    if code_team != TEAM_ID:
        fail(f"signed code team must be {TEAM_ID}, got {code_team}")

    profile_certificates = profile.get("DeveloperCertificates")
    if not isinstance(profile_certificates, list) or not all(
        isinstance(certificate, bytes) for certificate in profile_certificates
    ):
        fail("profile has no valid DeveloperCertificates list")
    signing_digest = hashlib.sha256(signing_certificate).digest()
    if signing_digest not in {hashlib.sha256(certificate).digest() for certificate in profile_certificates}:
        fail("profile does not authorize the certificate that signed this code")

    for key, expected_value in expected_signed.items():
        if signed_entitlements.get(key) != expected_value:
            fail(f"signed {args.kind} entitlement {key!r} must equal {expected_value!r}")
        if profile_entitlements.get(key) != expected_value:
            fail(f"profile does not authorize exact {args.kind} entitlement {key!r}")
    for key in forbidden:
        if key in signed_entitlements or key in profile_entitlements:
            fail(f"{args.kind} must never receive provider Keychain entitlement {key!r}")

    # Capability-bearing signed values must be authorized by the matching
    # profile value. This keeps a valid profile for another capability or team
    # from becoming a generic signing escape hatch.
    protected_prefixes = ("com.apple.developer.", "keychain-access-groups")
    protected_exact = {"com.apple.security.application-groups"}
    for key, value in signed_entitlements.items():
        if key.startswith(protected_prefixes) or key in protected_exact:
            if profile_entitlements.get(key) != value:
                fail(f"profile does not authorize signed entitlement {key!r}")

    print(f"Verified {args.kind} Apple profile, signed certificate, and exact entitlement contract.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
