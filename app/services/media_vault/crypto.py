"""Crypto + on-disk format primitives for the Private Media Vault.

NO app-internal imports here — scripts/vault-recover.py imports this module
directly so the recovery backdoor exercises the exact same code paths as the
live service (blob format, AAD binding, KDF, slot wrapping).

Design (see service.py for the operational contract):

- Per-vault random 256-bit master key (VMK).
- Files AND their metadata sidecars are encrypted separately with
  AES-256-GCM, per-file random 96-bit nonce, stored as ``nonce || ciphertext``
  (GCM tag appended by the AEAD implementation).
- Every blob is AAD-bound to its item id (file blob → ``<item_id>``, sidecar
  blob → ``<item_id>:meta``) so a blob renamed/swapped on disk fails
  authentication instead of decrypting under the wrong identity.
- The VMK is wrapped in two slots inside ``vault.json``:
    user slot   — scrypt(password, n=2**15, r=8, p=1, 32-byte salt) → AES-GCM
                  wrap. GCM tag validation IS the password verifier: a wrong
                  password raises ``InvalidTag`` → clean "wrong password".
    escrow slot — RSA-4096-OAEP(SHA-256) wrap under the Matrx escrow public
                  key (see escrow.py). Mandatory; written at creation.
- ``vault.json`` is written atomically (tmp + fsync + os.replace) and mirrored
  to ``vault.json.bak`` after every successful write.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

VAULT_FORMAT_VERSION = 1

# scrypt parameters — stored in vault.json so they can evolve without
# breaking existing vaults.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 32
KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # 96-bit GCM nonce


class VaultCryptoError(Exception):
    """Base class for vault crypto failures."""


class WrongPasswordError(VaultCryptoError):
    """The supplied password failed to unwrap the user slot."""


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


# ── AAD binding ───────────────────────────────────────────────────────────────

def file_aad(item_id: str) -> bytes:
    return item_id.encode("utf-8")


def meta_aad(item_id: str) -> bytes:
    return f"{item_id}:meta".encode("utf-8")


def init_image_aad(item_id: str) -> bytes:
    """AAD for the img2img SOURCE image stored beside a vaulted result.

    Distinct from file_aad so a result blob and an init-image blob can never be
    swapped for one another, even by an attacker who can move files around.
    """
    return f"{item_id}:init".encode("utf-8")


# ── blob encrypt/decrypt (nonce || ciphertext+tag) ────────────────────────────

def encrypt_blob(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt_blob(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < NONCE_BYTES + 16:
        raise VaultCryptoError("Blob too short to contain nonce + GCM tag")
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


# ── user slot (scrypt + AES-GCM wrap of the VMK) ──────────────────────────────

def _derive_user_key(password: str, kdf: dict[str, Any]) -> bytes:
    return Scrypt(
        salt=b64d(kdf["salt"]),
        length=int(kdf["length"]),
        n=int(kdf["n"]),
        r=int(kdf["r"]),
        p=int(kdf["p"]),
    ).derive(password.encode("utf-8"))


def make_user_slot(password: str, vmk: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Wrap the VMK under a password. Returns (kdf_params, user_slot)."""
    kdf: dict[str, Any] = {
        "algorithm": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "length": KEY_BYTES,
        "salt": b64e(os.urandom(SCRYPT_SALT_BYTES)),
    }
    key = _derive_user_key(password, kdf)
    nonce = os.urandom(NONCE_BYTES)
    wrapped = AESGCM(key).encrypt(nonce, vmk, b"matrx-vault-user-slot")
    slot = {"nonce": b64e(nonce), "wrapped_vmk": b64e(wrapped)}
    return kdf, slot


def unwrap_user_slot(password: str, kdf: dict[str, Any], slot: dict[str, Any]) -> bytes:
    """Unwrap the VMK with the password. GCM tag validation is the verifier —
    a wrong password raises :class:`WrongPasswordError`, nothing ambiguous."""
    key = _derive_user_key(password, kdf)
    try:
        return AESGCM(key).decrypt(
            b64d(slot["nonce"]), b64d(slot["wrapped_vmk"]), b"matrx-vault-user-slot"
        )
    except InvalidTag as exc:
        raise WrongPasswordError("Wrong vault password") from exc


# ── vault.json read/write ─────────────────────────────────────────────────────

def write_vault_config(vault_dir: Path, config: dict[str, Any]) -> None:
    """Atomic tmp+fsync+rename write of vault.json, then mirror to .bak.

    The .bak is only refreshed AFTER the primary write fully succeeded, so at
    every instant at least one intact copy of the wrapped slots exists.
    """
    vault_dir.mkdir(parents=True, exist_ok=True)
    target = vault_dir / "vault.json"
    tmp = vault_dir / "vault.json.tmp"
    payload = json.dumps(config, indent=2).encode("utf-8")
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    backup = vault_dir / "vault.json.bak"
    tmp_bak = vault_dir / "vault.json.bak.tmp"
    with open(tmp_bak, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_bak, backup)


def read_vault_config(vault_dir: Path) -> dict[str, Any]:
    config = json.loads((vault_dir / "vault.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or "user_slot" not in config:
        raise VaultCryptoError("vault.json is malformed (missing user_slot)")
    return config
