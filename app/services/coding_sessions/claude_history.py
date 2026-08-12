from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app.services.coding_sessions.models import BridgeRequest
from app.services.coding_sessions.service import CodingSessionBridgeOutbox
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import SyncMetaRepo, TokenRepo

IMPORTER_VERSION = "matrx-local/claude-history-v1"
MAX_DISCOVERED_SESSIONS = 10_000
MAX_PREVIEW_SESSIONS = 200
MAX_SELECTED_SESSIONS = 10
MAX_IMPORT_BYTES = 67_108_864
MAX_LINE_BYTES = 2_097_152
MAX_BATCH_ENTRIES = 100
MAX_BATCH_BYTES = 4_194_304


class ClaudeHistorySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    source_revision: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ClaudeHistoryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_account_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sessions: Annotated[
        list[ClaudeHistorySelection],
        Field(min_length=1, max_length=MAX_SELECTED_SESSIONS),
    ]


class ClaudeHistoryConflict(ValueError):
    pass


@dataclass(frozen=True)
class _AccountSnapshot:
    available: bool
    account_key: str | None
    fingerprint: str | None
    client_version: str | None
    reason: str | None


@dataclass(frozen=True)
class _SessionSource:
    session_id: str
    project_dir: Path
    main_file: Path
    streams: tuple[tuple[str, Path], ...]
    source_revision: str
    total_bytes: int
    latest_mtime_ns: int
    project_key: str
    project_name: str
    title: str
    git_branch: str | None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _conversation_id(user_id: str, provider_session_id: str) -> str:
    value = (
        "matrx:coding-session:"
        f"{user_id}:claude_code:{provider_session_id}:conversation"
    )
    return str(uuid5(NAMESPACE_URL, value))


def _safe_json(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(
            raw,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _source_revision(streams: tuple[tuple[str, Path], ...]) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total_bytes = 0
    latest_mtime_ns = 0
    for stream_key, path in streams:
        stat = path.stat()
        total_bytes += stat.st_size
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        digest.update(stream_key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode())
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode())
        digest.update(b"\0")
    return digest.hexdigest(), total_bytes, latest_mtime_ns


def _read_summary(path: Path, session_id: str) -> tuple[str, str, str | None]:
    title: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    candidates: list[bytes] = []
    with path.open("rb") as handle:
        for _ in range(40):
            line = handle.readline(262_145)
            if not line:
                break
            if len(line) <= 262_144:
                candidates.append(line)
        size = path.stat().st_size
        if size > 262_144:
            handle.seek(max(0, size - 262_144))
            if handle.tell() > 0:
                handle.readline()
            candidates.extend(handle.readlines())
    for raw in candidates:
        record = _safe_json(raw)
        if record is None:
            continue
        record_type = record.get("type")
        candidate_title = (
            record.get("customTitle")
            if record_type == "custom-title"
            else record.get("aiTitle") if record_type == "ai-title" else None
        )
        if isinstance(candidate_title, str) and candidate_title.strip():
            title = candidate_title.strip()[:160]
        if cwd is None and isinstance(record.get("cwd"), str):
            cwd = record["cwd"]
        if isinstance(record.get("gitBranch"), str) and record["gitBranch"]:
            git_branch = record["gitBranch"][:160]
    project_name = Path(cwd).name if cwd else "Local Claude project"
    return title or f"Claude session {session_id[:8]}", project_name, git_branch


def _discover_sources(config_dir: Path) -> tuple[list[_SessionSource], dict[str, int]]:
    projects_root = config_dir / "projects"
    if not projects_root.is_dir():
        return [], {"files": 0, "bytes": 0, "projects": 0}
    sources: list[_SessionSource] = []
    all_file_count = 0
    all_bytes = 0
    project_count = 0
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        roots: list[Path] = []
        for child in project_dir.iterdir():
            if child.is_file() and child.suffix == ".jsonl":
                try:
                    UUID(child.stem)
                except ValueError:
                    continue
                roots.append(child)
        if not roots:
            continue
        project_count += 1
        for main_file in roots:
            session_id = main_file.stem
            stream_list: list[tuple[str, Path]] = [("main", main_file)]
            subagent_dir = project_dir / session_id / "subagents"
            if subagent_dir.is_dir():
                for subagent in sorted(subagent_dir.glob("*.jsonl")):
                    stream_list.append((f"subagent:{subagent.stem}", subagent))
            streams = tuple(stream_list)
            revision, total_bytes, latest_mtime_ns = _source_revision(streams)
            all_file_count += len(streams)
            all_bytes += total_bytes
            title, project_name, git_branch = _read_summary(main_file, session_id)
            sources.append(
                _SessionSource(
                    session_id=session_id,
                    project_dir=project_dir,
                    main_file=main_file,
                    streams=streams,
                    source_revision=revision,
                    total_bytes=total_bytes,
                    latest_mtime_ns=latest_mtime_ns,
                    project_key="claude-local:" + _sha256_text(str(project_dir.resolve())),
                    project_name=project_name,
                    title=title,
                    git_branch=git_branch,
                )
            )
            if len(sources) > MAX_DISCOVERED_SESSIONS:
                raise ValueError(
                    f"Claude history exceeds the {MAX_DISCOVERED_SESSIONS}-session discovery cap"
                )
    sources.sort(key=lambda source: source.latest_mtime_ns, reverse=True)
    return sources, {
        "files": all_file_count,
        "bytes": all_bytes,
        "projects": project_count,
    }


async def _read_account_snapshot() -> _AccountSnapshot:
    executable = shutil.which("claude")
    if executable is None:
        return _AccountSnapshot(False, None, None, None, "claude_not_installed")
    try:
        auth_process = await asyncio.create_subprocess_exec(
            executable,
            "auth",
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(auth_process.communicate(), timeout=5)
        version_process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        version_stdout, _ = await asyncio.wait_for(
            version_process.communicate(), timeout=5
        )
    except (OSError, asyncio.TimeoutError):
        return _AccountSnapshot(False, None, None, None, "claude_status_unavailable")
    if auth_process.returncode != 0:
        return _AccountSnapshot(False, None, None, None, "claude_not_signed_in")
    snapshot = _safe_json(stdout)
    if not snapshot or snapshot.get("loggedIn") is not True:
        return _AccountSnapshot(False, None, None, None, "claude_not_signed_in")
    material = [
        snapshot.get("apiProvider"),
        snapshot.get("authMethod"),
        snapshot.get("orgId"),
        str(snapshot.get("email", "")).strip().lower() or None,
    ]
    if not any(isinstance(value, str) and value for value in material[2:]):
        return _AccountSnapshot(
            False,
            None,
            None,
            version_stdout.decode(errors="replace").strip()[:64] or None,
            "claude_account_identity_unavailable",
        )
    account_key = _sha256_text(
        "claude-code-account-v1\0" + "\0".join(str(value or "") for value in material)
    )
    return _AccountSnapshot(
        True,
        account_key,
        account_key[:12],
        version_stdout.decode(errors="replace").strip()[:64] or None,
        None,
    )


class ClaudeHistoryImporter:
    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        outbox: CodingSessionBridgeOutbox | None = None,
        config_dir: Path | None = None,
        account_reader: Callable[[], Any] = _read_account_snapshot,
    ) -> None:
        self._db = db or get_db()
        self._outbox = outbox
        self._config_dir = config_dir or Path.home() / ".claude"
        self._account_reader = account_reader
        self._tokens = TokenRepo(self._db)
        self._sync_meta = SyncMetaRepo(self._db)

    async def preview(self, *, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= MAX_PREVIEW_SESSIONS:
            raise ValueError(f"limit must be between 1 and {MAX_PREVIEW_SESSIONS}")
        account, discovered = await asyncio.gather(
            self._account_reader(),
            asyncio.to_thread(_discover_sources, self._config_dir),
        )
        sources, totals = discovered
        token_row = await self._tokens.get()
        matrx_user_available = bool(token_row and token_row.get("user_id"))
        return {
            "schema_version": 1,
            "source": "claude_local_jsonl",
            "explicit_action_required": True,
            "account_identity_available": account.available,
            "provider_account_key": account.account_key,
            "account_fingerprint": account.fingerprint,
            "account_blocked_reason": account.reason,
            "claude_client_version": account.client_version,
            "matrx_user_available": matrx_user_available,
            "import_ready": account.available and matrx_user_available,
            "totals": {
                "session_count": len(sources),
                "file_count": totals["files"],
                "bytes": totals["bytes"],
                "project_count": totals["projects"],
            },
            "limits": {
                "preview_sessions": MAX_PREVIEW_SESSIONS,
                "selected_sessions": MAX_SELECTED_SESSIONS,
                "import_bytes": MAX_IMPORT_BYTES,
                "line_bytes": MAX_LINE_BYTES,
            },
            "sessions": [
                {
                    "session_id": source.session_id,
                    "source_revision": source.source_revision,
                    "title": source.title,
                    "project_name": source.project_name,
                    "project_key": source.project_key,
                    "git_branch": source.git_branch,
                    "bytes": source.total_bytes,
                    "file_count": len(source.streams),
                    "subagent_count": len(source.streams) - 1,
                    "last_modified_ns": source.latest_mtime_ns,
                }
                for source in sources[:limit]
            ],
            "truncated": len(sources) > limit,
        }

    async def import_selected(
        self, request: ClaudeHistoryImportRequest
    ) -> dict[str, Any]:
        account = await self._account_reader()
        if not account.available or account.account_key is None:
            raise ClaudeHistoryConflict(
                account.reason or "Claude account identity is unavailable"
            )
        if account.account_key != request.provider_account_key:
            raise ClaudeHistoryConflict(
                "Claude account changed after preview; preview again before syncing"
            )
        token_row = await self._tokens.get()
        user_id = str(token_row.get("user_id")) if token_row else ""
        if not user_id:
            raise ClaudeHistoryConflict("Sign in to AI Matrx before syncing Claude history")

        sources, _totals = await asyncio.to_thread(
            _discover_sources, self._config_dir
        )
        by_id = {source.session_id: source for source in sources}
        selected_sources: list[_SessionSource] = []
        total_bytes = 0
        for selection in request.sessions:
            source = by_id.get(str(selection.session_id))
            if source is None:
                raise ClaudeHistoryConflict(
                    f"Claude session {selection.session_id} is no longer available"
                )
            if source.source_revision != selection.source_revision:
                raise ClaudeHistoryConflict(
                    f"Claude session {selection.session_id} changed after preview"
                )
            total_bytes += source.total_bytes
            if total_bytes > MAX_IMPORT_BYTES:
                raise ValueError(
                    f"Selected transcripts exceed the {MAX_IMPORT_BYTES}-byte import cap"
                )
            selected_sources.append(source)

        requests: list[BridgeRequest] = []
        imported_entries = 0
        corrupt_lines = 0
        for source in selected_sources:
            session_requests, entry_count, bad_count = await asyncio.to_thread(
                self._requests_for_source,
                source,
                user_id,
                account,
            )
            current_revision, _size, _mtime = _source_revision(source.streams)
            if current_revision != source.source_revision:
                raise ClaudeHistoryConflict(
                    f"Claude session {source.session_id} changed while it was being read"
                )
            requests.extend(session_requests)
            imported_entries += entry_count
            corrupt_lines += bad_count

        outbox = self._outbox
        if outbox is None:
            from app.services.coding_sessions.service import (
                get_coding_session_bridge_outbox,
            )

            outbox = get_coding_session_bridge_outbox()
        queued = await outbox.enqueue_many(requests)
        await self._sync_meta.set_last_sync(
            "claude_history_import",
            status="queued",
            last_hash=_sha256_text(
                "\0".join(source.source_revision for source in selected_sources)
            ),
        )
        return {
            "schema_version": 1,
            "accepted": True,
            "provider_account_fingerprint": account.fingerprint,
            "selected_sessions": len(selected_sources),
            "entries": imported_entries,
            "corrupt_lines": corrupt_lines,
            "source_complete": corrupt_lines == 0,
            "queued_batches": queued["queued"],
            "duplicate_pending_batches": queued["duplicate_pending"],
            "pending_outbox": queued["pending"],
            "native_restore_available": False,
            "continuation": "Open the original local Claude transcript with claude --resume <session-id> only while that local file, workspace, and login remain available.",
        }

    def _requests_for_source(
        self,
        source: _SessionSource,
        user_id: str,
        account: _AccountSnapshot,
    ) -> tuple[list[BridgeRequest], int, int]:
        transcript_digest = hashlib.sha256()
        parsed_streams: list[tuple[str, list[dict[str, Any]]]] = []
        valid_entries = 0
        corrupt_lines = 0
        for stream_key, path in source.streams:
            stream_entries: list[dict[str, Any]] = []
            seen_entry_ids: set[str] = set()
            transcript_digest.update(stream_key.encode("utf-8") + b"\0")
            with path.open("rb") as handle:
                for sequence, raw in enumerate(handle):
                    transcript_digest.update(raw)
                    if len(raw) > MAX_LINE_BYTES:
                        corrupt_lines += 1
                        continue
                    payload = _safe_json(raw)
                    if payload is None:
                        corrupt_lines += 1
                        continue
                    payload_hash = hashlib.sha256(
                        json.dumps(
                            payload,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        ).encode("utf-8")
                    ).hexdigest()
                    candidate_id = payload.get("uuid")
                    entry_id = (
                        candidate_id
                        if isinstance(candidate_id, str) and candidate_id
                        else f"line:{sequence}:{payload_hash[:32]}"
                    )
                    if entry_id in seen_entry_ids:
                        raise ValueError(
                            f"Claude transcript {source.session_id} reuses entry UUID {entry_id}"
                        )
                    seen_entry_ids.add(entry_id)
                    kind_value = payload.get("type")
                    kind = (
                        kind_value[:256]
                        if isinstance(kind_value, str) and kind_value
                        else "unknown"
                    )
                    entry: dict[str, Any] = {
                        "entry_id": entry_id[:512],
                        "source_sequence": sequence,
                        "kind": kind,
                        "payload_sha256": payload_hash,
                        "payload": payload,
                        "source_cursor": {
                            "source_kind": "claude_local_jsonl",
                            "line_number": sequence + 1,
                        },
                    }
                    timestamp = payload.get("timestamp")
                    if isinstance(timestamp, str):
                        try:
                            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        except ValueError:
                            pass
                        else:
                            entry["occurred_at"] = timestamp
                    stream_entries.append(entry)
                    valid_entries += 1
                    if valid_entries > 1_000_000:
                        raise ValueError("Claude transcript exceeds the entry-count cap")
            parsed_streams.append((stream_key, stream_entries))

        metadata = {
            "source_kind": "claude_local_jsonl",
            "provider_account_key": account.account_key,
            "importer_version": IMPORTER_VERSION,
            "client_version": account.client_version,
            "transcript_sha256": transcript_digest.hexdigest(),
            "transcript_bytes": source.total_bytes,
            "transcript_entry_count": valid_entries,
            "transcript_mtime_ns": source.latest_mtime_ns,
            "source_complete": corrupt_lines == 0,
            "corrupt_line_count": corrupt_lines,
        }
        conversation_id = _conversation_id(user_id, source.session_id)
        runtime_id = f"matrx-local:claude-history:{account.account_key}"
        requests: list[BridgeRequest] = []
        for stream_key, entries in parsed_streams:
            batch: list[dict[str, Any]] = []
            batch_bytes = 0
            for entry in entries:
                entry_bytes = len(
                    json.dumps(
                        entry,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                )
                if batch and (
                    len(batch) >= MAX_BATCH_ENTRIES
                    or batch_bytes + entry_bytes > MAX_BATCH_BYTES
                ):
                    requests.append(
                        self._append_request(
                            source,
                            stream_key,
                            batch,
                            metadata,
                            conversation_id,
                            runtime_id,
                        )
                    )
                    batch = []
                    batch_bytes = 0
                batch.append(entry)
                batch_bytes += entry_bytes
            if batch:
                requests.append(
                    self._append_request(
                        source,
                        stream_key,
                        batch,
                        metadata,
                        conversation_id,
                        runtime_id,
                    )
                )
        if not requests:
            raise ValueError(f"Claude session {source.session_id} has no valid entries")
        return requests, valid_entries, corrupt_lines

    @staticmethod
    def _append_request(
        source: _SessionSource,
        stream_key: str,
        entries: list[dict[str, Any]],
        metadata: dict[str, Any],
        conversation_id: str,
        runtime_id: str,
    ) -> BridgeRequest:
        return BridgeRequest.model_validate(
            {
                "action": "append_native",
                "provider": "claude_code",
                "provider_session_id": source.session_id,
                "provider_project_key": source.project_key,
                "conversation": {
                    "conversation_id": conversation_id,
                    "is_new": True,
                    "store": True,
                },
                "origin": "matrx_local",
                "stream_key": stream_key,
                "entries": entries,
                "source_metadata": metadata,
                "writer_runtime_id": runtime_id,
                "writer_lease_seconds": 300,
            }
        )

    async def status(self) -> dict[str, Any]:
        outbox = self._outbox
        if outbox is None:
            from app.services.coding_sessions.service import (
                get_coding_session_bridge_outbox,
            )

            outbox = get_coding_session_bridge_outbox()
        outbox_status = await outbox.status()
        sync = await self._sync_meta.get_last_sync("claude_history_import")
        return {
            "schema_version": 1,
            "source": "claude_local_jsonl",
            "pending_outbox": outbox_status["pending"],
            "oldest_pending": outbox_status["oldest"],
            "last_sync": sync,
            "native_restore_available": False,
        }


__all__ = [
    "ClaudeHistoryConflict",
    "ClaudeHistoryImporter",
    "ClaudeHistoryImportRequest",
    "ClaudeHistorySelection",
]
