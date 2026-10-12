"""Real SQLite/CAS and real encryption; OS keychain retrieval is a separate canary."""

import asyncio
from datetime import datetime, UTC, timedelta
from uuid import uuid4
from cryptography.fernet import Fernet
from pydantic import SecretStr
import pytest
from app.services.local_db.database import LocalDatabase
from app.services.local_db import secret_store
from app.services.personal_messaging import store


async def scenario(tmp_path, monkeypatch, run):
    monkeypatch.setattr(secret_store, "_fernet", Fernet(Fernet.generate_key()))
    db = LocalDatabase(tmp_path / "personal.db")
    await db.connect()
    try:
        await run(store.PersonalMessagingGrantRepo(db), db)
    finally:
        await db.close()


def pending():
    return store.DeviceGrant(
        id=uuid4(),
        actor_id=uuid4(),
        app_instance_id=uuid4(),
        generation=1,
        status="pending_local",
        client_id="public-test-client",
        redirect_uri="http://127.0.0.1:22140/personal-messaging/beeper/callback",
    )


def test_encrypted_custody_restart_cas_and_unrelated_settings(tmp_path, monkeypatch):
    async def run(repo, db):
        await repo.settings.set("unrelated", {"keep": True})
        grant = await repo.save(
            pending().model_copy(
                update={
                    "client_secret": SecretStr("private-client-test"),
                    "registration_access_token": SecretStr("private-registration-test"),
                }
            ),
            expected_revision=None,
        )
        sealed = await repo.save(
            grant.model_copy(
                update={
                    "status": "pending_credential",
                    "access_token": SecretStr("private-access-test"),
                }
            ),
            expected_revision=grant.revision,
        )
        probed = sealed.model_copy(
            update={
                "expires_at": datetime.now(UTC) + timedelta(days=1),
                "scopes": ["read"],
                "account_ref": "whatsapp",
                "native_user_ref": "native-owner",
            }
        )
        sealed = await repo.save(probed, expected_revision=sealed.revision)
        persisted = await repo.settings.get_all()
        assert persisted["unrelated"] == {"keep": True}
        raw = str(persisted)
        for value in [
            "private-access-test",
            "private-client-test",
            "private-registration-test",
        ]:
            assert value not in raw
        loaded = await store.PersonalMessagingGrantRepo(db).load(
            actor_id=sealed.actor_id,
            app_instance_id=sealed.app_instance_id,
            grant_id=sealed.id,
        )
        assert loaded.access_token.get_secret_value() == "private-access-test"
        assert loaded.client_secret.get_secret_value() == "private-client-test"
        assert "private-access-test" not in str(loaded.public_status())
        with pytest.raises(store.GrantConflict):
            await repo.save(sealed, expected_revision=grant.revision)
        wrong_actor = await repo.load(
            actor_id=uuid4(), app_instance_id=sealed.app_instance_id, grant_id=sealed.id
        )
        assert wrong_actor is None
        active = sealed.model_copy(
            update={
                "status": "active",
                "resources_generation": 1,
                "connection_id": uuid4(),
            }
        )
        a, b = await asyncio.gather(
            repo.save(active, expected_revision=sealed.revision),
            repo.save(active, expected_revision=sealed.revision),
            return_exceptions=True,
        )
        assert sum(isinstance(x, store.DeviceGrant) for x in [a, b]) == 1
        assert sum(isinstance(x, store.GrantConflict) for x in [a, b]) == 1

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_disconnect_generation_and_tombstone(tmp_path, monkeypatch):
    async def run(repo, db):
        grant = await repo.save(pending(), expected_revision=None)
        retired = await repo.save(
            grant.model_copy(
                update={"status": "retired", "cloud_retirement_pending": True}
            ),
            expected_revision=grant.revision,
        )
        with pytest.raises(store.GrantConflict):
            await repo.save(
                retired.model_copy(update={"generation": 2, "status": "pending_local"}),
                expected_revision=retired.revision,
            )
        retired = await repo.save(
            retired.model_copy(update={"cloud_retirement_pending": False}),
            expected_revision=retired.revision,
        )
        reconnected = await repo.save(
            retired.model_copy(update={"generation": 2, "status": "pending_local"}),
            expected_revision=retired.revision,
        )
        assert reconnected.generation == 2
        with pytest.raises(store.GrantConflict):
            await repo.save(grant, expected_revision=reconnected.revision)

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_wrong_binding_or_plaintext_refused(tmp_path, monkeypatch):
    async def run(repo, db):
        grant = await repo.save(pending(), expected_revision=None)
        wrong_actor = uuid4()
        key = repo._key(wrong_actor, grant.app_instance_id, grant.id)
        values = await repo.settings.get(store._PRIVATE_KEY)
        values[key] = next(iter(values.values()))
        await repo.settings.set(store._PRIVATE_KEY, values)
        with pytest.raises(store.GrantUnavailable):
            await repo.load(
                actor_id=wrong_actor,
                app_instance_id=grant.app_instance_id,
                grant_id=grant.id,
            )
        values[key] = "plaintext-test-token"
        await repo.settings.set(store._PRIVATE_KEY, values)
        with pytest.raises(store.GrantUnavailable):
            await repo.load(
                actor_id=wrong_actor,
                app_instance_id=grant.app_instance_id,
                grant_id=grant.id,
            )

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_expiry_and_keychain_failure_never_claim_connected(tmp_path, monkeypatch):
    async def run(repo, db):
        grant = pending().model_copy(
            update={
                "status": "active",
                "resources_generation": 1,
                "access_token": SecretStr("private"),
                "account_ref": "whatsapp",
                "native_user_ref": "native-owner",
                "connection_id": uuid4(),
                "scopes": ["read"],
                "expires_at": datetime.now(UTC) - timedelta(seconds=1),
            }
        )
        assert grant.public_status()["status"] == "needs_attention"

        def failed(_value):
            raise secret_store.SecretEncryptionUnavailableError("keychain unavailable")

        monkeypatch.setattr(store, "protect", failed)
        fresh = pending()
        with pytest.raises(secret_store.SecretEncryptionUnavailableError):
            await repo.save(fresh, expected_revision=None)
        assert (
            await repo.load(
                actor_id=fresh.actor_id,
                app_instance_id=fresh.app_instance_id,
                grant_id=fresh.id,
            )
            is None
        )

    asyncio.run(scenario(tmp_path, monkeypatch, run))


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_id", "other-client"),
        ("client_secret", SecretStr("other-secret")),
        ("registration_access_token", SecretStr("other-registration")),
        ("native_user_ref", "other-native-user"),
        ("account_ref", "other-account"),
        ("scopes", ["read", "write"]),
        ("expires_at", datetime.now(UTC) + timedelta(days=2)),
    ],
)
def test_active_consent_fields_are_immutable(tmp_path, monkeypatch, field, value):
    async def run(repo, db):
        row = await repo.save(pending(), expected_revision=None)
        row = await repo.save(
            row.model_copy(
                update={
                    "status": "pending_credential",
                    "access_token": SecretStr("private-access"),
                }
            ),
            expected_revision=row.revision,
        )
        row = await repo.save(
            row.model_copy(
                update={
                    "status": "active",
                    "resources_generation": 1,
                    "native_user_ref": "native-owner",
                    "account_ref": "whatsapp",
                    "scopes": ["read"],
                    "expires_at": datetime.now(UTC) + timedelta(days=1),
                    "connection_id": uuid4(),
                }
            ),
            expected_revision=row.revision,
        )
        with pytest.raises(store.GrantConflict):
            await repo.save(
                row.model_copy(update={field: value}), expected_revision=row.revision
            )
        loaded = await repo.load(
            actor_id=row.actor_id, app_instance_id=row.app_instance_id, grant_id=row.id
        )
        assert getattr(loaded, field) == getattr(row, field)

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_retirement_wipes_all_secrets_and_keeps_tombstone(tmp_path, monkeypatch):
    async def run(repo, db):
        row = await repo.save(
            pending().model_copy(
                update={
                    "client_secret": SecretStr("private-client"),
                    "registration_access_token": SecretStr("private-registration"),
                }
            ),
            expected_revision=None,
        )
        row = await repo.save(
            row.model_copy(
                update={
                    "status": "pending_credential",
                    "access_token": SecretStr("private-access"),
                }
            ),
            expected_revision=row.revision,
        )
        row = await repo.save(
            row.model_copy(
                update={"status": "revoking", "provider_revoke_pending": True}
            ),
            expected_revision=row.revision,
        )
        with pytest.raises(ValueError):
            await repo.save(
                row.model_copy(
                    update={
                        "status": "retired",
                        "access_token": None,
                        "provider_revoke_pending": False,
                    }
                ),
                expected_revision=row.revision,
            )
        row = await repo.save(
            row.model_copy(
                update={
                    "status": "retired",
                    "access_token": None,
                    "client_secret": None,
                    "registration_access_token": None,
                    "provider_revoke_pending": False,
                    "cloud_retirement_pending": True,
                }
            ),
            expected_revision=row.revision,
        )
        loaded = await repo.load(
            actor_id=row.actor_id, app_instance_id=row.app_instance_id, grant_id=row.id
        )
        assert loaded.status == "retired" and loaded.cloud_retirement_pending
        assert (
            loaded.access_token
            is loaded.client_secret
            is loaded.registration_access_token
            is None
        )

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_unverified_tokens_roundtrip_inactive_and_retire_together(
    tmp_path, monkeypatch
):
    async def run(repo, db):
        row = await repo.save(pending(), expected_revision=None)
        row = await repo.save(
            row.model_copy(
                update={
                    "status": "pending_credential",
                    "access_token": SecretStr("unverified-access"),
                    "cleanup_credentials": {
                        "refresh_token": SecretStr("unverified-refresh"),
                        "id_token": SecretStr("unverified-id"),
                    },
                }
            ),
            expected_revision=row.revision,
        )
        loaded = await repo.load(
            actor_id=row.actor_id, app_instance_id=row.app_instance_id, grant_id=row.id
        )
        assert (
            loaded.cleanup_credentials["refresh_token"].get_secret_value()
            == "unverified-refresh"
        )
        assert (
            loaded.cleanup_credentials["id_token"].get_secret_value() == "unverified-id"
        )
        raw = str(await repo.settings.get_all())
        for value in ["unverified-access", "unverified-refresh", "unverified-id"]:
            assert value not in raw and value not in str(loaded.public_status())
        row = await repo.save(
            row.model_copy(
                update={"status": "revoking", "provider_revoke_pending": True}
            ),
            expected_revision=row.revision,
        )
        row = await repo.save(
            row.model_copy(
                update={
                    "status": "retired",
                    "access_token": None,
                    "cleanup_credentials": {},
                    "provider_revoke_pending": False,
                }
            ),
            expected_revision=row.revision,
        )
        loaded = await repo.load(
            actor_id=row.actor_id, app_instance_id=row.app_instance_id, grant_id=row.id
        )
        assert (
            loaded.status == "retired"
            and loaded.access_token is None
            and loaded.cleanup_credentials == {}
        )

    asyncio.run(scenario(tmp_path, monkeypatch, run))
