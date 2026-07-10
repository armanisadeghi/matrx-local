"""Private Media Vault service — iPhone-hidden-folder-style encrypted store.

Singleton holding the unlocked vault master key (VMK) ONLY in memory; nothing
key-shaped ever touches disk unwrapped. Auto-locks after 15 minutes of
inactivity (lazy check on every operation — no background thread; every
successful vault op resets the timer).

Layout (``~/.matrx/media/vault/``):

    vault.json                 — version, KDF params, user + escrow wrapped
                                 slots, created_at (atomic write + .bak)
    blobs/<item_id>.bin        — nonce || AES-256-GCM(file bytes)
    blobs/<item_id>.meta.bin   — nonce || AES-256-GCM(sidecar JSON)

Filenames are opaque uuid4 ids — nothing readable from the filesystem.

The move/restore discipline is verify-before-delete: the newly-written copy
is fully round-tripped (decrypted / re-read and SHA-256-compared against the
source) BEFORE the source is deleted. A failed item reports a per-item error
and leaves its source untouched — data loss is the one unforgivable failure.

Extensibility: the vaulted sidecar is wrapped in an envelope carrying
``content_type`` / ``file_name`` / ``sha256`` explicitly, so any future file
type can enter the vault without a media-library sidecar.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.config import MATRX_HOME_DIR
from app.services.media_gen import library
from app.services.media_vault import crypto, escrow
from app.services.media_vault.crypto import (  # noqa: F401 — re-export for routes
    WrongPasswordError,
)

logger = get_logger()

AUTO_LOCK_SECONDS = 15 * 60
MIN_PASSWORD_LENGTH = 8

ENVELOPE_VERSION = 1


class VaultError(Exception):
    """Base class for vault operation failures."""


class VaultExistsError(VaultError):
    """create() called but a vault already exists."""


class VaultMissingError(VaultError):
    """Operation requires a vault that has not been created."""


class VaultLockedError(VaultError):
    """Operation requires the vault to be unlocked."""


def vault_dir() -> Path:
    """Root of the vault. Module-level function so tests can monkeypatch."""
    return MATRX_HOME_DIR / "media" / "vault"


def _blobs_dir() -> Path:
    return vault_dir() / "blobs"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_write(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


class MediaVaultService:
    """All operations are serialized under one lock — vault ops are quick
    (file-sized AES) and correctness beats parallelism here."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vmk: bytes | None = None
        self._last_activity: float = 0.0

    # ── internal state helpers (call with self._lock held) ──────────────────

    def _vault_exists(self) -> bool:
        return (vault_dir() / "vault.json").exists()

    def _autolock_if_stale(self) -> None:
        if self._vmk is not None and (
            time.monotonic() - self._last_activity > AUTO_LOCK_SECONDS
        ):
            logger.info("[media_vault] Auto-locking after inactivity")
            self._wipe_key()

    def _wipe_key(self) -> None:
        self._vmk = None
        self._last_activity = 0.0

    def _require_unlocked(self) -> bytes:
        self._autolock_if_stale()
        if not self._vault_exists():
            self._wipe_key()  # vault deleted out from under us — key is garbage
            raise VaultMissingError("No vault exists — create one first")
        if self._vmk is None:
            raise VaultLockedError("Vault is locked")
        self._last_activity = time.monotonic()
        return self._vmk

    # ── lifecycle ────────────────────────────────────────────────────────────

    def create(self, password: str) -> None:
        """Create the vault and unlock it. The escrow slot is MANDATORY —
        any escrow failure aborts creation before vault.json exists."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise VaultError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        with self._lock:
            if self._vault_exists():
                raise VaultExistsError("A vault already exists")
            vmk = os.urandom(crypto.KEY_BYTES)
            kdf, user_slot = crypto.make_user_slot(password, vmk)
            # Escrow BEFORE any disk write: if this raises, nothing exists.
            escrow_slot = escrow.make_escrow_slot(vmk)
            config = {
                "version": crypto.VAULT_FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "kdf": kdf,
                "user_slot": user_slot,
                "escrow_slot": escrow_slot,
            }
            crypto.write_vault_config(vault_dir(), config)
            _blobs_dir().mkdir(parents=True, exist_ok=True)
            self._vmk = vmk
            self._last_activity = time.monotonic()
            logger.info("[media_vault] Vault created (user + escrow slots written)")

    def unlock(self, password: str) -> None:
        with self._lock:
            if not self._vault_exists():
                raise VaultMissingError("No vault exists — create one first")
            config = crypto.read_vault_config(vault_dir())
            # Raises WrongPasswordError on a bad password (GCM tag check).
            vmk = crypto.unwrap_user_slot(password, config["kdf"], config["user_slot"])
            self._vmk = vmk
            self._last_activity = time.monotonic()
            logger.info("[media_vault] Vault unlocked")

    def lock(self) -> None:
        with self._lock:
            self._wipe_key()
            logger.info("[media_vault] Vault locked")

    def change_password(self, current_password: str, new_password: str) -> None:
        """Rewrap the user slot only; the escrow slot is untouched."""
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise VaultError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        with self._lock:
            if not self._vault_exists():
                raise VaultMissingError("No vault exists — create one first")
            config = crypto.read_vault_config(vault_dir())
            vmk = crypto.unwrap_user_slot(
                current_password, config["kdf"], config["user_slot"]
            )
            kdf, user_slot = crypto.make_user_slot(new_password, vmk)
            config["kdf"] = kdf
            config["user_slot"] = user_slot
            crypto.write_vault_config(vault_dir(), config)
            self._vmk = vmk
            self._last_activity = time.monotonic()
            logger.info("[media_vault] Password changed (user slot rewrapped)")

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._autolock_if_stale()
            exists = self._vault_exists()
            if not exists:
                self._wipe_key()
            unlocked = exists and self._vmk is not None
            item_count: int | None = None
            if unlocked:
                item_count = len(self._item_ids_on_disk())
            return {
                "exists": exists,
                "unlocked": unlocked,
                "item_count": item_count,
                "auto_lock_seconds": AUTO_LOCK_SECONDS,
            }

    # ── item access ──────────────────────────────────────────────────────────

    @staticmethod
    def _item_ids_on_disk() -> list[str]:
        """Blob ids present in blobs/ — ignores temp files and strays."""
        if not _blobs_dir().is_dir():
            return []
        return [
            p.name.removesuffix(".meta.bin")
            for p in _blobs_dir().glob("*.meta.bin")
            if library.is_valid_item_id(p.name.removesuffix(".meta.bin"))
        ]

    def _read_envelope(self, vmk: bytes, item_id: str) -> dict[str, Any]:
        meta_path = _blobs_dir() / f"{item_id}.meta.bin"
        if not meta_path.exists():
            raise VaultError(f"Unknown vault item: {item_id}")
        envelope = json.loads(
            crypto.decrypt_blob(vmk, meta_path.read_bytes(), crypto.meta_aad(item_id))
        )
        if not isinstance(envelope, dict):
            raise VaultError(f"Corrupt vault envelope for {item_id}")
        return envelope

    def list_items(self) -> list[dict[str, Any]]:
        """Decrypted metadata for every vaulted item, newest-vaulted first."""
        with self._lock:
            vmk = self._require_unlocked()
            items: list[dict[str, Any]] = []
            for item_id in self._item_ids_on_disk():
                try:
                    envelope = self._read_envelope(vmk, item_id)
                except Exception as exc:  # noqa: BLE001 — skip-and-scream
                    logger.error(
                        "[media_vault] Corrupt/unreadable item %s: %s", item_id, exc
                    )
                    continue
                items.append(self._envelope_to_listing(item_id, envelope))
            items.sort(key=lambda m: str(m.get("vaulted_at", "")), reverse=True)
            return items

    @staticmethod
    def _envelope_to_listing(item_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        meta = dict(envelope.get("library_meta") or {})
        meta.pop("file_path", None)  # plaintext path no longer exists
        meta["id"] = item_id
        meta["content_type"] = envelope.get("content_type")
        meta["file_name"] = envelope.get("file_name")
        meta["sha256"] = envelope.get("sha256")
        meta["vaulted_at"] = envelope.get("vaulted_at")
        return meta

    def read_file(self, item_id: str) -> tuple[bytes, str]:
        """Decrypted file bytes + content type. Plaintext never touches disk."""
        if not library.is_valid_item_id(item_id):
            raise VaultError(f"Invalid item id: {item_id}")
        with self._lock:
            vmk = self._require_unlocked()
            envelope = self._read_envelope(vmk, item_id)
            blob_path = _blobs_dir() / f"{item_id}.bin"
            if not blob_path.exists():
                raise VaultError(f"Vault blob missing for item: {item_id}")
            data = crypto.decrypt_blob(
                vmk, blob_path.read_bytes(), crypto.file_aad(item_id)
            )
            content_type = str(
                envelope.get("content_type") or "application/octet-stream"
            )
            return data, content_type

    def delete_item(self, item_id: str) -> bool:
        """Permanently delete a vaulted item (blob + encrypted sidecar)."""
        if not library.is_valid_item_id(item_id):
            return False
        with self._lock:
            self._require_unlocked()
            blob = _blobs_dir() / f"{item_id}.bin"
            meta = _blobs_dir() / f"{item_id}.meta.bin"
            if not blob.exists() and not meta.exists():
                return False
            blob.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            logger.info("[media_vault] Permanently deleted item %s", item_id)
            return True

    # ── move / restore ───────────────────────────────────────────────────────

    def move_from_library(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """Encrypt library items into the vault, verify, THEN delete originals.

        Per item: encrypt file + sidecar to temp paths, fsync, decrypt-verify
        the full round trip (SHA-256 vs the original bytes), rename into
        blobs/, and only then delete the library original. Any failure leaves
        the original untouched and is reported per-item.
        """
        with self._lock:
            vmk = self._require_unlocked()
            _blobs_dir().mkdir(parents=True, exist_ok=True)
            results = [self._move_one(vmk, item_id) for item_id in item_ids]
        for r in results:
            if not r["ok"]:
                logger.error(
                    "[media_vault] move failed for %s: %s", r["item_id"], r["error"]
                )
        return results

    def _move_one(self, vmk: bytes, item_id: str) -> dict[str, Any]:
        tmp_blob = tmp_meta = None
        try:
            if not library.is_valid_item_id(item_id):
                raise VaultError(f"Invalid item id: {item_id}")
            meta = library.get_item(item_id)
            if meta is None:
                raise VaultError(f"Unknown library item: {item_id}")
            src_path = Path(meta["file_path"])
            file_bytes = src_path.read_bytes()
            file_sha = _sha256(file_bytes)

            library_meta = {k: v for k, v in meta.items() if k != "file_path"}
            envelope = {
                "envelope_version": ENVELOPE_VERSION,
                "source": "media_library",
                "media_type": meta.get("media_type"),
                "content_type": library.content_type_for(str(meta["media_type"])),
                "file_name": meta.get("file_name"),
                "sha256": file_sha,
                "vaulted_at": datetime.now(timezone.utc).isoformat(),
                "library_meta": library_meta,
            }
            envelope_bytes = json.dumps(envelope).encode("utf-8")

            enc_file = crypto.encrypt_blob(vmk, file_bytes, crypto.file_aad(item_id))
            enc_meta = crypto.encrypt_blob(vmk, envelope_bytes, crypto.meta_aad(item_id))

            tmp_blob = _blobs_dir() / f".tmp-{uuid.uuid4()}.bin"
            tmp_meta = _blobs_dir() / f".tmp-{uuid.uuid4()}.meta.bin"
            _fsync_write(tmp_blob, enc_file)
            _fsync_write(tmp_meta, enc_meta)

            # DECRYPT-VERIFY the on-disk ciphertext — full round trip.
            round_trip = crypto.decrypt_blob(
                vmk, tmp_blob.read_bytes(), crypto.file_aad(item_id)
            )
            if _sha256(round_trip) != file_sha:
                raise VaultError("Round-trip verification failed (file bytes)")
            round_trip_meta = crypto.decrypt_blob(
                vmk, tmp_meta.read_bytes(), crypto.meta_aad(item_id)
            )
            if round_trip_meta != envelope_bytes:
                raise VaultError("Round-trip verification failed (sidecar)")

            os.replace(tmp_blob, _blobs_dir() / f"{item_id}.bin")
            os.replace(tmp_meta, _blobs_dir() / f"{item_id}.meta.bin")
            tmp_blob = tmp_meta = None

            # Only now is the plaintext original deleted.
            if not library.delete_item(item_id):
                logger.warning(
                    "[media_vault] Library item %s vanished during move — "
                    "vault copy is intact", item_id,
                )
            logger.info("[media_vault] Moved item %s into the vault", item_id)
            return {"item_id": item_id, "ok": True}
        except Exception as exc:  # noqa: BLE001 — per-item error report
            for tmp in (tmp_blob, tmp_meta):
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
            return {"item_id": item_id, "ok": False, "error": str(exc)}

    def restore_to_library(self, item_ids: list[str]) -> list[dict[str, Any]]:
        """Decrypt vault items back into the media library, verify the written
        plaintext, THEN delete the vault copies. Same discipline as move."""
        with self._lock:
            vmk = self._require_unlocked()
            results = [self._restore_one(vmk, item_id) for item_id in item_ids]
        for r in results:
            if not r["ok"]:
                logger.error(
                    "[media_vault] restore failed for %s: %s", r["item_id"], r["error"]
                )
        return results

    def _restore_one(self, vmk: bytes, item_id: str) -> dict[str, Any]:
        tmp_media = tmp_sidecar = None
        try:
            if not library.is_valid_item_id(item_id):
                raise VaultError(f"Invalid item id: {item_id}")
            envelope = self._read_envelope(vmk, item_id)
            if envelope.get("source") != "media_library":
                raise VaultError(
                    f"Item {item_id} did not come from the media library"
                )
            blob_path = _blobs_dir() / f"{item_id}.bin"
            file_bytes = crypto.decrypt_blob(
                vmk, blob_path.read_bytes(), crypto.file_aad(item_id)
            )
            expected_sha = str(envelope.get("sha256") or "")
            if expected_sha and _sha256(file_bytes) != expected_sha:
                raise VaultError("Decrypted bytes fail SHA-256 check — refusing")

            library_meta = envelope.get("library_meta")
            if not isinstance(library_meta, dict):
                raise VaultError(f"Corrupt vault envelope for {item_id}")
            media_type = str(envelope.get("media_type") or "")
            directory = library.media_type_dir(media_type)
            directory.mkdir(parents=True, exist_ok=True)
            file_name = str(envelope.get("file_name") or "")
            if not file_name or "/" in file_name or "\\" in file_name:
                raise VaultError(f"Unsafe file name in envelope: {file_name!r}")
            media_path = directory / file_name
            sidecar_path = directory / f"{item_id}.json"
            if media_path.exists() or sidecar_path.exists():
                raise VaultError(
                    f"Library already has files for {item_id} — refusing to overwrite"
                )

            sidecar_bytes = json.dumps(library_meta, indent=2).encode("utf-8")
            tmp_media = directory / f".vault-restore-{uuid.uuid4()}.tmp"
            tmp_sidecar = directory / f".vault-restore-{uuid.uuid4()}.json.tmp"
            _fsync_write(tmp_media, file_bytes)
            _fsync_write(tmp_sidecar, sidecar_bytes)

            # Verify the on-disk plaintext before touching the vault copies.
            if _sha256(tmp_media.read_bytes()) != _sha256(file_bytes):
                raise VaultError("Written media bytes fail verification")
            if tmp_sidecar.read_bytes() != sidecar_bytes:
                raise VaultError("Written sidecar fails verification")

            os.replace(tmp_media, media_path)
            os.replace(tmp_sidecar, sidecar_path)
            tmp_media = tmp_sidecar = None

            # Only now are the vault copies deleted.
            blob_path.unlink(missing_ok=True)
            (_blobs_dir() / f"{item_id}.meta.bin").unlink(missing_ok=True)
            logger.info("[media_vault] Restored item %s to the library", item_id)
            return {"item_id": item_id, "ok": True}
        except Exception as exc:  # noqa: BLE001 — per-item error report
            for tmp in (tmp_media, tmp_sidecar):
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
            return {"item_id": item_id, "ok": False, "error": str(exc)}


_service = MediaVaultService()


def get_vault_service() -> MediaVaultService:
    return _service
