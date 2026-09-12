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
import time

from app.common.keychain_helper import (
    KEYRING_SERVICE as _KEYRING_SERVICE,
    KEYRING_USERNAME as _KEYRING_USERNAME,
    read_key_from_helper,
)
from app.common.system_logger import get_logger

logger = get_logger()

_ENC_PREFIX = "enc:v1:"
# Cache the resolved Fernet instance. A FAILED keychain read is remembered
# only for a bounded window and then retried: on 2026-09-12 one slow helper
# answer at engine start (the helper competes with the startup index warm-up
# for disk) was cached as "unavailable" for the life of the process, every
# token save 500'd for hours, and nothing ever logged the actual cause.
_fernet: object | None = None
_fernet_unavailable_until: float = 0.0
_fernet_failures = 0
_fernet_last_cause: str | None = None
_fernet_lock = threading.Lock()
_RETRY_BASE_SECONDS = 30.0
_RETRY_MAX_SECONDS = 300.0


class SecretEncryptionUnavailableError(RuntimeError):
    """Raised when a requested credential cannot be encrypted for storage."""


def encryption_backend_or_raise():
    """Return keychain Fernet or refuse to substitute plaintext for encryption.

    The old resolver returned ``None`` when keychain access failed, causing
    callers to persist plaintext/base64 while reporting a successful encrypted
    write. Plaintext is not equivalent encryption; restore keychain access or
    leave the credential unpersisted and re-authenticate.
    """
    global _fernet, _fernet_unavailable_until, _fernet_failures, _fernet_last_cause
    if _fernet is not None:
        return _fernet
    if time.monotonic() < _fernet_unavailable_until:
        raise SecretEncryptionUnavailableError(
            "Credential encryption was requested, but the OS keychain-backed "
            f"Fernet backend is unavailable (last cause: {_fernet_last_cause}; "
            f"retrying in {max(0.0, _fernet_unavailable_until - time.monotonic()):.0f}s). "
            "Unlock/repair the OS keychain and install cryptography plus keyring "
            "(non-macOS), then retry. The honest alternative is to leave the "
            "credential unpersisted and re-authenticate; plaintext storage is refused."
        )

    with _fernet_lock:
        if _fernet is not None:
            return _fernet
        if time.monotonic() < _fernet_unavailable_until:
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
            if _fernet_failures:
                logger.info(
                    "[secret_store] OS keychain is back after %s failed attempt(s)",
                    _fernet_failures,
                )
            _fernet_failures = 0
            _fernet_last_cause = None
            return _fernet
        except Exception as exc:
            _fernet_failures += 1
            _fernet_last_cause = f"{type(exc).__name__}: {exc}"[:300]
            backoff = min(
                _RETRY_MAX_SECONDS, _RETRY_BASE_SECONDS * (2 ** (_fernet_failures - 1))
            )
            _fernet_unavailable_until = time.monotonic() + backoff
            # Every caller used to swallow the cause. It is the ONE fact anyone
            # debugging this needs, so it is logged here, once per attempt.
            logger.warning(
                "[secret_store] OS keychain DEK unavailable (attempt %s, retry in %.0fs): %s",
                _fernet_failures,
                backoff,
                _fernet_last_cause,
            )
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
