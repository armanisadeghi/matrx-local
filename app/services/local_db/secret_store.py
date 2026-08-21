"""Encryption-at-rest for credentials stored in the local SQLite DB.

The local DB (``~/.matrx/matrx.db``) holds the user's Supabase access/refresh
tokens and provider API keys. File permissions (0600) are the first line of
defence; this module adds encryption at rest so a stolen DB file (backup,
forensic copy, cloud-sync of the home dir) does not directly yield live
credentials.

Design — keychain-backed data-encryption key:
  * A single random Fernet data-encryption key (DEK) is generated once and
    stored in the OS keychain (macOS Keychain, Windows Credential Manager,
    Linux Secret Service) via ``keyring``. The DB never contains the DEK.
  * Secrets are Fernet-encrypted with the DEK and stored as ``enc:v1:<ct>``.

Fail-safe contract (this is load-bearing — it sits on the auth path):
  * If ``keyring`` or ``cryptography`` is unavailable, the keychain cannot be
    reached, or encryption fails, :func:`protect` refuses the write loudly.
  * :func:`unprotect` transparently handles all three historical formats —
    ``enc:v1:`` (decrypt), and bare plaintext (return as-is) — so there is no
    migration step and an unreadable keychain never bricks an existing login
    (worst case the user re-authenticates).
"""

from __future__ import annotations

import sys
import threading

from app.common.keychain_helper import (
    KEYRING_SERVICE as _KEYRING_SERVICE,
    KEYRING_USERNAME as _KEYRING_USERNAME,
    read_key_from_helper,
)
from app.common.system_logger import get_logger

logger = get_logger()

_ENC_PREFIX = "enc:v1:"
# Cache the resolved Fernet instance and unavailable state
# so we hit the keychain at most once per process.
_fernet: object | None = None
_fernet_unavailable = False
_fernet_lock = threading.Lock()


class SecretEncryptionUnavailableError(RuntimeError):
    """Raised when a requested credential cannot be encrypted for storage."""


def encryption_backend_or_raise():
    """Return keychain Fernet or refuse to substitute plaintext for encryption.

    The old resolver returned ``None`` when keychain access failed, causing
    callers to persist plaintext/base64 while reporting a successful encrypted
    write. Plaintext is not equivalent encryption; restore keychain access or
    leave the credential unpersisted and re-authenticate.
    """
    global _fernet, _fernet_unavailable
    if _fernet is not None:
        return _fernet
    if _fernet_unavailable:
        raise SecretEncryptionUnavailableError(
            "Credential encryption was requested, but the OS keychain-backed "
            "Fernet backend is unavailable. Unlock/repair the OS keychain and "
            "install cryptography plus keyring (non-macOS), then retry. The honest "
            "alternative is to leave the credential unpersisted and re-authenticate; "
            "plaintext storage is refused."
        )

    with _fernet_lock:
        if _fernet is not None:
            return _fernet
        if _fernet_unavailable:
            return encryption_backend_or_raise()

        try:
            from cryptography.fernet import Fernet

            if sys.platform == "darwin":
                key = read_key_from_helper()
            else:
                import keyring

                key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
                if not key:
                    key = Fernet.generate_key().decode("ascii")
                    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key)
                    logger.info(
                        "[secret_store] generated new DB encryption key in OS keychain"
                    )
            _fernet = Fernet(key.encode("ascii"))
            return _fernet
        except Exception as exc:
            _fernet_unavailable = True
            raise SecretEncryptionUnavailableError(
                "Credential encryption was requested, but the OS keychain-backed "
                f"Fernet backend could not be loaded ({exc}). Unlock/repair the OS "
                "keychain and install cryptography plus keyring (non-macOS), then "
                "retry. The honest alternative is to leave the credential "
                "unpersisted and re-authenticate; plaintext storage is refused."
            ) from exc


def protect(plaintext: str | None) -> str | None:
    """Encrypt a secret for storage; refuse the write when encryption is absent."""
    if not plaintext:
        return plaintext
    f = encryption_backend_or_raise()
    try:
        token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return _ENC_PREFIX + token
    except Exception as exc:
        raise SecretEncryptionUnavailableError(
            "Credential encryption was requested, but Fernet encryption failed. "
            "Repair the OS keychain encryption key and retry, or leave the "
            "credential unpersisted and re-authenticate; plaintext storage is refused."
        ) from exc


def unprotect(stored: str | None) -> str | None:
    """Decrypt a stored secret. Handles legacy plaintext transparently and
    never raises — an undecryptable value returns None so the caller treats
    the session as absent (re-auth) rather than crashing."""
    if not stored:
        return stored
    if not stored.startswith(_ENC_PREFIX):
        # Legacy / fallback plaintext value.
        return stored
    try:
        f = encryption_backend_or_raise()
    except SecretEncryptionUnavailableError:
        logger.warning(
            "[secret_store] have an encrypted secret but no key to "
            "decrypt it (keychain unavailable) — treating as absent"
        )
        return None
    try:
        ct = stored[len(_ENC_PREFIX) :]
        return f.decrypt(ct.encode("ascii")).decode("utf-8")
    except Exception:
        logger.warning(
            "[secret_store] failed to decrypt stored secret — "
            "treating as absent (re-auth required)"
        )
        return None
