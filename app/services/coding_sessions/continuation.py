"""How a local coding-agent session is continued — THE one wording.

The history-import result and every overview row read this module, so the
command a person copies and the sentence that qualifies it cannot drift apart.
It was computed and thrown away before (audit 2026-09-14, UI-03): the backend
said how to resume a session and no screen ever rendered it.
"""

from __future__ import annotations

CONTINUATION_NOTE = (
    "Open the original local Claude transcript with claude --resume <session-id> "
    "only while that local file, workspace, and login remain available."
)


def continuation_hint(session_id: str | None = None) -> dict[str, str]:
    """The copyable command for one session, plus the caveat that binds it."""
    return {
        "command": (
            f"claude --resume {session_id}" if session_id else "claude --resume <session-id>"
        ),
        "note": CONTINUATION_NOTE,
    }
