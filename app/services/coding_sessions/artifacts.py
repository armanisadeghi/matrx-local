"""Coding-session artifacts — keep what a session BUILT, not just what it said.

Claude Code writes a session's deliverables into a per-session scratchpad
under ``/tmp/claude-<uid>/<project-slug>/<session-id>/scratchpad/…``. The
bridge mirrors the conversation to AI Matrx but never those files, so
switching Claude accounts hid them, AI Matrx showed conversations without
the things they produced, and macOS eventually purges /tmp. Measured
2026-09-12: 723 scratchpads, 11 GB, most of it venvs/clones/node_modules
next to the handful of real deliverables.

This lane, every cycle:

1. scans every scratchpad it can see (all projects, all sessions — a
   session hidden by an account switch is still on disk);
2. copies every DELIVERABLE file (see ``_is_excluded_dir`` / size cap) into
   the durable per-session folder ``<app data>/coding-sessions/artifacts/
   claude_code/<session-id>/<relative path>`` — outside /tmp, readable by
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
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

PROVIDER = "claude_code"
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


def _is_excluded_dir(path: Path) -> bool:
    if path.name in _EXCLUDED_DIR_NAMES:
        return True
    # A git checkout or worktree inside the scratchpad is a working copy of
    # a repository, never a deliverable of the session; a folder carrying our
    # own manifest is a durable artifact folder (never capture ourselves).
    return (path / ".git").exists() or (path / MANIFEST_NAME).exists()


@dataclass
class SessionArtifacts:
    provider: str
    cli_session_id: str
    project_slug: str
    scratchpad: Path
    durable_dir: Path
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped_over_size: int = 0
    skipped_over_count: int = 0

    @property
    def uploaded_count(self) -> int:
        return sum(1 for f in self.files.values() if f.get("file_id"))

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
            "scratchpad": str(self.scratchpad),
            "durable_dir": str(self.durable_dir),
            "files": len(self.files),
            "bytes": sum(int(f.get("size") or 0) for f in self.files.values()),
            "uploaded": self.uploaded_count,
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
        roots: list[Path] | None = None,
        durable_root: Path | None = None,
        files_client: MatrxFilesClient | None = None,
        cloud_enabled: bool = True,
    ) -> None:
        self._db = db or get_db()
        self._roots_override = roots
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
    def roots(self) -> list[Path]:
        return self._roots_override if self._roots_override is not None else default_scratchpad_roots()

    @property
    def durable_root(self) -> Path:
        root = self._durable_root_override or (
            safe_dir("data") / "coding-sessions" / "artifacts" / PROVIDER
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
        self._task = asyncio.create_task(self._loop(), name="coding-session-artifacts")

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
                logger.warning("[coding_session_artifacts] tick failed", exc_info=True)
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
            "roots": [str(r) for r in self.roots],
            "durable_root": str(self.durable_root),
            "sessions": len(sessions),
            "files": sum(len(s.files) for s in sessions),
            "bytes": sum(int(f.get("size") or 0) for s in sessions for f in s.files.values()),
            "uploaded": sum(s.uploaded_count for s in sessions),
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
                "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
            },
        }

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
            self._last_run_at = _utc_now_iso()
            self._last_run_seconds = round((datetime.now(UTC) - started).total_seconds(), 3)
            self._last_tick = {
                "at": self._last_run_at,
                "seconds": self._last_run_seconds,
                "captured": captured,
                "uploaded": uploaded,
                "failed": failed,
            }
            return dict(self._last_tick)

    # ── capture (thread) ─────────────────────────────────────────────
    def _capture_all(self) -> int:
        captured = 0
        for root in self.roots:
            try:
                projects = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink()]
            except OSError:
                continue
            for project in projects:
                try:
                    sessions = [
                        s for s in project.iterdir() if s.is_dir() and not s.is_symlink()
                    ]
                except OSError:
                    continue
                for session_dir in sessions:
                    scratchpad = session_dir / "scratchpad"
                    if not scratchpad.is_dir():
                        continue
                    captured += self._capture_session(project.name, session_dir.name, scratchpad)
        return captured

    def _session(self, project_slug: str, cli_session_id: str, scratchpad: Path) -> SessionArtifacts:
        session = self._sessions.get(cli_session_id)
        if session is None:
            durable_dir = self.durable_root / cli_session_id
            session = SessionArtifacts(
                provider=PROVIDER,
                cli_session_id=cli_session_id,
                project_slug=project_slug,
                scratchpad=scratchpad,
                durable_dir=durable_dir,
                files=self._read_manifest(durable_dir),
            )
            self._sessions[cli_session_id] = session
        return session

    def _capture_session(self, project_slug: str, cli_session_id: str, scratchpad: Path) -> int:
        session = self._session(project_slug, cli_session_id, scratchpad)
        session.skipped_over_size = 0
        session.skipped_over_count = 0
        captured = 0
        seen = 0
        changed = False
        for dirpath, dirnames, filenames in os.walk(scratchpad):
            current = Path(dirpath)
            dirnames[:] = [
                d for d in dirnames
                if not (current / d).is_symlink() and not _is_excluded_dir(current / d)
            ]
            for name in filenames:
                if name in _EXCLUDED_FILE_NAMES or Path(name).suffix in _EXCLUDED_SUFFIXES:
                    continue
                source = current / name
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
                rel = source.relative_to(scratchpad).as_posix()
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
                session.files[rel] = {
                    "size": stat.st_size,
                    "mtime": mtime,
                    "sha256": digest,
                    "captured_at": _utc_now_iso(),
                    "file_id": None,
                    "uploaded_at": None,
                    "upload_error": None,
                    "upload_attempts": 0,
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
                        file_path=f"coding-sessions/{session.provider}/{session.cli_session_id}/{rel}",
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
                        request_id=f"csa:{session.cli_session_id}:{record['sha256'][:12]}",
                        idempotency_key=f"csa:{session.cli_session_id}:{rel}:{record['sha256']}",
                    )
                    file_id = response.get("file_id")
                    if not file_id:
                        raise RuntimeError("matrx-files upload returned no file_id")
                    record.update(
                        file_id=str(file_id), uploaded_at=_utc_now_iso(), upload_error=None
                    )
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
            "scratchpad": str(session.scratchpad),
            "written_at": _utc_now_iso(),
            "files": session.files,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
        os.replace(tmp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_lane: CodingSessionArtifactsLane | None = None


def get_coding_session_artifacts_lane() -> CodingSessionArtifactsLane:
    global _lane
    if _lane is None:
        from app.config import CLOUD_PARTICIPATION_ENABLED

        _lane = CodingSessionArtifactsLane(cloud_enabled=CLOUD_PARTICIPATION_ENABLED)
    return _lane


__all__ = [
    "CodingSessionArtifactsLane",
    "SessionArtifacts",
    "default_scratchpad_roots",
    "get_coding_session_artifacts_lane",
]
