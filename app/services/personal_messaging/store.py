"""Keychain-encrypted grants in device-only SQLite settings, with revision CAS."""

from __future__ import annotations
import json
from datetime import datetime
from uuid import UUID
from pydantic import SecretStr
from app.services.local_db.database import LocalDatabase
from app.services.local_db.repositories import AppSettingsRepo, _APP_SETTINGS_WRITE_LOCK
from app.services.local_db.secret_store import protect, unprotect
from .models import DeviceGrant, GrantConflict, GrantUnavailable

_PRIVATE_KEY = "personal_messaging_grants_v1"


class PersonalMessagingGrantRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self.settings = AppSettingsRepo(db)

    @staticmethod
    def _key(actor_id: UUID, app_instance_id: UUID, grant_id: UUID) -> str:
        return f"{actor_id}:{app_instance_id}:{grant_id}"

    @staticmethod
    def _decode(value: object) -> DeviceGrant:
        if not isinstance(value, str) or not value.startswith("enc:v1:"):
            raise GrantUnavailable("Device grant is not encrypted")
        plaintext = unprotect(value)
        if not plaintext:
            raise GrantUnavailable("Unlock the device keychain to reconnect messaging")
        try:
            return DeviceGrant.model_validate_json(plaintext)
        except ValueError:
            raise GrantUnavailable(
                "Saved device messaging grant is invalid. Reconnect."
            ) from None

    @staticmethod
    def _encrypt(grant: DeviceGrant) -> str:
        def private_value(value):
            if isinstance(value, SecretStr):
                return value.get_secret_value()
            if isinstance(value, dict):
                return {k: private_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [private_value(v) for v in value]
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, UUID):
                return str(value)
            return value

        ciphertext = protect(
            json.dumps(
                private_value(grant.model_dump(mode="python")), separators=(",", ":")
            )
        )
        if not ciphertext or not ciphertext.startswith("enc:v1:"):
            raise GrantUnavailable("Device credential encryption is unavailable")
        return ciphertext

    async def load(
        self, *, actor_id: UUID, app_instance_id: UUID, grant_id: UUID
    ) -> DeviceGrant | None:
        values = await self.settings.get(_PRIVATE_KEY, {})
        if not isinstance(values, dict):
            raise GrantUnavailable("Device grant storage is invalid")
        value = values.get(self._key(actor_id, app_instance_id, grant_id))
        if value is None:
            return None
        grant = self._decode(value)
        if (grant.actor_id, grant.app_instance_id, grant.id) != (
            actor_id,
            app_instance_id,
            grant_id,
        ):
            raise GrantUnavailable("Device grant owner binding is invalid")
        return grant

    async def save(
        self, grant: DeviceGrant, *, expected_revision: int | None
    ) -> DeviceGrant:
        async with _APP_SETTINGS_WRITE_LOCK:
            settings = await self.settings.get_all()
            values = settings.get(_PRIVATE_KEY, {})
            if not isinstance(values, dict):
                raise GrantUnavailable("Device grant storage is invalid")
            values = dict(values)
            key = self._key(grant.actor_id, grant.app_instance_id, grant.id)
            current = self._decode(values[key]) if key in values else None
            if (current is None) != (expected_revision is None) or (
                current is not None and current.revision != expected_revision
            ):
                raise GrantConflict(
                    "Messaging connection changed; reload before continuing"
                )
            if current is None and (
                grant.status != "pending_local"
                or grant.generation != 1
                or grant.access_token is not None
            ):
                raise GrantConflict(
                    "New messaging access must begin with pending consent"
                )
            if current is not None:
                if (current.actor_id, current.app_instance_id, current.id) != (
                    grant.actor_id,
                    grant.app_instance_id,
                    grant.id,
                ):
                    raise GrantUnavailable("Device grant owner binding is invalid")
                transitions = {
                    "pending_local": {
                        "pending_local",
                        "pending_credential",
                        "revoking",
                        "retired",
                    },
                    "pending_credential": {
                        "pending_credential",
                        "active",
                        "needs_attention",
                        "revoking",
                    },
                    "active": {"active", "needs_attention", "revoking"},
                    "needs_attention": {"needs_attention", "revoking"},
                    "revoking": {"revoking", "retired"},
                    "retired": {"retired"},
                }
                if grant.generation == current.generation:
                    if grant.status not in transitions[current.status]:
                        raise GrantConflict(
                            "Retired messaging access requires new consent"
                        )
                    initializing_client = (
                        current.status == "pending_local"
                        and grant.status == "pending_local"
                        and not current.client_id
                        and bool(grant.client_id)
                    )
                    if grant.redirect_uri != current.redirect_uri or (
                        grant.client_id != current.client_id and not initializing_client
                    ):
                        raise GrantConflict(
                            "OAuth client binding cannot change within a grant"
                        )
                    retiring = (
                        current.status == "revoking"
                        and grant.status == "retired"
                        and not grant.provider_revoke_pending
                    )
                    for name in ("client_secret", "registration_access_token"):
                        if getattr(grant, name) != getattr(current, name) and not (
                            initializing_client
                            or (retiring and getattr(grant, name) is None)
                        ):
                            raise GrantConflict(
                                "OAuth client credentials cannot change within a grant"
                            )
                    if (
                        current.cleanup_credentials != grant.cleanup_credentials
                        and not (
                            (
                                current.status == "pending_local"
                                and grant.status == "pending_credential"
                                and not current.cleanup_credentials
                                and current.access_token is None
                            )
                            or (retiring and not grant.cleanup_credentials)
                        )
                    ):
                        raise GrantConflict(
                            "Unverified credentials must remain in cleanup custody"
                        )
                    for name in (
                        "account_ref",
                        "native_user_ref",
                        "scopes",
                        "expires_at",
                        "bridge_provider",
                        "connection_id",
                    ):
                        before, after = getattr(current, name), getattr(grant, name)
                        if before != after and not (
                            current.status == "pending_credential"
                            and grant.status in ("pending_credential", "active")
                            and not before
                            and bool(after)
                        ):
                            raise GrantConflict(
                                "Native account and consent binding cannot change within a grant"
                            )
                    if current.access_token != grant.access_token and not (
                        (
                            current.status == "pending_local"
                            and grant.status == "pending_credential"
                            and current.access_token is None
                            and grant.access_token is not None
                        )
                        or (
                            current.status == "revoking"
                            and grant.status == "retired"
                            and grant.access_token is None
                            and not grant.provider_revoke_pending
                        )
                    ):
                        raise GrantConflict(
                            "Credential replacement requires a new consent generation"
                        )
                elif not (
                    current.status == "retired"
                    and grant.status == "pending_local"
                    and grant.generation == current.generation + 1
                    and not current.provider_revoke_pending
                    and not current.cloud_retirement_pending
                    and grant.account_ref == current.account_ref
                    and grant.native_user_ref == current.native_user_ref
                    and grant.connection_id == current.connection_id
                    and grant.expires_at is None
                    and grant.scopes == []
                    and grant.bridge_provider is None
                    and grant.access_token is None
                ):
                    raise GrantConflict(
                        "Finish retiring the previous grant before reconnecting"
                    )
            saved = DeviceGrant.model_validate(
                {
                    **grant.model_dump(),
                    "revision": (current.revision + 1) if current else 1,
                }
            )
            values[key] = self._encrypt(saved)
            settings[_PRIVATE_KEY] = values
            await self.settings.save_all(settings)
            return saved
