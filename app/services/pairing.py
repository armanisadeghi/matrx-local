"""Extension pairing token — the desktop side of the bridge pairing flow.

The matrx-extend Chrome extension refuses to send the user's Supabase JWT
to a probed localhost port (its audit P1-5: any local process could bind a
port in 22140-22159, mimic /health, and harvest the JWT). Instead it
authenticates `/extension/*` with an ENGINE-ISSUED pairing token.

This module owns that token:

  * One persistent random secret per engine home (`~/.matrx/pairing.json`;
    dev engines get their own under `~/.matrx-dev` via MATRX_HOME_DIR).
  * Minted lazily on first use, stable across restarts, chmod 600.
  * Handed out ONLY over direct loopback (`POST /extension/pair` — the
    route rejects tunnel-originated requests), so remote callers can never
    fetch it; remote pairing is a manual copy from the desktop UI.
  * Accepted as a bearer on `/extension/*` by `extension_auth` (constant-
    time compare). A paired caller is by definition the owner's own
    device, so it is accepted over the tunnel as well.

Security posture: on loopback this grants nothing new — the engine already
accepts any well-formed bearer there (the socket is the boundary). The
token's job is (1) letting the extension authenticate WITHOUT ever
transmitting a user credential, and (2) providing a real secret for the
tunnel path.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from typing import Optional

from app.common.system_logger import get_logger
from app.config import MATRX_HOME_DIR

logger = get_logger()

_PAIRING_FILE = MATRX_HOME_DIR / "pairing.json"
_TOKEN_PREFIX = "mxl_pair_"
_HMAC_PREFIX = "mxl_hmac_"

_lock = threading.Lock()
_cached_token: Optional[str] = None


def _read_from_disk() -> Optional[str]:
    try:
        raw = json.loads(_PAIRING_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        # Corrupt file: scream and re-mint rather than silently locking the
        # extension out forever.
        logger.warning(
            "[pairing] %s unreadable (%s: %s) — a new pairing token will be "
            "minted; previously paired extensions will re-pair on their "
            "next 401.",
            _PAIRING_FILE,
            type(exc).__name__,
            exc,
        )
        return None
    token = raw.get("pair_token") if isinstance(raw, dict) else None
    if isinstance(token, str) and token.startswith(_TOKEN_PREFIX):
        return token
    logger.warning(
        "[pairing] %s exists but holds no valid pair_token — re-minting.",
        _PAIRING_FILE,
    )
    return None


def _read_hmac_key_from_disk() -> Optional[str]:
    try:
        raw = json.loads(_PAIRING_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    key = raw.get("provenance_hmac_key") if isinstance(raw, dict) else None
    return key if isinstance(key, str) and key.startswith(_HMAC_PREFIX) else None


def _write_to_disk(token: str) -> None:
    provenance_key = _read_hmac_key_from_disk()
    payload: dict[str, object] = {"pair_token": token, "schema": 2}
    if provenance_key is not None:
        payload["provenance_hmac_key"] = provenance_key
    _PAIRING_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PAIRING_FILE.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        _PAIRING_FILE.chmod(0o600)
    except OSError:
        # Windows / exotic filesystems — permissions are best-effort there.
        pass


def get_or_create_pair_token() -> str:
    """Return the engine's pairing token, minting + persisting on first use."""
    global _cached_token
    with _lock:
        if _cached_token is not None:
            return _cached_token
        token = _read_from_disk()
        if token is None:
            token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
            _write_to_disk(token)
            logger.info(
                "[pairing] minted new extension pairing token (stored at %s)",
                _PAIRING_FILE,
            )
        _cached_token = token
        return token


def get_or_create_installation_hmac_key() -> str:
    """Return a stable random HMAC key without exposing provider identifiers."""
    global _cached_token
    with _lock:
        key = _read_hmac_key_from_disk()
        if key is not None:
            return key
        token = _cached_token or _read_from_disk()
        if token is None:
            token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
            _cached_token = token
        key = _HMAC_PREFIX + secrets.token_urlsafe(32)
        _PAIRING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PAIRING_FILE.write_text(
            json.dumps(
                {
                    "pair_token": token,
                    "provenance_hmac_key": key,
                    "schema": 2,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            _PAIRING_FILE.chmod(0o600)
        except OSError:
            pass
        logger.info("[pairing] minted stable local provenance HMAC key")
        return key


def rotate_pair_token() -> str:
    """Mint a fresh token, invalidating every previously paired extension."""
    global _cached_token
    with _lock:
        token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        _write_to_disk(token)
        _cached_token = token
        logger.info("[pairing] pairing token rotated — all extensions must re-pair")
        return token


def matches_pair_token(candidate: str) -> bool:
    """Constant-time check of a presented bearer against the pairing token.

    Cheap prefix gate first so ordinary Supabase JWTs skip the disk-backed
    lookup entirely.
    """
    if not candidate.startswith(_TOKEN_PREFIX):
        return False
    return hmac.compare_digest(candidate, get_or_create_pair_token())
