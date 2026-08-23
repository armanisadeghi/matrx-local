"""The LOCAL Claude Code runtime — start/send/cancel/resume on the user's Mac.

This is the desktop half of the managed provider-runtime story. The hosted
half (`aidream/services/coding_session_bridge/claude_managed_runtime.py`) runs
the Claude Agent SDK inside a Matrx Sandbox against brokered credentials; THIS
runtime runs the same official SDK on the user's own machine, against the
user's own installed Claude Code login, in the user's own repositories. That
is the entire point: the user's Anthropic subscription on the user's machine
is the user's own credential being used by the user.

Design decisions (all deliberate — read before changing):

- **The user's own `claude` is the CLI.** We resolve the installed Claude Code
  binary and pass it as ``cli_path``. No API key, no token broker, no
  ``CLAUDE_CONFIG_DIR`` override: the session uses the user's existing
  subscription login and writes its transcript into the user's real
  ``~/.claude/projects/<cwd-slug>/<session>.jsonl`` — so every session this
  runtime starts is a first-class Claude Code session the user can open,
  `--resume`, and see in Claude Code's own UI. If the CLI or its login is
  missing, the capability probe says exactly why; nothing silently falls back
  to another auth path.
- **Persistence is the EXISTING import pipeline, not a second transport.**
  After each completed turn (and at settle) the runtime runs a targeted pass
  of `ClaudeHistoryImporter.import_selected` for this one session. That is the
  certified path: exact JSONL entries become `append_native` batches in the
  durable outbox, land in `chat.coding_session` as fidelity=native with the
  writer lease held, carry account identity + Claude's own label, and are
  replay-safe. Live tokens stream over local SSE; the cloud ledger advances at
  turn boundaries.
- **The workspace allowlist is the safety boundary.** Only directories under
  an allowlisted root (default ``~/code``) can run, and each folder must be
  explicitly approved once (persisted). Everything else is refused loudly.
- **Resume is capability-gated truth.** `resumable()` answers only from
  Claude's own local store: the transcript file must exist on this machine.
  No local file → not resumable → the product offers a labeled seeded
  handoff, never a fake Resume.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app.common.system_logger import get_logger
from app.services.app_config import get_app_config
from app.services.app_config.models import CodingSessionRuntimeConfig
from app.services.coding_sessions.claude_probe import (
    read_account_snapshot as _read_account_snapshot,
    resolve_claude_executable,
)
from app.services.coding_sessions.claude_history import (
    ClaudeHistoryConflict,
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
    ClaudeHistorySelection,
    _bridge_provider_session_id,
    _conversation_id,
    _hash_source,
)
from app.services.coding_sessions.workspace_discovery import (
    WorkspaceDiscoveryResponse,
    discover_workspace_tree,
)
from app.services.local_db.database import get_db
from app.services.local_db.repositories import TokenRepo

logger = get_logger()

_JOURNAL_BUSY_TIMEOUT_MS = 15_000
_RESTART_REASON = "engine_restarted_before_terminal_state"
_PROMPT_NOT_RETAINED = "Prompt not retained"
_DURABLE_EXECUTION_ERRORS = frozenset(
    {
        "Local runtime stopped before a terminal provider result.",
        "Provider execution became idle beyond the configured limit.",
        "Provider execution exceeded the configured wall-clock limit.",
    }
)

WORKSPACE_ROOTS_SETTING = "claude_runtime_workspace_roots"
APPROVED_FOLDERS_SETTING = "claude_runtime_approved_folders"
DEFAULT_WORKSPACE_ROOTS = ["~/code"]


def _utc_iso(value: float | None = None) -> str:
    moment = datetime.now(tz=UTC) if value is None else datetime.fromtimestamp(value, tz=UTC)
    return moment.isoformat().replace("+00:00", "Z")


class LocalRuntimeRefused(RuntimeError):
    """The request is refused loudly — never silently downgraded."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class _ExecutionIdleTimeout(TimeoutError):
    """No SDK event arrived inside the configured idle window."""


class _ExecutionWallTimeout(TimeoutError):
    """The whole provider execution exceeded its configured wall clock."""


class LocalRuntimeStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(min_length=1, max_length=4096)
    prompt: str = Field(min_length=1, max_length=200_000)
    # RESUME (which is also SEND — a follow-up turn IS a resume with a new
    # prompt) names the raw Claude session UUID to continue with history.
    resume_session_id: str | None = Field(default=None, min_length=1, max_length=1024)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    max_turns: int = Field(default=30, ge=1, le=200)
    permission_mode: Literal["acceptEdits", "plan", "bypassPermissions"] = "acceptEdits"


class LocalRuntimeFolderRequest(BaseModel):
    """A folder selected by the user in the native desktop picker."""

    model_config = ConfigDict(extra="forbid")

    folder: str = Field(min_length=1, max_length=4096)


class LocalRuntimeWorkspaceRootsResponse(BaseModel):
    """Persisted filesystem authority plus approvals affected by a root change."""

    model_config = ConfigDict(extra="forbid")

    workspace_roots: list[str]
    approved_folders: list[str]
    affected_approvals: list[str] = Field(default_factory=list)


@dataclass
class _LocalRun:
    runtime_id: str
    session_id: str
    workspace: Path
    action: str  # "start" | "resume"
    status: str = "starting"  # starting|running|completed|failed|cancelled
    prompt_preview: str = ""
    provider_session_id: str | None = None
    provider_project_key: str | None = None
    conversation_id: str | None = None
    transcript_path: Path | None = None
    error: str | None = None
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    turns_completed: int = 0
    mirror_passes: int = 0
    mirror_error: str | None = None
    restart_reason: str | None = None
    runtime_config: dict[str, Any] = field(default_factory=dict)
    runtime_config_provenance: dict[str, Any] = field(default_factory=dict)
    next_sequence: int = 1
    client: Any = None
    task: asyncio.Task[None] | None = None
    events: deque[dict[str, Any]] = field(default_factory=deque)
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(
        default_factory=list
    )
    identity_ready: asyncio.Event = field(default_factory=asyncio.Event)
    emit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def mirror_status(self) -> str:
        if self.mirror_error is not None:
            return "failed"
        if self.mirror_passes > 0:
            return "enqueued"
        if self.status in {"starting", "running"}:
            return "pending"
        return "not_started"

    def public_status(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "session_id": self.session_id,
            "action": self.action,
            "status": self.status,
            "execution": {"status": self.status, "error": self.error},
            "workspace": str(self.workspace),
            "prompt_preview": self.prompt_preview,
            "provider_session_id": self.provider_session_id,
            "conversation_id": self.conversation_id,
            "error": self.error,
            "restart_reason": self.restart_reason,
            "started_at": _utc_iso(self.started_at),
            "ended_at": (
                _utc_iso(self.ended_at)
                if self.ended_at
                else None
            ),
            "turns_completed": self.turns_completed,
            "mirror_passes": self.mirror_passes,
            "mirror_error": self.mirror_error,
            "mirror": {
                "status": self.mirror_status(),
                "passes": self.mirror_passes,
                "error": self.mirror_error,
                "conversation_id": self.conversation_id,
            },
            "event_count": len(self.events),
            "first_event_sequence": (
                int(self.events[0]["sequence"]) if self.events else None
            ),
            "last_event_sequence": self.next_sequence - 1,
            "runtime_config_snapshot": {
                **self.runtime_config,
                **self.runtime_config_provenance,
            },
        }


def _message_payload(message: object) -> dict[str, Any]:
    if is_dataclass(message) and not isinstance(message, type):
        payload = asdict(message)
    elif hasattr(message, "model_dump"):
        payload = message.model_dump(mode="json")  # type: ignore[union-attr]
    else:
        payload = {"value": repr(message)}
    # Events cross an SSE boundary; anything non-JSON-serializable is repr'd.
    return json.loads(json.dumps(payload, default=repr))


def _settings():
    from app.services.cloud_sync.settings_sync import get_settings_sync

    return get_settings_sync()


def _workspace_roots() -> list[Path]:
    raw = _settings().get(WORKSPACE_ROOTS_SETTING, None)
    if raw is None:
        raw = DEFAULT_WORKSPACE_ROOTS
    roots: list[Path] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                roots.append(Path(item).expanduser())
        # An explicit empty list is meaningful: the user removed every root.
        return roots
    return [Path(DEFAULT_WORKSPACE_ROOTS[0]).expanduser()]


def _approved_folders() -> list[str]:
    raw = _settings().get(APPROVED_FOLDERS_SETTING, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def _is_under(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


class LocalClaudeRuntime:
    """Owns every locally-launched Claude Code session in this engine."""

    def __init__(
        self,
        *,
        importer: ClaudeHistoryImporter | None = None,
        db: Any = None,
        account_reader: Any = _read_account_snapshot,
        runtime_config: CodingSessionRuntimeConfig | None = None,
    ) -> None:
        self._runs: dict[str, _LocalRun] = {}
        self._importer = importer
        self._db = db
        self._account_reader = account_reader
        self._injected_runtime_config = runtime_config
        self._lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()
        self._hydrate_lock = asyncio.Lock()
        self._hydrated = False

    def _database(self):
        return self._db if self._db is not None else get_db()

    def _runtime_config(self) -> CodingSessionRuntimeConfig:
        if self._injected_runtime_config is not None:
            return self._injected_runtime_config
        return get_app_config().row.config.coding_session_runtime

    def _runtime_config_status(self) -> dict[str, Any]:
        config = self._runtime_config()
        values = config.model_dump(mode="json")
        if self._injected_runtime_config is not None:
            return {
                "source": "injected",
                "field_sources": {key: "injected" for key in values},
                **values,
            }
        resolved = get_app_config()
        section_supplied = (
            "coding_session_runtime" in resolved.row.config.model_fields_set
        )
        supplied_fields = config.model_fields_set if section_supplied else set()
        field_sources = {
            key: resolved.tier if key in supplied_fields else "compiled_defaults"
            for key in values
        }
        unique_sources = set(field_sources.values())
        source = next(iter(unique_sources)) if len(unique_sources) == 1 else "mixed"
        return {"source": source, "field_sources": field_sources, **values}

    def _apply_runtime_config_snapshot(self, run: _LocalRun) -> None:
        snapshot = self._runtime_config_status()
        run.runtime_config = {
            key: value
            for key, value in snapshot.items()
            if key not in {"source", "field_sources"}
        }
        run.runtime_config_provenance = {
            "source": snapshot["source"],
            "field_sources": snapshot["field_sources"],
        }

    @staticmethod
    def _durable_execution_error(error: str | None) -> str | None:
        if error is None or error in _DURABLE_EXECUTION_ERRORS:
            return error
        return "Provider execution failed; detailed error was not retained."

    @staticmethod
    def _durable_mirror_error(error: str | None) -> str | None:
        if error is None or error in {
            "mirror_timeout",
            "transcript_identity_timeout",
            "claude_account_unavailable",
            "no_matrx_user",
        }:
            return error
        return "AI Matrx mirror failed; detailed error was not retained."

    @classmethod
    def _run_upsert(cls, run: _LocalRun) -> tuple[str, tuple[Any, ...]]:
        durable_error = cls._durable_execution_error(run.error)
        durable_mirror_error = cls._durable_mirror_error(run.mirror_error)
        return (
            """INSERT INTO coding_session_runtime_runs (
                   runtime_id, session_id, workspace, action, status,
                   prompt_preview, provider_session_id, provider_project_key,
                   conversation_id, transcript_path, execution_error,
                   mirror_passes, mirror_error, cancel_requested, started_at,
                   ended_at, turns_completed, next_sequence, restart_reason,
                   runtime_config_json, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(runtime_id) DO UPDATE SET
                   status=excluded.status,
                   provider_session_id=excluded.provider_session_id,
                   provider_project_key=excluded.provider_project_key,
                   conversation_id=excluded.conversation_id,
                   transcript_path=excluded.transcript_path,
                   execution_error=excluded.execution_error,
                   mirror_passes=excluded.mirror_passes,
                   mirror_error=excluded.mirror_error,
                   cancel_requested=excluded.cancel_requested,
                   ended_at=excluded.ended_at,
                   turns_completed=excluded.turns_completed,
                   next_sequence=excluded.next_sequence,
                   restart_reason=excluded.restart_reason,
                   runtime_config_json=excluded.runtime_config_json,
                   updated_at=datetime('now')""",
            (
                run.runtime_id,
                run.session_id,
                str(run.workspace),
                run.action,
                run.status,
                _PROMPT_NOT_RETAINED,
                run.provider_session_id,
                run.provider_project_key,
                run.conversation_id,
                str(run.transcript_path) if run.transcript_path is not None else None,
                durable_error,
                run.mirror_passes,
                durable_mirror_error,
                int(run.cancel_requested),
                run.started_at,
                run.ended_at,
                run.turns_completed,
                run.next_sequence,
                run.restart_reason,
                json.dumps(
                    {
                        "values": run.runtime_config,
                        "provenance": run.runtime_config_provenance,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

    async def _journal_write(
        self, statements: list[tuple[str, tuple[Any, ...]]]
    ) -> None:
        """Commit runtime evidence independently from the shared DB connection."""
        async with aiosqlite.connect(str(self._database().path)) as connection:
            await connection.execute(f"PRAGMA busy_timeout={_JOURNAL_BUSY_TIMEOUT_MS}")
            await connection.execute("PRAGMA synchronous=FULL")
            await connection.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    await connection.execute(sql, params)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def _persist_run(self, run: _LocalRun) -> None:
        self._ensure_run_config(run)
        await self._journal_write([self._run_upsert(run)])

    def _ensure_run_config(self, run: _LocalRun) -> None:
        if not run.runtime_config:
            self._apply_runtime_config_snapshot(run)
        if run.events.maxlen is None:
            run.events = deque(
                run.events, maxlen=int(run.runtime_config["event_buffer_max"])
            )

    @classmethod
    def _journal_event(cls, event: dict[str, Any]) -> dict[str, Any]:
        """Persist replay evidence without durable prompt/SDK message content."""
        if event.get("event") == "sdk_message":
            return {
                "event": "sdk_message",
                "sdk_message_type": event.get("sdk_message_type"),
                "message_persisted": False,
                "message_redacted_reason": "runtime_content_not_journaled",
                "sequence": event["sequence"],
                "emitted_at": event["emitted_at"],
            }
        if event.get("event") == "runtime_finished":
            durable = dict(event)
            execution = dict(durable.get("execution") or {})
            execution["error"] = cls._durable_execution_error(execution.get("error"))
            mirror = dict(durable.get("mirror") or {})
            mirror["error"] = cls._durable_mirror_error(mirror.get("error"))
            durable["execution"] = execution
            durable["mirror"] = mirror
            durable["error"] = cls._durable_execution_error(durable.get("error"))
            return durable
        return event

    def _row_to_run(self, row: Any) -> _LocalRun:
        raw_config = json.loads(str(row["runtime_config_json"]))
        values = raw_config.get("values", raw_config)
        provenance = raw_config.get("provenance", {})
        config = CodingSessionRuntimeConfig.model_validate(values)
        return _LocalRun(
            runtime_id=str(row["runtime_id"]),
            session_id=str(row["session_id"]),
            workspace=Path(str(row["workspace"])),
            action=str(row["action"]),
            status=str(row["status"]),
            prompt_preview=str(row["prompt_preview"] or _PROMPT_NOT_RETAINED),
            provider_session_id=row["provider_session_id"],
            provider_project_key=row["provider_project_key"],
            conversation_id=row["conversation_id"],
            transcript_path=(
                Path(str(row["transcript_path"]))
                if row["transcript_path"] is not None
                else None
            ),
            error=row["execution_error"],
            cancel_requested=bool(row["cancel_requested"]),
            started_at=float(row["started_at"]),
            ended_at=(float(row["ended_at"]) if row["ended_at"] is not None else None),
            turns_completed=int(row["turns_completed"]),
            mirror_passes=int(row["mirror_passes"]),
            mirror_error=row["mirror_error"],
            restart_reason=row["restart_reason"],
            runtime_config=config.model_dump(mode="json"),
            runtime_config_provenance=dict(provenance),
            next_sequence=int(row["next_sequence"]),
            events=deque(maxlen=config.event_buffer_max),
        )

    async def _load_run(self, runtime_id: str) -> _LocalRun | None:
        row = await self._database().fetchone(
            "SELECT * FROM coding_session_runtime_runs WHERE runtime_id=?",
            (runtime_id,),
        )
        if row is None:
            return None
        run = self._row_to_run(row)
        events = await self._database().fetchall(
            """SELECT event_json FROM (
                   SELECT sequence, event_json
                   FROM coding_session_runtime_events
                   WHERE runtime_id=? ORDER BY sequence DESC LIMIT ?
               ) ORDER BY sequence""",
            (runtime_id, run.runtime_config["event_buffer_max"]),
        )
        for event in events:
            run.events.append(json.loads(str(event["event_json"])))
        self._runs[runtime_id] = run
        return run

    async def _recent_runtime_ids(self) -> list[str]:
        rows = await self._database().fetchall(
            """SELECT runtime_id FROM coding_session_runtime_runs
               ORDER BY started_at DESC LIMIT ?""",
            (self._runtime_config().status_history_runs,),
        )
        return [str(row["runtime_id"]) for row in rows]

    async def _enforce_loaded_run_bound(
        self, *, protected: set[str] | None = None
    ) -> None:
        keep = set(await self._recent_runtime_ids())
        keep.update(protected or set())
        keep.update(
            runtime_id
            for runtime_id, run in self._runs.items()
            if run.status in {"starting", "running"}
        )
        for runtime_id in list(self._runs):
            if runtime_id not in keep:
                self._runs.pop(runtime_id, None)

    async def _ensure_hydrated(self) -> None:
        if self._hydrated:
            return
        async with self._hydrate_lock:
            if self._hydrated:
                return
            rows = await self._database().fetchall(
                """SELECT * FROM coding_session_runtime_runs
                   WHERE status IN ('starting', 'running') ORDER BY started_at"""
            )
            for row in rows:
                run = self._row_to_run(row)
                run.status = "interrupted"
                run.error = "Local runtime stopped before a terminal provider result."
                run.restart_reason = _RESTART_REASON
                run.ended_at = time.time()
                await self._emit(
                    run,
                    {
                        "event": "runtime_interrupted",
                        "runtime_id": run.runtime_id,
                        "status": run.status,
                        "reason": _RESTART_REASON,
                    },
                )
                await self._load_run(run.runtime_id)
            for runtime_id in await self._recent_runtime_ids():
                if runtime_id not in self._runs:
                    await self._load_run(runtime_id)
            self._hydrated = True

    async def _ensure_run(self, runtime_id: str) -> _LocalRun | None:
        await self._ensure_hydrated()
        run = self._runs.get(runtime_id) or await self._load_run(runtime_id)
        await self._enforce_loaded_run_bound(protected={runtime_id})
        return run

    async def initialize(self) -> None:
        """Hydrate durable evidence and settle crash-interrupted runs at boot."""
        await self._ensure_hydrated()

    @property
    def shutdown_timeout_seconds(self) -> float:
        return self._runtime_config().shutdown_timeout_seconds

    # ------------------------------------------------------------ capabilities

    async def capabilities(self) -> dict[str, Any]:
        """Truthful availability: every prerequisite named, none guessed."""
        await self._ensure_hydrated()
        reasons: list[str] = []
        try:
            import claude_agent_sdk  # noqa: F401

            sdk_available = True
        except Exception as exc:  # pragma: no cover - environment-dependent
            sdk_available = False
            reasons.append(f"claude-agent-sdk unavailable: {exc}")
        cli = resolve_claude_executable()
        if cli is None:
            reasons.append(
                "Claude Code is not installed on this machine (no `claude` binary found)"
            )
        account = await self._account_reader()
        if not account.available:
            reasons.append(account.reason or "Claude login unavailable")
        token_row = await TokenRepo(self._database()).get()
        matrx_user = bool(token_row and token_row.get("user_id"))
        if not matrx_user:
            reasons.append("Sign in to AI Matrx in the desktop app")
        roots = self._normalized_roots()
        return {
            "schema_version": 1,
            "available": not reasons,
            "reasons": reasons,
            "sdk_available": sdk_available,
            "claude_cli": str(cli) if cli else None,
            "claude_account_label": account.account_label,
            "claude_account_display_identity": account.local_display_identity,
            "claude_client_version": account.client_version,
            "matrx_user_available": matrx_user,
            "auth_path": "user_subscription_login",
            "workspace_roots": [str(root) for root in roots],
            "approved_folders": _approved_folders(),
            "active_runs": len(
                [r for r in self._runs.values() if r.status in {"starting", "running"}]
            ),
            "runtime_config": self._runtime_config_status(),
            "capabilities": {
                "start": True,
                "send": True,
                "cancel": True,
                "resume_native": True,
                "fork_native": False,
                "stream": True,
            },
        }

    # --------------------------------------------------------------- approvals

    def list_approved(self) -> dict[str, Any]:
        return {
            "workspace_roots": [str(r) for r in self._normalized_roots()],
            "approved_folders": _approved_folders(),
        }

    @staticmethod
    def _existing_directory(folder: str, *, label: str = "Folder") -> Path:
        path = Path(folder).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise LocalRuntimeRefused(
                "workspace_missing", f"{label} does not exist: {folder}"
            ) from exc
        if not resolved.is_dir():
            raise LocalRuntimeRefused(
                "workspace_not_directory", f"{label} is not a directory: {folder}"
            )
        return resolved

    @staticmethod
    def _normalized_roots() -> list[Path]:
        """Resolved configured roots, preserving unavailable roots as paths."""
        roots: list[Path] = []
        for root in _workspace_roots():
            resolved = root.resolve(strict=False)
            if resolved not in roots:
                roots.append(resolved)
        return roots

    def add_workspace_root(self, folder: str) -> LocalRuntimeWorkspaceRootsResponse:
        """Persist one explicitly selected parent as local filesystem authority."""
        resolved = self._existing_directory(folder, label="Workspace root")
        roots = self._normalized_roots()
        if not any(resolved == root or _is_under(root, resolved) for root in roots):
            # A broader selected root replaces nested roots. Keeping both adds
            # no authority and makes the tree/settings misleading.
            roots = [root for root in roots if not _is_under(resolved, root)]
            roots.append(resolved)
            roots.sort(key=lambda item: (str(item).casefold(), str(item)))
            _settings().set(WORKSPACE_ROOTS_SETTING, [str(root) for root in roots])
        return LocalRuntimeWorkspaceRootsResponse(
            workspace_roots=[str(root) for root in roots],
            approved_folders=_approved_folders(),
        )

    def remove_workspace_root(self, folder: str) -> LocalRuntimeWorkspaceRootsResponse:
        """Remove exactly one root without silently deleting folder approvals."""
        target = Path(folder).expanduser().resolve(strict=False)
        roots = self._normalized_roots()
        remaining = [root for root in roots if root != target]
        if len(remaining) != len(roots):
            _settings().set(WORKSPACE_ROOTS_SETTING, [str(root) for root in remaining])
        affected = [
            approved
            for approved in _approved_folders()
            if _is_under(target, Path(approved).resolve(strict=False))
            and not any(
                _is_under(root, Path(approved).resolve(strict=False))
                for root in remaining
            )
        ]
        return LocalRuntimeWorkspaceRootsResponse(
            workspace_roots=[str(root) for root in remaining],
            approved_folders=_approved_folders(),
            affected_approvals=affected,
        )

    async def discover_workspaces(
        self, parent: str | None = None
    ) -> WorkspaceDiscoveryResponse:
        """Return a bounded project-aware tree beneath configured authority."""
        if self._discovery_lock.locked():
            raise LocalRuntimeRefused(
                "workspace_discovery_in_progress",
                "Workspace discovery is already running",
            )
        async with self._discovery_lock:
            configured = self._normalized_roots()
            existing_roots: list[Path] = []
            unavailable = 0
            for root in configured:
                try:
                    resolved = root.resolve(strict=True)
                except OSError:
                    unavailable += 1
                    continue
                if resolved.is_dir() and resolved not in existing_roots:
                    existing_roots.append(resolved)
                else:
                    unavailable += 1

            selected_parent: str | None = None
            if parent is not None:
                selected = self._existing_directory(parent, label="Discovery parent")
                if not any(_is_under(root, selected) for root in existing_roots):
                    raise LocalRuntimeRefused(
                        "workspace_outside_allowlist",
                        f"{selected} is outside the configured workspace roots. "
                        "Add it as a workspace root first.",
                    )
                scan_roots = [selected]
                selected_parent = str(selected)
            else:
                scan_roots = existing_roots

            return await asyncio.to_thread(
                discover_workspace_tree,
                scan_roots,
                parent=selected_parent,
                workspace_roots=[str(root) for root in configured],
                approved_folders=_approved_folders(),
                initial_skipped=unavailable,
            )

    def approve_folder(self, folder: str) -> dict[str, Any]:
        """One-click, persisted, per-folder approval — the first-launch gate."""
        resolved = self._existing_directory(folder)
        roots = self._normalized_roots()
        if not any(_is_under(root, resolved) for root in roots):
            roots_label = ", ".join(str(root) for root in roots) or "none configured"
            raise LocalRuntimeRefused(
                "workspace_outside_allowlist",
                f"{resolved} is outside the allowed workspace roots "
                f"({roots_label}). "
                "Add a root in settings first.",
            )
        approved = _approved_folders()
        if str(resolved) not in approved:
            approved.append(str(resolved))
            _settings().set(APPROVED_FOLDERS_SETTING, approved)
        return self.list_approved()

    def revoke_folder(self, folder: str) -> dict[str, Any]:
        resolved = str(Path(folder).expanduser().resolve(strict=False))
        approved = [item for item in _approved_folders() if item != resolved]
        _settings().set(APPROVED_FOLDERS_SETTING, approved)
        return self.list_approved()

    def _require_approved_workspace(self, workspace: str) -> Path:
        path = Path(workspace).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise LocalRuntimeRefused(
                "workspace_missing", f"Workspace does not exist: {workspace}"
            ) from exc
        if not resolved.is_dir():
            raise LocalRuntimeRefused(
                "workspace_not_directory", f"Workspace is not a directory: {workspace}"
            )
        roots = self._normalized_roots()
        if not any(_is_under(root, resolved) for root in roots):
            roots_label = ", ".join(str(root) for root in roots) or "none configured"
            raise LocalRuntimeRefused(
                "workspace_outside_allowlist",
                f"{resolved} is outside the allowed workspace roots ({roots_label})",
            )
        approved = _approved_folders()
        if not any(
            resolved == Path(item) or _is_under(Path(item), resolved)
            for item in approved
        ):
            raise LocalRuntimeRefused(
                "workspace_not_approved",
                f"{resolved} has not been approved for agent runs yet. Approve it "
                "once in the Matrx Local desktop app (Claude Code → Agent Runtime).",
            )
        return resolved

    # ------------------------------------------------------------------ resume

    def _config_dir(self) -> Path:
        importer = self._importer or ClaudeHistoryImporter()
        return importer._config_dir  # noqa: SLF001 — same package, one truth

    def _find_transcript(self, raw_session_id: str) -> Path | None:
        projects = self._config_dir() / "projects"
        if not projects.is_dir():
            return None
        for candidate in projects.glob(f"*/{raw_session_id}.jsonl"):
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def decode_provider_session_id(provider_session_id: str) -> str:
        """Raw Claude session UUID from either bridge identity form."""
        if provider_session_id.startswith("claude-sdk:"):
            import base64

            _prefix, _digest, encoded = provider_session_id.split(":", 2)
            padding = "=" * (-len(encoded) % 4)
            return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        return provider_session_id

    async def resumable(self, provider_session_id: str) -> dict[str, Any]:
        """Native local resume is offered ONLY when Claude's own file exists."""
        try:
            raw_id = self.decode_provider_session_id(provider_session_id)
            UUID(raw_id)
        except (ValueError, TypeError):
            return {
                "resumable": False,
                "reason": "not_a_claude_local_session_identity",
            }
        transcript = self._find_transcript(raw_id)
        if transcript is None:
            return {
                "resumable": False,
                "reason": "transcript_not_on_this_machine",
            }
        workspace = self._workspace_for_transcript(raw_id, transcript)
        if workspace is None:
            return {
                "resumable": False,
                "reason": "workspace_unknown",
                "transcript_present": True,
            }
        if not workspace.is_dir():
            return {
                "resumable": False,
                "reason": "workspace_missing",
                "workspace": str(workspace),
                "transcript_present": True,
            }
        return {
            "resumable": True,
            "session_id": raw_id,
            "workspace": str(workspace),
            "transcript_present": True,
        }

    def _workspace_for_transcript(self, raw_id: str, transcript: Path) -> Path | None:
        # Claude's desktop session index knows the exact local cwd; the
        # transcript's own records carry it too. Index first, JSONL scan as
        # fallback. ``local_cwd`` never enters metadata_payload or the cloud.
        try:
            from app.services.coding_sessions.claude_session_index import (
                read_session_index,
            )

            entries, _ = read_session_index(None)
            entry = entries.get(raw_id)
            cwd = entry.local_cwd if entry is not None else None
            if cwd is not None:
                return cwd
        except Exception:  # noqa: BLE001 — index is optional; JSONL is ground truth
            pass
        try:
            with transcript.open("rb") as handle:
                for _ in range(50):
                    line = handle.readline(262_144)
                    if not line:
                        break
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    cwd = record.get("cwd") if isinstance(record, dict) else None
                    if isinstance(cwd, str) and cwd:
                        return Path(cwd)
        except OSError:
            return None
        return None

    # ------------------------------------------------------------------- start

    async def start(self, request: LocalRuntimeStartRequest) -> dict[str, Any]:
        await self._ensure_hydrated()
        capabilities = await self.capabilities()
        if not capabilities["available"]:
            raise LocalRuntimeRefused(
                "runtime_unavailable", "; ".join(capabilities["reasons"])
            )
        workspace = self._require_approved_workspace(request.workspace)
        config = self._runtime_config()
        async with self._lock:
            active = [
                r for r in self._runs.values() if r.status in {"starting", "running"}
            ]
            if len(active) >= config.max_active_runs:
                raise LocalRuntimeRefused(
                    "too_many_active_runs",
                    f"{len(active)} runs are already active (max {config.max_active_runs})",
                )
            if request.resume_session_id is not None:
                raw_id = self.decode_provider_session_id(request.resume_session_id)
                try:
                    UUID(raw_id)
                except ValueError as exc:
                    raise LocalRuntimeRefused(
                        "invalid_session_id",
                        f"resume_session_id is not a Claude session identity: "
                        f"{request.resume_session_id}",
                    ) from exc
                if self._find_transcript(raw_id) is None:
                    raise LocalRuntimeRefused(
                        "transcript_not_on_this_machine",
                        f"No local Claude transcript exists for session {raw_id}; "
                        "native resume is not possible on this machine.",
                    )
                for run in active:
                    if run.session_id == raw_id:
                        raise LocalRuntimeRefused(
                            "session_already_running",
                            f"Session {raw_id} already has an active run "
                            f"({run.runtime_id})",
                        )
                session_id = raw_id
                action = "resume"
            else:
                session_id = str(uuid4())
                action = "start"
            run = _LocalRun(
                runtime_id=str(uuid4()),
                session_id=session_id,
                workspace=workspace,
                action=action,
                prompt_preview=request.prompt[:200],
                events=deque(maxlen=config.event_buffer_max),
            )
            self._apply_runtime_config_snapshot(run)
            await self._persist_run(run)
            self._runs[run.runtime_id] = run
        run.task = asyncio.create_task(
            self._execute(run, request), name=f"claude-local-run:{run.runtime_id}"
        )
        # Wait briefly for the session identity (transcript file + first
        # mirror) so the caller gets the conversation UUID to open. A slow
        # start is not an error — the caller can poll status.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                run.identity_ready.wait(), timeout=config.start_identity_wait_seconds
            )
        return run.public_status()

    async def status(self, runtime_id: str | None = None) -> dict[str, Any]:
        await self._ensure_hydrated()
        if runtime_id is not None:
            run = await self._ensure_run(runtime_id)
            if run is None:
                raise LocalRuntimeRefused("unknown_runtime", f"No run {runtime_id}")
            return run.public_status()
        await self._enforce_loaded_run_bound()
        return {
            "runtime_config": self._runtime_config_status(),
            "runs": [
                self._runs[runtime_id].public_status()
                for runtime_id in await self._recent_runtime_ids()
                if runtime_id in self._runs
            ]
        }

    async def cancel(self, runtime_id: str) -> dict[str, Any]:
        run = await self._ensure_run(runtime_id)
        if run is None:
            raise LocalRuntimeRefused("unknown_runtime", f"No run {runtime_id}")
        if run.status not in {"starting", "running"}:
            return {
                "runtime_id": runtime_id,
                "cancelled": False,
                "status": run.status,
                "interrupt": {"status": "not_active"},
            }
        run.cancel_requested = True
        await self._persist_run(run)
        client = run.client
        interrupt_status = "not_active"
        if client is not None and run.status in {"starting", "running"}:
            try:
                async with asyncio.timeout(
                    float(run.runtime_config["interrupt_timeout_seconds"])
                ):
                    await client.interrupt()
                cancelled = True
                interrupt_status = "acknowledged"
            except TimeoutError:
                logger.warning("[local_runtime] interrupt timed out for %s", runtime_id)
                cancelled = False
                interrupt_status = "timed_out"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[local_runtime] interrupt failed for %s: %s", runtime_id, exc
                )
                cancelled = False
                interrupt_status = "failed"
        else:
            cancelled = False
        return {
            "runtime_id": runtime_id,
            "cancelled": cancelled,
            "status": run.status,
            "interrupt": {"status": interrupt_status},
        }

    # --------------------------------------------------------------- streaming

    async def subscribe(self, runtime_id: str, *, after_sequence: int | None = None):
        """Replay after a cursor and report when the bounded buffer has a gap."""
        run = await self._ensure_run(runtime_id)
        if run is None:
            raise LocalRuntimeRefused("unknown_runtime", f"No run {runtime_id}")
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=int(run.runtime_config["subscriber_queue_max"])
        )
        buffered = list(run.events)
        if after_sequence is not None and buffered:
            earliest = int(buffered[0]["sequence"])
            latest = int(buffered[-1]["sequence"])
            if after_sequence < earliest - 1:
                queue.put_nowait(
                    {
                        "event": "stream_gap",
                        "requested_after": after_sequence,
                        "earliest_available": earliest,
                        "latest_available": latest,
                        "resync_required": True,
                    }
                )
        for event in buffered:
            if after_sequence is None or int(event["sequence"]) > after_sequence:
                queue.put_nowait(event)
        if run.status in {"completed", "failed", "cancelled", "interrupted"}:
            queue.put_nowait(None)
        else:
            run.subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            with contextlib.suppress(ValueError):
                run.subscribers.remove(queue)

    async def _emit(self, run: _LocalRun, event: dict[str, Any]) -> None:
        async with run.emit_lock:
            await self._emit_locked(run, event)

    async def _emit_locked(self, run: _LocalRun, event: dict[str, Any]) -> None:
        self._ensure_run_config(run)
        sequenced = dict(event)
        sequenced["sequence"] = run.next_sequence
        sequenced["emitted_at"] = _utc_iso()
        run.next_sequence += 1
        journal_event = self._journal_event(sequenced)
        upsert = self._run_upsert(run)
        await self._journal_write(
            [
                upsert,
                (
                    """INSERT INTO coding_session_runtime_events
                           (runtime_id, sequence, emitted_at, event_json)
                       VALUES (?, ?, ?, ?)""",
                    (
                        run.runtime_id,
                        int(sequenced["sequence"]),
                        str(sequenced["emitted_at"]),
                        json.dumps(
                            journal_event,
                            sort_keys=True,
                            separators=(",", ":"),
                            default=repr,
                        ),
                    ),
                ),
                (
                    """DELETE FROM coding_session_runtime_events
                       WHERE runtime_id=? AND sequence <= ?""",
                    (
                        run.runtime_id,
                        int(sequenced["sequence"])
                        - int(run.runtime_config["event_buffer_max"]),
                    ),
                ),
            ]
        )
        run.events.append(sequenced)
        for queue in list(run.subscribers):
            try:
                queue.put_nowait(sequenced)
            except asyncio.QueueFull:
                # A stalled consumer must never stall the run; it can re-attach
                # and replay the bounded buffer.
                self._close_subscriber_with_gap(run, queue)
                with contextlib.suppress(ValueError):
                    run.subscribers.remove(queue)

    @staticmethod
    def _close_subscriber_with_gap(
        run: _LocalRun, queue: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        while not queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(
            {
                "event": "stream_gap",
                "reason": "subscriber_queue_overflow",
                "earliest_available": (
                    int(run.events[0]["sequence"]) if run.events else None
                ),
                "latest_available": run.next_sequence - 1,
                "resync_required": True,
            }
        )
        queue.put_nowait(None)

    def _finish_subscribers(self, run: _LocalRun) -> None:
        for queue in list(run.subscribers):
            if queue.full():
                self._close_subscriber_with_gap(run, queue)
            else:
                queue.put_nowait(None)
        run.subscribers.clear()

    # --------------------------------------------------------------- execution

    async def _execute(self, run: _LocalRun, request: LocalRuntimeStartRequest) -> None:
        try:
            from claude_agent_sdk import (
                ClaudeAgentOptions,
                ClaudeSDKClient,
                ResultMessage,
            )

            cli = resolve_claude_executable()
            if cli is None:
                raise LocalRuntimeRefused(
                    "claude_cli_missing", "Claude Code binary disappeared before launch"
                )
            options = ClaudeAgentOptions(
                cwd=run.workspace,
                cli_path=cli,
                permission_mode=request.permission_mode,
                model=request.model,
                max_turns=request.max_turns,
                session_id=run.session_id if run.action == "start" else None,
                resume=run.session_id if run.action == "resume" else None,
                include_partial_messages=False,
                # THE AUTH PATH IS THE USER'S OWN SUBSCRIPTION LOGIN — always.
                # A developer/engine environment may carry ANTHROPIC_API_KEY or
                # a gateway override, and the CLI silently prefers those over
                # the claude.ai login (observed live in the E2E: "another auth
                # source is set and takes precedence"). Blank them for the
                # child so a runtime session can never silently bill an API
                # key instead of the user's subscription.
                env={
                    "ANTHROPIC_API_KEY": "",
                    "ANTHROPIC_AUTH_TOKEN": "",
                    "ANTHROPIC_BASE_URL": "",
                    "CLAUDE_CODE_USE_BEDROCK": "",
                    "CLAUDE_CODE_USE_VERTEX": "",
                },
                # "project" + "local" load the repo's CLAUDE.md and settings so
                # a runtime session works on a real codebase like any Claude
                # Code session. "user" is DELIBERATELY excluded: the user-level
                # AI Matrx plugin's event-mirror hooks would otherwise fire
                # inside this session and mint a SECOND (event_mirror, raw
                # UUID) binding beside this runtime's native ledger — the exact
                # dual-binding defect the bridge contract forbids. The native
                # ledger this runtime maintains is strictly richer than the
                # hook mirror, so nothing is lost.
                setting_sources=["project", "local"],
            )
            client = ClaudeSDKClient(options=options)
            run.client = client
            identity_task = asyncio.create_task(
                self._establish_identity(run),
                name=f"claude-local-identity:{run.runtime_id}",
            )
            saw_result = False
            try:
                try:
                    async with asyncio.timeout(
                        float(run.runtime_config["execution_timeout_seconds"])
                    ):
                        async with client:
                            run.status = "running"
                            await self._emit(
                                run,
                                {
                                    "event": "runtime_started",
                                    "runtime_id": run.runtime_id,
                                    "session_id": run.session_id,
                                    "action": run.action,
                                    "workspace": str(run.workspace),
                                },
                            )
                            if not run.cancel_requested:
                                await client.query(
                                    request.prompt, session_id=run.session_id
                                )
                                responses = client.receive_response().__aiter__()
                                while True:
                                    try:
                                        async with asyncio.timeout(
                                            float(
                                                run.runtime_config[
                                                    "idle_timeout_seconds"
                                                ]
                                            )
                                        ):
                                            message = await responses.__anext__()
                                    except StopAsyncIteration:
                                        break
                                    except TimeoutError as exc:
                                        raise _ExecutionIdleTimeout from exc
                                    payload = _message_payload(message)
                                    await self._emit(
                                        run,
                                        {
                                            "event": "sdk_message",
                                            "sdk_message_type": type(message).__name__,
                                            "message": payload,
                                        },
                                    )
                                    if isinstance(message, ResultMessage):
                                        saw_result = True
                                        run.turns_completed += 1
                                        await self._mirror_bounded(run)
                except _ExecutionIdleTimeout:
                    raise
                except TimeoutError as exc:
                    raise _ExecutionWallTimeout from exc
            finally:
                run.client = None
                identity_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await identity_task
            if run.cancel_requested:
                run.status = "cancelled"
            elif not saw_result:
                raise RuntimeError(
                    "Claude Agent SDK ended without a terminal ResultMessage"
                )
            else:
                run.status = "completed"
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except _ExecutionIdleTimeout:
            run.status = "failed"
            run.error = "Provider execution became idle beyond the configured limit."
        except _ExecutionWallTimeout:
            run.status = "failed"
            run.error = "Provider execution exceeded the configured wall-clock limit."
        except Exception as exc:  # noqa: BLE001
            run.status = "cancelled" if run.cancel_requested else "failed"
            if run.status == "failed":
                run.error = str(exc)
                logger.error(
                    "[local_runtime] run %s FAILED: %s",
                    run.runtime_id,
                    exc,
                    exc_info=True,
                )
        finally:
            await self._settle_run(run)

    async def _settle_run(self, run: _LocalRun) -> None:
        """Mirror best-effort, then always publish the execution terminal."""
        run.ended_at = time.time()
        try:
            await self._mirror_bounded(run, final=True)
        except BaseException as exc:  # cancellation must not suppress terminal state
            run.mirror_error = str(exc)
            logger.error(
                "[local_runtime] final mirror crashed for %s: %s",
                run.runtime_id,
                exc,
                exc_info=True,
            )
        finally:
            await self._emit(
                run,
                {
                    "event": "runtime_finished",
                    "runtime_id": run.runtime_id,
                    "status": run.status,
                    "execution": {"status": run.status, "error": run.error},
                    "mirror": {
                        "status": run.mirror_status(),
                        "passes": run.mirror_passes,
                        "error": run.mirror_error,
                    },
                    "session_id": run.session_id,
                    "conversation_id": run.conversation_id,
                    "error": run.error,
                },
            )
            run.identity_ready.set()
            self._finish_subscribers(run)

    async def _establish_identity(self, run: _LocalRun) -> None:
        """Wait for Claude's own transcript file, then compute bridge identity."""
        config = run.runtime_config
        deadline = time.time() + float(config["identity_timeout_seconds"])
        while time.time() < deadline:
            transcript = self._find_transcript(run.session_id)
            if transcript is not None:
                self._bind_identity(run, transcript)
                # First mirror as soon as the session exists, so the binding +
                # conversation are minted early and the browser can open it.
                await self._mirror_bounded(run)
                run.identity_ready.set()
                return
            await asyncio.sleep(float(config["identity_poll_seconds"]))
        run.mirror_error = "transcript_identity_timeout"
        await self._persist_run(run)
        logger.warning(
            "[local_runtime] transcript for session %s did not appear within %ss",
            run.session_id,
            config["identity_timeout_seconds"],
        )

    def _bind_identity(self, run: _LocalRun, transcript: Path) -> None:
        if run.provider_session_id is not None:
            return
        project_dir = transcript.parent
        project_key = (
            "claude-local:"
            + hashlib.sha256(str(project_dir.resolve()).encode("utf-8")).hexdigest()
        )
        run.transcript_path = transcript
        run.provider_project_key = project_key
        run.provider_session_id = _bridge_provider_session_id(
            project_key, run.session_id
        )

    async def _mirror_bounded(self, run: _LocalRun, *, final: bool = False) -> None:
        """Bound mirror latency so provider settlement always reaches terminal."""
        self._ensure_run_config(run)
        try:
            async with asyncio.timeout(
                float(run.runtime_config["mirror_timeout_seconds"])
            ):
                await self._mirror(run, final=final)
            await self._persist_run(run)
        except TimeoutError:
            run.mirror_error = "mirror_timeout"
            await self._persist_run(run)
            logger.error(
                "[local_runtime] mirror timed out for %s after %ss",
                run.runtime_id,
                run.runtime_config["mirror_timeout_seconds"],
            )

    # ------------------------------------------------------------------ mirror

    async def _mirror(self, run: _LocalRun, *, final: bool = False) -> None:
        """One targeted pass of the EXISTING certified import for this session.

        Reuses `ClaudeHistoryImporter.import_selected` — exact bytes, account
        identity, labels, subagents, batching, idempotent replay — pointed at
        this one live session. A conflict means Claude wrote more while we
        hashed; retried a few times, then left for the next turn boundary
        (or the capture reconciler, which closes any residual gap).
        """
        transcript = run.transcript_path or self._find_transcript(run.session_id)
        if transcript is None:
            return
        self._bind_identity(run, transcript)
        await self._persist_run(run)
        importer = self._importer or ClaudeHistoryImporter()
        account = await self._account_reader()
        if not account.available or account.account_key is None:
            run.mirror_error = account.reason or "claude_account_unavailable"
            return
        token_row = await TokenRepo(self._database()).get()
        user_id = str(token_row.get("user_id")) if token_row else ""
        if not user_id:
            run.mirror_error = "no_matrx_user"
            return
        assert run.provider_project_key is not None
        assert run.provider_session_id is not None
        if run.conversation_id is None:
            run.conversation_id = _conversation_id(
                user_id, run.provider_session_id, run.provider_project_key
            )
        projects_root = transcript.parent.parent
        attempts = int(run.runtime_config["mirror_conflict_retries"]) if final else 1
        for attempt in range(attempts):
            try:
                streams: list[tuple[str, Path]] = [("main", transcript)]
                subagent_dir = transcript.parent / run.session_id / "subagents"
                if subagent_dir.is_dir():
                    for subagent in sorted(subagent_dir.iterdir()):
                        if subagent.suffix == ".jsonl" and subagent.is_file():
                            streams.append((f"subagent:{subagent.stem}", subagent))
                revision, _bytes, _mtime = await asyncio.to_thread(
                    _hash_source, projects_root, tuple(streams)
                )
                result = await importer.import_selected(
                    ClaudeHistoryImportRequest(
                        provider_account_key=account.account_key,
                        sessions=[
                            ClaudeHistorySelection(
                                session_id=UUID(run.session_id),
                                provider_project_key=run.provider_project_key,
                                source_revision=revision,
                            )
                        ],
                    ),
                    enqueue_origin="local_runtime",
                )
                run.mirror_passes += 1
                run.mirror_error = None
                await self._emit(
                    run,
                    {
                        "event": "mirror_pass",
                        "conversation_id": run.conversation_id,
                        "provider_session_id": run.provider_session_id,
                        "entries": result.get("entries"),
                        "queued_batches": result.get("queued_batches"),
                        "pending_outbox": result.get("pending_outbox"),
                    },
                )
                return
            except ClaudeHistoryConflict as exc:
                run.mirror_error = str(exc)
                if attempt + 1 < attempts:
                    await asyncio.sleep(
                        float(run.runtime_config["mirror_retry_delay_seconds"])
                    )
            except Exception as exc:  # noqa: BLE001
                run.mirror_error = str(exc)
                logger.error(
                    "[local_runtime] mirror pass failed for %s: %s",
                    run.runtime_id,
                    exc,
                    exc_info=True,
                )
                return
        if run.mirror_error:
            logger.warning(
                "[local_runtime] mirror pass deferred for %s (%s); the capture "
                "reconciler will close any residual gap",
                run.runtime_id,
                run.mirror_error,
            )

    async def shutdown(self) -> None:
        active = [
            run
            for run in self._runs.values()
            if run.status in {"starting", "running"}
        ]

        async def _interrupt(run: _LocalRun) -> None:
            run.cancel_requested = True
            await self._persist_run(run)
            client = run.client
            if client is None:
                return
            try:
                async with asyncio.timeout(
                    float(run.runtime_config["interrupt_timeout_seconds"])
                ):
                    await client.interrupt()
            except Exception:  # timeout and provider interrupt failures
                logger.warning(
                    "[local_runtime] shutdown interrupt did not settle for %s",
                    run.runtime_id,
                    exc_info=True,
                )

        await asyncio.gather(*(_interrupt(run) for run in active))
        tasks: list[asyncio.Task[None]] = []
        for run in active:
            if run.task is not None and not run.task.done():
                run.task.cancel()
                tasks.append(run.task)
        if tasks:
            try:
                async with asyncio.timeout(
                    self._runtime_config().shutdown_timeout_seconds
                ):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                logger.error(
                    "[local_runtime] %s run task(s) did not settle before shutdown",
                    len(tasks),
                )
                raise RuntimeError(
                    f"{len(tasks)} local runtime task(s) did not settle before shutdown"
                )


_runtime: LocalClaudeRuntime | None = None


def get_local_claude_runtime() -> LocalClaudeRuntime:
    global _runtime
    if _runtime is None:
        _runtime = LocalClaudeRuntime()
    return _runtime


__all__ = [
    "APPROVED_FOLDERS_SETTING",
    "LocalClaudeRuntime",
    "LocalRuntimeFolderRequest",
    "LocalRuntimeRefused",
    "LocalRuntimeStartRequest",
    "LocalRuntimeWorkspaceRootsResponse",
    "WORKSPACE_ROOTS_SETTING",
    "get_local_claude_runtime",
]
