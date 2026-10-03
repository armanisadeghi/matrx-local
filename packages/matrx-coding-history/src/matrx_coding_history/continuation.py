"""How a local coding-agent session is continued — THE one wording.

The history-import result and every overview row read this module, so the
command a person copies and the sentence that qualifies it cannot drift apart.
It was computed and thrown away before (audit 2026-09-14, UI-03): the backend
said how to resume a session and no screen ever rendered it.

ONE FEATURE, FOUR PROVIDERS (Arman, 2026-09-17: "coding sessions is one
feature"). Every provider answers the same question here, including the two
that answer "you cannot". A provider without native resume gets ``command:
None`` and a sentence that says what is actually true — never a fabricated
command, and never a silent blank where a button used to be.
"""

from __future__ import annotations

from typing import Any

CLAUDE_CODE_NOTE = (
    "Open the original local Claude transcript with claude --resume <session-id> "
    "only while that local file, workspace, and login remain available."
)

CODEX_NOTE = (
    "Open the original local Codex thread with codex resume <session-id> only "
    "while that local rollout file, workspace, and login remain available."
)

# Cursor and VS Code chats live inside the editor's own workspace state. There
# is no documented command or URL that reopens one thread, so the honest answer
# is that this Mac cannot reopen it from here — not a command that fails.
CURSOR_NOTE = (
    "Cursor has no command or link that reopens one chat: open the workspace in "
    "Cursor and pick the chat from its own history. The mirrored conversation in "
    "AI Matrx is the copy you can open from here."
)

VSCODE_NOTE = (
    "VS Code has no command or link that reopens one chat: open the workspace in "
    "VS Code and pick the chat from its own history. The mirrored conversation in "
    "AI Matrx is the copy you can open from here."
)

_RESUME_COMMANDS = {
    "claude_code": ("claude --resume {session_id}", CLAUDE_CODE_NOTE),
    "codex": ("codex resume {session_id}", CODEX_NOTE),
}

_NO_RESUME_NOTES = {
    "cursor": CURSOR_NOTE,
    "vscode": VSCODE_NOTE,
}

# Kept so the pre-2026-09-17 spelling of the Claude-only note still resolves
# for callers that imported it by name.
CONTINUATION_NOTE = CLAUDE_CODE_NOTE


def continuation_hint(
    session_id: str | None = None, provider: str = "claude_code"
) -> dict[str, Any]:
    """The copyable command for one session, plus the caveat that binds it.

    ``command`` is None exactly when the provider has no native resume. The
    screen renders the note either way, so "no native resume" reads as a fact
    with a reason rather than as a missing control.
    """
    template = _RESUME_COMMANDS.get(provider)
    if template is not None:
        pattern, note = template
        return {
            "command": pattern.format(session_id=session_id or "<session-id>"),
            "note": note,
            "native_resume": True,
        }
    note = _NO_RESUME_NOTES.get(provider)
    if note is None:
        # An unknown provider is not a provider without resume: say which.
        return {
            "command": None,
            "note": (
                f"AI Matrx does not know how to continue a {provider} session "
                "locally, so it will not guess a command."
            ),
            "native_resume": False,
        }
    return {"command": None, "note": note, "native_resume": False}
