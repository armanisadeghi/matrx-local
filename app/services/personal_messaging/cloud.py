"""Personal connection metadata through owner-authenticated Supabase REST.

No native credential or chat contents enter this client. A cloud Connected row
is only a receipt; the device grant remains the authority for every read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY
from app.services.sync_client import get_sync_client
from .models import (
    DeviceGrant,
    GrantConflict,
    GrantUnavailable,
)


class NativeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    custody: Literal["native_device"] = "native_device"
    transport: Literal["same_machine"] = "same_machine"
    app_instance_id: UUID
    native_grant_id: UUID
    native_user_subject: str = Field(pattern=r"^whatsapp:[0-9a-f]{64}$")
    account_ref: str = Field(min_length=1)
    native_grant_generation: int = Field(ge=1, le=2147483647)
    native_grant_revision: int = Field(ge=1, le=2147483647)
    expires_at: datetime
    bridge_provider: Literal["cloud", "local"]

    @model_validator(mode="after")
    def aware_expiry(self):
        if self.expires_at.tzinfo is None:
            raise ValueError("Native consent expiry must be timezone aware")
        return self


class CloudReceipt(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    owner_type: Literal["user"]
    owner_user_id: UUID
    organization_id: None
    provider: Literal["beeper_whatsapp"]
    provider_subject: str
    scopes: list[Literal["read", "write"]]
    status: Literal["connected", "needs_attention", "disconnected", "revoked"]
    updated_at: datetime
    deleted_at: None
    metadata: NativeMetadata

    @model_validator(mode="after")
    def exact_scope_and_subject(self):
        if (
            self.scopes not in (["read"], ["read", "write"])
            or self.provider_subject != self.metadata.native_user_subject
        ):
            raise ValueError("Native connection receipt has inconsistent consent")
        if self.updated_at.tzinfo is None:
            raise ValueError("Native connection timestamp must be timezone aware")
        return self


class NativeResourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_instance_id: UUID
    native_grant_id: UUID
    native_grant_generation: int = Field(ge=1, le=2147483647)
    native_user_subject: str = Field(pattern=r"^whatsapp:[0-9a-f]{64}$")
    account_ref: str = Field(min_length=1)
    selected: bool


class CloudResourceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    connection_id: UUID
    resource_type: Literal["beeper_whatsapp_account", "beeper_whatsapp_chat"]
    resource_ref: str
    display_name: str
    permission_level: Literal["read", "read_write"]
    metadata: NativeResourceMetadata


class NativeConnectionClient:
    """Uses the daemon's fresh owner grant per operation; no Python session cache."""

    def __init__(self, actor_id: UUID, app_instance_id: UUID):
        self.actor_id = actor_id
        self.app_instance_id = app_instance_id

    def _subject(self, grant: DeviceGrant) -> str:
        if (grant.actor_id, grant.app_instance_id) != (
            self.actor_id,
            self.app_instance_id,
        ):
            raise GrantUnavailable(
                "Personal messaging belongs to a different device owner"
            )
        if (
            not grant.native_user_ref
            or not grant.account_ref
            or not grant.expires_at
            or not grant.bridge_provider
        ):
            raise GrantUnavailable(
                "Verify the linked WhatsApp account before registration"
            )
        return "whatsapp:" + sha256(grant.native_user_ref.encode()).hexdigest()

    async def _request(
        self, method: str, *, params: dict[str, str], body: dict | None = None
    ) -> tuple[int, list]:
        path = "/rest/v1/integration_connections"
        if method == "RESOURCES":
            path = "/rest/v1/integration_connection_resources"
            method = "GET"
            params = {
                **params,
                "select": "id,connection_id,resource_type,resource_ref,display_name,permission_level,metadata",
            }
        elif method == "GET":
            params = {
                **params,
                "select": "id,owner_type,owner_user_id,organization_id,provider,provider_subject,scopes,status,updated_at,deleted_at,metadata",
            }
        elif method in ("POST", "PATCH", "RESOURCE") and body is not None:
            # Existing restrictive client-write walls stay intact. Only the
            # registered native metadata RPC can write this provider's rows.
            previous = (
                body["previous"]
                if method == "RESOURCE"
                else None
                if method == "POST"
                else {
                    "id": params["id"].removeprefix("eq."),
                    "revision": int(
                        params["metadata->>native_grant_revision"].removeprefix("eq.")
                    ),
                    "updated_at": params["updated_at"].removeprefix("eq."),
                    "status": body["status"],
                }
            )
            if method in ("POST", "PATCH"):
                previous = {**(previous or {}), "scopes": body["scopes"]}
            body = {
                "p_operation": "resource"
                if method == "RESOURCE"
                else "create"
                if method == "POST"
                else "update",
                "p_binding": body["metadata"],
                "p_previous": previous,
            }
            path = "/rest/v1/rpc/personal_native_device_connection"
            method = "POST"
            params = {}
        else:
            raise GrantUnavailable("Unsupported personal connection operation")
        access = await get_sync_client().access_grant()
        if not access or access[1] != str(self.actor_id):
            raise GrantUnavailable(
                "Sign in as this device's owner to connect personal messaging"
            )
        # JWT remains in memory only for this operation; native OAuth tokens are
        # never accepted by this transport.
        headers = {
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Authorization": "Bearer " + access[0],
            "Accept-Profile": "users",
            "Content-Profile": "users",
            "Prefer": "return=representation",
        }
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(
                    method,
                    SUPABASE_URL.rstrip("/") + path,
                    headers=headers,
                    params=params,
                    json=body,
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 65536:
                            raise GrantUnavailable(
                                "Personal connection receipt exceeded its limit"
                            )
                    if response.status_code == 409:
                        return 409, []
                    if not response.is_success:
                        raise GrantUnavailable(
                            "Personal connection metadata could not be saved"
                        )
                    import json

                    rows = json.loads(content)
                    if not isinstance(rows, list):
                        raise ValueError("Receipt must be a list")
                    return response.status_code, rows
        except (httpx.HTTPError, ValueError):
            raise GrantUnavailable(
                "Personal connection metadata is unavailable. Retry reconciliation."
            ) from None

    def _binding(self, row: CloudReceipt, grant: DeviceGrant) -> None:
        if (
            row.owner_user_id != self.actor_id
            or row.metadata.app_instance_id != self.app_instance_id
            or row.provider_subject != self._subject(grant)
            or row.metadata.native_grant_id != grant.id
            or row.metadata.account_ref != grant.account_ref
            or (grant.connection_id and row.id != grant.connection_id)
            or (row.metadata.native_grant_generation == grant.generation and bool(grant.scopes) and row.scopes != grant.scopes)
        ):
            raise GrantConflict("Personal messaging connection binding changed")

    async def find(self, grant: DeviceGrant) -> CloudReceipt | None:
        _, rows = await self._request(
            "GET",
            params={
                "owner_user_id": "eq." + str(self.actor_id),
                "provider": "eq.beeper_whatsapp",
                "provider_subject": "eq." + self._subject(grant),
                "metadata->>app_instance_id": "eq." + str(self.app_instance_id),
                "deleted_at": "is.null",
                "limit": "2",
            },
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise GrantConflict("More than one personal connection matches this device")
        try:
            row = CloudReceipt.model_validate(rows[0])
        except ValueError:
            raise GrantUnavailable(
                "Saved personal connection is invalid. Reconcile before continuing."
            ) from None
        self._binding(row, grant)
        return row

    def _metadata(self, grant: DeviceGrant, revision: int) -> NativeMetadata:
        if not grant.account_ref or not grant.expires_at or not grant.bridge_provider:
            raise GrantUnavailable(
                "Verify native account binding before saving metadata"
            )
        return NativeMetadata(
            app_instance_id=self.app_instance_id,
            native_grant_id=grant.id,
            native_user_subject=self._subject(grant),
            account_ref=grant.account_ref,
            native_grant_generation=grant.generation,
            native_grant_revision=revision,
            expires_at=grant.expires_at,
            bridge_provider=grant.bridge_provider,
        )

    async def register_pending(self, grant: DeviceGrant) -> CloudReceipt:
        if (
            grant.status != "pending_credential"
            or grant.scopes != grant.requested_scopes
            or grant.expires_at is None
            or grant.expires_at <= datetime.now(UTC)
        ):
            raise GrantUnavailable(
                "New consent must be sealed and verified before registration"
            )
        current = await self.find(grant)
        if current:
            if current.metadata.native_grant_generation == grant.generation:
                if current.metadata != self._metadata(
                    grant, current.metadata.native_grant_revision
                ) or current.status not in ("needs_attention", "connected"):
                    raise GrantConflict(
                        "This consent generation has already changed or retired"
                    )
                return current
            if (
                current.metadata.native_grant_generation + 1 != grant.generation
                or current.status not in ("disconnected", "revoked")
            ):
                raise GrantConflict("Retire previous consent before reconnecting")
            return await self._patch(current, grant, "needs_attention")
        payload = {
            "owner_type": "user",
            "owner_user_id": str(self.actor_id),
            "organization_id": None,
            "provider": "beeper_whatsapp",
            "provider_subject": self._subject(grant),
            "account_name": "WhatsApp",
            "scopes": grant.scopes,
            "status": "needs_attention",
            "metadata": self._metadata(grant, 1).model_dump(mode="json"),
        }
        code, rows = await self._request("POST", params={}, body=payload)
        if code == 409:
            # Creation raced. Read canonical identity; never overwrite another
            # grant or invent a successful registration from a conflict.
            current = await self.find(grant)
            if (
                not current
                or current.metadata.native_grant_generation != grant.generation
                or current.metadata
                != self._metadata(grant, current.metadata.native_grant_revision)
                or current.status != "needs_attention"
            ):
                raise GrantConflict(
                    "Personal connection creation raced; reconcile saved consent"
                )
            return current
        created = self._validated_single(rows, grant)
        if created.status != "needs_attention" or created.metadata != self._metadata(
            grant, 1
        ):
            raise GrantConflict(
                "Created personal connection returned a different consent"
            )
        return created

    def _validated_single(self, rows: list, grant: DeviceGrant) -> CloudReceipt:
        if len(rows) != 1:
            raise GrantConflict("Personal connection changed; reload before continuing")
        try:
            row = CloudReceipt.model_validate(rows[0])
        except ValueError:
            raise GrantUnavailable(
                "Personal connection returned an invalid receipt"
            ) from None
        self._binding(row, grant)
        return row

    async def _patch(
        self,
        current: CloudReceipt,
        grant: DeviceGrant,
        status: Literal["connected", "needs_attention", "disconnected"],
    ) -> CloudReceipt:
        self._binding(current, grant)
        revision = current.metadata.native_grant_revision + 1
        stamp = max(datetime.now(UTC), current.updated_at + timedelta(microseconds=1))
        _, rows = await self._request(
            "PATCH",
            params={
                "id": "eq." + str(current.id),
                "owner_user_id": "eq." + str(self.actor_id),
                "provider": "eq.beeper_whatsapp",
                "metadata->>native_grant_revision": "eq."
                + str(current.metadata.native_grant_revision),
                "updated_at": "eq." + current.updated_at.isoformat(),
                "deleted_at": "is.null",
            },
            body={
                "status": status,
                "scopes": grant.scopes or current.scopes,
                "updated_at": stamp.isoformat(),
                "metadata": self._metadata(grant, revision).model_dump(mode="json"),
            },
        )
        row = self._validated_single(rows, grant)
        if row.status != status or row.metadata != self._metadata(grant, revision):
            raise GrantConflict(
                "Personal connection update returned a different consent revision"
            )
        return row

    async def activate(self, grant: DeviceGrant, current: CloudReceipt) -> CloudReceipt:
        if (
            grant.status != "pending_credential"
            or grant.expires_at is None
            or grant.expires_at <= datetime.now(UTC)
        ):
            raise GrantUnavailable("Verify current native consent before activating")
        if (
            current.metadata.native_grant_generation != grant.generation
            or current.metadata
            != self._metadata(grant, current.metadata.native_grant_revision)
            or current.status not in ("needs_attention", "connected")
        ):
            raise GrantConflict("Activation requires the matching pending consent")
        self._binding(current, grant)
        return await self._patch(current, grant, "connected")

    async def retire(self, grant: DeviceGrant) -> CloudReceipt | None:
        if grant.status not in ("revoking", "retired"):
            raise GrantUnavailable("Disable local reads before retiring cloud metadata")
        current = await self.find(grant)
        if current is None:
            if grant.connection_id:
                raise GrantConflict("Saved personal connection could not be found")
            return None
        if current.metadata.native_grant_generation != grant.generation:
            if current.metadata.native_grant_generation + 1 == grant.generation and current.status in ("disconnected", "revoked"):
                return current  # No cloud receipt was created for this rejected new consent.
            raise GrantConflict("A different consent generation owns this connection")
        return (
            current
            if current.status in ("disconnected", "revoked")
            else await self._patch(current, grant, "disconnected")
        )

    async def select_resource(
        self,
        grant: DeviceGrant,
        current: CloudReceipt,
        *,
        resource_type: Literal["beeper_whatsapp_account", "beeper_whatsapp_chat"],
        resource_ref: str,
        display_name: str,
        selected: bool,
    ) -> CloudResourceReceipt:
        self._binding(current, grant)
        if (
            grant.status not in ("pending_credential", "active")
            or grant.expires_at is None
            or grant.expires_at <= datetime.now(UTC)
        ):
            raise GrantUnavailable(
                "Verify current native consent before selecting chats"
            )
        if current.status not in (
            "needs_attention",
            "connected",
        ) or current.metadata != self._metadata(
            grant, current.metadata.native_grant_revision
        ):
            raise GrantConflict("Chat selection requires the matching current consent")
        if (
            resource_type == "beeper_whatsapp_account"
            and resource_ref != grant.account_ref
        ):
            raise GrantUnavailable("Native account selection does not match this grant")
        if resource_type == "beeper_whatsapp_chat" and (
            grant.status not in ("pending_credential", "active")
            or selected != (resource_ref in grant.selected_chat_ids)
        ):
            raise GrantUnavailable(
                "Select and verify this native chat on the device first"
            )
        _, rows = await self._request(
            "RESOURCE",
            params={},
            body={
                "metadata": current.metadata.model_dump(mode="json"),
                "previous": {
                    "id": str(current.id),
                    "revision": current.metadata.native_grant_revision,
                    "updated_at": current.updated_at.isoformat(),
                    "resource_type": resource_type,
                    "resource_ref": resource_ref,
                    "display_name": display_name,
                    "selected": selected,
                },
            },
        )
        if len(rows) != 1:
            raise GrantConflict("Chat selection raced with a changed consent")
        try:
            row = CloudResourceReceipt.model_validate(rows[0])
        except ValueError:
            raise GrantUnavailable(
                "Native chat selection returned an invalid receipt"
            ) from None
        if (
            row.connection_id != current.id
            or row.permission_level != ("read_write" if grant.scopes == ["read", "write"] else "read")
            or row.resource_type != resource_type
            or row.resource_ref != resource_ref
            or row.metadata
            != NativeResourceMetadata(
                app_instance_id=self.app_instance_id,
                native_grant_id=grant.id,
                native_grant_generation=grant.generation,
                native_user_subject=self._subject(grant),
                account_ref=current.metadata.account_ref,
                selected=selected,
            )
        ):
            raise GrantConflict("Native chat selection returned a different binding")
        return row

    async def selected_resources(
        self, grant: DeviceGrant, current: CloudReceipt
    ) -> list[CloudResourceReceipt]:
        self._binding(current, grant)
        if current.metadata != self._metadata(
            grant, current.metadata.native_grant_revision
        ):
            raise GrantConflict("Personal messaging consent changed. Reconcile.")
        resources = []
        seen_ids = set()
        offset = 0
        while True:
            _, rows = await self._request(
                "RESOURCES",
                params={
                    "connection_id": "eq." + str(current.id),
                    "deleted_at": "is.null",
                    "metadata->>selected": "eq.true",
                    "metadata->>native_grant_generation": "eq." + str(grant.generation),
                    "limit": "50",
                    "offset": str(offset),
                    "order": "id.asc",
                },
            )
            try:
                page = [CloudResourceReceipt.model_validate(row) for row in rows]
            except ValueError:
                raise GrantUnavailable(
                    "Native resource receipts are invalid."
                ) from None
            for resource in page:
                if resource.id in seen_ids:
                    raise GrantConflict(
                        "Native resource pagination repeated a receipt."
                    )
                seen_ids.add(resource.id)
                if (
                    resource.connection_id != current.id
                    or resource.permission_level != ("read_write" if grant.scopes == ["read", "write"] else "read")
                    or resource.metadata
                    != NativeResourceMetadata(
                        app_instance_id=self.app_instance_id,
                        native_grant_id=grant.id,
                        native_grant_generation=grant.generation,
                        native_user_subject=self._subject(grant),
                        account_ref=str(grant.account_ref),
                        selected=True,
                    )
                ):
                    raise GrantConflict("Native resource binding changed.")
            resources.extend(page)
            if len(page) < 50:
                break
            offset += len(page)
        if len({(r.resource_type, r.resource_ref) for r in resources}) != len(
            resources
        ):
            raise GrantConflict("Native selection has duplicate resource receipts.")
        return resources

    async def clear_unselected_resources(
        self, grant: DeviceGrant, current: CloudReceipt
    ) -> None:
        expected = {
            ("beeper_whatsapp_account", grant.account_ref),
            *(("beeper_whatsapp_chat", chat_id) for chat_id in grant.selected_chat_ids),
        }
        for resource in await self.selected_resources(grant, current):
            if (resource.resource_type, resource.resource_ref) not in expected:
                await self.select_resource(
                    grant,
                    current,
                    resource_type=resource.resource_type,
                    resource_ref=resource.resource_ref,
                    display_name=resource.display_name,
                    selected=False,
                )

    async def verify_resources(self, grant: DeviceGrant, current: CloudReceipt) -> None:
        if current.status != "connected":
            raise GrantConflict("Personal messaging consent changed. Reconcile.")
        resources = await self.selected_resources(grant, current)
        expected = {
            ("beeper_whatsapp_account", grant.account_ref),
            *(("beeper_whatsapp_chat", chat_id) for chat_id in grant.selected_chat_ids),
        }
        seen = {(r.resource_type, r.resource_ref) for r in resources}
        if seen != expected:
            raise GrantUnavailable(
                "Current native selection receipts are missing or inconsistent. Reconcile."
            )
