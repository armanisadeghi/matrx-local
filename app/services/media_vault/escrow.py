"""Matrx escrow slot for the Private Media Vault.

Every vault created on any machine gets a SECOND wrap of its master key
(VMK): RSA-4096-OAEP(SHA-256) under the Matrx escrow PUBLIC key below. This
is the recovery backdoor — with the corresponding PRIVATE key (which must
NEVER enter this repo or any user machine), ``scripts/vault-recover.py`` can
unwrap the VMK and either decrypt the blobs or mint a fresh user slot for a
forgotten password.

The public key is safe to embed and commit: it can only ENCRYPT. Its source
of truth lives outside the repo at
``~/.matrx-escrow/matrx-media-escrow-public.pem``.

NO app-internal imports here — scripts/vault-recover.py imports this module
standalone.
"""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.media_vault.crypto import b64d, b64e

ESCROW_ALGORITHM = "RSA-4096-OAEP-SHA256"

# Matrx media-escrow PUBLIC key (RSA-4096). Embedded verbatim from
# ~/.matrx-escrow/matrx-media-escrow-public.pem.
ESCROW_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAt9udaKmeVyzgson3KD5y
uVTGUTBsZcqKoK0rNQCkl2Z8XtpgOB4zidT1FYoaOz8WalJ/+tc0DPU2EpbS7T9M
8pPrNxCIGYoknXC2bscOv0xMwRSTXkd3GIANZzB3uPjeh1ZR6vT9AsduA6rHW+S1
PDxVWDRbl+r8wkQMnDR3tKsLNmrpQyZfh5KFUb91LbDVEJNm6s8rKFYc0ii+RMbr
cPqDQW9ltqkobInNk9VfVyUFdPAA2veFtR264eB7tZC1+ntXlsmD6AwMZ1FTmQiu
LbAYJXYoGAqnhIT7lNQuX/tjFrmq1ZhjL3cj6hE45wn9WOcFcZHhbpTyzTLANOqz
1Y6CvX1DtNj2PM6bsfxxSjKJ4P+0Wo98YPa2T6zpCmKKVLM0q9BNVKSEGnmTsLfW
IkzmoxEeMgpyheI+bjIHXk/citUlkpQ7adVr4VFxRZUkFDVnTnDfztEX6q7+Zs4R
1nf3iv+mhG+rXGMFo8m94u/WoFilYQUHXyBfuPXy/me1CGiQa6KK+IG+gsjwhueO
vVtKQXihVg9nZ9Ymw5M/Xn6DWaUHE5gB26GUSNGFgHThgxn6I2w0D+Dnh3PgH3gZ
YTdC6CsttIt1+ARxsjR34tEVjR+g61whpFXoratHy+UBiMXhLHd2Wr9B7VNGvpie
tkng3Gnlix4pwohjYwGIr20CAwEAAQ==
-----END PUBLIC KEY-----
"""


class EscrowError(Exception):
    """Raised when the escrow slot cannot be produced or consumed. Vault
    creation treats this as fatal — a vault without an escrow slot must be
    impossible."""


_OAEP = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


def make_escrow_slot(vmk: bytes, public_key_pem: str | None = None) -> dict[str, Any]:
    """Wrap the VMK under the escrow public key. Raises EscrowError loudly on
    ANY failure — callers must abort vault creation, never skip the slot.

    ``public_key_pem`` defaults to the module-level constant, resolved at call
    time (so tests can monkeypatch ``ESCROW_PUBLIC_KEY_PEM`` with a throwaway
    keypair)."""
    pem = public_key_pem if public_key_pem is not None else ESCROW_PUBLIC_KEY_PEM
    try:
        public_key = serialization.load_pem_public_key(pem.encode("ascii"))
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise EscrowError("Escrow public key is not an RSA key")
        if public_key.key_size < 4096:
            raise EscrowError(
                f"Escrow key too small: {public_key.key_size} bits (need 4096)"
            )
        wrapped = public_key.encrypt(vmk, _OAEP)
    except EscrowError:
        raise
    except Exception as exc:
        raise EscrowError(f"Failed to build escrow slot: {exc}") from exc
    return {"algorithm": ESCROW_ALGORITHM, "wrapped_vmk": b64e(wrapped)}


def unwrap_escrow_slot(
    slot: dict[str, Any],
    private_key_pem: bytes,
    private_key_password: bytes | None = None,
) -> bytes:
    """Recover the VMK from the escrow slot with the RSA PRIVATE key.

    Used ONLY by scripts/vault-recover.py (and its tests, with throwaway
    keypairs). The private key never ships with the app.
    """
    algorithm = slot.get("algorithm")
    if algorithm != ESCROW_ALGORITHM:
        raise EscrowError(f"Unknown escrow algorithm: {algorithm!r}")
    try:
        private_key = serialization.load_pem_private_key(
            private_key_pem, password=private_key_password
        )
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise EscrowError("Escrow private key is not an RSA key")
        return private_key.decrypt(b64d(slot["wrapped_vmk"]), _OAEP)
    except EscrowError:
        raise
    except Exception as exc:
        raise EscrowError(f"Failed to unwrap escrow slot: {exc}") from exc
