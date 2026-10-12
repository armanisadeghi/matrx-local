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
    scopes: list[Literal["read"]] = Field(default_factory=list)
    expires_at: datetime | None = None
    connection_id: UUID | None = None
    provider_revoke_pending: bool = False
    cloud_retirement_pending: bool = False
    bridge_provider: Literal["cloud", "local"] | None = None

    @model_validator(mode="after")
    def valid_active_grant(self):
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("Grant expiry requires a timezone")
        if self.status == "active" and (
            self.access_token is None
            or not self.access_token.get_secret_value()
            or not self.account_ref
            or not self.native_user_ref
            or not self.connection_id
            or not self.expires_at
            or self.scopes != ["read"]
            or self.cleanup_credentials
        ):
            raise ValueError(
                "Active device grant requires verified custody and registration"
            )
        if self.scopes and self.scopes != ["read"]:
            raise ValueError("This personal messaging connection is read-only")
        if (
            self.status == "retired"
            and any(
                secret is not None
                for secret in (
                    self.access_token,
                    self.client_secret,
                    self.registration_access_token,
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
            "refresh_supported": False,
            "renewal": "reauthorization",
            "provider_revoke_pending": self.provider_revoke_pending,
            "cloud_retirement_pending": self.cloud_retirement_pending,
        }
