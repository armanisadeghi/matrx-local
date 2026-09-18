"""Codex artifacts are captured by the SAME lane Claude Code's are.

Every rollout these tests read is a copy of a REAL Codex session on this Mac,
redacted by the committed ``tests/fixtures/codex/make_fixtures.py`` — the
adapter is only worth trusting against Codex's real file shape. The only thing
a test rewrites is the session ``cwd``, so the written file can exist inside a
temporary repository instead of the fixture's neutral root.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from app.services.coding_sessions import artifacts as mod
from app.services.coding_sessions.artifacts import (
    MANIFEST_NAME,
    CodingSessionArtifactsLane,
)
from app.services.coding_sessions.codex_writes import (
    CodexRolloutSessionSource,
    codex_home,
    parse_apply_patch,
    read_codex_session_writes,
    rollout_files,
)
from app.services.local_db.database import LocalDatabase
from tests.unit.test_coding_session_artifacts import _FakeFilesClient, _Tokens

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex"
WITH_WRITES = FIXTURES / "session-with-writes.jsonl"
SECOND = FIXTURES / "session-second.jsonl"
FIXTURE_CWD = "/Users/fixture/code/demo-repo"
# The identities and the one in-cwd write inside those real rollouts.
SESSION_WITH_WRITES = "019f94a6-dd83-71f0-aee4-4456aff06c27"
SESSION_SECOND = "019effa5-9237-78d3-a133-e723d9ae8276"
WRITTEN_REL = "scripts/sync-types.mjs"
# Shell calls in each fixture whose writes Codex does not record (fixture 1:
# two ``exec`` custom tool calls; fixture 2: 33 ``exec_command``/``write_stdin``
# function calls).
UNATTRIBUTED_WITH_WRITES = 2
UNATTRIBUTED_SECOND = 33


def _rehome_rollout(fixture: Path, home: Path, cwd: Path) -> Path:
    """Copy a real rollout into a private CODEX_HOME, rooted at ``cwd``.

    Line for line the fixture, with only the session's own directory
    rewritten — the entry types, the tool names and the patch headers are the
    ones Codex wrote.
    """
    target = home / "sessions" / "2026" / "07" / "24" / f"rollout-{fixture.stem}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fixture.read_text().replace(FIXTURE_CWD, str(cwd)))
    return target


async def _codex_lane(
    tmp_path: Path, *, home: Path, client: _FakeFilesClient, cloud: bool = True
) -> tuple[LocalDatabase, CodingSessionArtifactsLane]:
    db = LocalDatabase(tmp_path / "codex.db")
    await db.connect()
    lane = CodingSessionArtifactsLane(
        db=db,
        provider="codex",
        source=CodexRolloutSessionSource(home=home),
        durable_root=tmp_path / "durable-codex",
        files_client=client,  # type: ignore[arg-type]
        cloud_enabled=cloud,
    )
    return db, lane


# ── the source, read against the real rollouts ───────────────────────────


def test_a_real_rollout_yields_its_identity_and_its_in_cwd_write() -> None:
    writes = read_codex_session_writes(WITH_WRITES)
    assert writes is not None
    assert writes.session_id == SESSION_WITH_WRITES
    assert writes.cwd == Path(FIXTURE_CWD)
    assert writes.written == (Path(FIXTURE_CWD) / WRITTEN_REL,)
    assert writes.apply_patch_calls == 1
    assert writes.originator == "Codex Desktop"
    # The durable layout is keyed the same way Claude Code's projects are.
    assert writes.project_slug == "-Users-fixture-code-demo-repo"


def test_a_patch_outside_the_session_cwd_is_never_an_artifact() -> None:
    """This real session patched ``~/.codex/config.toml``: it configured a
    tool, it did not produce a deliverable of that project. Counted, not
    captured — and never filed under the session's folder."""
    writes = read_codex_session_writes(SECOND)
    assert writes is not None
    assert writes.session_id == SESSION_SECOND
    assert writes.apply_patch_calls == 1
    assert writes.written == ()
    assert writes.outside_cwd == (Path("/Users/armanisadeghi/.codex/config.toml"),)
    assert writes.notes()["paths_outside_cwd"] == 1


def test_the_unattributable_shell_calls_are_counted_per_session() -> None:
    """The honest limit: Codex names a file only in apply_patch, so every
    shell call is work whose writes the record does not contain. The number
    exists per session or the screen would imply the list is complete."""
    first = read_codex_session_writes(WITH_WRITES)
    second = read_codex_session_writes(SECOND)
    assert first is not None and second is not None
    assert first.unattributed_tool_calls == UNATTRIBUTED_WITH_WRITES
    assert second.unattributed_tool_calls == UNATTRIBUTED_SECOND
    assert first.notes()["unattributed_tool_calls"] == UNATTRIBUTED_WITH_WRITES


def test_a_deleted_file_is_not_an_artifact(tmp_path: Path) -> None:
    """A path a patch created and a later patch deleted is not a deliverable
    — and a rename's DESTINATION is the file that exists afterwards."""
    parsed = parse_apply_patch(
        "*** Begin Patch\n"
        f"*** Add File: {FIXTURE_CWD}/kept.md\n"
        f"*** Delete File: {FIXTURE_CWD}/gone.md\n"
        "*** End Patch"
    )
    assert parsed == {
        "written": [f"{FIXTURE_CWD}/kept.md"],
        "deleted": [f"{FIXTURE_CWD}/gone.md"],
    }
    moved = parse_apply_patch(
        "*** Begin Patch\n"
        f"*** Update File: {FIXTURE_CWD}/old.md\n"
        f"*** Move to: {FIXTURE_CWD}/new.md\n"
        "*** End Patch"
    )
    assert moved["written"] == [f"{FIXTURE_CWD}/new.md"]

    # End to end on a rollout: the same path added then deleted is gone.
    home = tmp_path / "codex-home"
    rollout = home / "sessions" / "2026" / "07" / "24" / "rollout-delete.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        "\n".join(
            json.dumps(entry)
            for entry in (
                {
                    "type": "session_meta",
                    "payload": {
                        "session_id": "deadbeef-0000-0000-0000-000000000001",
                        "cwd": FIXTURE_CWD,
                        "timestamp": "2026-07-24T15:03:11.515Z",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "input": (
                            "*** Begin Patch\n"
                            f"*** Add File: {FIXTURE_CWD}/temp.md\n"
                            "*** End Patch"
                        ),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "input": (
                            "*** Begin Patch\n"
                            f"*** Delete File: {FIXTURE_CWD}/temp.md\n"
                            "*** End Patch"
                        ),
                    },
                },
            )
        )
        + "\n"
    )
    writes = read_codex_session_writes(rollout)
    assert writes is not None
    assert writes.written == ()
    assert writes.deleted == (Path(FIXTURE_CWD) / "temp.md",)


def test_codex_home_is_one_accessor_tests_can_point_elsewhere(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_HOME", "/tmp/not-a-real-codex-home")
    assert codex_home() == Path("/tmp/not-a-real-codex-home")
    assert rollout_files(Path("/tmp/not-a-real-codex-home")) == []


def test_discovery_skips_rollouts_with_no_structured_write_and_says_how_many(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codex-home"
    repo = tmp_path / "demo-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "sync-types.mjs").write_text("export default 1;\n")
    _rehome_rollout(WITH_WRITES, home, repo)
    _rehome_rollout(SECOND, home, repo)  # its only patch is outside cwd
    source = CodexRolloutSessionSource(home=home)
    discovered = list(source.discover())
    assert [d.cli_session_id for d in discovered] == [SESSION_WITH_WRITES]
    assert discovered[0].files == (repo / "scripts" / "sync-types.mjs",)
    assert discovered[0].root == repo
    stats = source.scan_stats()
    assert stats["rollouts_scanned"] == 2
    assert stats["rollouts_without_structured_writes"] == 1


# ── the lane, on the Codex source ────────────────────────────────────────


async def test_codex_lane_captures_and_publishes_the_file_the_rollout_names(
    tmp_path: Path, monkeypatch
) -> None:
    """End to end: a Codex lane built on a real rollout captures the file that
    rollout says the session wrote into the CODEX durable root, and publishes
    it to its own cloud path with a placement of its own."""
    home = tmp_path / "codex-home"
    repo = tmp_path / "demo-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "sync-types.mjs").write_text("export const synced = true;\n")
    _rehome_rollout(WITH_WRITES, home, repo)
    client = _FakeFilesClient()
    db, lane = await _codex_lane(tmp_path, home=home, client=client)
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        tick = await lane.run_once()
        assert tick["captured"] == 1 and tick["uploaded"] == 1 and tick["failed"] == 0

        durable = tmp_path / "durable-codex" / SESSION_WITH_WRITES
        assert (durable / WRITTEN_REL).read_text() == "export const synced = true;\n"
        manifest = json.loads((durable / MANIFEST_NAME).read_text())
        assert manifest["provider"] == "codex"
        assert manifest["files"][WRITTEN_REL]["sha256"]

        assert [u["file_path"] for u in client.uploads] == [
            f"coding-sessions/codex/{SESSION_WITH_WRITES}/{WRITTEN_REL}"
        ]
        meta = client.uploads[0]["metadata"]
        assert meta["provider"] == "codex"
        assert meta["cli_session_id"] == SESSION_WITH_WRITES
        assert meta["relative_path"] == WRITTEN_REL

        detail = lane.session_detail(SESSION_WITH_WRITES)
        assert detail is not None and detail["provider"] == "codex"
        entry = detail["entries"][WRITTEN_REL]
        assert entry["verified_at"] and entry["deduplicated"] is False
        assert client.rows[entry["file_id"]]["file_path"] == (
            f"coding-sessions/codex/{SESSION_WITH_WRITES}/{WRITTEN_REL}"
        )
        status = lane.status()
        assert status["provider"] == "codex" and status["uploaded"] == 1
        assert status["roots"] == [str(home / "sessions")]

        # Second tick: nothing new, nothing re-uploaded.
        assert (await lane.run_once())["uploaded"] == 0 and len(client.uploads) == 1
    finally:
        await db.close()


async def test_codex_lane_says_how_many_writes_codex_does_not_record(
    tmp_path: Path, monkeypatch
) -> None:
    """Law 4 on the screen: the lane carries the per-session number of shell
    calls whose writes Codex never recorded, with a sentence that stops the
    captured list from implying it is everything the session wrote."""
    home = tmp_path / "codex-home"
    repo = tmp_path / "demo-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "sync-types.mjs").write_text("export const synced = true;\n")
    _rehome_rollout(WITH_WRITES, home, repo)
    client = _FakeFilesClient()
    db, lane = await _codex_lane(tmp_path, home=home, client=client)
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        await lane.run_once()
        summary = lane.session_summaries()[0]
        assert summary["source_notes"]["unattributed_tool_calls"] == UNATTRIBUTED_WITH_WRITES
        gaps = lane.status()["source_gaps"]
        assert gaps["counts"]["unattributed_tool_calls"] == UNATTRIBUTED_WITH_WRITES
        assert any(
            "whose writes Codex does not record" in message for message in gaps["messages"]
        )
    finally:
        await db.close()


async def test_a_written_path_that_is_gone_is_counted_not_silently_dropped(
    tmp_path: Path, monkeypatch
) -> None:
    """The rollout says the session wrote a file; the file is no longer there.
    Nothing to capture — but the lane says so, by name and by number, instead
    of reporting a session with zero artifacts."""
    home = tmp_path / "codex-home"
    repo = tmp_path / "demo-repo"
    repo.mkdir()  # the written file was deleted/moved after the session ran
    _rehome_rollout(WITH_WRITES, home, repo)
    client = _FakeFilesClient()
    db, lane = await _codex_lane(tmp_path, home=home, client=client)
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        tick = await lane.run_once()
        assert tick["captured"] == 0 and client.uploads == []
        summary = lane.session_summaries()[0]
        assert summary["missing_source_files"] == 1
        detail = lane.session_detail(SESSION_WITH_WRITES)
        assert detail is not None and detail["entries"] == {}
        gaps = lane.status()["source_gaps"]
        assert gaps["counts"]["missing_source_files"] == 1
        assert any("no longer on disk" in message for message in gaps["messages"])
    finally:
        await db.close()


async def test_each_provider_keeps_its_own_durable_root_and_cloud_path(
    tmp_path: Path, monkeypatch
) -> None:
    """One feature, two providers: the Claude Code lane's durable folder and
    cloud paths are untouched by Codex's, and each lane is its own registry
    entry."""
    from app.services.coding_sessions.artifacts import (
        ARTIFACT_PROVIDERS,
        build_artifact_session_source,
    )

    assert ARTIFACT_PROVIDERS == ("claude_code", "codex")
    assert build_artifact_session_source("claude_code").provider == "claude_code"
    assert build_artifact_session_source("codex").provider == "codex"
    with pytest.raises(ValueError):
        build_artifact_session_source("cursor")

    home = tmp_path / "codex-home"
    repo = tmp_path / "demo-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "sync-types.mjs").write_text("export const synced = true;\n")
    _rehome_rollout(WITH_WRITES, home, repo)

    # Claude Code, same tick, same fake door: its own root, its own paths.
    pad = tmp_path / "roots" / "claude-501" / "-Users-me-code" / "cc-session" / "scratchpad"
    pad.mkdir(parents=True)
    (pad / "report.md").write_text("export const synced = true;\n")  # identical bytes

    client = _FakeFilesClient()
    db = LocalDatabase(tmp_path / "both.db")
    await db.connect()
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    codex_lane = CodingSessionArtifactsLane(
        db=db,
        provider="codex",
        source=CodexRolloutSessionSource(home=home),
        durable_root=tmp_path / "durable" / "codex",
        files_client=client,  # type: ignore[arg-type]
    )
    claude_lane = CodingSessionArtifactsLane(
        db=db,
        roots=[tmp_path / "roots" / "claude-501"],
        durable_root=tmp_path / "durable" / "claude_code",
        files_client=client,  # type: ignore[arg-type]
    )
    try:
        await codex_lane.run_once()
        await claude_lane.run_once()
        assert sorted(u["file_path"] for u in client.uploads) == [
            "coding-sessions/claude_code/cc-session/report.md",
            f"coding-sessions/codex/{SESSION_WITH_WRITES}/{WRITTEN_REL}",
        ]
        assert (tmp_path / "durable" / "codex" / SESSION_WITH_WRITES / WRITTEN_REL).is_file()
        assert (tmp_path / "durable" / "claude_code" / "cc-session" / "report.md").is_file()
        # Identical bytes in two providers still get a placement each.
        codex_entry = codex_lane.session_detail(SESSION_WITH_WRITES)["entries"][WRITTEN_REL]
        claude_entry = claude_lane.session_detail("cc-session")["entries"]["report.md"]
        assert codex_entry["file_id"] != claude_entry["file_id"]
        assert codex_entry["deduplicated"] is False and claude_entry["deduplicated"] is False
    finally:
        await db.close()


def test_a_lane_never_takes_a_source_from_another_provider(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CodingSessionArtifactsLane(
            db=LocalDatabase(tmp_path / "unused.db"),
            provider="codex",
            source=mod.ScratchpadSessionSource(roots=[tmp_path]),
            durable_root=tmp_path / "durable",
        )


def test_each_provider_durable_root_is_its_own_folder_and_claude_code_never_moves(
    tmp_path: Path, monkeypatch
) -> None:
    """Claude Code's durable folder is where it has always been —
    ``<app data>/coding-sessions/artifacts/claude_code`` — and Codex gets a
    sibling, never the same folder."""
    monkeypatch.setattr(mod, "safe_dir", lambda _name: tmp_path / "data")
    claude = CodingSessionArtifactsLane(
        db=LocalDatabase(tmp_path / "unused.db"), roots=[tmp_path / "nope"]
    )
    codex = CodingSessionArtifactsLane(
        db=LocalDatabase(tmp_path / "unused.db"),
        provider="codex",
        source=CodexRolloutSessionSource(home=tmp_path / "codex-home"),
    )
    assert claude.durable_root.relative_to(tmp_path).as_posix() == (
        "data/coding-sessions/artifacts/claude_code"
    )
    assert codex.durable_root.relative_to(tmp_path).as_posix() == (
        "data/coding-sessions/artifacts/codex"
    )


def test_a_clipped_scan_window_is_announced_not_silent(tmp_path: Path) -> None:
    """The scan is bounded (5,041 rollouts on this Mac). A rollout the bound
    left out is a gap, and a user with older Codex artifacts must not see an
    empty list that looks like "you wrote nothing"."""
    home = tmp_path / "codex-home"
    repo = tmp_path / "demo-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "sync-types.mjs").write_text("export default 1;\n")
    _rehome_rollout(WITH_WRITES, home, repo)
    _rehome_rollout(SECOND, home, repo)
    source = CodexRolloutSessionSource(home=home, max_age_days=None, max_rollouts=1)
    list(source.discover())
    stats = source.scan_stats()
    assert stats["rollouts_available"] == 2 and stats["rollouts_scanned"] == 1
    assert stats["rollouts_outside_scan_window"] == 1
    assert any(
        "were not scanned this pass" in message for message in source.describe_gaps({})
    )


async def test_a_write_inside_a_repository_checkout_is_never_captured(
    tmp_path: Path, monkeypatch
) -> None:
    """THE REPOSITORY RULE HOLDS FOR EVERY PROVIDER (ruled 2026-09-18, CS-31).

    Claude Code's walk has always refused to capture anything inside a git
    checkout: a working copy of a repository is not a session deliverable, and
    this lane copies what it captures into a durable folder and uploads it to
    AI Matrx. An explicit file list must not be a second door into the same
    upload with that rule switched off — otherwise "capture Codex the same
    way" quietly becomes "start uploading the user's source code".

    The consequence is deliberate and measured: the only writes Codex records
    structurally are ``apply_patch`` paths, and on this Mac those are almost
    all repository source. So this rule excludes nearly every Codex write
    there is — and the lane says so, with a count, instead of capturing them.
    """
    home = tmp_path / "codex-home"
    repo = tmp_path / "demo-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "sync-types.mjs").write_text("export const synced = true;\n")
    # The one difference from the capture test above: this really is a checkout.
    (repo / ".git").mkdir()
    _rehome_rollout(WITH_WRITES, home, repo)
    client = _FakeFilesClient()
    db, lane = await _codex_lane(tmp_path, home=home, client=client)
    monkeypatch.setattr(mod, "TokenRepo", lambda _db: _Tokens({"access_token": "jwt"}))
    try:
        tick = await lane.run_once()

        assert tick["captured"] == 0, "a repository's source was captured"
        assert client.uploads == [], "a repository's source was uploaded to the cloud"
        assert not (tmp_path / "durable-codex" / SESSION_WITH_WRITES / WRITTEN_REL).exists()

        status = lane.status()
        counts = status["source_gaps"]["counts"]
        assert counts["skipped_repository_files"] == 1
        messages = " ".join(status["source_gaps"]["messages"])
        assert "repository working copy" in messages
        assert "never copies your source code out of a checkout" in messages
    finally:
        await db.close()
