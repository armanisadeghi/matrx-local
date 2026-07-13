"""In-process tests for the Private Media Vault (/media-vault + service).

IMPORTANT: like test_media_gen_params_library.py these do NOT use the
spawned-engine ``http`` fixture — everything runs in-process against a
FastAPI TestClient (no lifespan → no services started, no engine touched).
Run with:

    uv run pytest tests/smoke/test_media_vault.py -v

Covers:
  - full lifecycle: create → move a fabricated library item → locked 423 →
    unlock → list/file → restore → bytes identical → change password
  - wrong-password unlock (403) and old-password-after-change (403)
  - escrow round-trip with a THROWAWAY RSA-4096 keypair (never the real
    private key) through the exact functions scripts/vault-recover.py uses
  - escrow failure makes vault creation fail loudly (no vault.json)
  - a failed move leaves the library original untouched
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.services.media_vault import crypto as vault_crypto
from app.services.media_vault import escrow as vault_escrow
from app.services.media_vault import service as vault_service_module
from app.services.media_vault.service import get_vault_service

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
PASSWORD = "correct horse battery"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app
    # No context manager on purpose: lifespan must NOT run (it would start
    # engine services). Plain requests still traverse the middleware stack.
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture()
def vault_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Throwaway library + vault dirs; vault locked before AND after."""
    from app.services.media_gen import library

    monkeypatch.setattr(library, "generated_media_dir", lambda: tmp_path / "generated")
    monkeypatch.setattr(
        vault_service_module, "vault_dir", lambda: tmp_path / "vault"
    )
    service = get_vault_service()
    service.lock()
    yield {"library": library, "tmp": tmp_path, "service": service}
    service.lock()


def fabricate_library_item(library) -> dict:
    return library.save_generated_image(
        FAKE_PNG,
        model_id="stabilityai/sdxl-turbo",
        prompt="a fabricated vault test image",
        negative_prompt="blurry",
        params={"num_inference_steps": 1, "width": 512, "height": 512},
        seed=42,
        width=512,
        height=512,
        elapsed_seconds=0.01,
    )


# ── full lifecycle through the HTTP contract ─────────────────────────────────

def test_vault_full_lifecycle(client: TestClient, vault_env) -> None:
    library = vault_env["library"]

    # No vault yet.
    st = client.get("/media-vault/status").json()
    assert st == {
        "exists": False, "unlocked": False, "item_count": None,
        "auto_lock_seconds": 15 * 60,
    }
    assert client.get("/media-vault/items").status_code == 404, "no vault → 404"
    assert client.post(
        "/media-vault/unlock", json={"password": PASSWORD}
    ).status_code == 404

    # Password policy: min 8 chars.
    r = client.post("/media-vault/create", json={"password": "short"})
    assert r.status_code == 400 and "8" in r.json()["detail"]

    # Create — unlocks on success, escrow slot present on disk.
    r = client.post("/media-vault/create", json={"password": PASSWORD})
    assert r.status_code == 200 and r.json() == {"created": True, "unlocked": True}
    config = json.loads((vault_env["tmp"] / "vault" / "vault.json").read_text())
    assert config["escrow_slot"]["algorithm"] == "RSA-4096-OAEP-SHA256"
    assert config["kdf"]["n"] == 2**15
    assert (vault_env["tmp"] / "vault" / "vault.json.bak").exists()

    # Second create → 409.
    assert client.post(
        "/media-vault/create", json={"password": PASSWORD}
    ).status_code == 409

    # Move a fabricated library item into the vault.
    item = fabricate_library_item(library)
    item_id = item["id"]
    r = client.post("/media-vault/move", json={"item_ids": [item_id]})
    assert r.status_code == 200
    assert r.json()["results"] == [{"item_id": item_id, "ok": True, "error": None}]

    # Original is GONE from the library; ciphertext blobs exist and are opaque.
    assert not Path(item["file_path"]).exists()
    blob = (vault_env["tmp"] / "vault" / "blobs" / f"{item_id}.bin").read_bytes()
    assert FAKE_PNG[:8] not in blob, "blob must be ciphertext, not plaintext"

    # /media-library/file/{id} is THE read path for a media id wherever it lives:
    # vaulting an item must not turn every historical reference to it (job
    # thumbnails, share links) into a 404 "Unknown media item" — that lie is what
    # produced an endless 404 storm against a vault that held the items all along.
    # Unlocked → the bytes. Locked → 423, never 404. Unknown id → 404.
    assert client.get(f"/media-library/file/{item_id}").status_code == 200
    assert client.get(f"/media-library/file/{item_id}").content == FAKE_PNG
    client.post("/media-vault/lock")
    assert client.get(f"/media-library/file/{item_id}").status_code == 423
    assert client.post("/media-vault/unlock", json={"password": PASSWORD}).status_code == 200
    assert client.get(f"/media-library/file/{uuid.uuid4()}").status_code == 404

    st = client.get("/media-vault/status").json()
    assert st["exists"] and st["unlocked"] and st["item_count"] == 1

    # Lock → every vault access is HTTP 423.
    assert client.post("/media-vault/lock").json() == {"locked": True}
    assert client.get("/media-vault/status").json()["item_count"] is None
    assert client.get("/media-vault/items").status_code == 423
    assert client.get(f"/media-vault/file/{item_id}").status_code == 423
    assert client.post(
        "/media-vault/move", json={"item_ids": [item_id]}
    ).status_code == 423
    assert client.post(
        "/media-vault/restore", json={"item_ids": [item_id]}
    ).status_code == 423
    assert client.delete(f"/media-vault/items/{item_id}").status_code == 423

    # Wrong password → 403; right password unlocks.
    assert client.post(
        "/media-vault/unlock", json={"password": "not-the-password"}
    ).status_code == 403
    assert client.post(
        "/media-vault/unlock", json={"password": PASSWORD}
    ).status_code == 200

    # List — decrypted metadata, no plaintext path.
    body = client.get("/media-vault/items").json()
    assert body["total"] == 1
    listed = body["items"][0]
    assert listed["id"] == item_id
    assert listed["prompt"] == "a fabricated vault test image"
    assert listed["content_type"] == "image/png"
    assert listed["sha256"] == hashlib.sha256(FAKE_PNG).hexdigest()
    assert "file_path" not in listed

    # File — exact bytes, correct content type, served from memory.
    rf = client.get(f"/media-vault/file/{item_id}")
    assert rf.status_code == 200
    assert rf.headers["content-type"].startswith("image/png")
    assert rf.content == FAKE_PNG

    # Restore — bytes + sidecar identical, vault copy gone.
    r = client.post("/media-vault/restore", json={"item_ids": [item_id]})
    assert r.json()["results"][0]["ok"] is True
    restored = Path(item["file_path"])
    assert restored.read_bytes() == FAKE_PNG
    sidecar = json.loads((restored.parent / f"{item_id}.json").read_text())
    assert sidecar["prompt"] == "a fabricated vault test image"
    assert sidecar["seed"] == 42
    assert client.get("/media-vault/items").json()["total"] == 0
    assert not (vault_env["tmp"] / "vault" / "blobs" / f"{item_id}.bin").exists()
    assert client.get(f"/media-library/file/{item_id}").status_code == 200

    # Restoring again → per-item error (nothing left in the vault).
    r = client.post("/media-vault/restore", json={"item_ids": [item_id]})
    result = r.json()["results"][0]
    assert result["ok"] is False and result["error"]

    # Change password: wrong current → 403; then old fails, new works.
    assert client.post(
        "/media-vault/change-password",
        json={"current_password": "wrong-current", "new_password": "another secret"},
    ).status_code == 403
    assert client.post(
        "/media-vault/change-password",
        json={"current_password": PASSWORD, "new_password": "another secret"},
    ).status_code == 200
    client.post("/media-vault/lock")
    assert client.post(
        "/media-vault/unlock", json={"password": PASSWORD}
    ).status_code == 403, "old password must be dead"
    assert client.post(
        "/media-vault/unlock", json={"password": "another secret"}
    ).status_code == 200


def test_vault_delete_item_permanent(client: TestClient, vault_env) -> None:
    library = vault_env["library"]
    client.post("/media-vault/create", json={"password": PASSWORD})
    item = fabricate_library_item(library)
    client.post("/media-vault/move", json={"item_ids": [item["id"]]})
    r = client.delete(f"/media-vault/items/{item['id']}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.get(f"/media-vault/file/{item['id']}").status_code == 404
    assert client.delete(f"/media-vault/items/{item['id']}").status_code == 404


# ── move failure leaves the original intact ──────────────────────────────────

def test_move_failure_leaves_original_untouched(
    client: TestClient, vault_env
) -> None:
    library = vault_env["library"]
    client.post("/media-vault/create", json={"password": PASSWORD})
    item = fabricate_library_item(library)

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated encryption failure")

    # Separate MonkeyPatch instance: undoing it must NOT undo the vault/library
    # dir redirects owned by the vault_env fixture.
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(vault_crypto, "encrypt_blob", boom)
        r = client.post("/media-vault/move", json={"item_ids": [item["id"]]})
    finally:
        mp.undo()
    result = r.json()["results"][0]
    assert result["ok"] is False and "simulated" in result["error"]

    # Original file + sidecar untouched; nothing landed in the vault.
    assert Path(item["file_path"]).read_bytes() == FAKE_PNG
    assert client.get(f"/media-library/file/{item['id']}").status_code == 200
    blobs = vault_env["tmp"] / "vault" / "blobs"
    assert list(blobs.glob(f"{item['id']}*")) == []
    assert list(blobs.glob(".tmp-*")) == [], "temp files must be cleaned up"

    # Unknown / invalid ids are per-item errors, not 500s.
    r = client.post(
        "/media-vault/move",
        json={"item_ids": ["00000000-0000-0000-0000-000000000000", "../evil"]},
    )
    results = r.json()["results"]
    assert all(res["ok"] is False for res in results)


# ── escrow: creation fails loudly without it ─────────────────────────────────

def test_create_fails_loudly_when_escrow_fails(
    client: TestClient, vault_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args, **_kwargs):
        raise vault_escrow.EscrowError("simulated escrow outage")

    monkeypatch.setattr(vault_escrow, "make_escrow_slot", boom)
    # service.py holds its own reference via `escrow.make_escrow_slot` lookup
    monkeypatch.setattr(
        vault_service_module.escrow, "make_escrow_slot", boom
    )
    r = client.post("/media-vault/create", json={"password": PASSWORD})
    # EscrowError is deliberately NOT a VaultError: it escapes to safe_route's
    # loud 500 instead of being softened into a client error.
    assert r.status_code == 500 and "escrow" in r.json()["detail"].lower()
    assert not (vault_env["tmp"] / "vault" / "vault.json").exists(), (
        "a vault without an escrow slot must be impossible"
    )
    assert client.get("/media-vault/status").json()["exists"] is False


# ── escrow round-trip via the vault-recover.py code path ─────────────────────

def _load_recover_module():
    spec = importlib.util.spec_from_file_location(
        "vault_recover", REPO_ROOT / "scripts" / "vault-recover.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def throwaway_keypair() -> tuple[bytes, str]:
    """(private_pem_bytes, public_pem_str) — NEVER the real escrow key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


def test_escrow_recovery_round_trip(
    client: TestClient,
    vault_env,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    throwaway_keypair,
) -> None:
    """Create a vault escrowed to a throwaway keypair, then recover it with
    scripts/vault-recover.py's own functions: decrypt-all AND rewrap."""
    private_pem, public_pem = throwaway_keypair
    monkeypatch.setattr(vault_escrow, "ESCROW_PUBLIC_KEY_PEM", public_pem)

    library = vault_env["library"]
    assert client.post(
        "/media-vault/create", json={"password": PASSWORD}
    ).status_code == 200
    item = fabricate_library_item(library)
    assert client.post(
        "/media-vault/move", json={"item_ids": [item["id"]]}
    ).json()["results"][0]["ok"] is True
    client.post("/media-vault/lock")

    recover = _load_recover_module()
    vdir = vault_env["tmp"] / "vault"

    # 1. Unwrap the VMK from the escrow slot (the backdoor itself).
    vmk = recover.recover_vmk(vdir, private_pem, None)
    assert isinstance(vmk, bytes) and len(vmk) == 32

    # 2. Decrypt-all: recovered bytes identical to the original.
    out_dir = tmp_path / "recovered"
    recover.decrypt_all(vdir, vmk, out_dir)
    assert (out_dir / item["file_name"]).read_bytes() == FAKE_PNG
    envelope = json.loads((out_dir / f"{item['id']}.json").read_text())
    assert envelope["library_meta"]["prompt"] == "a fabricated vault test image"

    # 3. Rewrap: forgotten password replaced without exposing contents.
    recover.rewrap_user_slot(vdir, vmk, "recovered secret 99")
    assert client.post(
        "/media-vault/unlock", json={"password": PASSWORD}
    ).status_code == 403, "old password must no longer work"
    assert client.post(
        "/media-vault/unlock", json={"password": "recovered secret 99"}
    ).status_code == 200
    rf = client.get(f"/media-vault/file/{item['id']}")
    assert rf.status_code == 200 and rf.content == FAKE_PNG

    # Escrow slot untouched by the rewrap — a second recovery still works.
    assert recover.recover_vmk(vdir, private_pem, None) == vmk


def test_wrong_private_key_cannot_unwrap(
    client: TestClient, vault_env, monkeypatch: pytest.MonkeyPatch, throwaway_keypair
) -> None:
    _, public_pem = throwaway_keypair
    monkeypatch.setattr(vault_escrow, "ESCROW_PUBLIC_KEY_PEM", public_pem)
    client.post("/media-vault/create", json={"password": PASSWORD})

    other = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    other_pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    config = vault_crypto.read_vault_config(vault_env["tmp"] / "vault")
    with pytest.raises(vault_escrow.EscrowError):
        vault_escrow.unwrap_escrow_slot(config["escrow_slot"], other_pem)


# ── unit: auto-lock timer ─────────────────────────────────────────────────────

def test_auto_lock_after_inactivity(vault_env, monkeypatch: pytest.MonkeyPatch) -> None:
    service = vault_env["service"]
    service.create(PASSWORD)
    assert service.status()["unlocked"] is True
    # Jump the clock past the auto-lock window.
    real_monotonic = vault_service_module.time.monotonic
    monkeypatch.setattr(
        vault_service_module.time, "monotonic",
        lambda: real_monotonic() + vault_service_module.AUTO_LOCK_SECONDS + 1,
    )
    assert service.status()["unlocked"] is False, "must lazily auto-lock"
    with pytest.raises(vault_service_module.VaultLockedError):
        service.list_items()


# ── the img2img source image survives the vault round trip ───────────────────

FAKE_INIT_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(255, -1, -1)) * 4

PASSWORD_INIT = "vault-init-image-pw"


def test_init_image_survives_vault_round_trip(client: TestClient, vault_env) -> None:
    """Vaulting an img2img image must NOT destroy the photo it was made from.

    The move deletes the plaintext original (result AND source image), so the
    source has to be encrypted into the vault with it. Before this, vaulting an
    img2img result silently and permanently destroyed the user's input image —
    and Remix, which promises to reproduce exactly what made the picture, could
    never be honest about it again.
    """
    library = vault_env["library"]
    item = library.save_generated_image(
        FAKE_PNG,
        model_id="stabilityai/sdxl-turbo",
        prompt="made from a photo",
        negative_prompt="",
        params={"has_init_image": True},
        seed=99,
        width=512,
        height=512,
        elapsed_seconds=0.01,
        init_image_bytes=FAKE_INIT_PNG,
    )
    item_id = item["id"]
    blobs = vault_env["tmp"] / "vault" / "blobs"

    assert client.post("/media-vault/create", json={"password": PASSWORD_INIT}).status_code == 200
    assert client.post("/media-vault/move", json={"item_ids": [item_id]}).json()[
        "results"
    ][0]["ok"] is True

    # The plaintext source image is gone from the library …
    init_plaintext = Path(item["file_path"]).parent / f"{item_id}.init.png"
    assert not init_plaintext.exists()
    # … and its ciphertext is in the vault.
    init_blob = blobs / f"{item_id}.init.bin"
    assert init_blob.exists()
    assert FAKE_INIT_PNG[:8] not in init_blob.read_bytes(), "must be ciphertext"

    # Unlocked: the source image is served (decrypted in memory) and the
    # listing advertises it, so the client offers a full Remix.
    r = client.get(f"/media-library/items/{item_id}/init-image")
    assert r.status_code == 200 and r.content == FAKE_INIT_PNG
    listed = client.get("/media-vault/items").json()["items"][0]
    assert listed["init_image_file"] == f"{item_id}.init.png"

    # Locked: 423 (one unlock away), never a lying 404.
    client.post("/media-vault/lock")
    assert client.get(f"/media-library/items/{item_id}/init-image").status_code == 423

    # Restore: the source image comes back to the library, byte-identical, and
    # the sidecar names it truthfully.
    client.post("/media-vault/unlock", json={"password": PASSWORD_INIT})
    assert client.post("/media-vault/restore", json={"item_ids": [item_id]}).json()[
        "results"
    ][0]["ok"] is True
    assert init_plaintext.read_bytes() == FAKE_INIT_PNG
    sidecar = json.loads(
        (Path(item["file_path"]).parent / f"{item_id}.json").read_text()
    )
    assert sidecar["init_image_file"] == f"{item_id}.init.png"
    assert not init_blob.exists(), "vault copy must be gone after restore"
    r = client.get(f"/media-library/items/{item_id}/init-image")
    assert r.status_code == 200 and r.content == FAKE_INIT_PNG


def test_vault_permanent_delete_takes_the_init_image(client: TestClient, vault_env) -> None:
    """A permanent vault delete must not orphan the encrypted source image."""
    library = vault_env["library"]
    item = library.save_generated_image(
        FAKE_PNG, model_id="m", prompt="p", negative_prompt="", params={},
        seed=1, width=8, height=8, elapsed_seconds=0.0,
        init_image_bytes=FAKE_INIT_PNG,
    )
    item_id = item["id"]
    blobs = vault_env["tmp"] / "vault" / "blobs"
    client.post("/media-vault/create", json={"password": PASSWORD_INIT})
    client.post("/media-vault/move", json={"item_ids": [item_id]})
    assert (blobs / f"{item_id}.init.bin").exists()

    assert client.delete(f"/media-vault/items/{item_id}").status_code == 200
    assert not (blobs / f"{item_id}.init.bin").exists(), "orphaned encrypted blob"
    assert not (blobs / f"{item_id}.bin").exists()
    assert client.get(f"/media-library/items/{item_id}/init-image").status_code == 404
