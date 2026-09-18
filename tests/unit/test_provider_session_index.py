"""Coding sessions is ONE feature: four providers, one list, one judgement.

Arman, 2026-09-17: *"coding sessions is one feature"*. Until that day the
screen listed Claude Code only, while AI Matrx already held 2,814 Codex, 4
Cursor and 2 VS Code sessions that no local screen would ever show. These are
the guards for the four adapters and the one join behind them.

THE CODEX GUARDS RUN ON REAL FILES. ``tests/fixtures/codex/*.jsonl`` are copies
of two ACTUAL rollouts from this machine, redacted by the committed
``tests/fixtures/codex/make_fixtures.py`` — real ``session_meta``, real
``ordinal`` numbering, real ``apply_patch`` headers, every string over 24
characters dropped and the real cwd rewritten. A hand-written fixture would
have proved only that the parser matches the fixture's author.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from app.services.coding_sessions import codex_session_index as codex
from app.services.coding_sessions import overview as overview_module
from app.services.coding_sessions import session_providers
from app.services.coding_sessions.codex_provider import CodexSessionProvider
from app.services.coding_sessions.editor_providers import (
    CursorSessionProvider,
    VSCodeSessionProvider,
    _read_only_connection,
)
from app.services.coding_sessions.provider_index_store import ProviderIndexStore
from app.services.coding_sessions.session_providers import (
    ProviderListing,
    SessionSummary,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _codex_tree(tmp_path: Path) -> Path:
    """The two real rollouts, in Codex's own YYYY/MM/DD layout."""
    root = tmp_path / "codex" / "sessions"
    day = root / "2026" / "07" / "24"
    day.mkdir(parents=True)
    names = {
        "session-with-writes.jsonl": (
            "rollout-2026-07-24T08-03-11-019f94a6-dd83-71f0-aee4-4456aff06c27.jsonl"
        ),
        "session-second.jsonl": (
            "rollout-2026-06-25T09-38-21-019effa5-9237-78d3-a133-e723d9ae8276.jsonl"
        ),
    }
    for fixture, real_name in names.items():
        (day / real_name).write_bytes((FIXTURES / fixture).read_bytes())
    return root


# ── Codex: the reader ───────────────────────────────────────────────────────


def test_a_real_rollout_reduces_to_codexs_own_facts(tmp_path: Path) -> None:
    """Session id, cwd and an EXACT entry count, from two bounded reads."""
    root = _codex_tree(tmp_path)
    path = next(root.rglob("*019f94a6*"))
    stamp = path.stat()
    entry = codex.read_rollout(path, mtime_ns=stamp.st_mtime_ns, size=stamp.st_size)

    assert entry.session_id == "019f94a6-dd83-71f0-aee4-4456aff06c27"
    # The thread id comes from the file name, and is what the server may also
    # know the session by after a context rollover fork.
    assert entry.thread_id == "019f94a6-dd83-71f0-aee4-4456aff06c27"
    assert entry.cwd == "/Users/fixture/code/demo-repo"
    assert entry.project == "demo-repo"
    assert entry.unreadable is False
    # 34 lines in the fixture, and the count comes from the LAST entry's
    # ordinal — never from reading the middle of a 30 GB tree.
    assert entry.entries == 34
    assert entry.last_activity_at > entry.started_at > 0
    assert entry.bytes == stamp.st_size


def test_the_middle_of_a_rollout_is_never_read(tmp_path: Path) -> None:
    """The count and the stamps come from the head and the tail ONLY.

    This is the guard on the one reason the Codex list is affordable at all:
    5,041 files and 30.33 GB on this Mac (measured 2026-09-17). Reading them
    whole to list them would repeat the exact mistake the Claude screen already
    paid for — 31.76 s and 58.96 s inside one request, 1,209 s on a fresh
    engine.

    It is proved by making the middle UNREADABLE: half a megabyte of bytes that
    are not JSON at all, between a real ``session_meta`` head and a real tail.
    Any implementation that parses the middle, or counts lines, gets this
    wrong; one that reads the first 64 KB and the last 256 KB cannot.
    """
    root = _codex_tree(tmp_path)
    source = next(root.rglob("*019effa5*"))
    entries = [line for line in source.read_text().splitlines() if line.strip()]
    head, tail = entries[:3], entries[3:]
    # The real last ordinal, which is what the entry count must come from.
    last_ordinal = max(
        json.loads(line)["ordinal"]
        for line in entries
        if isinstance(json.loads(line).get("ordinal"), int)
    )
    garbage = "\n".join("!! not json at all !!" + "x" * 200 for _ in range(2_500))
    padded = root / "2026" / "07" / "24" / (
        "rollout-2026-07-24T09-00-00-019effa5-9237-78d3-a133-e723d9ae8277.jsonl"
    )
    padded.write_text("\n".join(head) + "\n" + garbage + "\n" + "\n".join(tail) + "\n")
    stamp = padded.stat()
    assert stamp.st_size > codex.HEAD_BYTES + codex.TAIL_BYTES, (
        "the fixture must be bigger than both read windows or it proves nothing"
    )

    entry = codex.read_rollout(padded, mtime_ns=stamp.st_mtime_ns, size=stamp.st_size)

    assert entry.unreadable is False, "the head was readable; this is not an error"
    assert entry.entries == last_ordinal + 1
    # And the identity still came from the head, the activity from the tail.
    assert entry.session_id == "019effa5-9237-78d3-a133-e723d9ae8276"
    assert entry.cwd == "/Users/fixture/code/demo-repo"
    assert entry.last_activity_at == codex.parse_iso_ms(
        json.loads(tail[-1])["timestamp"]
    )


def test_codex_thread_names_beat_the_first_prompt(tmp_path: Path) -> None:
    """A thread a person NAMED reads with that name, and says so."""
    root = _codex_tree(tmp_path)
    index = tmp_path / "codex" / "session_index.jsonl"
    index.write_text(
        json.dumps(
            {
                "id": "019f94a6-dd83-71f0-aee4-4456aff06c27",
                "thread_name": "Sync the generated types",
                "updated_at": "2026-07-24T16:00:00Z",
            }
        )
        + "\n"
    )
    rows = []
    for path in sorted(root.rglob("rollout-*.jsonl")):
        stamp = path.stat()
        rows.append(
            codex.read_rollout(path, mtime_ns=stamp.st_mtime_ns, size=stamp.st_size)
        )
    merged = codex.merge_entries(rows, codex.read_thread_names(index))

    named = merged["019f94a6-dd83-71f0-aee4-4456aff06c27"]
    assert named.title == "Sync the generated types"
    assert named.title_source == "codex_thread_name"
    # The thread Codex never named falls back to the prompt, and says which.
    other = merged["019effa5-9237-78d3-a133-e723d9ae8276"]
    assert other.title_source == "first_prompt"


def test_an_unreadable_rollout_is_counted_and_explains_itself(tmp_path: Path) -> None:
    """A file with no session_meta is a row with a reason, never a silent drop."""
    root = tmp_path / "sessions" / "2026" / "09" / "17"
    root.mkdir(parents=True)
    broken = root / "rollout-2026-09-17T00-00-00-11111111-2222-3333-4444-555555555555.jsonl"
    broken.write_text('{"type":"event_msg","payload":{"type":"task_started"}}\n')
    stamp = broken.stat()

    entry = codex.read_rollout(broken, mtime_ns=stamp.st_mtime_ns, size=stamp.st_size)
    assert entry.unreadable is True
    assert entry.unreadable_reason
    assert "session_meta" in entry.unreadable_reason
    # Still identified, still listed: its file name carries the thread id.
    assert entry.session_id == "11111111-2222-3333-4444-555555555555"


# ── Codex: the adapter and its incremental index ────────────────────────────


def _codex_adapter(tmp_path: Path, root: Path) -> CodexSessionProvider:
    return CodexSessionProvider(
        store=ProviderIndexStore("codex", tmp_path / "codex-index.sqlite3"),
        rollout_root=root,
        thread_index=tmp_path / "codex" / "session_index.jsonl",
    )


@pytest.mark.anyio
async def test_the_codex_index_re_reads_only_what_moved(tmp_path: Path) -> None:
    """The second refresh reads ZERO files; touching one reads exactly one.

    The whole point of persisting the index: a cold engine start after the
    first run must not re-read 30.33 GB.
    """
    root = _codex_tree(tmp_path)
    adapter = _codex_adapter(tmp_path, root)

    first = await adapter.refresh()
    assert first["changed_files"] == 2

    second = await adapter.refresh()
    assert second["changed_files"] == 0, "a refresh re-read files nothing had touched"
    assert second["revision"] == first["revision"] + 1

    touched = next(root.rglob("*019f94a6*"))
    touched.write_bytes(touched.read_bytes() + b'{"ordinal":99,"timestamp":"2026-09-17T00:00:00.000Z","type":"event_msg","payload":{"type":"task_complete"}}\n')
    third = await adapter.refresh()
    assert third["changed_files"] == 1

    listing = await adapter.listing()
    updated = next(
        row for row in listing.rows
        if row.session_id == "019f94a6-dd83-71f0-aee4-4456aff06c27"
    )
    assert updated.facts["entries"] == 100


@pytest.mark.anyio
async def test_a_removed_rollout_leaves_the_index(tmp_path: Path) -> None:
    """A deleted rollout is pruned, not kept as a row for a file that is gone."""
    root = _codex_tree(tmp_path)
    adapter = _codex_adapter(tmp_path, root)
    await adapter.refresh()
    assert len((await adapter.listing()).rows) == 2

    next(root.rglob("*019effa5*")).unlink()
    result = await adapter.refresh()
    assert result["removed_files"] == 1
    assert len((await adapter.listing()).rows) == 1


@pytest.mark.anyio
async def test_codex_rows_never_fake_a_pin_or_an_archive(tmp_path: Path) -> None:
    """An absent concept is None, never a false that reads as 'not pinned'."""
    root = _codex_tree(tmp_path)
    adapter = _codex_adapter(tmp_path, root)
    await adapter.refresh()
    listing = await adapter.listing()

    assert listing.supports_pins is False
    for row in listing.rows:
        assert row.pinned is None
        assert row.pinned_rank is None
        assert row.in_claude_sidebar is None


@pytest.mark.anyio
async def test_a_codex_thread_with_no_name_anywhere_is_counted_not_hidden(
    tmp_path: Path,
) -> None:
    """Measured on this Mac: 1,831 Codex sessions, 33 with no name at all.

    Neither Codex's own thread name nor a readable first prompt, so they are
    listed by id — and the count is said out loud rather than leaving a block
    of rows that look like sessions about nothing.
    """
    root = tmp_path / "sessions" / "2026" / "09" / "17"
    root.mkdir(parents=True)
    nameless = root / (
        "rollout-2026-09-17T00-00-00-33333333-3333-4333-8333-333333333333.jsonl"
    )
    nameless.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-17T00:00:00.000Z",
                "ordinal": 0,
                "type": "session_meta",
                "payload": {
                    "session_id": "33333333-3333-4333-8333-333333333333",
                    "cwd": "/Users/fixture/code/demo-repo",
                    "timestamp": "2026-09-17T00:00:00.000Z",
                    "thread_source": "user",
                },
            }
        )
        + "\n"
    )
    adapter = CodexSessionProvider(
        store=ProviderIndexStore("codex", tmp_path / "idx.sqlite3"),
        rollout_root=tmp_path / "sessions",
        thread_index=tmp_path / "absent-index.jsonl",
    )
    await adapter.refresh()
    listing = await adapter.listing()

    row = listing.rows[0]
    assert row.title == "Codex thread 33333333"
    assert row.title_source is None
    assert listing.totals["unnamed"] == 1
    assert "1 Codex thread(s) have no name in Codex" in (listing.note or "")


@pytest.mark.anyio
async def test_a_cold_codex_index_says_cold_in_the_screens_own_word(
    tmp_path: Path,
) -> None:
    """Before the first refresh completes the state is ``cold``, then ``fresh``."""
    root = _codex_tree(tmp_path)
    adapter = _codex_adapter(tmp_path, root)

    cold = await adapter.listing()
    assert cold.index["state"] == "cold"
    assert cold.rows == []

    await adapter.refresh()
    warm = await adapter.listing()
    assert warm.index["state"] in {"fresh", "refreshing"}
    assert warm.index["files_read"] == 2


# ── Cursor ──────────────────────────────────────────────────────────────────


def _cursor_state(tmp_path: Path) -> Path:
    """Cursor's two real stores, in Cursor's real schema.

    The columns and their nullability are the ones measured on this Mac:
    ``lastUpdatedAt`` is NULL for 1,726 of 4,570 rows, which is exactly why
    the adapter must read ``recency`` instead.
    """
    state = tmp_path / "Cursor"
    global_storage = state / "User" / "globalStorage"
    global_storage.mkdir(parents=True)
    headers = sqlite3.connect(global_storage / "state.vscdb")
    headers.execute(
        """CREATE TABLE composerHeaders (
             composerId TEXT PRIMARY KEY, workspaceId TEXT, createdAt INTEGER,
             lastUpdatedAt INTEGER, isArchived INTEGER, isSubagent INTEGER,
             recency INTEGER, checkpointAt INTEGER, value TEXT,
             subagentTypeName TEXT)"""
    )
    rows = [
        (
            "aaaaaaaa-0000-0000-0000-000000000001",
            "8346184a",
            1_789_000_000_000,
            None,
            0,
            0,
            1_789_000_500_000,
            json.dumps(
                {
                    "workspaceIdentifier": {
                        "uri": {"fsPath": "/Users/fixture/code/matrx-frontend"}
                    }
                }
            ),
        ),
        (
            "aaaaaaaa-0000-0000-0000-000000000002",
            "62ff9390",
            1_788_000_000_000,
            1_788_000_900_000,
            1,
            0,
            1_788_000_900_000,
            json.dumps(
                {"workspaceIdentifier": {"uri": {"fsPath": "/Users/fixture/code/aidream"}}}
            ),
        ),
        # An empty-window chat: Cursor records no folder at all for it.
        (
            "aaaaaaaa-0000-0000-0000-000000000003",
            "1789612885917",
            1_787_000_000_000,
            None,
            0,
            1,
            1_787_000_100_000,
            json.dumps({}),
        ),
    ]
    headers.executemany(
        """INSERT INTO composerHeaders
             (composerId, workspaceId, createdAt, lastUpdatedAt, isArchived,
              isSubagent, recency, value)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    headers.commit()
    headers.close()

    search = sqlite3.connect(global_storage / "conversation-search.db")
    search.execute(
        """CREATE TABLE conversations (
             fts_rowid INTEGER PRIMARY KEY, source TEXT NOT NULL, scope TEXT NOT NULL,
             id TEXT NOT NULL, title TEXT NOT NULL, branches TEXT NOT NULL,
             updated_at INTEGER NOT NULL, is_archived INTEGER NOT NULL,
             root_fingerprint TEXT, cache_fingerprint TEXT)"""
    )
    search.executemany(
        "INSERT INTO conversations (source, scope, id, title, branches, updated_at, is_archived)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("local", "", rows[0][0], "Table pagination", "[]", 1_789_000_500_000, 0),
            ("local", "", rows[1][0], "Scope fade audit", "[]", 1_788_000_900_000, 1),
            # 403 of 2,974 real rows have an empty title. One here too.
            ("local", "", rows[2][0], "", "[]", 1_787_000_100_000, 0),
        ],
    )
    search.commit()
    search.close()
    return state


@pytest.mark.anyio
async def test_cursor_chats_are_listed_from_cursors_own_ledger(tmp_path: Path) -> None:
    adapter = CursorSessionProvider(
        store=ProviderIndexStore("cursor", tmp_path / "cursor-index.sqlite3"),
        state_dir=_cursor_state(tmp_path),
    )
    await adapter.refresh()
    listing = await adapter.listing()

    rows = {row.session_id: row for row in listing.rows}
    assert len(rows) == 3
    named = rows["aaaaaaaa-0000-0000-0000-000000000001"]
    assert named.title == "Table pagination"
    assert named.title_source == "cursor_title"
    assert named.project == "matrx-frontend"
    # recency, NOT lastUpdatedAt: that column is NULL on this row, as it is on
    # 1,726 of 4,570 real rows.
    assert named.last_activity_at == 1_789_000_500_000
    assert rows["aaaaaaaa-0000-0000-0000-000000000002"].archived is True


@pytest.mark.anyio
async def test_cursor_never_invents_a_size_a_count_a_pin_or_a_folder(
    tmp_path: Path,
) -> None:
    """Four absent facts, four Nones, and one sentence that names them.

    A Cursor chat is rows in a shared database: a 0 in the size column would
    read as "empty", and a message count costs 3 m 36 s against a 27 GB file.
    """
    adapter = CursorSessionProvider(
        store=ProviderIndexStore("cursor", tmp_path / "cursor-index.sqlite3"),
        state_dir=_cursor_state(tmp_path),
    )
    await adapter.refresh()
    listing = await adapter.listing()
    rows = {row.session_id: row for row in listing.rows}

    for row in listing.rows:
        assert row.bytes is None, "a Cursor chat has no size; 0 would read as empty"
        assert row.pinned is None, "Cursor has no chat pins"
        assert row.facts["entries"] is None, "Cursor exposes no cheap message count"

    empty_window = rows["aaaaaaaa-0000-0000-0000-000000000003"]
    assert empty_window.project is None
    # No title in Cursor itself: listed by id, and the source says it is not
    # a name anybody gave it.
    assert empty_window.title.startswith("Cursor chat ")
    assert empty_window.title_source is None

    assert listing.supports_pins is False
    assert listing.supports_resume is False
    note = listing.note or ""
    assert "size" in note and "message count" in note
    assert "1 Cursor chat(s) have no name" in note
    assert "empty Cursor window" in note


@pytest.mark.anyio
async def test_cursor_missing_is_not_cursor_empty(tmp_path: Path) -> None:
    """"Not installed" and "no chats" must never render the same."""
    adapter = CursorSessionProvider(
        store=ProviderIndexStore("cursor", tmp_path / "cursor-index.sqlite3"),
        state_dir=tmp_path / "no-cursor-here",
    )
    await adapter.refresh()
    listing = await adapter.listing()
    assert listing.rows == []
    assert listing.note
    assert "not on this Mac" in listing.note


def test_cursors_store_is_opened_read_only(tmp_path: Path) -> None:
    """Arman's editor state is his: the connection cannot write to it.

    ``mode=ro`` is the guarantee, not a convention — a bug in this lane must
    not be able to damage Cursor's 27 GB store.
    """
    state = _cursor_state(tmp_path)
    path = state / "User" / "globalStorage" / "state.vscdb"
    before = path.read_bytes()
    with _read_only_connection(path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM composerHeaders")
    assert path.read_bytes() == before


# ── VS Code ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_vscode_lists_nothing_and_says_exactly_why(tmp_path: Path) -> None:
    """An empty tab with no reason reads as a bug. This reads as the truth."""
    adapter = VSCodeSessionProvider(
        state_dir=tmp_path / "Code", extensions_dir=tmp_path / "extensions"
    )
    listing = await adapter.listing()
    assert listing.rows == []
    assert listing.lists_locally is False
    assert listing.note and "not installed on this Mac" in listing.note
    # Never a permanent "cold": there is no index building behind this.
    assert listing.index["state"] == "fresh"
    assert listing.index["refreshing"] is False

    (tmp_path / "Code").mkdir()
    editor_only = await adapter.listing()
    assert "extension is not installed" in (editor_only.note or "")

    (tmp_path / "extensions" / "aimatrx.aimatrx-1.0.0").mkdir(parents=True)
    installed = await adapter.listing()
    assert "keeps no local record" in (installed.note or "")


# ── The one join, over every provider ───────────────────────────────────────


class _FakeProvider:
    def __init__(self, provider: str, rows: list[SessionSummary], **kwargs) -> None:
        self.provider = provider
        self._rows = rows
        self._kwargs = kwargs

    async def listing(self) -> ProviderListing:
        return ProviderListing(
            provider=self.provider,
            rows=self._rows,
            index={
                "state": "fresh",
                "refreshing": False,
                "files_read": len(self._rows),
                "updated_at": None,
                "changed_files": 0,
                "duration_seconds": None,
                "limit_reached": False,
                "unreadable": 0,
                "error": None,
            },
            totals={"sessions": len(self._rows)},
            **self._kwargs,
        )

    def start_refresh(self) -> bool:
        return False


class _ExplodingProvider:
    provider = "cursor"

    async def listing(self) -> ProviderListing:
        raise RuntimeError("cursor store is mid-migration")

    def start_refresh(self) -> bool:
        return False


def _row(provider: str, session_id: str, **kwargs) -> SessionSummary:
    return SessionSummary(
        provider=provider,
        session_id=session_id,
        title=f"{provider} session",
        last_activity_at=1_789_000_000_000,
        **kwargs,
    )


def _install(monkeypatch: pytest.MonkeyPatch, adapters: list[object]) -> None:
    session_providers._reset_registry_for_tests(adapters)  # type: ignore[arg-type]
    monkeypatch.setattr(
        session_providers, "_install_default_adapters", lambda: None
    )


def _no_queue_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _queue(_provider="claude_code"):
        return {}, {"checked": True, "reason": None, "detail": None}

    async def _totals():
        return (0, 0), {"checked": True, "reason": None, "detail": None}

    monkeypatch.setattr(overview_module, "_queue_by_session", _queue)
    monkeypatch.setattr(overview_module, "_queue_totals", _totals)


@pytest.mark.anyio
async def test_the_overview_lists_every_provider_with_provider_on_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        [
            _FakeProvider("claude_code", [_row("claude_code", "claude-1")], supports_pins=True),
            _FakeProvider("codex", [_row("codex", "codex-1")], supports_resume=True),
        ],
    )
    _no_queue_patches(monkeypatch)

    async def _cloud(provider="claude_code", *, force=False):
        return {}, {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 0,
            "checked_at": "2026-09-17T00:00:00+00:00",
        }

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)

    out = await overview_module.overview()
    assert out["listed_providers"] == ["claude_code", "codex"]
    assert {row["provider"] for row in out["conversations"]} == {"claude_code", "codex"}
    assert out["totals"]["conversations"] == 2
    assert [block["provider"] for block in out["providers"]] == ["claude_code", "codex"]

    # Continue is per provider, and a provider without native resume does not
    # get a fabricated command.
    codex_row = next(r for r in out["conversations"] if r["provider"] == "codex")
    assert codex_row["continuation"]["command"] == "codex resume codex-1"
    assert codex_row["continuation"]["native_resume"] is True
    claude_row = next(r for r in out["conversations"] if r["provider"] == "claude_code")
    assert claude_row["continuation"]["command"] == "claude --resume claude-1"


@pytest.mark.anyio
async def test_a_codex_binding_never_marks_a_claude_session_in_the_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inventory is asked PER PROVIDER, and answers do not cross over.

    Two providers can hold the same id string (an imported session, a reused
    uuid). One shared inventory cache would have told the screen that a Claude
    Code session nobody had mirrored was safely in AI Matrx.
    """
    shared_id = "same-id-both-providers"
    _install(
        monkeypatch,
        [
            _FakeProvider("claude_code", [_row("claude_code", shared_id)]),
            _FakeProvider("codex", [_row("codex", shared_id)]),
        ],
    )
    _no_queue_patches(monkeypatch)

    async def _cloud(provider="claude_code", *, force=False):
        meta = {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 0,
            "checked_at": "2026-09-17T00:00:00+00:00",
        }
        if provider == "codex":
            return (
                {
                    shared_id: {
                        "conversation_id": "conv-codex",
                        "fidelity": "full",
                        "last_seen_at": "2026-09-17T00:00:00+00:00",
                    }
                },
                {**meta, "sessions": 1},
            )
        return {}, meta

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)

    out = await overview_module.overview()
    by_provider = {row["provider"]: row for row in out["conversations"]}
    assert by_provider["codex"]["state"] == "in_cloud"
    assert by_provider["codex"]["cloud"]["conversation_id"] == "conv-codex"
    assert by_provider["claude_code"]["state"] == "not_in_cloud"
    assert by_provider["claude_code"]["cloud"] is None


@pytest.mark.anyio
async def test_a_provider_that_cannot_list_locally_is_filled_from_the_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VS Code keeps no local record, so its rows come from AI Matrx itself."""
    _install(
        monkeypatch,
        [_FakeProvider("vscode", [], lists_locally=False, note="no local record")],
    )
    _no_queue_patches(monkeypatch)

    async def _cloud(provider="vscode", *, force=False):
        return (
            {
                "vsc-1": {
                    "conversation_id": "conv-vsc",
                    "fidelity": "full",
                    "last_seen_at": "2026-09-16T10:00:00+00:00",
                    "conversation_title": "Fix the build",
                }
            },
            {
                "checked": True,
                "reason": None,
                "detail": None,
                "sessions": 1,
                "checked_at": "2026-09-17T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)

    out = await overview_module.overview()
    assert len(out["conversations"]) == 1
    row = out["conversations"][0]
    assert row["provider"] == "vscode"
    assert row["title"] == "Fix the build"
    assert row["on_disk"] is False, "a cloud-only row must not claim a local copy"
    assert row["state"] == "in_cloud"
    assert row["cloud"]["conversation_id"] == "conv-vsc"
    # Its block still carries the reason the list is not local.
    assert out["providers"][0]["lists_locally"] is False
    assert out["providers"][0]["note"] == "no local record"


@pytest.mark.anyio
async def test_one_broken_provider_never_empties_the_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that throws is that provider's state, not everybody's."""
    _install(
        monkeypatch,
        [
            _FakeProvider("claude_code", [_row("claude_code", "claude-1")]),
            _ExplodingProvider(),
        ],
    )
    _no_queue_patches(monkeypatch)

    async def _cloud(provider="claude_code", *, force=False):
        return {}, {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 0,
            "checked_at": "2026-09-17T00:00:00+00:00",
        }

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)

    out = await overview_module.overview()
    assert len(out["conversations"]) == 1
    broken = next(b for b in out["providers"] if b["provider"] == "cursor")
    assert broken["index"]["error"]
    assert "mid-migration" in broken["note"]
    # The screen's own aggregate index must report the failure, not hide it.
    assert "mid-migration" in (out["index"]["error"] or "")


@pytest.mark.anyio
async def test_one_unchecked_provider_makes_the_whole_cloud_check_unchecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial inventory is not a smaller truth — it is unsafe input."""
    _install(
        monkeypatch,
        [
            _FakeProvider("claude_code", [_row("claude_code", "claude-1")]),
            _FakeProvider("codex", [_row("codex", "codex-1")]),
        ],
    )
    _no_queue_patches(monkeypatch)

    async def _cloud(provider="claude_code", *, force=False):
        if provider == "codex":
            return {}, {
                "checked": False,
                "reason": "aidream_unreachable",
                "detail": "AI Matrx could not be reached.",
                "sessions": 0,
                "checked_at": None,
            }
        return {}, {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 3,
            "checked_at": "2026-09-17T00:00:00+00:00",
        }

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)

    out = await overview_module.overview()
    assert out["cloud"]["checked"] is False
    assert out["cloud"]["reason"] == "aidream_unreachable"
    assert "codex" in out["cloud"]["detail"]
    # And the per-provider truth is still there, unmixed.
    blocks = {b["provider"]: b for b in out["providers"]}
    assert blocks["claude_code"]["cloud"]["checked"] is True
    assert blocks["codex"]["cloud"]["checked"] is False
    # The Codex row cannot be "not in cloud" when nobody could ask.
    codex_row = next(r for r in out["conversations"] if r["provider"] == "codex")
    assert codex_row["state"] in {"queued", "unknown"}


@pytest.mark.anyio
async def test_a_cold_provider_makes_the_screen_cold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing cold-index polling keeps working when a provider joins."""
    cold = _FakeProvider("codex", [])

    async def _cold_listing() -> ProviderListing:
        return ProviderListing(
            provider="codex",
            rows=[],
            index={
                "state": "cold",
                "refreshing": True,
                "files_read": 0,
                "updated_at": None,
                "changed_files": None,
                "duration_seconds": None,
                "limit_reached": False,
                "unreadable": 0,
                "error": None,
            },
            totals={"sessions": 0},
        )

    cold.listing = _cold_listing  # type: ignore[method-assign]
    _install(monkeypatch, [_FakeProvider("claude_code", [_row("claude_code", "c1")]), cold])
    _no_queue_patches(monkeypatch)

    async def _cloud(provider="claude_code", *, force=False):
        return {}, {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 0,
            "checked_at": "2026-09-17T00:00:00+00:00",
        }

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)

    out = await overview_module.overview()
    assert out["index"]["state"] == "cold"
    assert out["index"]["refreshing"] is True


@pytest.mark.anyio
async def test_the_real_codex_adapter_reaches_the_overview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end on the REAL fixtures: Codex rows in the one list.

    Not a fake adapter: the actual reader, the actual persisted index, and the
    actual join — the path the screen takes.
    """
    root = _codex_tree(tmp_path)
    adapter = _codex_adapter(tmp_path, root)
    await adapter.refresh()
    _install(monkeypatch, [adapter])
    _no_queue_patches(monkeypatch)

    async def _cloud(provider="codex", *, force=False):
        return {}, {
            "checked": True,
            "reason": None,
            "detail": None,
            "sessions": 0,
            "checked_at": "2026-09-17T00:00:00+00:00",
        }

    monkeypatch.setattr(overview_module, "cloud_inventory", _cloud)

    asked = time.perf_counter()
    out = await overview_module.overview()
    elapsed = time.perf_counter() - asked

    assert out["listed_providers"] == ["codex"]
    assert out["totals"]["conversations"] == 2
    ids = {row["session_id"] for row in out["conversations"]}
    assert "019f94a6-dd83-71f0-aee4-4456aff06c27" in ids
    for row in out["conversations"]:
        assert row["provider"] == "codex"
        assert row["project"] == "demo-repo"
        assert row["continuation"]["command"].startswith("codex resume ")
    assert elapsed < 1.0, f"the overview took {elapsed:.2f}s to answer from rows"
