"""What a Codex session WROTE, read from its own rollout file.

Coding sessions are one feature, so Codex artifacts are captured by the same
lane Claude Code's are (:mod:`app.services.coding_sessions.artifacts`). Only
DISCOVERY differs, and for Codex it is not a directory walk: measured on this
Mac 2026-09-17, Codex keeps no per-session scratchpad at all (``~/.codex/
artifacts`` holds two hand-made folders; ``~/.codex/tmp/arg0/*`` is
random-named and not session-keyed). The one structured, honest record of a
file a Codex session wrote is its ``apply_patch`` tool call inside the rollout
at ``~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl``, whose body carries
real ``*** Add File:`` / ``*** Update File:`` / ``*** Delete File:`` headers.

**THE LIMIT, SAID OUT LOUD.** Modern Codex writes mostly through shell
commands, and a shell call records no file list — only the command. Measured
across every rollout in Arman's ``~/.codex`` on 2026-09-17: 5,041 rollouts,
of which 144 contain any ``apply_patch`` at all (2,776 calls), yielding 326
``Add File`` + 4,317 ``Update File`` + 56 ``Delete File`` headers — 4,120
non-deleted write paths inside their session's ``cwd`` and 523 outside it —
against 523,106 shell/exec tool calls whose writes Codex does not record. So
this source can name a MINORITY of what Codex wrote, and every session it
reports carries ``unattributed_tool_calls``: the number of shell calls whose
writes are not in the record. The screen says that number; it never implies
the captured list is complete (law 4).

What the source deliberately refuses:

* a path OUTSIDE the session's ``cwd`` is not a session artifact (a Codex
  session editing ``~/.codex/config.toml`` is configuring a tool, not
  producing a deliverable), and is counted, not captured;
* a ``Delete File`` is not an artifact;
* a written path that no longer exists on disk is counted as missing by the
  capture lane rather than silently dropped.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.services.coding_sessions.artifacts import DiscoveredSession

logger = get_logger()

PROVIDER = "codex"
CODEX_HOME_ENV = "CODEX_HOME"
ROLLOUT_GLOB = "rollout-*.jsonl"

# Discovery knobs. There are 5,041 rollouts on this Mac and each is read in
# full, so an unbounded rescan every tick would burn minutes of CPU: the
# source reads only rollouts touched recently and remembers what it parsed
# (keyed on size+mtime), so a steady state costs one stat() per rollout.
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_MAX_ROLLOUTS = 500

# Tool names whose file writes Codex does not record. Measured from every
# rollout on this Mac 2026-09-17: ``exec`` (508,016 calls), ``exec_command``
# (10,086) and ``write_stdin`` (4,977 — input typed into a running shell) are
# the shell family actually in use; the rest are names Codex builds have used
# for the same thing.
_SHELL_TOOL_NAMES = frozenset(
    {
        "exec",
        "exec_command",
        "write_stdin",
        "shell",
        "local_shell",
        "container.exec",
        "bash",
    }
)
_WRITE_TOOL_NAMES = frozenset({"apply_patch"})
_CALL_PAYLOAD_TYPES = frozenset({"function_call", "custom_tool_call", "local_shell_call"})

_ADD = "*** Add File:"
_UPDATE = "*** Update File:"
_DELETE = "*** Delete File:"
_MOVE = "*** Move to:"


def codex_home() -> Path:
    """Codex's home — ``$CODEX_HOME`` when set, else ``~/.codex``.

    The ONE accessor: tests and the fixtures point it elsewhere instead of
    reaching into a real user's home.
    """
    configured = os.environ.get(CODEX_HOME_ENV)
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def codex_sessions_root(home: Path | None = None) -> Path:
    return (home or codex_home()) / "sessions"


def rollout_files(
    home: Path | None = None,
    *,
    max_age_days: float | None = DEFAULT_MAX_AGE_DAYS,
    max_rollouts: int | None = DEFAULT_MAX_ROLLOUTS,
) -> list[Path]:
    """Rollout files, newest first, bounded by age and count."""
    root = codex_sessions_root(home)
    try:
        candidates = list(root.rglob(ROLLOUT_GLOB))
    except OSError:
        return []
    dated: list[tuple[float, Path]] = []
    cutoff = None
    if max_age_days is not None:
        import time

        cutoff = time.time() - max_age_days * 86_400.0
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            continue
        dated.append((mtime, path))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    ordered = [path for _, path in dated]
    return ordered if max_rollouts is None else ordered[:max_rollouts]


def parse_apply_patch(body: str) -> dict[str, list[str]]:
    """The absolute paths an ``apply_patch`` body names, by what it did.

    ``*** Move to:`` renames the file the preceding header opened, so the
    destination is what exists afterwards and the source path is not a
    written file at all.
    """
    written: list[str] = []
    deleted: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith(_ADD):
            written.append(line[len(_ADD) :].strip())
        elif line.startswith(_UPDATE):
            written.append(line[len(_UPDATE) :].strip())
        elif line.startswith(_DELETE):
            deleted.append(line[len(_DELETE) :].strip())
        elif line.startswith(_MOVE) and written:
            written[-1] = line[len(_MOVE) :].strip()
    return {"written": written, "deleted": deleted}


def _patch_body(payload: dict[str, Any]) -> str:
    """The patch text of an ``apply_patch`` call, whichever shape it arrives in.

    ``custom_tool_call`` carries the raw patch in ``input``; ``function_call``
    carries JSON arguments (``{"input": "*** Begin Patch…"}``).
    """
    for key in ("arguments", "input", "patch"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _patch_body(value)
            if nested:
                return nested
            continue
        if not isinstance(value, str) or not value:
            continue
        if "***" in value:
            return value
        try:
            parsed = json.loads(value)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            nested = _patch_body(parsed)
            if nested:
                return nested
    return ""


@dataclass(frozen=True)
class CodexSessionWrites:
    """One Codex rollout, read: who it was and what it can prove it wrote."""

    rollout: Path
    session_id: str
    cwd: Path
    started_at: str | None = None
    originator: str | None = None
    source: str | None = None
    # Absolute paths, inside cwd, that the session's patches wrote.
    written: tuple[Path, ...] = ()
    # Named by a patch but OUTSIDE cwd — another tool's file, never a session
    # artifact. Counted so nothing disappears quietly.
    outside_cwd: tuple[Path, ...] = ()
    deleted: tuple[Path, ...] = ()
    apply_patch_calls: int = 0
    # Shell/exec calls whose file writes Codex does not record. The honest
    # size of what this source CANNOT see for this session.
    unattributed_tool_calls: int = 0

    @property
    def project_slug(self) -> str:
        """The same shape Claude Code's project folder uses (``-Users-me-code``)."""
        return str(self.cwd).replace(os.sep, "-").replace("/", "-") or "-"

    def notes(self) -> dict[str, Any]:
        return {
            "rollout": str(self.rollout),
            "attributed_writes": len(self.written),
            "paths_outside_cwd": len(self.outside_cwd),
            "deleted_paths": len(self.deleted),
            "apply_patch_calls": self.apply_patch_calls,
            "unattributed_tool_calls": self.unattributed_tool_calls,
        }


def read_codex_session_writes(rollout: Path) -> CodexSessionWrites | None:
    """Read one rollout. ``None`` when it has no session identity to speak of."""
    try:
        text = rollout.read_text(errors="replace")
    except OSError as exc:
        logger.info("[codex_writes] could not read %s: %s", rollout, exc)
        return None
    session_id = ""
    cwd = ""
    started_at: str | None = None
    originator: str | None = None
    source: str | None = None
    written: list[str] = []
    deleted: list[str] = []
    apply_patch_calls = 0
    shell_calls = 0
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        if entry.get("type") == "session_meta":
            # A resumed/forked rollout can carry several; the FIRST one is
            # this file's identity.
            if not session_id:
                session_id = str(payload.get("session_id") or payload.get("id") or "")
                cwd = str(payload.get("cwd") or "")
                started_at = payload.get("timestamp") or entry.get("timestamp")
                originator = payload.get("originator")
                source = payload.get("source")
            continue
        if payload.get("type") not in _CALL_PAYLOAD_TYPES:
            continue
        name = payload.get("name")
        if name in _WRITE_TOOL_NAMES:
            apply_patch_calls += 1
            parsed = parse_apply_patch(_patch_body(payload))
            written.extend(parsed["written"])
            deleted.extend(parsed["deleted"])
        elif name in _SHELL_TOOL_NAMES or payload.get("type") == "local_shell_call":
            shell_calls += 1
    if not session_id or not cwd:
        return None
    root = Path(cwd)
    inside: list[Path] = []
    outside: list[Path] = []
    seen: set[str] = set()
    deleted_paths = {Path(path) for path in deleted}
    for path in written:
        candidate = Path(path)
        if not candidate.is_absolute() or str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if candidate == root or root in candidate.parents:
            # A path a later patch deleted is not an artifact of this session.
            if candidate not in deleted_paths:
                inside.append(candidate)
        else:
            outside.append(candidate)
    return CodexSessionWrites(
        rollout=rollout,
        session_id=session_id,
        cwd=root,
        started_at=started_at,
        originator=originator,
        source=source,
        written=tuple(inside),
        outside_cwd=tuple(outside),
        deleted=tuple(sorted(deleted_paths)),
        apply_patch_calls=apply_patch_calls,
        unattributed_tool_calls=shell_calls,
    )


@dataclass
class _Cached:
    size: int
    mtime: int
    writes: CodexSessionWrites | None


class CodexRolloutSessionSource:
    """The artifacts lane's Codex source: sessions and their written files.

    Implements ``ArtifactSessionSource``. It hands the lane an explicit file
    list (never a directory to walk), rooted at the session's ``cwd`` so the
    durable layout has the same shape as Claude Code's.
    """

    provider = PROVIDER

    def __init__(
        self,
        home: Path | None = None,
        *,
        max_age_days: float | None = DEFAULT_MAX_AGE_DAYS,
        max_rollouts: int | None = DEFAULT_MAX_ROLLOUTS,
    ) -> None:
        self._home = home
        self._max_age_days = max_age_days
        self._max_rollouts = max_rollouts
        self._cache: dict[str, _Cached] = {}
        self._last_scan: dict[str, int] = {}

    @property
    def home(self) -> Path:
        return self._home or codex_home()

    def locations(self) -> list[str]:
        return [str(codex_sessions_root(self._home))]

    def rollouts(self) -> list[Path]:
        return rollout_files(
            self._home, max_age_days=self._max_age_days, max_rollouts=self._max_rollouts
        )

    def read(self, rollout: Path) -> CodexSessionWrites | None:
        """Read a rollout, reusing the last parse while the file is unchanged."""
        try:
            stat = rollout.stat()
        except OSError:
            return None
        key = str(rollout)
        cached = self._cache.get(key)
        if cached and cached.size == stat.st_size and cached.mtime == int(stat.st_mtime):
            return cached.writes
        writes = read_codex_session_writes(rollout)
        self._cache[key] = _Cached(stat.st_size, int(stat.st_mtime), writes)
        return writes

    def scan_stats(self) -> dict[str, int]:
        """What the last discovery pass saw — including the rollouts that hold
        no structured write at all, which is the majority."""
        return dict(self._last_scan)

    def describe_gaps(self, counts: dict[str, int]) -> list[str]:
        messages: list[str] = []
        unattributed = int(counts.get("unattributed_tool_calls") or 0)
        if unattributed:
            messages.append(
                f"{unattributed} Codex tool calls whose writes Codex does not record. "
                "Codex only names a file it changed with apply_patch; work done through "
                "shell commands leaves no file list, so this list of artifacts is what "
                "Codex recorded, not everything the session wrote."
            )
        outside = int(counts.get("paths_outside_cwd") or 0)
        if outside:
            messages.append(
                f"{outside} file(s) these sessions patched live outside the session's "
                "own project folder and are deliberately not captured here."
            )
        clipped = int(self._last_scan.get("rollouts_outside_scan_window") or 0)
        if clipped:
            messages.append(
                f"{clipped} older Codex session(s) were not scanned this pass "
                f"(the scan reads the newest {self._max_rollouts} rollouts from the last "
                f"{self._max_age_days} days), so artifacts older than that window are not "
                "listed yet."
            )
        without = int(self._last_scan.get("rollouts_without_structured_writes") or 0)
        if without:
            messages.append(
                f"{without} Codex session(s) recorded no file writes at all, so they have "
                "no artifacts to show even though they may have written files."
            )
        return messages

    def discover(self) -> Iterator[DiscoveredSession]:
        # The scan is BOUNDED (5,041 rollouts on this Mac, each read in full),
        # so how many rollouts the bound left out is itself a gap the screen
        # must be able to say — a quietly clipped scan looks exactly like a
        # user with no Codex artifacts.
        available = len(rollout_files(self._home, max_age_days=None, max_rollouts=None))
        seen = with_writes = without = 0
        for rollout in self.rollouts():
            seen += 1
            writes = self.read(rollout)
            if writes is None:
                continue
            if not writes.written:
                without += 1
                continue
            with_writes += 1
            yield DiscoveredSession(
                provider=self.provider,
                project_slug=writes.project_slug,
                cli_session_id=writes.session_id,
                root=writes.cwd,
                files=writes.written,
                notes=writes.notes(),
            )
        self._last_scan = {
            "rollouts_available": available,
            "rollouts_scanned": seen,
            "rollouts_with_structured_writes": with_writes,
            "rollouts_without_structured_writes": without,
            "rollouts_outside_scan_window": max(available - seen, 0),
        }


__all__ = [
    "CODEX_HOME_ENV",
    "DEFAULT_MAX_AGE_DAYS",
    "DEFAULT_MAX_ROLLOUTS",
    "PROVIDER",
    "CodexRolloutSessionSource",
    "CodexSessionWrites",
    "codex_home",
    "codex_sessions_root",
    "parse_apply_patch",
    "read_codex_session_writes",
    "rollout_files",
]
