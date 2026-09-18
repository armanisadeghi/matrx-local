"""ONE interface for "the coding sessions on this Mac", whoever wrote them.

Arman, 2026-09-17: *"coding sessions is one feature"*. Claude Code, Codex,
Cursor and VS Code sessions are the same thing to the person looking at the
screen, so the screen asks ONE question and gets ONE list. What differs between
providers is only where the sessions are kept and how much each provider is
willing to say about them — and that difference lives behind this interface, in
one adapter per provider, never in the screen and never in the overview.

WHAT AN ADAPTER OWES. Exactly one method that matters: ``listing()`` returns
the provider's sessions reduced to :class:`SessionSummary` — the same fields
for every provider — plus the same index report the Claude index gives
(``cold`` / ``refreshing`` / ``fresh``), so a provider whose first index is
still building says so in the words the screen already knows.

WHAT AN ADAPTER MUST NOT DO. It never asks the server anything and never
judges cloud state. Whether AI Matrx holds a session, whether a delivery is
queued, and whether the local copy is newer are one join done once for every
provider in :mod:`app.services.coding_sessions.overview`. An adapter that
answered that itself would be four chances to disagree about the same fact.

HONESTY IS PART OF THE CONTRACT. ``note`` is where a provider says what it
cannot show — a chat store that keeps no timestamps, a session count it can
only bound, an editor with no resume command. A provider that lists nothing and
says nothing is a bug; a provider that lists nothing and says WHY is a state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Every provider the bridge, the hook ingress and the readiness facade already
# name, in the order the screen shows them.
PROVIDERS: tuple[str, ...] = ("claude_code", "codex", "cursor", "vscode")

DISPLAY_NAMES = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "vscode": "VS Code",
}


@dataclass(frozen=True)
class SessionSummary:
    """One coding session, in the ONE shape every provider produces."""

    provider: str
    session_id: str
    title: str
    last_activity_at: int
    title_source: str | None = None
    project: str | None = None
    # None where a provider genuinely has no size for a session (a Cursor
    # chat is rows in a shared database, not a file). Never a 0 standing in.
    bytes: int | None = 0
    on_disk: bool = False
    # Pins only exist where the provider has them. ``None`` means "this
    # provider has no such concept", which is not the same as "not pinned".
    pinned: bool | None = None
    pinned_rank: int | None = None
    category: str | None = None
    archived: bool = False
    # True/False only for Claude Code, whose sidebar is a separate ledger from
    # its transcripts. ``None`` everywhere else: a provider without a sidebar
    # cannot be "missing from" one.
    in_claude_sidebar: bool | None = None
    # Extra identities the same session is known by on the server (Codex forks
    # a thread on context rollover, so a thread id and a session id can both
    # name it). Joined against the cloud inventory in addition to session_id.
    alias_ids: tuple[str, ...] = ()
    # Provider-specific facts the row may show. Never a state, never a count
    # the screen adds up across providers.
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderListing:
    """One provider's answer: its sessions, its index state, its honesty."""

    provider: str
    rows: list[SessionSummary] = field(default_factory=list)
    # Same shape and same three states as the Claude index report.
    index: dict[str, Any] = field(default_factory=dict)
    totals: dict[str, Any] = field(default_factory=dict)
    # What this provider cannot show, in a sentence a person can act on.
    # None only when there is genuinely nothing missing.
    note: str | None = None
    # Whether the provider itself has pins and native single-session resume.
    # The screen renders an absent control as absent, never as dead.
    supports_pins: bool = False
    supports_resume: bool = False
    # True when this provider's sessions are listed from local evidence at
    # all. False means the rows below are whatever the bridge saw, not a scan.
    lists_locally: bool = True


@runtime_checkable
class SessionProvider(Protocol):
    """The whole contract. Four implementations, one shape."""

    provider: str

    async def listing(self) -> ProviderListing:
        """This provider's sessions from local evidence. Never asks a server."""
        ...

    def start_refresh(self) -> bool:
        """Kick a background index refresh; return whether one is in flight."""
        ...


_REGISTRY: dict[str, SessionProvider] = {}


def register(adapter: SessionProvider) -> None:
    _REGISTRY[adapter.provider] = adapter


def session_providers() -> list[SessionProvider]:
    """Every registered adapter, in the screen's provider order."""
    _install_default_adapters()
    return [_REGISTRY[name] for name in PROVIDERS if name in _REGISTRY]


def session_provider(provider: str) -> SessionProvider | None:
    _install_default_adapters()
    return _REGISTRY.get(provider)


def _install_default_adapters() -> None:
    """Register the shipped adapters once, lazily.

    Lazily because each adapter imports its provider's reader, and importing
    four readers to answer one provider's question would put a cold Codex walk
    in the path of a Claude-only request.
    """
    if _REGISTRY:
        return
    from app.services.coding_sessions.claude_provider import ClaudeCodeSessionProvider
    from app.services.coding_sessions.codex_provider import get_codex_provider
    from app.services.coding_sessions.editor_providers import (
        get_cursor_provider,
        get_vscode_provider,
    )

    # The process-wide instance in each case: an adapter owns its refresh
    # single-flight and its index snapshot cache, and a second instance would
    # be a second background walk of the same tree.
    for adapter in (
        ClaudeCodeSessionProvider(),
        get_codex_provider(),
        get_cursor_provider(),
        get_vscode_provider(),
    ):
        register(adapter)


def _reset_registry_for_tests(adapters: list[SessionProvider] | None = None) -> None:
    _REGISTRY.clear()
    for adapter in adapters or []:
        register(adapter)


__all__ = [
    "DISPLAY_NAMES",
    "PROVIDERS",
    "ProviderListing",
    "SessionProvider",
    "SessionSummary",
    "register",
    "session_provider",
    "session_providers",
]
