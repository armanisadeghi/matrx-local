"""Credential-free customer response schemas for the native personal messaging API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from matrx_ai.db.message_parts import TextPart


class PublicGrantStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    generation: int | None = None
    revision: int | None = None
    status: Literal[
        "not_connected",
        "pending_local",
        "pending_credential",
        "active",
        "needs_attention",
        "revoking",
        "retired",
    ]
    account_ref: str | None = None
    selected_chat_ids: list[str] = Field(default_factory=list)
    scopes: list[Literal["read"]] = Field(default_factory=list)
    expires_at: datetime | None = None
    connection_id: UUID | None = None
    bridge_provider: Literal["cloud", "local"] | None = None
    resources_generation: int | None = None
    recovery_required: bool = False
    cleanup_client_label: str | None = None
    cleanup_confirmation: Literal["owner_attested"] | None = None
    cleanup_confirmation_generation: int | None = None
    refresh_supported: Literal[False] = False
    renewal: Literal["reauthorization"] = "reauthorization"
    provider_revoke_pending: bool = False
    cloud_retirement_pending: bool = False


class PersonalConsentStart(BaseModel):
    authorization_url: str
    grant: PublicGrantStatus
    required_duration: Literal["one_day"]


class PersonalActivation(BaseModel):
    grant: PublicGrantStatus
    removed_chat_ids: list[str]


class PersonalAccountSummary(BaseModel):
    account_ref: str
    name: str
    status: str
    bridge_provider: Literal["cloud", "local"]


class PersonalChatSummary(BaseModel):
    id: str
    title: str
    selected: bool


class PersonalChatPage(BaseModel):
    items: list[PersonalChatSummary]
    has_more: bool
    oldest_cursor: str
    newest_cursor: str


class PersonalMessageContent(BaseModel):
    parts: list[TextPart]


class PersonalMessage(BaseModel):
    id: str
    account_id: str
    chat_id: str
    sender_id: str
    sender_name: str | None
    direction: Literal["inbound", "outbound", "unknown"]
    created_at: datetime
    content: PersonalMessageContent
    deleted: bool
    attachment_count: int = Field(ge=0)
    attachment_access: Literal["beeper_only"] | None


class PersonalMessagePage(BaseModel):
    items: list[PersonalMessage]
    has_more: bool
    oldest_cursor: str
    newest_cursor: str
    history_coverage: Literal["available_in_beeper"]
    refresh_supported: Literal[False] = False
    renewal: Literal["reauthorization"] = "reauthorization"
