"""Keychain-encrypted grants in device-only SQLite settings, with revision CAS."""

from __future__ import annotations
import json
from datetime import UTC, datetime
from uuid import UUID
from pydantic import SecretStr
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import AppSettingsRepo, _APP_SETTINGS_WRITE_LOCK
from app.services.local_db.secret_store import protect, unprotect
from .models import DeviceGrant, GrantConflict, GrantUnavailable

_PRIVATE_KEY = "personal_messaging_grants_v1"


class PersonalMessagingGrantRepo:
    def __init__(self, db: LocalDatabase | None = None):
        self.db = db or get_db()
        self.settings = AppSettingsRepo(self.db)

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
        self,
        grant: DeviceGrant,
        *,
        expected_revision: int | None,
        cleanup_client_label: str | None = None,
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
            attested_cleanup = cleanup_client_label is not None
            if attested_cleanup and (
                current is None
                or current.status != "revoking"
                or grant.status != "revoking"
                or cleanup_client_label != current.client_label
                or not cleanup_client_label
                or not (
                    current.registration_pending
                    or current.exchange_pending
                    or current.client_secret
                    or current.registration_access_token
                )
                or grant.generation != current.generation
                or grant.cleanup_confirmation != "owner_attested"
                or grant.cleanup_confirmation_generation != current.generation
                or grant.registration_pending
                or grant.exchange_pending
                or grant.provider_revoke_pending
                or grant.access_token
                or grant.client_secret
                or grant.registration_access_token
                or grant.cleanup_credentials
                or grant.oauth_verifier
                or grant.oauth_state_hash
                or grant.oauth_expires_at
            ):
                raise GrantConflict(
                    "Cleanup attestation must match the exact quarantined native client"
                )
            if (
                current
                and not attested_cleanup
                and (
                    grant.cleanup_confirmation != current.cleanup_confirmation
                    or grant.cleanup_confirmation_generation
                    != current.cleanup_confirmation_generation
                )
            ):
                raise GrantConflict("Native cleanup provenance cannot be rewritten")
            if current is None and (
                grant.status != "pending_local"
                or grant.generation != 1
                or grant.access_token is not None
                or grant.cleanup_confirmation is not None
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
                    "needs_attention": {
                        "needs_attention",
                        "pending_credential",
                        "revoking",
                    },
                    "revoking": {"revoking", "retired"},
                    "retired": {"retired"},
                }
                if grant.generation == current.generation:
                    if grant.status not in transitions[current.status]:
                        raise GrantConflict(
                            "Retired messaging access requires new consent"
                        )
                    if (
                        current.status == "needs_attention"
                        and grant.status == "pending_credential"
                        and (
                            not current.access_token
                            or current.cleanup_credentials
                            or current.registration_pending
                            or current.exchange_pending
                            or not current.expires_at
                            or current.expires_at <= datetime.now(UTC)
                            or current.scopes != current.requested_scopes
                        )
                    ):
                        raise GrantConflict(
                            "Only unexpired sealed consent can reconcile metadata"
                        )
                    for name in (
                        "request_profile",
                        "max_duration_seconds",
                        "client_label",
                        "oauth_state_hash",
                        "oauth_verifier",
                        "oauth_expires_at",
                    ):
                        before, after = getattr(current, name), getattr(grant, name)
                        clearing_oauth = (
                            name != "client_label"
                            and after is None
                            and (
                                attested_cleanup
                                or (
                                    current.status == "pending_local"
                                    and grant.status == "pending_credential"
                                )
                                or (
                                    current.status == "revoking"
                                    and grant.status == "retired"
                                )
                            )
                        )
                        if before != after and not clearing_oauth:
                            raise GrantConflict(
                                "OAuth state is immutable within a consent generation"
                            )
                    if current.oauth_claimed and not grant.oauth_claimed:
                        raise GrantConflict(
                            "Claimed callback cannot become available again"
                        )
                    if current.oauth_claimed != grant.oauth_claimed and not (
                        current.status == grant.status == "pending_local"
                        and grant.oauth_claimed
                    ):
                        raise GrantConflict(
                            "Only a pending consent callback can be claimed"
                        )
                    if not current.registration_pending and grant.registration_pending:
                        raise GrantConflict(
                            "Registration begins only with a new consent generation"
                        )
                    if (
                        current.registration_pending
                        and not grant.registration_pending
                        and not (
                            attested_cleanup
                            or current.status == grant.status == "pending_local"
                            and not current.client_id
                            and grant.client_id
                        )
                    ):
                        raise GrantConflict(
                            "Interrupted registration needs observed provider cleanup"
                        )
                    if (
                        not current.exchange_pending
                        and grant.exchange_pending
                        and not (
                            current.status == grant.status == "pending_local"
                            and not current.oauth_claimed
                            and grant.oauth_claimed
                        )
                    ):
                        raise GrantConflict(
                            "Native issuance requires an atomic callback claim"
                        )
                    if (
                        current.exchange_pending
                        and not grant.exchange_pending
                        and not (
                            attested_cleanup
                            or current.status == "pending_local"
                            and grant.status == "pending_credential"
                            and grant.access_token
                        )
                    ):
                        raise GrantConflict(
                            "Interrupted issuance cannot be declared recovered"
                        )
                    if (
                        current.resources_generation != grant.resources_generation
                        and not (
                            current.status == "pending_credential"
                            and grant.status in ("pending_credential", "active")
                            and grant.resources_generation == grant.generation
                        )
                    ):
                        raise GrantConflict(
                            "Current resource receipts are required before activation"
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
                            attested_cleanup
                            or initializing_client
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
                            or attested_cleanup
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
                        attested_cleanup
                        or (
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
                    and (
                        (grant.client_id == current.client_id and not grant.registration_pending)
                        if grant.request_profile == current.request_profile and grant.redirect_uri == current.redirect_uri
                        else (not grant.client_id and grant.registration_pending)
                    )
                    and grant.account_ref == current.account_ref
                    and grant.native_user_ref == current.native_user_ref
                    and grant.connection_id == current.connection_id
                    and grant.expires_at is None
                    and grant.scopes == []
                    and grant.bridge_provider is None
                    and grant.access_token is None
                    and grant.resources_generation is None
                    and not grant.oauth_claimed
                    and not grant.exchange_pending
                    and not current.registration_pending
                    and not current.exchange_pending
                    and grant.cleanup_confirmation == current.cleanup_confirmation
                    and grant.cleanup_confirmation_generation
                    == current.cleanup_confirmation_generation
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

    async def list(self, *, actor_id: UUID, app_instance_id: UUID) -> list[DeviceGrant]:
        values = await self.settings.get(_PRIVATE_KEY, {})
        if not isinstance(values, dict):
            raise GrantUnavailable("Device grant storage is invalid")
        prefix = f"{actor_id}:{app_instance_id}:"
        grants = []
        for key, value in values.items():
            if isinstance(key, str) and key.startswith(prefix):
                grant = self._decode(value)
                if key != self._key(actor_id, app_instance_id, grant.id) or (
                    grant.actor_id,
                    grant.app_instance_id,
                ) != (actor_id, app_instance_id):
                    raise GrantUnavailable("Device grant owner binding is invalid")
                grants.append(grant)
        return grants
