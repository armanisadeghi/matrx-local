"""Messages tools — read iMessage/SMS from chat.db and send via AppleScript (macOS only).

Reading requires Full Disk Access to ~/Library/Messages/chat.db.
Sending requires Automation permission (Apple Events to Messages.app).

No additional TCC service or entitlement beyond those already declared:
  - com.apple.security.files.all (Full Disk Access)
  - com.apple.security.automation.apple-events (Automation)
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

def _fda_hint() -> str:
    """Diagnosis-aware remediation from the canonical access-health authority.

    chat.db is itself one of the FDA-protected probe locations, so when FDA
    is provably granted this correctly stops telling the user to grant it
    again (the failure has another cause), and when it's provably denied the
    grant instructions are justified.
    """
    from app.services.access_health import fda_hint

    return fda_hint()
_AUTOMATION_HINT = (
    "Automation access to Messages is required to send messages. "
    "Grant it in System Settings → Privacy & Security → Automation → AI Matrx → Messages."
)


def _ts_to_iso(apple_ts: int | None) -> str | None:
    """Convert Apple absolute time (seconds since 2001-01-01) to ISO 8601 UTC."""
    if apple_ts is None:
        return None
    try:
        import datetime
        # Apple epoch is 2001-01-01 00:00:00 UTC = Unix 978307200
        unix_ts = apple_ts / 1_000_000_000 + 978307200  # nanoseconds
        return datetime.datetime.fromtimestamp(unix_ts, tz=datetime.timezone.utc).isoformat()
    except Exception:
        return None


def _list_messages_sync(
    limit: int,
    contact_filter: str | None,
    unread_only: bool,
) -> list[dict[str, Any]]:
    if not _CHAT_DB.exists():
        raise PermissionError(
            f"chat.db not found at {_CHAT_DB}. {_fda_hint()}"
        )

    try:
        conn = sqlite3.connect(f"file:{_CHAT_DB}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        raise PermissionError(f"Cannot open chat.db: {exc}. {_fda_hint()}") from exc

    try:
        where_clauses = []
        params: list[Any] = []

        if contact_filter:
            where_clauses.append("(h.id LIKE ? OR c.display_name LIKE ?)")
            params += [f"%{contact_filter}%", f"%{contact_filter}%"]
        if unread_only:
            where_clauses.append("m.is_read = 0")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        query = f"""
            SELECT
                m.rowid        AS message_id,
                m.text         AS body,
                m.date         AS apple_date,
                m.is_from_me   AS is_from_me,
                m.is_read      AS is_read,
                m.service      AS service,
                h.id           AS handle_id,
                c.display_name AS chat_name,
                c.chat_identifier AS chat_identifier
            FROM message m
            LEFT JOIN handle h  ON m.handle_id = h.rowid
            LEFT JOIN chat_message_join cmj ON cmj.message_id = m.rowid
            LEFT JOIN chat c ON c.rowid = cmj.chat_id
            {where_sql}
            ORDER BY m.date DESC
            LIMIT ?
        """
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        messages = []
        for row in rows:
            messages.append({
                "message_id": row["message_id"],
                "body": row["body"],
                "date": _ts_to_iso(row["apple_date"]),
                "is_from_me": bool(row["is_from_me"]),
                "is_read": bool(row["is_read"]),
                "service": row["service"],
                "handle_id": row["handle_id"],
                "chat_name": row["chat_name"],
                "chat_identifier": row["chat_identifier"],
            })
        return messages
    finally:
        conn.close()


async def tool_list_messages(
    session: ToolSession,
    limit: int = 50,
    contact: str | None = None,
    unread_only: bool = False,
) -> ToolResult:
    """List recent iMessage and SMS messages from the Messages app.

    Reads directly from ~/Library/Messages/chat.db.
    Requires Full Disk Access (System Settings → Privacy & Security → Full Disk Access).

    Args:
        limit: Maximum messages to return (default 50, max 500).
        contact: Filter by contact name or phone number/email (partial match).
        unread_only: If True, only return unread messages.
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Messages tool is only available on macOS.", type=ToolResultType.ERROR)

    limit = max(1, min(limit, 500))

    try:
        messages = await asyncio.get_event_loop().run_in_executor(
            None, _list_messages_sync, limit, contact, unread_only
        )
    except PermissionError as exc:
        return ToolResult(
            output=str(exc),
            metadata={"available": False, "hint": _fda_hint()},
            type=ToolResultType.ERROR,
        )
    except Exception as exc:
        logger.exception("tool_list_messages failed")
        return ToolResult(output=f"Failed to list messages: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Found {len(messages)} message(s).",
        metadata={"messages": messages, "count": len(messages)},
        type=ToolResultType.SUCCESS,
    )


def _get_conversations_sync(limit: int) -> list[dict[str, Any]]:
    if not _CHAT_DB.exists():
        raise PermissionError(f"chat.db not found. {_fda_hint()}")

    try:
        conn = sqlite3.connect(f"file:{_CHAT_DB}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        raise PermissionError(f"Cannot open chat.db: {exc}. {_fda_hint()}") from exc

    try:
        rows = conn.execute("""
            SELECT
                c.rowid        AS chat_id,
                c.display_name AS display_name,
                c.chat_identifier AS chat_identifier,
                c.service_name AS service,
                (
                    SELECT text FROM message m
                    JOIN chat_message_join cmj ON cmj.message_id = m.rowid
                    WHERE cmj.chat_id = c.rowid
                    ORDER BY m.date DESC LIMIT 1
                ) AS last_message,
                (
                    SELECT m.date FROM message m
                    JOIN chat_message_join cmj ON cmj.message_id = m.rowid
                    WHERE cmj.chat_id = c.rowid
                    ORDER BY m.date DESC LIMIT 1
                ) AS last_date
            FROM chat c
            ORDER BY last_date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [
            {
                "chat_id": row["chat_id"],
                "display_name": row["display_name"],
                "chat_identifier": row["chat_identifier"],
                "service": row["service"],
                "last_message": row["last_message"],
                "last_date": _ts_to_iso(row["last_date"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


async def tool_list_conversations(
    session: ToolSession,
    limit: int = 25,
) -> ToolResult:
    """List recent iMessage/SMS conversations with the last message preview.

    Args:
        limit: Maximum conversations to return (default 25, max 200).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Messages tool is only available on macOS.", type=ToolResultType.ERROR)

    limit = max(1, min(limit, 200))

    try:
        conversations = await asyncio.get_event_loop().run_in_executor(
            None, _get_conversations_sync, limit
        )
    except PermissionError as exc:
        return ToolResult(output=str(exc), metadata={"available": False}, type=ToolResultType.ERROR)
    except Exception as exc:
        logger.exception("tool_list_conversations failed")
        return ToolResult(output=f"Failed to list conversations: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Found {len(conversations)} conversation(s).",
        metadata={"conversations": conversations, "count": len(conversations)},
        type=ToolResultType.SUCCESS,
    )


async def _run_applescript(script: str, timeout: int = 30) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode or 0


async def tool_send_message(
    session: ToolSession,
    recipient: str,
    body: str,
    service: str = "iMessage",
) -> ToolResult:
    """Send an iMessage or SMS via the Messages app using AppleScript.

    Requires Automation permission for Messages.app
    (System Settings → Privacy & Security → Automation → AI Matrx → Messages).

    Args:
        recipient: Phone number, email address, or contact name to send to.
        body: Message text to send.
        service: "iMessage" or "SMS" (default "iMessage").
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Messages tool is only available on macOS.", type=ToolResultType.ERROR)

    if not body.strip():
        return ToolResult(output="Message body cannot be empty.", type=ToolResultType.ERROR)

    # Sanitize for AppleScript string embedding
    safe_body = body.replace('"', '\\"').replace("\\", "\\\\")
    safe_recipient = recipient.replace('"', '\\"')
    safe_service = "iMessage" if service.lower() != "sms" else "SMS"

    script = f"""
tell application "Messages"
    set targetService to first service whose service type = {safe_service}
    set targetBuddy to buddy "{safe_recipient}" of targetService
    send "{safe_body}" to targetBuddy
end tell
"""

    try:
        stdout, stderr, rc = await _run_applescript(script, timeout=30)
    except asyncio.TimeoutError:
        return ToolResult(output="Message send timed out.", type=ToolResultType.ERROR)
    except Exception as exc:
        return ToolResult(output=f"Failed to send message: {exc}", type=ToolResultType.ERROR)

    if rc != 0:
        err_lower = stderr.lower()
        if "-1743" in stderr or "not authorized" in err_lower or "assistive" in err_lower:
            return ToolResult(
                output=f"Automation permission denied. {_AUTOMATION_HINT}",
                type=ToolResultType.ERROR,
            )
        return ToolResult(
            output=f"Failed to send message (exit {rc}): {stderr.strip() or stdout.strip()}",
            type=ToolResultType.ERROR,
        )

    return ToolResult(
        output=f"Message sent to {recipient} via {service}.",
        metadata={"recipient": recipient, "service": service, "body": body},
        type=ToolResultType.SUCCESS,
    )
