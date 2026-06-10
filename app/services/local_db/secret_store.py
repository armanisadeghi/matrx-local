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
  * If ``keyring`` or ``cryptography`` is unavailable, the keychain can't be
    reached, or anything else goes wrong, :func:`protect` returns the value
    UNCHANGED (plaintext) after a one-time warning. We never refuse to store
    a token — that would lock the user out of their own session.
  * :func:`unprotect` transparently handles all three historical formats —
    ``enc:v1:`` (decrypt), and bare plaintext (return as-is) — so there is no
    migration step and an unreadable keychain never bricks an existing login
    (worst case the user re-authenticates).
"""

from __future__ import annotations

from app.common.system_logger import get_logger

logger = get_logger()

_ENC_PREFIX = "enc:v1:"
_KEYRING_SERVICE = "matrx-local"
_KEYRING_USERNAME = "db-encryption-key"

# Cache the resolved Fernet instance (or False once we know it's unavailable)
# so we hit the keychain at most once per process.
_fernet: object | None = None
_fernet_unavailable = False
_warned_once = False


def _warn_once(msg: str) -> None:
    global _warned_once
    if not _warned_once:
        _warned_once = True
        logger.warning("[secret_store] %s — secrets stored UNENCRYPTED at rest "
                       "(file permissions still apply).", msg)


def _get_fernet():
    """Return a Fernet instance backed by a keychain-stored DEK, or None.

    Never raises. On any failure returns None and the caller stores plaintext.
    """
    global _fernet, _fernet_unavailable
    if _fernet is not None:
        return _fernet
    if _fernet_unavailable:
        return None

    try:
        import keyring
        from cryptography.fernet import Fernet
    except Exception as exc:  # ImportError or backend import failure
        _fernet_unavailable = True
        _warn_once(f"keychain/crypto libraries unavailable ({exc})")
        return None

    try:
        key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        if not key:
            key = Fernet.generate_key().decode("ascii")
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, key)
            logger.info("[secret_store] generated new DB encryption key in OS keychain")
        _fernet = Fernet(key.encode("ascii"))
        return _fernet
    except Exception as exc:
        # Keychain locked, no backend (headless Linux without Secret Service),
        # corrupt key, etc. Fall back to plaintext rather than blocking auth.
        _fernet_unavailable = True
        _warn_once(f"OS keychain unavailable ({exc})")
        return None


def protect(plaintext: str | None) -> str | None:
    """Encrypt a secret for storage. Returns plaintext unchanged on any
    failure (never raises, never blocks a write)."""
    if not plaintext:
        return plaintext
    f = _get_fernet()
    if f is None:
        return plaintext
    try:
        token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return _ENC_PREFIX + token
    except Exception:
        logger.debug("[secret_store] encrypt failed; storing plaintext", exc_info=True)
        return plaintext


def unprotect(stored: str | None) -> str | None:
    """Decrypt a stored secret. Handles legacy plaintext transparently and
    never raises — an undecryptable value returns None so the caller treats
    the session as absent (re-auth) rather than crashing."""
    if not stored:
        return stored
    if not stored.startswith(_ENC_PREFIX):
        # Legacy / fallback plaintext value.
        return stored
    f = _get_fernet()
    if f is None:
        logger.warning("[secret_store] have an encrypted secret but no key to "
                       "decrypt it (keychain unavailable) — treating as absent")
        return None
    try:
        ct = stored[len(_ENC_PREFIX):]
        return f.decrypt(ct.encode("ascii")).decode("utf-8")
    except Exception:
        logger.warning("[secret_store] failed to decrypt stored secret — "
                       "treating as absent (re-auth required)")
        return None
