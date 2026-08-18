"""User-review delegated tools — the client half of an authorization boundary.

A normal delegated call is EXECUTED by this engine: the sweep resolves the
tool in ``app.tools.catalog`` and runs it through the dispatcher. A
**user-review** call is executed by nothing. It is parked, rendered to the
human in the desktop UI, and resolved only by that human's explicit click.

``google_email_send`` is the entire category today, and its shape is
deliberate rather than incidental:

* The tool has **no server executor anywhere in the platform** (it is absent
  from ``aidream/tools/_generated_declarations.py``). That absence IS the
  Gmail authorization boundary — with no server path, an agent-authored
  ``user_confirmed`` cannot exist, let alone authorize anything.
* Therefore the desktop must never dispatch it. Parking is not a fallback for
  "we have no handler"; parking IS the handler.
* The engine never sends mail. It holds the proposal, hands it to the UI, and
  turns the user's decision into a tool result. The reviewed bytes go from the
  card straight to aidream's ``/api/google-workspace/gmail/send-reviewed`` with
  the user's own JWT — exactly as matrx-frontend does it.

Never add a ``matrx-local`` dispatcher entry for a tool listed here, and never
accept a "the user confirmed" flag from the agent's arguments.
"""

from __future__ import annotations

from typing import Any

# Cloud tool names this engine parks for human review instead of executing.
USER_REVIEW_TOOLS: frozenset[str] = frozenset({"google_email_send"})

# Cloud tool name → the card the desktop UI renders for it.
REVIEW_KINDS: dict[str, str] = {"google_email_send": "email_review"}

# The only decisions a human can hand back. `sent` requires that the UI has
# already completed the reviewed send; every other outcome means nothing left
# the user's mailbox.
REVIEW_OUTCOMES: frozenset[str] = frozenset(
    {"sent", "declined", "cancelled", "error"}
)

# Address list caps mirror matrx-frontend's `googleEmailSendArgsSchema`.
_MAX_CC = 20
_MAX_SUBJECT = 998
_MAX_BODY = 100_000


def is_user_review_tool(tool_name: str) -> bool:
    return tool_name in USER_REVIEW_TOOLS


def _text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def normalize_review_arguments(tool_name: str, args: Any) -> dict[str, Any]:
    """Turn an agent's raw arguments into the fields the card renders.

    Never raises: a malformed proposal still deserves a card the user can
    correct or decline. The card — not this function — is the gate.
    """
    payload = args if isinstance(args, dict) else {}
    if tool_name != "google_email_send":
        return dict(payload)
    raw_cc = payload.get("cc")
    cc: list[str] = []
    if isinstance(raw_cc, list):
        for entry in raw_cc[:_MAX_CC]:
            if isinstance(entry, str) and entry.strip():
                cc.append(entry.strip())
    elif isinstance(raw_cc, str) and raw_cc.strip():
        cc = [part.strip() for part in raw_cc.split(",") if part.strip()][:_MAX_CC]
    return {
        "to": _text(payload.get("to"), limit=320).strip(),
        "cc": cc,
        "subject": _text(payload.get("subject"), limit=_MAX_SUBJECT),
        "body": _text(payload.get("body"), limit=_MAX_BODY),
    }


def build_review_output(tool_name: str, decision: dict[str, Any]) -> dict[str, Any]:
    """Map the UI's decision onto the tool's documented result shape.

    Mirrors matrx-frontend's ``GoogleEmailSendResult``: declining is a normal
    outcome (``declined``), dismissing is ``cancelled``, and a failed send is
    ``{sent: false, error}`` — never a success, and never silence.
    """
    outcome = str(decision.get("outcome") or "").strip()
    if tool_name != "google_email_send":
        return {"outcome": outcome}

    if outcome == "sent":
        cc = decision.get("cc")
        return {
            "sent": True,
            "message_id": decision.get("message_id") or None,
            "to": decision.get("to") or None,
            "cc": [str(entry) for entry in cc] if isinstance(cc, list) else [],
            "subject": decision.get("subject") or None,
            "edited": bool(decision.get("edited")),
            "from_email": decision.get("from_email") or None,
        }
    if outcome == "declined":
        return {"sent": False, "declined": True}
    if outcome == "cancelled":
        return {"sent": False, "cancelled": True}
    return {
        "sent": False,
        "error": str(decision.get("error") or "The message was not sent."),
    }
