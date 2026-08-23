"""The RETURN direction: an AI Matrx rename written into Claude Code's own index.

Arman's ruling (2026-08-16): *"The Claude Code title is what we should use for
our label. And when our conversations go to Claude Code, or if I update this,
then the Claude Code value should be updated to match."*

The inbound half (Claude Code → AI Matrx) lives in
:mod:`app.services.coding_sessions.title_sync`. This module is the other half:
it writes a title the user typed in AI Matrx into the very records Claude Code
reads, at
``<app support>/Claude/claude-code-sessions/<accountUuid>/<orgUuid>/local_<id>.json``.

**This writes into another application's data, so every rule here is a
restriction.** All of them were derived by observing the real app on
2026-08-16, not assumed:

- **Two fields, never a third.** Only ``title`` and ``titleSource`` are
  assigned. Claude Code's own rename writes exactly those two (it inserts
  ``titleSource`` immediately after ``title`` and touches nothing else), and a
  human rename in AI Matrx is a user-set title, so ``titleSource`` is ``user``
  — the same value Claude records for a title its auto-titler must not replace.
- **Byte-fidelity or refuse.** Claude's writers use three different JSON
  serializations across its history (all 4,282 records on this machine match
  one of them exactly). Before writing, the parsed record is re-serialized with
  each candidate and compared to the original bytes; the candidate that
  reproduces them is reused for the write. If none does — a format this module
  has never seen — the file is left untouched. So every byte except the two
  changed values is preserved, including key order and unicode escaping.
- **Backed up before the first write.** The original bytes of each record are
  copied once into ``<MATRX_HOME_DIR>/backups/claude-session-index/`` before
  that file is ever modified. Later writes do not overwrite the backup: it is a
  snapshot of the file as it was before AI Matrx first touched it.
- **Fenced against a concurrent Claude Code write.** Size, mtime_ns and the
  content SHA-256 are captured on read and re-checked immediately before the
  atomic rename. A record that moved underneath us is refused, never clobbered.
- **Atomic.** Written to a temp file in the same directory, fsynced, then
  ``os.replace``d, so a reader never sees a partial record.
- **Every copy of the session.** These index files are unioned across Claude
  accounts, so one session commonly has five records. All of them are written,
  which is what keeps a stale sibling from winning the next inbound read.

**Latency is honest, not instant.** Observed on 2026-08-16: Claude Code loads
this index at startup and serves it from memory — a write to disk is not picked
up live. The user's rename appears in Claude Code's sidebar after Claude Code
reloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.common.system_logger import get_logger
from app.config import MATRX_HOME_DIR

logger = get_logger()

TITLE_FIELD = "title"
TITLE_SOURCE_FIELD = "titleSource"
# An AI Matrx rename is the human naming their own session; that is exactly
# what Claude Code records as `user` when the human renames it there.
TITLE_SOURCE_USER = "user"

MAX_RECORD_BYTES = 8_388_608
MAX_TITLE_CHARS = 160

# Every JSON serialization Claude Code's writers have produced, newest first.
# A record must round-trip byte-exactly through one of these or it is not
# written. Verified against all 4,282 records on this machine 2026-08-16.
_SERIALIZERS: tuple[tuple[tuple[str, str], bool], ...] = (
    ((", ", ": "), True),
    ((", ", ": "), False),
    ((",", ":"), False),
)


def default_backup_root() -> Path:
    """Where a record's pre-modification bytes are kept, once per file."""
    return MATRX_HOME_DIR / "backups" / "claude-session-index"


@dataclass(frozen=True)
class RecordWriteOutcome:
    """What happened to one ``local_*.json`` record."""

    path: Path
    status: str
    detail: str | None = None


@dataclass(frozen=True)
class LabelWriteResult:
    """What happened to one session's label across all of its records."""

    cli_session_id: str
    title: str
    written: int
    unchanged: int
    refused: int
    outcomes: tuple[RecordWriteOutcome, ...]

    @property
    def applied(self) -> bool:
        """True when Claude's index now shows this title everywhere it can."""
        return self.refused == 0 and (self.written > 0 or self.unchanged > 0)

    def summary(self) -> dict[str, Any]:
        """Counts plus refusal reasons — never a raw path."""
        reasons = sorted(
            {
                outcome.status
                for outcome in self.outcomes
                if outcome.status not in {"written", "unchanged"}
            }
        )
        return {
            "written": self.written,
            "unchanged": self.unchanged,
            "refused": self.refused,
            "refusal_reasons": reasons,
        }


def _serializer_for(
    record: dict[str, Any], raw: bytes
) -> tuple[tuple[str, str], bool] | None:
    """The exact serialization that reproduces ``raw``, or ``None``."""
    for separators, ensure_ascii in _SERIALIZERS:
        candidate = json.dumps(
            record, ensure_ascii=ensure_ascii, separators=separators
        ).encode("utf-8")
        if candidate == raw:
            return separators, ensure_ascii
    return None


def _fence(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return info.st_size, info.st_mtime_ns


def _writable(path: Path) -> bool:
    """Prove the current app user can open the exact record for writing."""
    try:
        descriptor = os.open(path, os.O_WRONLY)
    except OSError:
        return False
    os.close(descriptor)
    return True


class ClaudeSessionIndexWriter:
    """Writes an AI Matrx title into Claude Code's own session-index records."""

    def __init__(self, *, backup_root: Path | None = None) -> None:
        self._backup_root = backup_root or default_backup_root()

    def write_title(
        self, *, cli_session_id: str, title: str, record_paths: tuple[Path, ...]
    ) -> LabelWriteResult:
        """Set ``title`` on every record carrying ``cli_session_id``.

        ``record_paths`` comes from the same index read that produced the
        session's current labels, so this never scans on its own and never
        touches a record the reader did not already resolve to this session.
        """
        cleaned = " ".join(title.split()).strip()[:MAX_TITLE_CHARS]
        if not cleaned:
            raise ValueError("refusing to write an empty title into Claude's index")
        outcomes = [
            self._write_record(path, cli_session_id=cli_session_id, title=cleaned)
            for path in record_paths
        ]
        result = LabelWriteResult(
            cli_session_id=cli_session_id,
            title=cleaned,
            written=sum(1 for o in outcomes if o.status == "written"),
            unchanged=sum(1 for o in outcomes if o.status == "unchanged"),
            refused=sum(
                1 for o in outcomes if o.status not in {"written", "unchanged"}
            ),
            outcomes=tuple(outcomes),
        )
        if result.refused:
            logger.warning(
                "[coding_session_bridge] claude index write refused session=%s %s",
                cli_session_id,
                result.summary(),
            )
        return result

    def _write_record(
        self, path: Path, *, cli_session_id: str, title: str
    ) -> RecordWriteOutcome:
        before = _fence(path)
        if before is None:
            return RecordWriteOutcome(path, "missing")
        if not _writable(path):
            return RecordWriteOutcome(path, "not_writable")
        if before[0] > MAX_RECORD_BYTES:
            return RecordWriteOutcome(path, "oversize")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return RecordWriteOutcome(path, "unreadable", str(exc))
        try:
            record = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return RecordWriteOutcome(path, "unparseable")
        if not isinstance(record, dict):
            return RecordWriteOutcome(path, "unparseable")
        if record.get("cliSessionId") != cli_session_id:
            # The record was rewritten for a different session between the
            # index read and now. Never write a label onto the wrong session.
            return RecordWriteOutcome(path, "identity_changed")
        if (
            record.get(TITLE_FIELD) == title
            and record.get(TITLE_SOURCE_FIELD) == TITLE_SOURCE_USER
        ):
            return RecordWriteOutcome(path, "unchanged")
        serializer = _serializer_for(record, raw)
        if serializer is None:
            return RecordWriteOutcome(path, "unknown_format")
        updated = _with_title(record, title)
        payload = json.dumps(
            updated, ensure_ascii=serializer[1], separators=serializer[0]
        ).encode("utf-8")
        try:
            self._backup_once(path, raw)
        except OSError as exc:
            return RecordWriteOutcome(path, "backup_failed", str(exc))
        try:
            return self._replace(
                path, payload, fence=before, digest=hashlib.sha256(raw).digest()
            )
        except OSError as exc:
            return RecordWriteOutcome(path, "write_failed", str(exc))

    def _replace(
        self, path: Path, payload: bytes, *, fence: tuple[int, int], digest: bytes
    ) -> RecordWriteOutcome:
        directory = path.parent
        handle, temp_name = tempfile.mkstemp(
            dir=directory, prefix=".matrx-", suffix=".json"
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Re-fence immediately before the rename. Claude Code may hold this
            # file open or rewrite it at any moment; if it moved since we read
            # it, its version is newer than ours and we refuse rather than
            # clobber. The next inbound pass will pull Claude's value instead.
            if (
                _fence(path) != fence
                or hashlib.sha256(path.read_bytes()).digest() != digest
            ):
                return RecordWriteOutcome(path, "concurrent_modification")
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return RecordWriteOutcome(path, "written")

    def _backup_once(self, path: Path, raw: bytes) -> None:
        """Snapshot the record as it was before AI Matrx ever touched it."""
        target = self._backup_root / _backup_name(path)
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".matrx-")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, target)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        shutil.copystat(path, target, follow_symlinks=False)


def _with_title(record: dict[str, Any], title: str) -> dict[str, Any]:
    """A copy with ``title``/``titleSource`` set, in Claude's own key order.

    Claude Code inserts ``titleSource`` directly after ``title`` when it first
    records one; matching that keeps a first write from reordering the record.
    """
    updated: dict[str, Any] = {}
    for key, value in record.items():
        if key == TITLE_SOURCE_FIELD:
            continue
        updated[key] = title if key == TITLE_FIELD else value
        if key == TITLE_FIELD:
            updated[TITLE_SOURCE_FIELD] = TITLE_SOURCE_USER
    if TITLE_FIELD not in updated:
        updated[TITLE_FIELD] = title
        updated[TITLE_SOURCE_FIELD] = TITLE_SOURCE_USER
    return updated


def _backup_name(path: Path) -> str:
    """A flat, collision-free backup name: ``<account>_<org>_<file>``."""
    return "_".join(path.parts[-3:])


__all__ = [
    "MAX_RECORD_BYTES",
    "MAX_TITLE_CHARS",
    "TITLE_SOURCE_USER",
    "ClaudeSessionIndexWriter",
    "LabelWriteResult",
    "RecordWriteOutcome",
    "default_backup_root",
]
