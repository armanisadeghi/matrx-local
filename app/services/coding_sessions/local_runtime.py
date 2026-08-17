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

from pydantic import BaseModel, ConfigDict, Field

from app.common.system_logger import get_logger
from app.services.coding_sessions.claude_history import (
    ClaudeHistoryConflict,
    ClaudeHistoryImporter,
    ClaudeHistoryImportRequest,
    ClaudeHistorySelection,
    _bridge_provider_session_id,
    _conversation_id,
    _hash_source,
    _read_account_snapshot,
)
from app.services.local_db.database import get_db
from app.services.local_db.repositories import TokenRepo

logger = get_logger()

_EVENT_BUFFER_MAX = 2000
_SUBSCRIBER_QUEUE_MAX = 4000
_TRANSCRIPT_WAIT_SECONDS = 30.0
_MIRROR_CONFLICT_RETRIES = 3
_MAX_ACTIVE_RUNS = 4

WORKSPACE_ROOTS_SETTING = "claude_runtime_workspace_roots"
APPROVED_FOLDERS_SETTING = "claude_runtime_approved_folders"
DEFAULT_WORKSPACE_ROOTS = ["~/code"]


class LocalRuntimeRefused(RuntimeError):
    """The request is refused loudly — never silently downgraded."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


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
    client: Any = None
    task: asyncio.Task[None] | None = None
    events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=_EVENT_BUFFER_MAX)
    )
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    identity_ready: asyncio.Event = field(default_factory=asyncio.Event)

    def public_status(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "session_id": self.session_id,
            "action": self.action,
            "status": self.status,
            "workspace": str(self.workspace),
            "prompt_preview": self.prompt_preview,
            "provider_session_id": self.provider_session_id,
            "conversation_id": self.conversation_id,
            "error": self.error,
            "started_at": datetime.fromtimestamp(self.started_at, tz=UTC).isoformat(),
            "ended_at": (
                datetime.fromtimestamp(self.ended_at, tz=UTC).isoformat()
                if self.ended_at
                else None
            ),
            "turns_completed": self.turns_completed,
            "mirror_passes": self.mirror_passes,
            "mirror_error": self.mirror_error,
            "event_count": len(self.events),
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


def _find_claude_cli() -> Path | None:
    """The user's own installed Claude Code binary — never a bundled copy."""
    import shutil as _shutil

    found = _shutil.which("claude")
    if found:
        return Path(found)
    for candidate in (
        Path.home() / ".claude" / "local" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ):
        if candidate.is_file():
            return candidate
    return None


def _settings():
    from app.services.cloud_sync.settings_sync import get_settings_sync

    return get_settings_sync()


def _workspace_roots() -> list[Path]:
    raw = _settings().get(WORKSPACE_ROOTS_SETTING, DEFAULT_WORKSPACE_ROOTS)
    roots: list[Path] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                roots.append(Path(item).expanduser())
    return roots or [Path(DEFAULT_WORKSPACE_ROOTS[0]).expanduser()]


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
    ) -> None:
        self._runs: dict[str, _LocalRun] = {}
        self._importer = importer
        self._db = db
        self._account_reader = account_reader
        self._lock = asyncio.Lock()

    def _database(self):
        return self._db if self._db is not None else get_db()

    # ------------------------------------------------------------ capabilities

    async def capabilities(self) -> dict[str, Any]:
        """Truthful availability: every prerequisite named, none guessed."""
        reasons: list[str] = []
        try:
            import claude_agent_sdk  # noqa: F401

            sdk_available = True
        except Exception as exc:  # pragma: no cover - environment-dependent
            sdk_available = False
            reasons.append(f"claude-agent-sdk unavailable: {exc}")
        cli = _find_claude_cli()
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
        roots = _workspace_roots()
        return {
            "schema_version": 1,
            "available": not reasons,
            "reasons": reasons,
            "sdk_available": sdk_available,
            "claude_cli": str(cli) if cli else None,
            "claude_account_label": account.account_label,
            "claude_client_version": account.client_version,
            "matrx_user_available": matrx_user,
            "auth_path": "user_subscription_login",
            "workspace_roots": [str(root) for root in roots],
            "approved_folders": _approved_folders(),
            "active_runs": len(
                [r for r in self._runs.values() if r.status in {"starting", "running"}]
            ),
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
            "workspace_roots": [str(r) for r in _workspace_roots()],
            "approved_folders": _approved_folders(),
        }

    def approve_folder(self, folder: str) -> dict[str, Any]:
        """One-click, persisted, per-folder approval — the first-launch gate."""
        path = Path(folder).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise LocalRuntimeRefused(
                "workspace_missing", f"Folder does not exist: {folder}"
            ) from exc
        if not resolved.is_dir():
            raise LocalRuntimeRefused("workspace_not_directory", f"Not a directory: {folder}")
        if not any(_is_under(root.resolve(), resolved) for root in _workspace_roots()):
            raise LocalRuntimeRefused(
                "workspace_outside_allowlist",
                f"{resolved} is outside the allowed workspace roots "
                f"({', '.join(str(r) for r in _workspace_roots())}). "
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
        roots = [root.resolve() for root in _workspace_roots()]
        if not any(_is_under(root, resolved) for root in roots):
            raise LocalRuntimeRefused(
                "workspace_outside_allowlist",
                f"{resolved} is outside the allowed workspace roots "
                f"({', '.join(str(r) for r in roots)})",
            )
        approved = _approved_folders()
        if not any(
            resolved == Path(item) or _is_under(Path(item), resolved) for item in approved
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
        # Claude's desktop session index knows the exact cwd; the transcript's
        # own records carry it too. Index first, JSONL scan as fallback.
        try:
            from app.services.coding_sessions.claude_session_index import (
                read_session_index,
            )

            entries, _ = read_session_index(None)
            entry = entries.get(raw_id)
            cwd = getattr(entry, "cwd", None) if entry is not None else None
            if isinstance(cwd, str) and cwd:
                return Path(cwd)
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
        capabilities = await self.capabilities()
        if not capabilities["available"]:
            raise LocalRuntimeRefused(
                "runtime_unavailable", "; ".join(capabilities["reasons"])
            )
        workspace = self._require_approved_workspace(request.workspace)
        async with self._lock:
            active = [
                r for r in self._runs.values() if r.status in {"starting", "running"}
            ]
            if len(active) >= _MAX_ACTIVE_RUNS:
                raise LocalRuntimeRefused(
                    "too_many_active_runs",
                    f"{len(active)} runs are already active (max {_MAX_ACTIVE_RUNS})",
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
            )
            self._runs[run.runtime_id] = run
        run.task = asyncio.create_task(
            self._execute(run, request), name=f"claude-local-run:{run.runtime_id}"
        )
        # Wait briefly for the session identity (transcript file + first
        # mirror) so the caller gets the conversation UUID to open. A slow
        # start is not an error — the caller can poll status.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(run.identity_ready.wait(), timeout=20.0)
        return run.public_status()

    def status(self, runtime_id: str | None = None) -> dict[str, Any]:
        if runtime_id is not None:
            run = self._runs.get(runtime_id)
            if run is None:
                raise LocalRuntimeRefused("unknown_runtime", f"No run {runtime_id}")
            return run.public_status()
        return {
            "runs": [
                run.public_status()
                for run in sorted(
                    self._runs.values(), key=lambda r: r.started_at, reverse=True
                )
            ]
        }

    async def cancel(self, runtime_id: str) -> dict[str, Any]:
        run = self._runs.get(runtime_id)
        if run is None:
            raise LocalRuntimeRefused("unknown_runtime", f"No run {runtime_id}")
        run.cancel_requested = True
        client = run.client
        if client is not None and run.status in {"starting", "running"}:
            try:
                await client.interrupt()
                cancelled = True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[local_runtime] interrupt failed for %s: %s", runtime_id, exc
                )
                cancelled = False
        else:
            cancelled = False
        return {"runtime_id": runtime_id, "cancelled": cancelled, "status": run.status}

    # --------------------------------------------------------------- streaming

    async def subscribe(self, runtime_id: str):
        """Replay buffered events, then yield live events until the run ends."""
        run = self._runs.get(runtime_id)
        if run is None:
            raise LocalRuntimeRefused("unknown_runtime", f"No run {runtime_id}")
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_MAX
        )
        for event in list(run.events):
            queue.put_nowait(event)
        if run.status in {"completed", "failed", "cancelled"}:
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

    def _emit(self, run: _LocalRun, event: dict[str, Any]) -> None:
        run.events.append(event)
        for queue in list(run.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled consumer must never stall the run; it can re-attach
                # and replay the bounded buffer.
                with contextlib.suppress(ValueError):
                    run.subscribers.remove(queue)

    def _finish_subscribers(self, run: _LocalRun) -> None:
        for queue in list(run.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
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

            cli = _find_claude_cli()
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
                self._establish_identity(run), name=f"claude-local-identity:{run.runtime_id}"
            )
            saw_result = False
            try:
                async with client:
                    run.status = "running"
                    self._emit(
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
                        await client.query(request.prompt, session_id=run.session_id)
                        async for message in client.receive_response():
                            payload = _message_payload(message)
                            self._emit(
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
                                await self._mirror(run)
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
        except Exception as exc:  # noqa: BLE001
            run.status = "cancelled" if run.cancel_requested else "failed"
            if run.status == "failed":
                run.error = str(exc)
                logger.error(
                    "[local_runtime] run %s FAILED: %s", run.runtime_id, exc, exc_info=True
                )
        finally:
            run.ended_at = time.time()
            # Final mirror so the ledger holds the settle state even when the
            # run failed or was cancelled mid-turn.
            await self._mirror(run, final=True)
            self._emit(
                run,
                {
                    "event": "runtime_finished",
                    "runtime_id": run.runtime_id,
                    "status": run.status,
                    "session_id": run.session_id,
                    "conversation_id": run.conversation_id,
                    "error": run.error,
                },
            )
            run.identity_ready.set()
            self._finish_subscribers(run)

    async def _establish_identity(self, run: _LocalRun) -> None:
        """Wait for Claude's own transcript file, then compute bridge identity."""
        deadline = time.time() + _TRANSCRIPT_WAIT_SECONDS
        while time.time() < deadline:
            transcript = self._find_transcript(run.session_id)
            if transcript is not None:
                self._bind_identity(run, transcript)
                # First mirror as soon as the session exists, so the binding +
                # conversation are minted early and the browser can open it.
                await self._mirror(run)
                run.identity_ready.set()
                return
            await asyncio.sleep(0.5)
        logger.warning(
            "[local_runtime] transcript for session %s did not appear within %ss",
            run.session_id,
            _TRANSCRIPT_WAIT_SECONDS,
        )

    def _bind_identity(self, run: _LocalRun, transcript: Path) -> None:
        if run.provider_session_id is not None:
            return
        project_dir = transcript.parent
        project_key = "claude-local:" + hashlib.sha256(
            str(project_dir.resolve()).encode("utf-8")
        ).hexdigest()
        run.transcript_path = transcript
        run.provider_project_key = project_key
        run.provider_session_id = _bridge_provider_session_id(
            project_key, run.session_id
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
        attempts = _MIRROR_CONFLICT_RETRIES if final else 1
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
                    )
                )
                run.mirror_passes += 1
                run.mirror_error = None
                self._emit(
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
                    await asyncio.sleep(1.0)
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
        for run in list(self._runs.values()):
            if run.status in {"starting", "running"}:
                run.cancel_requested = True
                client = run.client
                if client is not None:
                    with contextlib.suppress(Exception):
                        await client.interrupt()
            if run.task is not None and not run.task.done():
                run.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await run.task


_runtime: LocalClaudeRuntime | None = None


def get_local_claude_runtime() -> LocalClaudeRuntime:
    global _runtime
    if _runtime is None:
        _runtime = LocalClaudeRuntime()
    return _runtime


__all__ = [
    "APPROVED_FOLDERS_SETTING",
    "LocalClaudeRuntime",
    "LocalRuntimeRefused",
    "LocalRuntimeStartRequest",
    "WORKSPACE_ROOTS_SETTING",
    "get_local_claude_runtime",
]
