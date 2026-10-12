"""Selected-message attachment access; no arbitrary URL or local-file door."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote, urlsplit

import httpx
from matrx_ai.db.message_parts import TextPart

if TYPE_CHECKING:
    from .beeper import DesktopApi, NativeMessage
    from .models import DeviceGrant

MAX_ATTACHMENT_BYTES = 128 * 1024 * 1024


def attachment_source(value: dict) -> str | None:
    source = value.get("id")
    if not isinstance(source, str) or not 1 <= len(source) <= 8192:
        return None
    try:
        parsed = urlsplit(source)
    except ValueError:
        return None
    return (
        source
        if parsed.scheme in ("mxc", "localmxc")
        and parsed.netloc
        and parsed.path
        and not parsed.query
        and not parsed.fragment
        else None
    )


def attachment_revision(message: NativeMessage, index: int) -> str:
    value = [
        message.id,
        message.editedTimestamp.isoformat() if message.editedTimestamp else None,
        index,
        message.attachments[index],
    ]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def project_message(
    message: NativeMessage, grant: DeviceGrant, chat_id: str
) -> dict | None:
    from .beeper import BeeperFailure

    if (
        message.accountID != grant.account_ref
        or message.chatID != chat_id
        or message.timestamp.tzinfo is None
    ):
        raise BeeperFailure(
            "message_mismatch", "Beeper returned a different conversation."
        )
    if message.isHidden:
        return None
    parts = []
    attachments = []
    if message.isDeleted:
        parts.append(
            TextPart(id=message.id + ":deleted", text="Message deleted").model_dump(
                mode="json"
            )
        )
    else:
        if message.text:
            parts.append(
                TextPart(id=message.id + ":text", text=message.text).model_dump(
                    mode="json"
                )
            )
        for index, value in enumerate(message.attachments):
            name = value.get("fileName")
            size = value.get("fileSize")
            mime = value.get("mimeType")
            attachments.append(
                {
                    "index": index,
                    "revision": attachment_revision(message, index),
                    "name": name[:240]
                    if isinstance(name, str) and name
                    else "Attachment",
                    "size_bytes": size
                    if isinstance(size, int)
                    and not isinstance(size, bool)
                    and size >= 0
                    else None,
                    "mime_type": mime[:120] if isinstance(mime, str) else None,
                    "available": attachment_source(value) is not None,
                }
            )
    return {
        "id": message.id,
        "account_id": message.accountID,
        "chat_id": message.chatID,
        "sender_id": message.senderID,
        "sender_name": message.senderName,
        "direction": "outbound"
        if message.isSender is True
        else "inbound"
        if message.isSender is False
        else "unknown",
        "created_at": message.timestamp.isoformat(),
        "content": {"parts": parts},
        "deleted": message.isDeleted,
        "attachment_count": len(attachments),
        "attachment_access": "local_device" if attachments else None,
        "attachments": attachments,
    }


async def retrieve_message(
    api: DesktopApi, grant: DeviceGrant, chat_id: str, message_id: str
) -> NativeMessage | None:
    from .beeper import BeeperFailure, NativeMessage

    if not message_id or len(message_id) > 1024:
        raise BeeperFailure("invalid_message", "Choose an available WhatsApp message.")
    await api.chat(grant, chat_id)
    try:
        result = await api._call(
            "GET",
            "/v1/chats/"
            + quote(chat_id, safe="")
            + "/messages/"
            + quote(message_id, safe=""),
            token=api.active(grant),
        )
    except BeeperFailure as error:
        if error.code == "native_not_found":
            return None
        raise
    try:
        message = NativeMessage.model_validate(result)
    except ValueError:
        raise BeeperFailure(
            "invalid_response", "Beeper returned an invalid message."
        ) from None
    if message.id != message_id:
        raise BeeperFailure("message_mismatch", "Beeper returned a different message.")
    project_message(message, grant, chat_id)
    return None if message.isDeleted or message.isHidden else message


@dataclass(frozen=True)
class NativeAttachment:
    data: bytes = field(repr=False)
    mime_type: str
    name: str


async def read_attachment(
    api: DesktopApi,
    grant: DeviceGrant,
    message: NativeMessage,
    index: int,
    *,
    max_bytes: int = MAX_ATTACHMENT_BYTES,
) -> NativeAttachment:
    from .beeper import ORIGIN, BeeperFailure

    if (
        index < 0
        or index >= len(message.attachments)
        or message.isHidden
        or message.isDeleted
    ):
        raise BeeperFailure("attachment_unavailable", "Open this attachment in Beeper.")
    value = message.attachments[index]
    source = attachment_source(value)
    if source is None:
        raise BeeperFailure("attachment_unavailable", "Open this attachment in Beeper.")
    try:
        async with httpx.AsyncClient(
            timeout=60, trust_env=False, follow_redirects=False
        ) as client:
            async with client.stream(
                "GET",
                ORIGIN + "/v1/assets/serve",
                params={"url": source},
                headers={
                    "Authorization": "Bearer " + api.active(grant).get_secret_value()
                },
            ) as response:
                if response.status_code in (401, 403):
                    raise BeeperFailure(
                        "permission_required",
                        "Check your WhatsApp connection before opening attachments.",
                    )
                if not response.is_success:
                    raise BeeperFailure(
                        "attachment_unavailable", "Open this attachment in Beeper."
                    )
                length = response.headers.get("content-length")
                if length and (not length.isdigit() or int(length) > max_bytes):
                    raise BeeperFailure(
                        "attachment_too_large",
                        "This attachment exceeds the preview limit. Open it in Beeper.",
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > max_bytes:
                        raise BeeperFailure(
                            "attachment_too_large",
                            "This attachment exceeds the preview limit. Open it in Beeper.",
                        )
                    data.extend(chunk)
                mime = (
                    response.headers.get("content-type", "application/octet-stream")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if (
                    len(mime) > 120
                    or not mime
                    or any(
                        c not in "abcdefghijklmnopqrstuvwxyz0123456789!#$&^_.+-/"
                        for c in mime
                    )
                ):
                    mime = "application/octet-stream"
                # Beeper's asset proxy can use a generic transport MIME. The
                # exact selected attachment supplies its provider-declared type.
                declared = value.get("mimeType")
                if mime == "application/octet-stream" and isinstance(declared, str) and declared in {
                    "image/png", "image/jpeg", "image/webp", "image/gif", "image/avif",
                    "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/webm",
                    "video/mp4", "video/webm", "video/quicktime",
                }:
                    mime = declared
                name = value.get("fileName")
                return NativeAttachment(
                    bytes(data),
                    mime,
                    name[:240] if isinstance(name, str) and name else "Attachment",
                )
    except httpx.HTTPError:
        raise BeeperFailure(
            "attachment_unavailable",
            "WhatsApp attachment could not load. Retry or open it in Beeper.",
        ) from None
