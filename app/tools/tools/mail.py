"""Mail tools — read and send email via Mail.app using AppleScript (macOS only).

Accessing Mail requires Automation permission (Apple Events to Mail.app).
No additional TCC service or entitlement beyond:
  - com.apple.security.automation.apple-events (already declared)

Note: This tool works via AppleScript and requires Mail.app to be configured
with at least one mail account.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.services.action_needed import os_permission_needed
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_AUTOMATION_HINT = (
    "Automation access to Mail is required. "
    "Grant it in System Settings → Privacy & Security → Automation → AI Matrx → Mail."
)


async def _run_applescript(script: str, timeout: int = 30) -> tuple[str, str, int]:
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode or 0


def _check_applescript_error(stderr: str, rc: int) -> str | None:
    """Return a user-facing error string if stderr indicates a permission error, else None."""
    if rc == 0:
        return None
    err_lower = stderr.lower()
    if "-1743" in stderr or "not authorized" in err_lower or "assistive" in err_lower:
        return f"Automation permission denied. {_AUTOMATION_HINT}"
    return f"AppleScript error (exit {rc}): {stderr.strip()}"


def _automation_action(stderr: str, feature: str):
    err_lower = stderr.lower()
    if not (
        "-1743" in stderr
        or "not authorized" in err_lower
        or "assistive" in err_lower
    ):
        return None
    return os_permission_needed(
        feature=feature,
        permission_key="automation",
        target="Mail",
        source="tool.mail",
    )


async def tool_list_emails(
    session: ToolSession,
    mailbox: str = "INBOX",
    limit: int = 25,
    unread_only: bool = False,
) -> ToolResult:
    """List recent emails from Mail.app via AppleScript.

    Args:
        mailbox: Mailbox name to read from (default "INBOX").
        limit: Maximum messages to return (default 25, max 200).
        unread_only: If True, only return unread messages.
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Mail tool is only available on macOS.", type=ToolResultType.ERROR)

    limit = max(1, min(limit, 200))
    read_filter = "whose read status is false" if unread_only else ""

    script = f"""
set output to ""
tell application "Mail"
    set allAccounts to every account
    set msgList to {{}}
    repeat with anAccount in allAccounts
        try
            set theMailbox to mailbox "{mailbox}" of anAccount
            set theMessages to (messages {read_filter} of theMailbox)
            set theMessages to items 1 thru (minimum value of {{count of theMessages, {limit}}}) of theMessages
            repeat with aMsg in theMessages
                set msgInfo to (subject of aMsg) & "|||" & (sender of aMsg) & "|||" & ((date received of aMsg) as string) & "|||" & (read status of aMsg as string)
                set end of msgList to msgInfo
            end repeat
        end try
    end repeat
    set AppleScript's text item delimiters to "\\n"
    set output to msgList as string
    set AppleScript's text item delimiters to ""
end tell
output
"""

    try:
        stdout, stderr, rc = await _run_applescript(script, timeout=30)
    except asyncio.TimeoutError:
        return ToolResult(output="Mail.app query timed out.", type=ToolResultType.ERROR)
    except Exception as exc:
        return ToolResult(output=f"Failed to list emails: {exc}", type=ToolResultType.ERROR)

    err_msg = _check_applescript_error(stderr, rc)
    if err_msg:
        return ToolResult(
            output=err_msg,
            type=ToolResultType.ERROR,
            action_needed=_automation_action(stderr, "Read Mail"),
        )

    emails: list[dict[str, Any]] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|||")
        if len(parts) >= 4:
            emails.append({
                "subject": parts[0].strip(),
                "sender": parts[1].strip(),
                "date": parts[2].strip(),
                "read": parts[3].strip().lower() == "true",
            })

    return ToolResult(
        output=f"Found {len(emails)} email(s) in {mailbox}.",
        metadata={"emails": emails, "count": len(emails), "mailbox": mailbox},
        type=ToolResultType.SUCCESS,
    )


async def tool_send_email(
    session: ToolSession,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> ToolResult:
    """Send an email via Mail.app using AppleScript.

    Requires Automation access to Mail.app
    (System Settings → Privacy & Security → Automation → AI Matrx → Mail).

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body (plain text).
        cc: CC recipient email address (optional).
        bcc: BCC recipient email address (optional).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Mail tool is only available on macOS.", type=ToolResultType.ERROR)

    if not to.strip() or not subject.strip():
        return ToolResult(output="Recipient and subject are required.", type=ToolResultType.ERROR)

    safe_to = to.replace('"', '\\"')
    safe_subject = subject.replace('"', '\\"')
    safe_body = body.replace('"', '\\"').replace("\\", "\\\\")

    cc_line = f'\n    make new to recipient at end of cc recipients of newMsg with properties {{address:"{cc.replace(chr(34), chr(92)+chr(34))}"}}\n' if cc else ""
    bcc_line = f'\n    make new to recipient at end of bcc recipients of newMsg with properties {{address:"{bcc.replace(chr(34), chr(92)+chr(34))}"}}\n' if bcc else ""

    script = f"""
tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{safe_subject}", content:"{safe_body}", visible:false}}
    tell newMsg
        make new to recipient at end of to recipients with properties {{address:"{safe_to}"}}{cc_line}{bcc_line}
    end tell
    send newMsg
end tell
"""

    try:
        stdout, stderr, rc = await _run_applescript(script, timeout=30)
    except asyncio.TimeoutError:
        return ToolResult(output="Mail send timed out.", type=ToolResultType.ERROR)
    except Exception as exc:
        return ToolResult(output=f"Failed to send email: {exc}", type=ToolResultType.ERROR)

    err_msg = _check_applescript_error(stderr, rc)
    if err_msg:
        return ToolResult(
            output=err_msg,
            type=ToolResultType.ERROR,
            action_needed=_automation_action(stderr, "Send Mail"),
        )

    return ToolResult(
        output=f"Email sent to {to}: {subject}",
        metadata={"to": to, "subject": subject, "cc": cc, "bcc": bcc},
        type=ToolResultType.SUCCESS,
    )


async def tool_get_email_accounts(session: ToolSession) -> ToolResult:
    """List configured Mail.app accounts and their mailboxes."""
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Mail tool is only available on macOS.", type=ToolResultType.ERROR)

    script = """
set output to ""
tell application "Mail"
    set allAccounts to every account
    set accountList to {}
    repeat with anAccount in allAccounts
        set accountName to name of anAccount
        set accountEmail to email address of anAccount
        set mailboxNames to {}
        repeat with mb in every mailbox of anAccount
            set end of mailboxNames to name of mb
        end repeat
        set AppleScript's text item delimiters to ","
        set mbStr to mailboxNames as string
        set AppleScript's text item delimiters to ""
        set end of accountList to accountName & "|||" & accountEmail & "|||" & mbStr
    end repeat
    set AppleScript's text item delimiters to "\\n"
    set output to accountList as string
    set AppleScript's text item delimiters to ""
end tell
output
"""

    try:
        stdout, stderr, rc = await _run_applescript(script, timeout=20)
    except asyncio.TimeoutError:
        return ToolResult(output="Mail.app query timed out.", type=ToolResultType.ERROR)
    except Exception as exc:
        return ToolResult(output=f"Failed to get accounts: {exc}", type=ToolResultType.ERROR)

    err_msg = _check_applescript_error(stderr, rc)
    if err_msg:
        return ToolResult(
            output=err_msg,
            type=ToolResultType.ERROR,
            action_needed=_automation_action(stderr, "Mail accounts"),
        )

    accounts: list[dict[str, Any]] = []
    for line in stdout.strip().splitlines():
        parts = line.split("|||")
        if len(parts) >= 2:
            accounts.append({
                "name": parts[0].strip(),
                "email": parts[1].strip(),
                "mailboxes": [m.strip() for m in parts[2].split(",")] if len(parts) > 2 else [],
            })

    return ToolResult(
        output=f"Found {len(accounts)} Mail account(s).",
        metadata={"accounts": accounts, "count": len(accounts)},
        type=ToolResultType.SUCCESS,
    )
