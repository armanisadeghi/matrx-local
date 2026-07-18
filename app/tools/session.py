"""ToolSession — mutable state shared across tool calls within a connection."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named path aliases
#
# Remote callers (React, microservices, AI models) should never construct
# absolute paths themselves — they don't know which drive or OS the engine is
# running on.  Instead they use these logical names and let the engine resolve
# them to real absolute paths.
#
# Supported prefixes in file_path arguments:
#   @matrx/     → ~/.matrx/           (discovery, settings, instance, tasks)
#   @docs/      → ~/.matrx/documents/ (user notes — may be user-configured)
#   @temp/      → platform cache dir  (screenshots, audio, extracted files…)
#   @data/      → platform data dir   (persistent app data)
#   @logs/      → platform log dir
#   @home/      → user home directory (~ equivalent, explicit)
#   ~/          → user home directory (classic Unix shorthand, always expanded)
#
# Example: { "file_path": "@matrx/local.json" }  →  C:\Users\arman\.matrx\local.json
# ---------------------------------------------------------------------------

def _build_alias_map() -> dict[str, Path]:
    from app.config import MATRX_HOME_DIR, MATRX_USER_DIR, TEMP_DIR, DATA_DIR, LOG_DIR
    try:
        from app.services.paths.manager import safe_dir
        notes      = safe_dir("notes")
        files      = safe_dir("files")
        code       = safe_dir("code")
        workspaces = safe_dir("workspaces")
        agentdata  = safe_dir("agent_data")
    except Exception:
        from app.config import MATRX_NOTES_DIR, MATRX_FILES_DIR, MATRX_CODE_DIR, MATRX_WORKSPACES_DIR, MATRX_DATA_DIR
        notes, files, code = MATRX_NOTES_DIR, MATRX_FILES_DIR, MATRX_CODE_DIR
        workspaces, agentdata = MATRX_WORKSPACES_DIR, MATRX_DATA_DIR

    return {
        "@matrx":      MATRX_HOME_DIR,   # ~/.matrx/  — engine internals
        "@notes":      notes,            # user-configured notes dir
        "@files":      files,            # user-configured files dir
        "@code":       code,             # user-configured code dir
        "@workspaces": workspaces,       # agent workspace dir
        "@agentdata":  agentdata,        # agent internal data
        "@user":       MATRX_USER_DIR,   # ~/Documents/Matrx/
        "@temp":       TEMP_DIR,
        "@data":       DATA_DIR,
        "@logs":       Path(str(LOG_DIR)),
        "@home":       Path.home(),
        "@docs":       notes,            # deprecated alias — same as @notes
    }


def resolve_named_path(path: str) -> str:
    """Expand named aliases and ~ in a path string to a real absolute path.

    Handles:
      @matrx/local.json          → <MATRX_HOME_DIR>/local.json
      @docs/folder/note.md       → <DOCUMENTS_BASE_DIR>/folder/note.md
      @temp/screenshots/img.png  → <TEMP_DIR>/screenshots/img.png
      @data/...                  → <DATA_DIR>/...
      @logs/...                  → <LOG_DIR>/...
      @home/...                  → Path.home()/...
      ~/...                      → Path.home()/...   (standard Unix shorthand)
      /absolute/path             → unchanged
      relative/path              → unchanged (caller must join with cwd)
    """
    for alias, base in _build_alias_map().items():
        prefix = alias + "/"
        if path.startswith(prefix):
            return str(base / path[len(prefix):])
        if path == alias:
            return str(base)
    # Standard ~ expansion
    return os.path.expanduser(path)


@dataclass
class BackgroundShell:
    shell_id: str
    process: asyncio.subprocess.Process
    output_buffer: list[str] = field(default_factory=list)
    read_offset: int = 0
    is_complete: bool = False
    return_code: int | None = None


class ToolSession:
    """Tracks mutable state across tool calls for a single connected client.

    Each WebSocket or long-lived HTTP session gets its own ToolSession so
    working directory, background processes, and file-read history persist
    across sequential tool invocations.
    """

    def __init__(
        self,
        working_dir: str | None = None,
        *,
        execution_origin: str = "local",
        cloud_call_id: str | None = None,
        cloud_tool_name: str | None = None,
        cloud_access_token: str | None = None,
    ) -> None:
        raw = working_dir or str(Path.home())
        # Always fully expand ~ so cwd is always an absolute path.
        # On Windows, Path.home() returns the correct drive (e.g. D:\app_dev\...)
        # but a literal "~" passed by the client would otherwise stay as-is and
        # later get joined as C:\Users\<user>\~\... which is wrong.
        self.cwd: str = str(Path(raw).expanduser().resolve())
        self.files_read: set[str] = set()
        self.background_shells: dict[str, BackgroundShell] = {}
        self._shell_counter: int = 0
        # Artifact-producing tools use this context to select the execution
        # lane. Local is offline-first; cloud_delegated publishes inline for
        # the fastest possible suspended-turn continuation.
        self.execution_origin = execution_origin
        self.cloud_call_id = cloud_call_id
        self.cloud_tool_name = cloud_tool_name
        self.cloud_access_token = cloud_access_token

    def mark_file_read(self, path: str) -> None:
        self.files_read.add(os.path.realpath(path))

    def has_read_file(self, path: str) -> bool:
        return os.path.realpath(path) in self.files_read

    def next_shell_id(self) -> str:
        self._shell_counter += 1
        return f"shell_{self._shell_counter}"

    def resolve_path(self, path: str) -> str:
        """Resolve a path from a tool argument to a real absolute path.

        Handles named aliases (@matrx/, @docs/, @temp/, @home/, etc.),
        ~ expansion, absolute paths, and relative paths (joined to cwd).
        """
        expanded = resolve_named_path(path)
        if os.path.isabs(expanded):
            return expanded
        return os.path.join(self.cwd, expanded)

    async def cleanup(self) -> None:
        for shell in self.background_shells.values():
            if not shell.is_complete:
                try:
                    shell.process.kill()
                except ProcessLookupError:
                    pass
