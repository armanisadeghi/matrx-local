"""FULL DOWNLOAD, this-computer half: find a session's provider file, hand it over.

AI Matrx keeps only a coding session's text and the NAMES of the tools it
called (aidream ``coding_session_bridge/kept_shape.py``). The full transcript —
every tool input and output — is the provider's own file on the computer that
ran it. Full Download therefore looks HERE first (Arman, 2026-09-28: "it should
look on your computer first and get it directly first if it's available and
then go to s3 if it's not").

Two verbs, both owner-driven, never automatic:

* :func:`save_to_downloads` copies the file (plus any sub-agent streams) into
  this computer's Downloads folder and says exactly where it went.
* :func:`backup_to_cloud` sends the same bytes to the ONE server upload door
  (``POST /coding-sessions/raw-transcripts/backup``), which refuses unless the
  owner turned on the Feature Knob ``coding_session_bridge.raw_transcript_backup``.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")


@dataclass(frozen=True)
class LocatedTranscript:
    main: Path
    sidechains: list[Path]

    @property
    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in [self.main, *self.sidechains])


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def locate(
    provider: str, native_session_id: str, transcript_path: str | None = None
) -> LocatedTranscript | None:
    """The provider's own file for this session on this computer, or None.

    Only files under the provider's own transcript root are ever returned — a
    path hint from the cloud that points anywhere else is ignored.
    """
    if not _ID.match(native_session_id or ""):
        return None
    if provider == "claude_code":
        from app.services.coding_sessions.claude_index_store import default_transcripts_root
        from app.services.coding_sessions.sync_truth_reader import transcript_paths

        root = default_transcripts_root()
        if transcript_path:
            hinted = Path(transcript_path).expanduser()
            if hinted.is_file() and not hinted.is_symlink() and _within(hinted, root):
                sidechain_dir = hinted.parent / native_session_id
                sidechains = (
                    sorted(p for p in sidechain_dir.rglob("*.jsonl") if p.is_file())
                    if sidechain_dir.is_dir()
                    else []
                )
                return LocatedTranscript(main=hinted, sidechains=sidechains)
        paths = transcript_paths(native_session_id)
        main = next((p for p in paths if p.name == f"{native_session_id}.jsonl"), None)
        if main is None:
            return None
        return LocatedTranscript(main=main, sidechains=[p for p in paths if p != main])
    if provider == "codex":
        from app.services.coding_sessions.codex_session_index import default_rollout_root

        root = default_rollout_root()
        if not root.is_dir():
            return None
        matches = sorted(
            (p for p in root.rglob(f"rollout-*{native_session_id}.jsonl") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        if not matches:
            return None
        return LocatedTranscript(main=matches[-1], sidechains=[])
    return None


def _downloads_dir() -> Path:
    target = Path.home() / "Downloads"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not find a free name for {path.name} in Downloads")


def save_to_downloads(located: LocatedTranscript, provider: str, native_session_id: str) -> dict[str, Any]:
    """Copy the file (and its sub-agent streams, in a sibling folder) into Downloads."""
    downloads = _downloads_dir()
    prefix = "claude-code" if provider == "claude_code" else provider
    destination = _unique(downloads / f"{prefix}-{native_session_id}.jsonl")
    shutil.copy2(located.main, destination)
    sidechain_folder: str | None = None
    if located.sidechains:
        folder = _unique(downloads / f"{prefix}-{native_session_id}-subagents")
        folder.mkdir()
        for stream in located.sidechains:
            shutil.copy2(stream, _unique(folder / stream.name))
        sidechain_folder = str(folder)
    return {
        "saved_path": str(destination),
        "subagent_folder": sidechain_folder,
        "subagent_streams": len(located.sidechains),
        "bytes": located.total_bytes,
    }


async def backup_to_cloud(
    located: LocatedTranscript,
    *,
    provider: str,
    provider_session_id: str,
    provider_project_key: str | None = None,
) -> dict[str, Any]:
    """Send the main transcript to the server's one backup door. Refusals are answers."""
    from app.services.aidream.client import AIDreamError, AIDreamOfflineError, get_aidream_client
    from app.services.local_db.database import get_db
    from app.services.local_db.repositories import TokenRepo

    client = get_aidream_client()
    if client is None:
        return {"backed_up": False, "detail": "No AI Matrx server is configured on this computer."}
    token_row = await TokenRepo(get_db()).get()
    if not token_row or not token_row.get("access_token"):
        return {"backed_up": False, "detail": "This computer is not signed in to AI Matrx."}
    params = {"provider": provider, "provider_session_id": provider_session_id}
    if provider_project_key:
        params["provider_project_key"] = provider_project_key
    try:
        answer = await client.post_bytes(
            "/coding-sessions/raw-transcripts/backup",
            located.main.read_bytes(),
            params=params,
            content_type="application/x-ndjson",
            jwt=str(token_row["access_token"]),
        )
    except AIDreamOfflineError:
        return {"backed_up": False, "detail": "AI Matrx could not be reached from this computer."}
    except AIDreamError as exc:
        body = getattr(exc, "body", None)
        detail = body.get("detail") if isinstance(body, dict) else None
        message = (
            detail.get("user_message")
            if isinstance(detail, dict) and detail.get("user_message")
            else str(exc)
        )
        code = detail.get("error") if isinstance(detail, dict) else None
        return {"backed_up": False, "code": code, "detail": message}
    return {"backed_up": True, "backup": answer}
