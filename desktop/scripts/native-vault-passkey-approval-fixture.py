"""Test-only fixture (admin@admin.com, never a real person): record ONE passkey
approval for a fill-device key thumbprint, exactly the row
``fill_devices.approve_fill_device_with_passkey`` writes after the auth server
verified a WebAuthn assertion. A live WebAuthn ceremony needs a person's
authenticator, so the live harness stands in for that one step and says so; the
claim path it then exercises (Mac registers without a password, server claims
the approval once) is the real one. Run with cwd = aidream.
Usage: native-vault-passkey-approval-fixture.py <user_id> <key_thumbprint>"""
import asyncio, sys, time, uuid
from datetime import UTC, datetime


async def main() -> None:
    from aidream.package_integration import configure_packages

    configure_packages()
    from aidream.services.user_secrets.fill_devices import PASSKEY_APPROVAL_TTL_SECONDS, VaultFillApproval

    user_id, thumbprint = sys.argv[1], sys.argv[2].lower()
    assert len(thumbprint) == 64 and set(thumbprint) <= set("0123456789abcdef"), "thumbprint"
    await VaultFillApproval.create(
        user_id=user_id,
        key_thumbprint=thumbprint,
        method="passkey",
        proof_session_id=str(uuid.uuid4()),
        label="live harness stand-in for a verified passkey assertion",
        expires_at=datetime.fromtimestamp(time.time() + PASSKEY_APPROVAL_TTL_SECONDS, UTC),
    )
    print("approval recorded")


asyncio.run(main())
