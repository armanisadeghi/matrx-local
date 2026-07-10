#!/usr/bin/env python3
"""Private Media Vault recovery backdoor.

Unwraps the vault master key (VMK) from the MANDATORY escrow slot in
vault.json using the Matrx escrow RSA-4096 PRIVATE key, then either:

  --out <dir>            decrypt every blob (file + sidecar JSON) into <dir>
  --new-password <pw>    rewrap a fresh user slot so the user regains access
                         with a new password — contents never exposed

Both may be given. The private key must NEVER enter the repo or a user
machine; this script is run by Matrx support with the key supplied at
runtime.

Usage:
    uv run python scripts/vault-recover.py \
        --private-key ~/.matrx-escrow/matrx-media-escrow-private.pem \
        --vault-dir ~/.matrx/media/vault \
        [--out /tmp/recovered] [--new-password 'NewSecret123'] \
        [--key-password 'pem passphrase']

Uses the exact same code paths as the live service:
app/services/media_vault/crypto.py + escrow.py (both app-import-free).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import NoReturn

# Make `app.*` importable when run from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.services.media_vault import crypto  # noqa: E402
from app.services.media_vault.escrow import unwrap_escrow_slot  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def fail(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def recover_vmk(vault_dir: Path, private_key_pem: bytes, key_password: bytes | None) -> bytes:
    config = crypto.read_vault_config(vault_dir)
    escrow_slot = config.get("escrow_slot")
    if not isinstance(escrow_slot, dict):
        fail("vault.json has no escrow slot — this vault is unrecoverable by escrow")
    vmk = unwrap_escrow_slot(escrow_slot, private_key_pem, key_password)
    if len(vmk) != crypto.KEY_BYTES:
        fail(f"Unwrapped VMK has wrong length: {len(vmk)} bytes")
    return vmk


def decrypt_all(vault_dir: Path, vmk: bytes, out_dir: Path) -> None:
    blobs_dir = vault_dir / "blobs"
    if not blobs_dir.is_dir():
        print("No blobs/ directory — vault is empty.")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    for meta_path in sorted(blobs_dir.glob("*.meta.bin")):
        item_id = meta_path.name.removesuffix(".meta.bin")
        try:
            envelope = json.loads(
                crypto.decrypt_blob(vmk, meta_path.read_bytes(), crypto.meta_aad(item_id))
            )
            blob_path = blobs_dir / f"{item_id}.bin"
            data = crypto.decrypt_blob(vmk, blob_path.read_bytes(), crypto.file_aad(item_id))

            file_name = str(envelope.get("file_name") or f"{item_id}.bin")
            if "/" in file_name or "\\" in file_name or file_name.startswith("."):
                file_name = f"{item_id}.bin"
            (out_dir / file_name).write_bytes(data)
            (out_dir / f"{item_id}.json").write_text(
                json.dumps(envelope, indent=2), encoding="utf-8"
            )
            print(f"  recovered {item_id} -> {file_name} ({len(data)} bytes)")
            ok += 1
        except Exception as exc:  # noqa: BLE001 — keep recovering the rest
            print(f"  FAILED {item_id}: {exc}", file=sys.stderr)
            failed += 1
    print(f"Decrypted {ok} item(s) to {out_dir}" + (f"; {failed} FAILED" if failed else ""))
    if failed:
        raise SystemExit(2)


def rewrap_user_slot(vault_dir: Path, vmk: bytes, new_password: str) -> None:
    if len(new_password) < MIN_PASSWORD_LENGTH:
        fail(f"--new-password must be at least {MIN_PASSWORD_LENGTH} characters")
    config = crypto.read_vault_config(vault_dir)
    kdf, user_slot = crypto.make_user_slot(new_password, vmk)
    config["kdf"] = kdf
    config["user_slot"] = user_slot
    # Escrow slot untouched. Sanity-check the rewrap before committing.
    assert crypto.unwrap_user_slot(new_password, kdf, user_slot) == vmk
    crypto.write_vault_config(vault_dir, config)
    print(f"User slot rewrapped — the vault at {vault_dir} now opens with the new password.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--private-key", required=True, type=Path,
                        help="Path to the Matrx escrow RSA private key PEM")
    parser.add_argument("--vault-dir", required=True, type=Path,
                        help="Vault directory containing vault.json + blobs/")
    parser.add_argument("--out", type=Path,
                        help="Decrypt all vault items into this directory")
    parser.add_argument("--new-password",
                        help="Rewrap a fresh user slot with this password "
                             "(restores access without exposing contents)")
    parser.add_argument("--key-password",
                        help="Passphrase of the private key PEM, if encrypted "
                             "(prompted interactively if the PEM needs one)")
    args = parser.parse_args()

    if not args.out and not args.new_password:
        parser.error("Nothing to do: pass --out and/or --new-password")

    vault_dir = args.vault_dir.expanduser()
    if not (vault_dir / "vault.json").exists():
        fail(f"No vault.json in {vault_dir}")

    private_key_pem = args.private_key.expanduser().read_bytes()
    key_password = args.key_password.encode() if args.key_password else None
    if key_password is None and b"ENCRYPTED" in private_key_pem:
        key_password = getpass.getpass("Private key passphrase: ").encode()

    vmk = recover_vmk(vault_dir, private_key_pem, key_password)
    print("Escrow slot unwrapped — VMK recovered.")

    if args.out:
        decrypt_all(vault_dir, vmk, args.out.expanduser())
    if args.new_password:
        rewrap_user_slot(vault_dir, vmk, args.new_password)


if __name__ == "__main__":
    main()
