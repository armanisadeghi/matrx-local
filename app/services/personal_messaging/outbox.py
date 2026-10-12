"""Encrypted local native write journal. No cloud mirror or automatic resend."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from app.services.local_db.database import LocalDatabase
from app.services.local_db.secret_store import protect, unprotect
from .beeper import BeeperFailure
from .models import DeviceGrant, GrantConflict, GrantUnavailable

State = Literal["pending", "dispatching", "accepted", "unknown", "failed", "cancelled"]


class PrivateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor_id: UUID
    app_instance_id: UUID
    client_id: UUID
    grant_id: UUID
    grant_generation: int = Field(ge=1)
    native_subject_hash: str
    account_ref: str
    chat_id: str
    text: str = Field(repr=False, min_length=1, max_length=65536)


class OutboxEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    state: State
    pending_message_id: str | None = None
    final_message_id: str | None = None
    network_status: Literal["SUCCESS", "PENDING", "FAIL_RETRIABLE", "FAIL_PERMANENT"] | None = None
    network_timestamp: datetime | None = None
    delivered: bool | None = None
    seen: bool | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
    draft_text: str = Field(repr=False)


def subject(grant: DeviceGrant) -> str:
    if not grant.native_user_ref or not grant.account_ref:
        raise GrantUnavailable("Verify the WhatsApp account first.")
    return hashlib.sha256(grant.native_user_ref.encode()).hexdigest()


class NativeOutboxRepo:
    def __init__(self, db: LocalDatabase):
        self.db = db

    @staticmethod
    def _draft(row) -> PrivateDraft:
        cipher = row["payload_cipher"]
        if not isinstance(cipher, str) or not cipher.startswith("enc:v1:"):
            raise GrantUnavailable("The message draft is not encrypted.")
        value = unprotect(cipher)
        if not value:
            raise GrantUnavailable("Unlock the device keychain to read the draft.")
        draft = PrivateDraft.model_validate_json(value)
        for key in ("actor_id", "app_instance_id", "client_id", "grant_id", "grant_generation", "native_subject_hash", "account_ref", "chat_id"):
            if str(getattr(draft, key)) != str(row[key]):
                raise GrantConflict("The saved message binding changed.")
        return draft

    @classmethod
    def _entry(cls, row) -> OutboxEntry:
        draft = cls._draft(row)
        return OutboxEntry(client_id=draft.client_id, state=row["state"],
            pending_message_id=row["pending_message_id"], final_message_id=row["final_message_id"],
            network_status=row["network_status"], network_timestamp=row["network_timestamp"], delivered=None if row["delivered"] is None else bool(row["delivered"]),
            seen=None if row["seen"] is None else bool(row["seen"]), error_code=row["error_code"],
            created_at=row["created_at"], updated_at=row["updated_at"], draft_text=draft.text)

    @staticmethod
    def _binding(grant: DeviceGrant, chat_id: str) -> tuple[str, ...]:
        return (str(grant.actor_id), str(grant.app_instance_id), subject(grant), str(grant.account_ref), chat_id)

    async def get(self, grant: DeviceGrant, chat_id: str, client_id: UUID) -> OutboxEntry | None:
        row = await self.db.fetchone("SELECT * FROM personal_message_outbox WHERE actor_id=? AND app_instance_id=? AND native_subject_hash=? AND account_ref=? AND chat_id=? AND client_id=?",
            (*self._binding(grant, chat_id), str(client_id)))
        return self._entry(row) if row else None

    async def recent(self, grant: DeviceGrant, chat_id: str) -> list[OutboxEntry]:
        # Coordinator's owner lock ensures same-process dispatch has ended before this runs.
        # Surviving dispatch markers are ambiguous, never send jobs to resume.
        await self.db.execute("UPDATE personal_message_outbox SET state='unknown',error_code='send_interrupted',updated_at=? WHERE actor_id=? AND app_instance_id=? AND native_subject_hash=? AND account_ref=? AND chat_id=? AND state='dispatching'",
            (datetime.now(UTC).isoformat(), *self._binding(grant, chat_id)))
        await self.db.commit()
        rows = await self.db.fetchall("SELECT * FROM personal_message_outbox WHERE actor_id=? AND app_instance_id=? AND native_subject_hash=? AND account_ref=? AND chat_id=? ORDER BY created_at DESC LIMIT 50", self._binding(grant, chat_id))
        return [self._entry(row) for row in reversed(rows)]

    async def reserve(self, grant: DeviceGrant, chat_id: str, client_id: UUID, text: str) -> tuple[OutboxEntry, bool]:
        draft = PrivateDraft(actor_id=grant.actor_id, app_instance_id=grant.app_instance_id, client_id=client_id, grant_id=grant.id, grant_generation=grant.generation,
            native_subject_hash=subject(grant), account_ref=str(grant.account_ref), chat_id=chat_id, text=text)
        cipher = protect(draft.model_dump_json())
        if not isinstance(cipher, str) or not cipher.startswith("enc:v1:"):
            raise GrantUnavailable("The message could not be encrypted.")
        await self.db.execute("BEGIN IMMEDIATE")
        try:
            existing = await self.db.fetchone("SELECT * FROM personal_message_outbox WHERE actor_id=? AND app_instance_id=? AND client_id=?", (str(grant.actor_id), str(grant.app_instance_id), str(client_id)))
            if existing:
                if self._draft(existing) != draft:
                    raise GrantConflict("This message identifier already belongs to another draft.")
                entry = self._entry(existing)
                await self.db.commit()
                return entry, False
            unresolved = await self.db.fetchone("SELECT client_id FROM personal_message_outbox WHERE actor_id=? AND app_instance_id=? AND native_subject_hash=? AND account_ref=? AND chat_id=? AND state IN ('pending','dispatching','unknown') LIMIT 1", self._binding(grant, chat_id))
            if unresolved:
                raise BeeperFailure("send_unresolved", "A previous message may have been sent. Check its status first.")
            now = datetime.now(UTC).isoformat()
            await self.db.execute("INSERT INTO personal_message_outbox(actor_id,app_instance_id,client_id,grant_id,grant_generation,native_subject_hash,account_ref,chat_id,operation,state,payload_cipher,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?)",
                (str(grant.actor_id), str(grant.app_instance_id), str(client_id), str(grant.id), grant.generation, subject(grant), str(grant.account_ref), chat_id, "send", cipher, now, now))
            await self.db.commit()
        except BaseException:
            if self.db.db.in_transaction:
                await self.db.execute("ROLLBACK")
            raise
        entry = await self.get(grant, chat_id, client_id)
        if entry is None:
            raise GrantConflict("The saved message could not be verified.")
        return entry, True

    async def transition(self, grant: DeviceGrant, chat_id: str, client_id: UUID, *, expected: tuple[State, ...], state: State,
        pending_message_id: str | None = None, final_message_id: str | None = None,
        network_status: str | None = None, network_timestamp: datetime | None = None, delivered: bool | None = None, seen: bool | None = None,
        error_code: str | None = None) -> OutboxEntry:
        placeholders = ",".join("?" for _ in expected)
        result = await self.db.execute("UPDATE personal_message_outbox SET state=?,pending_message_id=COALESCE(?,pending_message_id),final_message_id=COALESCE(?,final_message_id),network_status=COALESCE(?,network_status),network_timestamp=COALESCE(?,network_timestamp),delivered=CASE WHEN delivered=1 OR ?=1 THEN 1 ELSE COALESCE(?,delivered) END,seen=CASE WHEN seen=1 OR ?=1 THEN 1 ELSE COALESCE(?,seen) END,error_code=?,updated_at=? WHERE actor_id=? AND app_instance_id=? AND native_subject_hash=? AND account_ref=? AND chat_id=? AND client_id=? AND state IN ("+placeholders+")",
            (state,pending_message_id,final_message_id,network_status,network_timestamp.isoformat() if network_timestamp else None,delivered,delivered,seen,seen,error_code,datetime.now(UTC).isoformat(),*self._binding(grant,chat_id),str(client_id),*expected))
        await self.db.commit()
        if result.rowcount != 1:
            raise GrantConflict("The message state changed. Check its status.")
        entry = await self.get(grant,chat_id,client_id)
        if entry is None:
            raise GrantConflict("The message state is unavailable.")
        return entry

    async def retire(self, grant: DeviceGrant) -> None:
        await self.db.execute("UPDATE personal_message_outbox SET state='cancelled',error_code='connection_retired',updated_at=? WHERE actor_id=? AND app_instance_id=? AND grant_id=? AND grant_generation=? AND state='pending'",
            (datetime.now(UTC).isoformat(),str(grant.actor_id),str(grant.app_instance_id),str(grant.id),grant.generation))
        await self.db.commit()
