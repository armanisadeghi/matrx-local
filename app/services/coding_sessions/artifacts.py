"""Coding-session artifacts — keep what a session BUILT, not just what it said.

Coding sessions are ONE feature, so this lane is not Claude-Code-only: a
lane is built PER PROVIDER (``provider="claude_code"``, ``provider="codex"``,
…) and every provider differs in exactly one place — how its sessions and
their files are DISCOVERED. That difference is an
:class:`ArtifactSessionSource`; everything after discovery (exclusions, size
and count caps, the durable copy, the manifest, the upload, the read-back
confirmation and the placement repair) is this one shared machinery.

Claude Code writes a session's deliverables into a per-session scratchpad
under ``/tmp/claude-<uid>/<project-slug>/<session-id>/scratchpad/…``
(:class:`ScratchpadSessionSource`). The bridge mirrors the conversation to
AI Matrx but never those files, so switching Claude accounts hid them, AI
Matrx showed conversations without the things they produced, and macOS
eventually purges /tmp. Measured 2026-09-12: 723 scratchpads, 11 GB, most of
it venvs/clones/node_modules next to the handful of real deliverables.

Codex has no scratchpad at all: its structured record of what a session
wrote is the ``apply_patch`` calls in its rollout file, so its source hands
this lane an explicit file list instead of a directory to walk
(:mod:`app.services.coding_sessions.codex_writes`). That record is
INCOMPLETE by construction — a Codex session that writes through shell
commands records no file list — so every source also reports the gap it
knows about (``source_notes``), and the lane carries those numbers into
``status()`` and each session summary. A gap that is counted and named is
honest; a gap that is silent is a defect (law 4).

This lane, every cycle:

1. asks its source for every session it can see (all projects, all sessions
   — a session hidden by an account switch is still on disk);
2. copies every DELIVERABLE file (see ``_is_excluded_dir`` / size cap) into
   the durable per-session folder ``<app data>/coding-sessions/artifacts/
   <provider>/<session-id>/<relative path>`` — outside /tmp, readable by
   any session on this Mac, revealed from the Coding Sessions screen;
3. publishes each new or changed file to AI Matrx through the same
   matrx-files upload the screenshot publisher uses, tagged with the
   session id so the conversation can list its artifacts. The organization
   is the platform rule (default → sole → personal), never a question.

Every number the screen shows comes from the manifest this lane writes —
``<durable session folder>/.matrx-artifacts.json`` — one record per file:
size, mtime, sha256, captured_at, file_id, uploaded_at, upload_error.
Nothing here is ever deleted: a file that disappears from the scratchpad
stays in the durable folder (that is the point).

**One record per PATH, one cloud ROW per path, one content behind them.**
An undeclared matrx-files write is implicitly ``alias_existing``: bytes the
account already holds return the canonical row's ``file_id`` with no new row,
and the path this lane asked for is recorded NOWHERE (measured 2026-09-17:
10,635 captured paths in one session resolved to 5,633 cloud rows; 287
artifacts of three sessions were filed under ANOTHER session's row, so the
app's Artifacts panel — which lists rows carrying this session's id — could
not show them). Bytes were never lost; the PLACEMENT was.

So every upload here now DECLARES ``intent=force_new_copy``: the door writes
a row of this placement's own, at this session's path, carrying this
session's id, linked to the canonical content by
``cld_files.duplicate_of_file_id`` — the platform's existing "one content,
many placements" primitive. Nothing new was invented and no schema changed.

An entry counts as IN AI Matrx only once the returned ``file_id`` has been
READ BACK successfully (``verified_at``); a file id the server no longer
serves is cleared, announced as ``missing_in_cloud`` and re-uploaded. A row
read back at a DIFFERENT path has no placement of its own: it is announced as
``unplaced`` and re-filed by ``_queue_placement_repairs`` — the repair for
every entry an older build aliased away. After ``MAX_PLACEMENT_REPAIRS``
rounds the entry stops and says the door is not honouring the placement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.common.system_logger import get_logger
from app.services.aidream.organization import OrganizationNotResolvedError
from app.services.file_sync.client import FileSyncHTTPError, MatrxFilesClient
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import TokenRepo
from app.services.paths.manager import safe_dir
from app.services.session_freshness import (
    request_session_grant,
    session_blocker,
)

logger = get_logger()

DEFAULT_PROVIDER = "claude_code"
MANIFEST_NAME = ".matrx-artifacts.json"
SCAN_INTERVAL_SECONDS = 120.0
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_SESSION = 2_000
UPLOADS_PER_TICK = 120
UPLOAD_CONCURRENCY = 6
# Uploads through the edge drop intermittently mid-body (measured 2026-09-12:
# 12 MB bodies died at 2 MB and 11 MB, then succeeded). Retry across ticks,
# then stop and SAY so — the durable local copy is kept either way.
MAX_UPLOAD_ATTEMPTS = 8
# Read-back budget per tick. Every fresh upload is confirmed immediately; this
# budget works through the backlog of entries that were marked "uploaded" by
# older builds (which believed a returned file id without ever checking it).
VERIFY_PER_TICK = 240
VERIFY_CONCURRENCY = 6
# Rounds an entry gets to obtain a placement of its own before the lane stops
# re-uploading and says the door is not honouring the declared placement (an
# old server build is exactly what that looks like).
MAX_PLACEMENT_REPAIRS = 3
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "target",
        ".next",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".turbo",
        # Browser profiles some sessions launch Chromium with (1,271 files
        # of cache in one real session) are runtime state, never deliverables.
        "chrome-profile",
        "chromium-profile",
        "browser-profile",
    }
)
_EXCLUDED_FILE_NAMES = frozenset({".DS_Store", MANIFEST_NAME})
_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".sqlite3-wal", ".sqlite3-shm", ".db-wal", ".db-shm"})
_CODE_MIME_OVERRIDES = {
    # Python's platform MIME registry treats .ts/.mts as MPEG transport streams.
    # In this lane they are coding-session deliverables, so that classification
    # makes source files enter video renderers and fail as corrupt media.
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".mts": "text/typescript",
    ".cts": "text/typescript",
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _artifact_mime_type(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    return _CODE_MIME_OVERRIDES.get(suffix) or mimetypes.guess_type(relative_path)[0] or "application/octet-stream"


def default_scratchpad_roots() -> list[Path]:
    """Every root that can hold ``<project>/<session>/scratchpad`` trees."""
    configured = os.environ.get("CLAUDE_SCRATCHPAD_ROOTS")
    if configured:
        return [Path(p).expanduser() for p in configured.split(os.pathsep) if p]
    roots: list[Path] = []
    if os.name != "nt":
        uid = os.getuid()
        for base in ("/tmp", "/private/tmp"):
            candidate = Path(base) / f"claude-{uid}"
            if candidate.is_dir():
                roots.append(candidate)
                break  # /tmp and /private/tmp are the same tree on macOS
    return roots


def _inside_repository(candidate: Path, root: Path) -> bool:
    """Is this file inside a git checkout or worktree at or under ``root``?

    Walks from the file's own folder up to (and including) ``root``. A
    ``.git`` entry anywhere on that path means the file belongs to a
    repository working copy, which is never a session deliverable.
    """
    folder = candidate.parent
    while True:
        if (folder / ".git").exists():
            return True
        if folder == root or root not in folder.parents:
            return False
        folder = folder.parent


def _is_excluded_dir(path: Path) -> bool:
    if path.name in _EXCLUDED_DIR_NAMES:
        return True
    # A git checkout or worktree inside the scratchpad is a working copy of
    # a repository, never a deliverable of the session; a folder carrying our
    # own manifest is a durable artifact folder (never capture ourselves).
    return (path / ".git").exists() or (path / MANIFEST_NAME).exists()


@dataclass(frozen=True)
class DiscoveredSession:
    """One provider session this lane should capture, as its source sees it.

    ``root`` is what relative paths are computed against, so the durable
    layout is the same shape for every provider. ``files`` is the honest
    difference between providers: ``None`` means "walk ``root``" (Claude
    Code's scratchpad), while an explicit tuple means "these exact files"
    (Codex, whose writes are named by its rollout, not by a directory).
    ``notes`` carries what the source knows it CANNOT see — numbers and a
    sentence, never a silent gap.
    """

    provider: str
    project_slug: str
    cli_session_id: str
    root: Path
    files: tuple[Path, ...] | None = None
    notes: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ArtifactSessionSource(Protocol):
    """How ONE provider's sessions and their written files are discovered.

    The whole per-provider difference lives here. Implementations do no
    copying, no uploading and no manifest work: they answer "which sessions
    exist, and which files did each one write".
    """

    provider: str

    def locations(self) -> list[str]:
        """Where this source looks — shown verbatim on the status screen."""

    def discover(self) -> Iterable[DiscoveredSession]:
        """Every session this source can see right now."""


class ScratchpadSessionSource:
    """Claude Code: ``<root>/<project-slug>/<session-id>/scratchpad`` trees."""

    provider = DEFAULT_PROVIDER

    def __init__(self, roots: list[Path] | None = None) -> None:
        self._roots_override = roots

    @property
    def roots(self) -> list[Path]:
        return self._roots_override if self._roots_override is not None else default_scratchpad_roots()

    def locations(self) -> list[str]:
        return [str(root) for root in self.roots]

    def discover(self) -> Iterator[DiscoveredSession]:
        for root in self.roots:
            try:
                projects = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
            except OSError:
                continue
            for project in projects:
                try:
                    sessions = [s for s in project.iterdir() if s.is_dir() and not s.is_symlink()]
                except OSError:
                    continue
                for session_dir in sessions:
                    scratchpad = session_dir / "scratchpad"
                    if not scratchpad.is_dir():
                        continue
                    yield DiscoveredSession(
                        provider=self.provider,
                        project_slug=project.name,
                        cli_session_id=session_dir.name,
                        root=scratchpad,
                    )


@dataclass
class SessionArtifacts:
    provider: str
    cli_session_id: str
    project_slug: str
    source_root: Path
    durable_dir: Path
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped_over_size: int = 0
    skipped_over_count: int = 0
    # What the source could not read: files it was told about that are no
    # longer on disk, plus whatever gap the source itself reports.
    missing_source_files: list[str] = field(default_factory=list)
    # Files the source named that belong to a repository working copy. The
    # repository rule excludes them for every provider, and the count is the
    # honest reason a session with real writes can still capture nothing.
    skipped_repository_files: int = 0
    source_notes: dict[str, Any] = field(default_factory=dict)

    @property
    def uploaded_count(self) -> int:
        """Entries whose cloud file id was read back successfully. Nothing
        counts as "in AI Matrx" on the strength of an upload response alone."""
        return sum(1 for f in self.files.values() if f.get("file_id") and f.get("verified_at"))

    @property
    def awaiting_confirmation_count(self) -> int:
        return sum(1 for f in self.files.values() if f.get("file_id") and not f.get("verified_at"))

    @property
    def deduplicated_count(self) -> int:
        """Paths whose bytes are byte-identical to an existing cloud file, so
        matrx-files aliased them onto that row instead of writing a new one."""
        return sum(1 for f in self.files.values() if f.get("file_id") and f.get("deduplicated"))

    @property
    def unplaced_count(self) -> int:
        """Confirmed entries whose cloud row is filed under ANOTHER path, so
        the session's own path is recorded nowhere and the app cannot list it
        here. The repair pass re-files them; this is how many are still owed."""
        return sum(
            1
            for f in self.files.values()
            if f.get("file_id") and f.get("deduplicated")
        )

    @property
    def placement_failed_count(self) -> int:
        """Entries the door refused to give their own placement after every
        repair round. Loud, never silent — the file is safe, the listing is not."""
        return sum(1 for f in self.files.values() if f.get("placement_error") and f.get("deduplicated"))

    @property
    def cloud_rows_count(self) -> int:
        """Distinct cloud rows this session's confirmed entries resolve to —
        the number of rows the cloud actually holds for it."""
        return len({f["file_id"] for f in self.files.values() if f.get("file_id")})

    @property
    def distinct_content_count(self) -> int:
        return len({f.get("sha256") for f in self.files.values() if f.get("sha256")})

    @property
    def superseded_version_count(self) -> int:
        """Earlier cloud rows/ids a path had before its content changed."""
        return sum(len(f.get("previous_file_ids") or ()) for f in self.files.values())

    @property
    def missing_in_cloud_count(self) -> int:
        """Entries whose recorded file id the server no longer serves. The
        bytes are still in the durable folder; the publish wave re-uploads."""
        return sum(1 for f in self.files.values() if f.get("verify_error"))

    @property
    def pending_upload_count(self) -> int:
        return sum(1 for f in self.files.values() if not f.get("file_id"))

    @property
    def failed_upload_count(self) -> int:
        return sum(1 for f in self.files.values() if f.get("upload_error"))

    @property
    def abandoned_upload_count(self) -> int:
        return sum(
            1
            for f in self.files.values()
            if not f.get("file_id")
            and int(f.get("upload_attempts") or 0) >= MAX_UPLOAD_ATTEMPTS
        )

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "cli_session_id": self.cli_session_id,
            "project_slug": self.project_slug,
            "source_root": str(self.source_root),
            # Kept for the screens that already read this key; for Claude Code
            # it is the scratchpad, for every other provider the source root.
            "scratchpad": str(self.source_root),
            "durable_dir": str(self.durable_dir),
            "missing_source_files": len(self.missing_source_files),
            "skipped_repository_files": self.skipped_repository_files,
            "source_notes": dict(self.source_notes),
            "files": len(self.files),
            "bytes": sum(int(f.get("size") or 0) for f in self.files.values()),
            "uploaded": self.uploaded_count,
            "awaiting_confirmation": self.awaiting_confirmation_count,
            "deduplicated": self.deduplicated_count,
            "unplaced": self.unplaced_count,
            "placement_failed": self.placement_failed_count,
            "cloud_rows": self.cloud_rows_count,
            "distinct_content": self.distinct_content_count,
            "superseded_versions": self.superseded_version_count,
            "missing_in_cloud": self.missing_in_cloud_count,
            "pending_upload": self.pending_upload_count,
            "failed_upload": self.failed_upload_count,
            "abandoned_upload": self.abandoned_upload_count,
            "skipped_over_size": self.skipped_over_size,
            "skipped_over_count": self.skipped_over_count,
        }


class CodingSessionArtifactsLane:
    """Capture → durable copy → publish, with every state visible."""

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        provider: str = DEFAULT_PROVIDER,
        source: ArtifactSessionSource | None = None,
        roots: list[Path] | None = None,
        durable_root: Path | None = None,
        files_client: MatrxFilesClient | None = None,
        cloud_enabled: bool = True,
    ) -> None:
        self._db = db or get_db()
        if source is None:
            source = build_artifact_session_source(provider, roots=roots)
        elif roots is not None:
            raise ValueError("pass either a source or roots, never both")
        if source.provider != provider:
            raise ValueError(
                f"source provider {source.provider!r} does not match lane provider {provider!r}"
            )
        self.provider = provider
        self._source = source
        self._durable_root_override = durable_root
        self._client = files_client or MatrxFilesClient()
        self._cloud_enabled = cloud_enabled
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._lock = asyncio.Lock()
        self._sessions: dict[str, SessionArtifacts] = {}
        self._last_run_at: str | None = None
        self._last_run_seconds: float | None = None
        self._last_error: dict[str, str] | None = None
        self._blocker: dict[str, Any] | None = None
        # A missing session is the one blocker whose payload ages (quiet while
        # the desktop answers our ask, honest once that window closes), so only
        # its start is kept and the text is built on every read.
        self._session_blocker_since: str | None = None
        self._last_tick: dict[str, Any] = {}

    # ── configuration ────────────────────────────────────────────────
    @property
    def source(self) -> ArtifactSessionSource:
        return self._source

    @property
    def roots(self) -> list[str]:
        """Where this lane's source looks, as the status screen shows it."""
        return self._source.locations()

    @property
    def durable_root(self) -> Path:
        root = self._durable_root_override or (
            safe_dir("data") / "coding-sessions" / "artifacts" / self.provider
        )
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── lifecycle ────────────────────────────────────────────────────
    async def start_background(self) -> None:
        if self.active:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._loop(), name=f"coding-session-artifacts:{self.provider}"
        )

    async def stop_background(self) -> None:
        self._stopping = True
        self._wake.set()
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the loop must survive
                self._last_error = {"code": type(exc).__name__, "message": str(exc)[:500]}
                logger.warning(
                    "[coding_session_artifacts] %s tick failed", self.provider, exc_info=True
                )
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=SCAN_INTERVAL_SECONDS)
            except TimeoutError:
                pass

    # ── status ───────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        sessions = list(self._sessions.values())
        return {
            "active": self.active,
            "provider": self.provider,
            "roots": list(self.roots),
            "durable_root": str(self.durable_root),
            "sessions": len(sessions),
            # What this provider's record does NOT contain. Kept per lane so a
            # screen can say the number out loud instead of implying the list
            # of captured files is everything the session wrote.
            "source_gaps": self._source_gaps(sessions),
            "files": sum(len(s.files) for s in sessions),
            "bytes": sum(int(f.get("size") or 0) for s in sessions for f in s.files.values()),
            "uploaded": sum(s.uploaded_count for s in sessions),
            "awaiting_confirmation": sum(s.awaiting_confirmation_count for s in sessions),
            "deduplicated": sum(s.deduplicated_count for s in sessions),
            "unplaced": sum(s.unplaced_count for s in sessions),
            "placement_failed": sum(s.placement_failed_count for s in sessions),
            "cloud_rows": len(
                {
                    f["file_id"]
                    for s in sessions
                    for f in s.files.values()
                    if f.get("file_id")
                }
            ),
            "distinct_content": len(
                {
                    f["sha256"]
                    for s in sessions
                    for f in s.files.values()
                    if f.get("sha256")
                }
            ),
            "superseded_versions": sum(s.superseded_version_count for s in sessions),
            "missing_in_cloud": sum(s.missing_in_cloud_count for s in sessions),
            "pending_upload": sum(s.pending_upload_count for s in sessions),
            "failed_upload": sum(s.failed_upload_count for s in sessions),
            "abandoned_upload": sum(s.abandoned_upload_count for s in sessions),
            "skipped_over_size": sum(s.skipped_over_size for s in sessions),
            "skipped_over_count": sum(s.skipped_over_count for s in sessions),
            "cloud_enabled": self._cloud_enabled,
            "blocker": (
                session_blocker(
                    lane="coding_session_artifacts", since=self._session_blocker_since
                )
                if self._session_blocker_since is not None
                else dict(self._blocker)
                if self._blocker
                else None
            ),
            "last_error": dict(self._last_error) if self._last_error else None,
            "last_run_at": self._last_run_at,
            "last_run_seconds": self._last_run_seconds,
            "last_tick": dict(self._last_tick),
            "limits": {
                "max_file_bytes": MAX_FILE_BYTES,
                "max_files_per_session": MAX_FILES_PER_SESSION,
                "uploads_per_tick": UPLOADS_PER_TICK,
                "max_upload_attempts": MAX_UPLOAD_ATTEMPTS,
                "verify_per_tick": VERIFY_PER_TICK,
                "max_placement_repairs": MAX_PLACEMENT_REPAIRS,
                "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
            },
        }

    def _source_gaps(self, sessions: list[SessionArtifacts]) -> dict[str, Any]:
        """Everything this lane knows it could NOT capture, with a sentence.

        Numbers come from the sources (``DiscoveredSession.notes``) plus the
        files a source named that are no longer on disk. The wording of a
        provider-specific gap belongs to that provider's source; the generic
        "the file is gone" sentence belongs here.
        """
        totals: dict[str, int] = {}
        for session in sessions:
            for key, value in session.source_notes.items():
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
                totals[key] = totals.get(key, 0) + value
        missing = sum(len(session.missing_source_files) for session in sessions)
        totals["missing_source_files"] = missing
        repository = sum(session.skipped_repository_files for session in sessions)
        totals["skipped_repository_files"] = repository
        messages: list[str] = []
        describe = getattr(self._source, "describe_gaps", None)
        if callable(describe):
            messages.extend(describe(totals))
        if missing:
            messages.append(
                f"{missing} file(s) this session wrote are no longer on disk, so they "
                "could not be captured. Their names are kept on each session."
            )
        if repository:
            messages.append(
                f"{repository} file(s) this session wrote are part of a repository "
                "working copy, so they are not captured. Artifacts are a session's "
                "own deliverables; AI Matrx never copies your source code out of a "
                "checkout, for any coding agent."
            )
        return {"counts": totals, "messages": messages}

    def session_summaries(self) -> list[dict[str, Any]]:
        return sorted(
            (s.summary() for s in self._sessions.values()),
            key=lambda s: s["cli_session_id"],
        )

    def session_detail(self, cli_session_id: str) -> dict[str, Any] | None:
        session = self._sessions.get(cli_session_id)
        if session is None:
            return None
        return {**session.summary(), "entries": dict(session.files)}

    # ── one tick ─────────────────────────────────────────────────────
    async def run_once(self) -> dict[str, Any]:
        async with self._lock:
            started = datetime.now(UTC)
            captured = await asyncio.to_thread(self._capture_all)
            uploaded, failed = await self._publish_pending()
            confirmed, missing = await self._verify_backlog()
            # Entries the read-back found filed under ANOTHER path have no
            # placement of their own; queue them and let the next publish
            # wave re-file them. Ordering matters: the queue is drained by
            # _publish_pending, so a repair queued now is sent next tick.
            refiling = self._queue_placement_repairs()
            self._last_run_at = _utc_now_iso()
            self._last_run_seconds = round((datetime.now(UTC) - started).total_seconds(), 3)
            self._last_tick = {
                "at": self._last_run_at,
                "seconds": self._last_run_seconds,
                "captured": captured,
                "uploaded": uploaded,
                "failed": failed,
                "confirmed": confirmed,
                "missing_in_cloud": missing,
                "queued_for_refiling": refiling,
            }
            return dict(self._last_tick)

    # ── capture (thread) ─────────────────────────────────────────────
    def _capture_all(self) -> int:
        captured = 0
        for discovered in self._source.discover():
            captured += self._capture_session(discovered)
        return captured

    def _session(self, discovered: DiscoveredSession) -> SessionArtifacts:
        session = self._sessions.get(discovered.cli_session_id)
        if session is None:
            durable_dir = self.durable_root / discovered.cli_session_id
            session = SessionArtifacts(
                provider=self.provider,
                cli_session_id=discovered.cli_session_id,
                project_slug=discovered.project_slug,
                source_root=discovered.root,
                durable_dir=durable_dir,
                files=self._read_manifest(durable_dir),
            )
            self._sessions[discovered.cli_session_id] = session
        session.source_notes = dict(discovered.notes)
        return session

    def _walk_candidates(self, root: Path) -> Iterator[Path]:
        """Every file under ``root`` that could be a deliverable."""
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            dirnames[:] = [
                d for d in dirnames
                if not (current / d).is_symlink() and not _is_excluded_dir(current / d)
            ]
            for name in filenames:
                yield current / name

    def _listed_candidates(
        self, session: SessionArtifacts, root: Path, files: tuple[Path, ...]
    ) -> Iterator[Path]:
        """The exact files a source named. One that is gone is COUNTED, never
        dropped quietly: a session whose writes were later deleted or moved is
        a real state the screen must be able to say out loud."""
        session.missing_source_files = []
        session.skipped_repository_files = 0
        for candidate in files:
            try:
                rel_parts = candidate.relative_to(root).parts
            except ValueError:
                continue
            if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts[:-1]):
                continue
            if any(
                (root / Path(*rel_parts[: index + 1]) / MANIFEST_NAME).exists()
                for index in range(len(rel_parts) - 1)
            ):
                continue
            # THE REPOSITORY RULE HOLDS FOR EVERY PROVIDER (ruled 2026-09-18,
            # lane CS-31). A working copy of a repository is never a session
            # deliverable, and this lane copies what it captures into a durable
            # folder and uploads it to AI Matrx. Claude Code's walk has always
            # excluded anything inside a checkout (``_is_excluded_dir``); an
            # explicit file list must not be a second door into the same
            # upload with the rule switched off, or "capture Codex the same
            # way" would quietly mean "start uploading the user's source code".
            #
            # The consequence is measured and deliberate, not a surprise: the
            # only file writes Codex records structurally are ``apply_patch``
            # paths, and those are almost all repository source, so this rule
            # excludes nearly every Codex write there is. That is reported as a
            # count and a sentence (``describe_gaps``) rather than resolved by
            # dropping the rule.
            if _inside_repository(candidate, root):
                session.skipped_repository_files += 1
                continue
            if not candidate.is_file():
                session.missing_source_files.append(candidate.relative_to(root).as_posix())
                continue
            yield candidate

    def _capture_session(self, discovered: DiscoveredSession) -> int:
        session = self._session(discovered)
        root = discovered.root
        session.skipped_over_size = 0
        session.skipped_over_count = 0
        captured = 0
        seen = 0
        changed = False
        candidates = (
            self._walk_candidates(root)
            if discovered.files is None
            else self._listed_candidates(session, root, discovered.files)
        )
        for source in candidates:
            name = source.name
            if name in _EXCLUDED_FILE_NAMES or Path(name).suffix in _EXCLUDED_SUFFIXES:
                continue
            if source.is_symlink():
                continue
            try:
                stat = source.stat()
            except OSError:
                continue
            if stat.st_size > MAX_FILE_BYTES:
                session.skipped_over_size += 1
                continue
            seen += 1
            if seen > MAX_FILES_PER_SESSION:
                session.skipped_over_count += 1
                continue
            rel = source.relative_to(root).as_posix()
            record = session.files.get(rel)
            mtime = int(stat.st_mtime)
            if record and record.get("size") == stat.st_size and record.get("mtime") == mtime:
                continue
            try:
                digest = _sha256(source)
                if record and record.get("sha256") == digest:
                    record["size"], record["mtime"] = stat.st_size, mtime
                    changed = True
                    continue
                target = session.durable_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            except OSError as exc:
                logger.info("[coding_session_artifacts] could not capture %s: %s", source, exc)
                continue
            # New content at a path the cloud already holds: keep the id of
            # the row it superseded so the ledger can say how many versions
            # of this session's work AI Matrx carries.
            previous = list((record or {}).get("previous_file_ids") or [])
            if record and record.get("file_id"):
                previous.append(str(record["file_id"]))
            session.files[rel] = {
                "size": stat.st_size,
                "mtime": mtime,
                "sha256": digest,
                "captured_at": _utc_now_iso(),
                "file_id": None,
                "uploaded_at": None,
                "upload_error": None,
                "upload_attempts": 0,
                "previous_file_ids": previous,
                "repair_attempts": int((record or {}).get("repair_attempts") or 0),
                "deduplicated": None,
                "cloud_file_path": None,
                "verified_at": None,
                "verify_error": None,
                "placement_repairs": int((record or {}).get("placement_repairs") or 0),
                "placement_error": None,
            }
            captured += 1
            changed = True
        if changed:
            self._write_manifest(session)
        return captured

    # ── publish ──────────────────────────────────────────────────────
    async def _publish_pending(self) -> tuple[int, int]:
        if not self._cloud_enabled:
            self._blocker = {
                "code": "cloud_disabled",
                "message": "Artifact publishing to AI Matrx is turned off; files are kept locally.",
                "remedy": "Turn cloud participation on in Matrx Local settings.",
            }
            return 0, 0
        pending = [
            (session, rel, record)
            for session in self._sessions.values()
            for rel, record in session.files.items()
            if not record.get("file_id")
            and int(record.get("upload_attempts") or 0) < MAX_UPLOAD_ATTEMPTS
        ]
        if not pending:
            self._blocker = None
            self._session_blocker_since = None
            return 0, 0
        tokens = TokenRepo(self._db)
        token_row = await tokens.get()
        jwt = token_row.get("access_token") if token_row else None
        if not jwt or tokens.is_expired(token_row):
            self._blocker = None
            self._session_blocker_since = (
                self._session_blocker_since or self._last_run_at or _utc_now_iso()
            )
            await request_session_grant(
                lane="coding_session_artifacts", reason="expired_or_missing"
            )
            return 0, 0
        self._client.set_jwt(jwt)
        counts = {"uploaded": 0, "failed": 0}
        touched: set[str] = set()
        halt = asyncio.Event()  # a session/organization refusal stops the wave
        gate = asyncio.Semaphore(UPLOAD_CONCURRENCY)

        async def _one(session: SessionArtifacts, rel: str, record: dict[str, Any]) -> None:
            if halt.is_set():
                return
            async with gate:
                if halt.is_set():
                    return
                touched.add(session.cli_session_id)
                path = session.durable_dir / rel
                try:
                    content = await asyncio.to_thread(path.read_bytes)
                    mime = _artifact_mime_type(rel)
                    response = await self._client.upload(
                        file_path=_cloud_path(session, rel),
                        content=content,
                        filename=Path(rel).name,
                        mime_type=mime,
                        visibility="private",
                        metadata={
                            "kind": "coding_session_artifact",
                            "provider": session.provider,
                            "cli_session_id": session.cli_session_id,
                            "project_slug": session.project_slug,
                            "relative_path": rel,
                            "sha256": record["sha256"],
                        },
                        # THE PATH IS THE POINT. Without a declared intent the
                        # door treats every write as alias_existing: bytes the
                        # account already holds resolve to that row and this
                        # (session, path) is recorded NOWHERE — measured
                        # 2026-09-17, 287 artifacts of three sessions filed
                        # under another session's row. force_new_copy gives
                        # this placement its own row, linked to the canonical
                        # content by duplicate_of_file_id.
                        intent="force_new_copy",
                        reason=(
                            "coding-session artifact placement: session "
                            f"{session.cli_session_id} path {rel}"
                        ),
                        request_id=f"csa:{session.cli_session_id}:{record['sha256'][:12]}",
                        # A REPAIR is a new operation, never a replay. Measured
                        # 2026-09-17 against production: re-sending the same
                        # idempotency key after the row went to the trash
                        # replayed the stored response — handing the ledger the
                        # dead file id again, forever. The repair round goes into
                        # the key so the door actually writes a new row.
                        idempotency_key=(
                            f"csa:{session.cli_session_id}:{rel}:{record['sha256']}"
                            + (f":r{repairs}" if (repairs := int(record.get("repair_attempts") or 0)) else "")
                        ),
                    )
                    file_id = response.get("file_id")
                    if not file_id:
                        raise RuntimeError("matrx-files upload returned no file_id")
                    record.update(
                        file_id=str(file_id),
                        uploaded_at=_utc_now_iso(),
                        upload_error=None,
                        deduplicated=None,
                        cloud_file_path=None,
                        verified_at=None,
                        verify_error=None,
                    )
                    # A returned id is a claim: the response's own ``is_new`` /
                    # ``file_path`` are not evidence (measured 2026-09-17 against
                    # production: ``is_new`` comes back null and ``file_path``
                    # merely echoes the request even when the bytes were aliased
                    # onto an existing row). Only the row the server serves back
                    # says what happened, so the read-back decides both whether
                    # this entry is in AI Matrx and whether it shares a file.
                    await self._confirm_record(record, expected_path=_cloud_path(session, rel))
                    counts["uploaded"] += 1
                    self._blocker = None
                    self._session_blocker_since = None
                except OrganizationNotResolvedError as exc:
                    self._blocker = {
                        "code": "no_organization",
                        "message": str(exc),
                        "remedy": exc.remedy,
                    }
                    halt.set()
                except FileSyncHTTPError as exc:
                    if exc.is_auth:
                        self._blocker = None
                        self._session_blocker_since = (
                            self._session_blocker_since
                            or self._last_run_at
                            or _utc_now_iso()
                        )
                        await request_session_grant(
                            lane="coding_session_artifacts", reason="rejected_401"
                        )
                        halt.set()
                        return
                    record["upload_error"] = f"HTTP {exc.status_code}: {exc.body[:300]}"
                    record["upload_attempts"] = int(record.get("upload_attempts") or 0) + 1
                    counts["failed"] += 1
                except Exception as exc:  # noqa: BLE001 — one bad file never stops the rest
                    record["upload_error"] = (
                        f"{type(exc).__name__}: {str(exc)[:300] or 'connection dropped mid-upload'}"
                    )
                    record["upload_attempts"] = int(record.get("upload_attempts") or 0) + 1
                    counts["failed"] += 1

        await asyncio.gather(*(_one(s, r, rec) for s, r, rec in pending[:UPLOADS_PER_TICK]))
        uploaded, failed = counts["uploaded"], counts["failed"]
        for sid in touched:
            await asyncio.to_thread(self._write_manifest, self._sessions[sid])
        return uploaded, failed

    # ── confirmation / repair ────────────────────────────────────────
    async def _confirm_record(
        self, record: dict[str, Any], *, expected_path: str | None = None
    ) -> bool:
        """Read the recorded cloud file id back. True when AI Matrx serves it.

        A file id the server does not serve (404/410, or a row in the trash) is
        not storage: the id is cleared, the reason is kept on the entry, and the
        ordinary publish wave uploads the durable copy again.

        The row's own ``file_path`` also settles the dedupe question: a row filed
        at a DIFFERENT path than this entry asked for is an existing
        byte-identical file this path now shares, not a file of its own.
        """
        file_id = record.get("file_id")
        if not file_id:
            return False
        try:
            row = await self._client.get_record(str(file_id))
        except FileSyncHTTPError as exc:
            if exc.status_code in (404, 410):
                self._mark_missing(record, f"HTTP {exc.status_code}: AI Matrx no longer serves this file")
                return False
            # Auth/rate-limit/5xx say nothing about the row — leave it awaiting.
            record["verify_error"] = None
            return False
        except Exception as exc:  # noqa: BLE001 — a read-back never breaks the wave
            record["verify_error"] = None
            logger.info("[coding_session_artifacts] could not confirm %s: %s", file_id, exc)
            return False
        if not isinstance(row, dict) or not row.get("id"):
            self._mark_missing(record, "AI Matrx returned no record for this file id")
            return False
        if row.get("deleted_at") or row.get("is_deleted"):
            self._mark_missing(record, "the AI Matrx row for this file is in the trash")
            return False
        cloud_path = row.get("file_path")
        if isinstance(cloud_path, str) and cloud_path:
            record["cloud_file_path"] = cloud_path
            if expected_path is not None:
                shares_another_row = cloud_path != expected_path
                record["deduplicated"] = shares_another_row
                if not shares_another_row:
                    # It has its own placement now — nothing left to announce.
                    record["placement_error"] = None
        record["verified_at"] = _utc_now_iso()
        record["verify_error"] = None
        return True

    def _queue_placement_repairs(self) -> int:
        """Re-file every entry that has no placement of its own.

        An entry whose cloud row sits at another path is in AI Matrx only as
        somebody else's file: the session's own path is recorded nowhere, so
        the app cannot list it under this session. The repair is the ordinary
        upload again, now DECLARING the placement, which the door answers with
        a row of this entry's own. Idempotent: once the row comes back filed at
        the expected path the entry is no longer ``deduplicated`` and is never
        queued again. Bounded: after ``MAX_PLACEMENT_REPAIRS`` rounds the entry
        stops and SAYS the door is not honouring the placement rather than
        re-uploading forever (that is what an old server build looks like).
        """
        queued = 0
        touched: set[str] = set()
        for session in self._sessions.values():
            for rel, record in session.files.items():
                if queued >= UPLOADS_PER_TICK:
                    break
                if not record.get("deduplicated") or not record.get("file_id"):
                    continue
                rounds = int(record.get("placement_repairs") or 0)
                if rounds >= MAX_PLACEMENT_REPAIRS:
                    record["placement_error"] = (
                        "AI Matrx filed this under "
                        f"{record.get('cloud_file_path') or 'another path'} instead of this "
                        "session's own path, and did not honour the placement request after "
                        f"{rounds} attempts. The file itself is safe here and in AI Matrx."
                    )
                    continue
                record.update(
                    placement_repairs=rounds + 1,
                    placement_error=(
                        "This path shares another file's AI Matrx row; it is being re-filed "
                        "under this session's own path."
                    ),
                    repair_attempts=int(record.get("repair_attempts") or 0) + 1,
                    file_id=None,
                    uploaded_at=None,
                    verified_at=None,
                    deduplicated=None,
                    cloud_file_path=None,
                    upload_attempts=0,
                    upload_error=None,
                )
                queued += 1
                touched.add(session.cli_session_id)
        for sid in touched:
            self._write_manifest(self._sessions[sid])
        if queued:
            logger.info(
                "[coding_session_artifacts] %s artifact path(s) had no placement of their own "
                "and are queued to be re-filed under their own session path",
                queued,
            )
        return queued

    @staticmethod
    def _mark_missing(record: dict[str, Any], reason: str) -> None:
        record["verify_error"] = reason
        record["verified_at"] = None
        record["file_id"] = None
        record["uploaded_at"] = None
        record["repair_attempts"] = int(record.get("repair_attempts") or 0) + 1
        # The repair is the re-upload: give the entry its attempts back so an
        # earlier exhausted budget cannot leave a known-missing file unsent.
        record["upload_attempts"] = 0
        record["upload_error"] = None

    async def _verify_backlog(self, *, recheck: bool = False) -> tuple[int, int]:
        """Confirm a bounded slice of entries against AI Matrx.

        The tick confirms what was never read back. ``recheck`` also re-reads
        the longest-unchecked confirmed entries — a row can disappear long after
        it was written, and a stale confirmation is the same lie as no check.
        """
        if not self._cloud_enabled:
            return 0, 0
        candidates = [
            (session, rel, record)
            for session in self._sessions.values()
            for rel, record in session.files.items()
            if record.get("file_id") and (recheck or not record.get("verified_at"))
        ]
        if recheck:
            candidates.sort(key=lambda triple: triple[2].get("verified_at") or "")
        stale = candidates[:VERIFY_PER_TICK]
        if not stale:
            return 0, 0
        tokens = TokenRepo(self._db)
        token_row = await tokens.get()
        jwt = token_row.get("access_token") if token_row else None
        if not jwt or tokens.is_expired(token_row):
            return 0, 0
        self._client.set_jwt(jwt)
        counts = {"confirmed": 0, "missing": 0}
        touched: set[str] = set()
        gate = asyncio.Semaphore(VERIFY_CONCURRENCY)

        async def _one(session: SessionArtifacts, rel: str, record: dict[str, Any]) -> None:
            async with gate:
                before = record.get("file_id")
                ok = await self._confirm_record(
                    record, expected_path=_cloud_path(session, rel)
                )
                if ok:
                    counts["confirmed"] += 1
                    touched.add(session.cli_session_id)
                elif record.get("file_id") != before:
                    counts["missing"] += 1
                    touched.add(session.cli_session_id)

        await asyncio.gather(*(_one(s, rel, r) for s, rel, r in stale))
        for sid in touched:
            await asyncio.to_thread(self._write_manifest, self._sessions[sid])
        if counts["missing"]:
            logger.warning(
                "[coding_session_artifacts] %s artifact(s) were missing from AI Matrx and are queued for re-upload",
                counts["missing"],
            )
        return counts["confirmed"], counts["missing"]

    async def verify_now(self) -> dict[str, Any]:
        """Operator-facing repair: confirm a slice now, re-upload what is gone,
        and re-file every path AI Matrx holds under somebody else's."""
        async with self._lock:
            confirmed, missing = await self._verify_backlog(recheck=True)
            refiling = self._queue_placement_repairs()
        result = {
            "confirmed": confirmed,
            "missing_in_cloud": missing,
            "queued_for_refiling": refiling,
        }
        if missing or refiling:
            async with self._lock:
                uploaded, failed = await self._publish_pending()
            result |= {"re_uploaded": uploaded, "re_upload_failed": failed}
        status = self.status()
        result |= {
            "files": status["files"],
            "uploaded": status["uploaded"],
            "awaiting_confirmation": status["awaiting_confirmation"],
            "still_missing_in_cloud": status["missing_in_cloud"],
            "still_unplaced": status["unplaced"],
        }
        return result

    # ── manifest ─────────────────────────────────────────────────────
    @staticmethod
    def _read_manifest(durable_dir: Path) -> dict[str, dict[str, Any]]:
        path = durable_dir / MANIFEST_NAME
        try:
            data = json.loads(path.read_bytes())
        except (OSError, ValueError):
            return {}
        files = data.get("files") if isinstance(data, dict) else None
        return {k: v for k, v in files.items() if isinstance(v, dict)} if isinstance(files, dict) else {}

    @staticmethod
    def _write_manifest(session: SessionArtifacts) -> None:
        session.durable_dir.mkdir(parents=True, exist_ok=True)
        path = session.durable_dir / MANIFEST_NAME
        payload = {
            "provider": session.provider,
            "cli_session_id": session.cli_session_id,
            "project_slug": session.project_slug,
            "source_root": str(session.source_root),
            "scratchpad": str(session.source_root),
            "written_at": _utc_now_iso(),
            "files": session.files,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
        os.replace(tmp, path)


def _cloud_path(session: SessionArtifacts, rel: str) -> str:
    """The ONE logical path this lane asks matrx-files to file an entry at."""
    return f"coding-sessions/{session.provider}/{session.cli_session_id}/{rel}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_artifact_session_source(
    provider: str, *, roots: list[Path] | None = None
) -> ArtifactSessionSource:
    """The source for one provider. The ONE place a provider is wired in."""
    if provider == DEFAULT_PROVIDER:
        return ScratchpadSessionSource(roots=roots)
    if roots is not None:
        raise ValueError(f"roots= only applies to {DEFAULT_PROVIDER}, not {provider!r}")
    if provider == "codex":
        from app.services.coding_sessions.codex_writes import CodexRolloutSessionSource

        return CodexRolloutSessionSource()
    raise ValueError(f"no artifact source for provider {provider!r}")


# Every provider whose sessions this lane captures. Coding sessions are ONE
# feature: a provider added here is captured, published, confirmed and
# repaired by exactly the same machinery, with its own durable root.
ARTIFACT_PROVIDERS: tuple[str, ...] = (DEFAULT_PROVIDER, "codex")

_lanes: dict[str, CodingSessionArtifactsLane] = {}


def get_coding_session_artifacts_lane(
    provider: str = DEFAULT_PROVIDER,
) -> CodingSessionArtifactsLane:
    """The one lane for a provider (default: Claude Code), created on first use."""
    lane = _lanes.get(provider)
    if lane is None:
        if provider not in ARTIFACT_PROVIDERS:
            raise ValueError(
                f"unknown coding-session artifact provider {provider!r}; "
                f"known: {', '.join(ARTIFACT_PROVIDERS)}"
            )
        from app.config import CLOUD_PARTICIPATION_ENABLED

        lane = CodingSessionArtifactsLane(
            provider=provider, cloud_enabled=CLOUD_PARTICIPATION_ENABLED
        )
        _lanes[provider] = lane
    return lane


def coding_session_artifact_lanes() -> list[CodingSessionArtifactsLane]:
    """Every configured lane — one per provider. Start/stop/status cover all."""
    return [get_coding_session_artifacts_lane(provider) for provider in ARTIFACT_PROVIDERS]


async def start_coding_session_artifact_lanes() -> None:
    for lane in coding_session_artifact_lanes():
        await lane.start_background()


async def stop_coding_session_artifact_lanes() -> None:
    for lane in coding_session_artifact_lanes():
        await lane.stop_background()


__all__ = [
    "ARTIFACT_PROVIDERS",
    "DEFAULT_PROVIDER",
    "ArtifactSessionSource",
    "CodingSessionArtifactsLane",
    "DiscoveredSession",
    "ScratchpadSessionSource",
    "SessionArtifacts",
    "build_artifact_session_source",
    "coding_session_artifact_lanes",
    "default_scratchpad_roots",
    "get_coding_session_artifacts_lane",
    "start_coding_session_artifact_lanes",
    "stop_coding_session_artifact_lanes",
]
