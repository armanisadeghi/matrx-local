"""Public types and safe projections for device-owned messaging grants."""

from __future__ import annotations
from datetime import datetime, UTC
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

GrantStatus = Literal[
    "pending_local",
    "pending_credential",
    "active",
    "needs_attention",
    "revoking",
    "retired",
]


class GrantUnavailable(RuntimeError):
    pass


class GrantConflict(RuntimeError):
    pass


class DeviceGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    actor_id: UUID
    app_instance_id: UUID
    provider: Literal["beeper_whatsapp"] = "beeper_whatsapp"
    generation: int = Field(ge=1)
    revision: int = Field(default=0, ge=0)
    status: GrantStatus
    client_id: str
    redirect_uri: str
    client_secret: SecretStr | None = Field(default=None, repr=False)
    registration_access_token: SecretStr | None = Field(default=None, repr=False)
    access_token: SecretStr | None = Field(default=None, repr=False)
    cleanup_credentials: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    account_ref: str | None = None
    native_user_ref: str | None = None
    selected_chat_ids: list[str] = Field(default_factory=list)
    request_profile: Literal["read", "read_write"] = "read"
    max_duration_seconds: Literal[86400, 2592000] = 86400
    scopes: list[Literal["read", "write"]] = Field(default_factory=list)
    expires_at: datetime | None = None
    connection_id: UUID | None = None
    provider_revoke_pending: bool = False
    cloud_retirement_pending: bool = False
    bridge_provider: Literal["cloud", "local"] | None = None
    resources_generation: int | None = Field(default=None, ge=1)
    client_label: str = ""
    cleanup_confirmation: Literal["owner_attested"] | None = None
    cleanup_confirmation_generation: int | None = Field(default=None, ge=1)
    registration_pending: bool = False
    exchange_pending: bool = False
    oauth_state_hash: str | None = Field(
        default=None, repr=False, pattern=r"^[0-9a-f]{64}$"
    )
    oauth_verifier: SecretStr | None = Field(default=None, repr=False)
    oauth_expires_at: datetime | None = None
    oauth_claimed: bool = False

    @property
    def requested_scopes(self) -> list[Literal["read", "write"]]:
        return ["read"] if self.request_profile == "read" else ["read", "write"]

    @model_validator(mode="after")
    def valid_active_grant(self):
        if (self.cleanup_confirmation is None) != (
            self.cleanup_confirmation_generation is None
        ) or (
            self.cleanup_confirmation_generation is not None
            and self.cleanup_confirmation_generation > self.generation
        ):
            raise ValueError(
                "Native cleanup provenance must identify its consent generation"
            )
        if (
            self.status == "active"
            and self.cleanup_confirmation_generation == self.generation
        ):
            raise ValueError("Attested cleanup requires new consent before activation")
        if self.oauth_expires_at is not None and self.oauth_expires_at.tzinfo is None:
            raise ValueError("OAuth callback deadline requires a timezone")
        if self.status == "active" and (
            self.registration_pending
            or self.exchange_pending
            or self.oauth_verifier is not None
            or self.resources_generation != self.generation
        ):
            raise ValueError(
                "Active access requires complete native issuance and current resource receipts"
            )
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("Grant expiry requires a timezone")
        if self.status == "active" and (
            self.access_token is None
            or not self.access_token.get_secret_value()
            or not self.account_ref
            or not self.native_user_ref
            or not self.connection_id
            or not self.expires_at
            or self.scopes != self.requested_scopes
            or self.cleanup_credentials
        ):
            raise ValueError(
                "Active device grant requires verified custody and registration"
            )
        if self.scopes and self.scopes != self.requested_scopes:
            raise ValueError("Issued scope must match the sealed customer request")
        if (
            self.status == "retired"
            and any(
                secret is not None
                for secret in (
                    self.access_token,
                    self.client_secret,
                    self.registration_access_token,
                    self.oauth_verifier,
                )
            )
            or (self.status == "retired" and self.cleanup_credentials)
        ):
            raise ValueError(
                "Retired grant cannot retain OAuth or registration secrets"
            )
        return self

    def public_status(self) -> dict:
        # Active is time-bounded even if the last saved status has not changed.
        expired = self.expires_at is not None and self.expires_at <= datetime.now(UTC)
        return {
            "id": str(self.id),
            "request_profile": self.request_profile,
            "max_duration_seconds": self.max_duration_seconds,
            "generation": self.generation,
            "revision": self.revision,
            "status": "needs_attention"
            if self.status == "active" and expired
            else self.status,
            "account_ref": self.account_ref,
            "selected_chat_ids": self.selected_chat_ids,
            "scopes": self.scopes,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "connection_id": str(self.connection_id) if self.connection_id else None,
            "bridge_provider": self.bridge_provider,
            "resources_generation": self.resources_generation,
            "recovery_required": self.registration_pending
            or self.exchange_pending
            or self.client_secret is not None
            or self.registration_access_token is not None,
            "cleanup_client_label": self.client_label
            if self.registration_pending
            or self.exchange_pending
            or self.client_secret
            or self.registration_access_token
            else None,
            "cleanup_confirmation": self.cleanup_confirmation,
            "cleanup_confirmation_generation": self.cleanup_confirmation_generation,
            "refresh_supported": False,
            "renewal": "reauthorization",
            "provider_revoke_pending": self.provider_revoke_pending,
            "cloud_retirement_pending": self.cloud_retirement_pending,
        }
