"""Per-turn token usage read from Claude Code's own transcripts.

Every assistant turn Claude Code writes to ``~/.claude/projects/**/*.jsonl``
carries ``message.usage`` (input, output, cache-creation and cache-read
tokens), ``message.model`` and a timestamp. That is the local truth of what
this Mac spent — no account API, no guesswork.

Two facts about those files decide the shape of this module:

* **One message is written many times.** A streamed reply lands as one line
  per content block, all carrying the same ``message.id``/``requestId`` and
  the same usage (measured 2026-09-17: 4,698 assistant lines for 1,700
  distinct messages, one message repeated 14 times). Counting lines would
  overstate usage several-fold, so a turn is identified by that pair and
  counted once.
* **The files only grow.** A transcript is append-only while the session is
  open, so this reader keeps a byte offset per transcript and reads only the
  tail that appeared since the last refresh. The full 10 GB tree is read once,
  in the background, and never again — the same discipline as the record
  index that owns the store this feeds.

Nothing here touches the filesystem beyond opening the one transcript it is
handed read-only; walking the tree belongs to
:func:`claude_index_store.refresh_transcripts_sync`, which already stats every
transcript and hands the moved ones here.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# How many (message id, request id) keys to remember per transcript so a
# message whose duplicate lines straddle two refreshes is still counted once.
# Duplicates of one message are contiguous; 64 is generous.
RECENT_KEYS = 64

# The stamp a cursor carries while its transcript is only partly read.
PENDING_STAMP = -1

# A line longer than this is not an assistant turn we can price; it is skipped
# without being parsed so one pathological line cannot stall a refresh.
MAX_LINE_BYTES = 4 * 1024 * 1024

USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
)

# Claude's own placeholder for a turn it produced without a model call.
_SYNTHETIC_MODEL = "<synthetic>"
_ASSISTANT_TAG = b'"type":"assistant"'


@dataclass
class UsageCursor:
    """Where the reader stopped in one transcript, persisted between refreshes."""

    offset: int = 0
    size: int = 0
    mtime_ns: int = 0
    recent_keys: list[str] = field(default_factory=list)


@dataclass
class UsageIncrement:
    """What one bounded read of one transcript's tail produced."""

    # (hour bucket ISO, model) -> counters incl. "requests"
    cells: dict[tuple[str, str], Counter[str]]
    cursor: UsageCursor
    # True when the file shrank or was rewritten and the session's stored
    # usage must be replaced by this increment rather than added to.
    restarted: bool
    bytes_read: int
    # True when the byte budget stopped this read before the file's end.
    truncated: bool


def hour_bucket(timestamp: str) -> str | None:
    """``2026-09-11T02:51:58.037Z`` -> ``2026-09-11T02`` (UTC), or None."""
    try:
        when = dt.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H")


def turn_from_line(raw: bytes) -> tuple[str, str, str, dict[str, int]] | None:
    """(dedupe key, hour bucket, model, tokens) for one assistant line, or None.

    None for anything that is not a priced assistant turn: other record
    types, synthetic turns, turns with no usage block, unparseable lines.
    """
    if _ASSISTANT_TAG not in raw:
        return None
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    model = str(message.get("model") or "").strip()
    if not model or model == _SYNTHETIC_MODEL:
        return None
    bucket = hour_bucket(record.get("timestamp"))
    if bucket is None:
        return None
    message_id = str(message.get("id") or "")
    request_id = str(record.get("requestId") or "")
    key = f"{message_id}|{request_id}" if (message_id or request_id) else str(record.get("uuid") or "")
    if not key:
        return None
    tokens = {
        "input_tokens": _count(usage.get("input_tokens")),
        "output_tokens": _count(usage.get("output_tokens")),
        "cache_creation_tokens": _count(usage.get("cache_creation_input_tokens")),
        "cache_read_tokens": _count(usage.get("cache_read_input_tokens")),
    }
    return key, bucket, model, tokens


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def read_usage_increment(
    path: Path,
    cursor: UsageCursor | None,
    *,
    size: int,
    mtime_ns: int,
    byte_budget: int,
) -> UsageIncrement:
    """Read the bytes of ``path`` that appeared since ``cursor``.

    ``size``/``mtime_ns`` are the stamp the caller's walk already took, so the
    cursor records exactly the file the caller saw. A partial trailing line
    (Claude mid-write) is left for the next refresh. ``byte_budget`` bounds
    this read; when it stops the file short, ``truncated`` is True and the
    cursor points at the last complete line consumed.
    """
    previous = cursor or UsageCursor()
    restarted = previous.offset > size
    start = 0 if restarted else previous.offset
    recent: list[str] = [] if restarted else list(previous.recent_keys)
    seen = set(recent)
    cells: dict[tuple[str, str], Counter[str]] = {}
    consumed = start
    truncated = False
    with path.open("rb") as source:
        source.seek(start)
        while True:
            if consumed - start >= byte_budget:
                truncated = True
                break
            raw = source.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                # Mid-write tail: Claude has not finished this line yet.
                break
            consumed += len(raw)
            if len(raw) > MAX_LINE_BYTES:
                continue
            turn = turn_from_line(raw)
            if turn is None:
                continue
            key, bucket, model, tokens = turn
            if key in seen:
                continue
            seen.add(key)
            recent.append(key)
            if len(recent) > RECENT_KEYS:
                seen.discard(recent.pop(0))
            cell = cells.setdefault((bucket, model), Counter())
            cell["requests"] += 1
            for name in USAGE_FIELDS:
                cell[name] += tokens[name]
    # Only a read that reached the file's end records the walk's stamp; a
    # truncated one stores the PENDING stamp so the next refresh's stamp
    # comparison picks the file up again where this one stopped.
    next_cursor = UsageCursor(
        offset=consumed,
        size=PENDING_STAMP if truncated else size,
        mtime_ns=PENDING_STAMP if truncated else mtime_ns,
        recent_keys=recent,
    )
    return UsageIncrement(
        cells=cells,
        cursor=next_cursor,
        restarted=restarted,
        bytes_read=consumed - start,
        truncated=truncated,
    )


def merge_cells(
    into: dict[tuple[str, str], Counter[str]], cells: Iterable[tuple[tuple[str, str], Counter[str]]]
) -> None:
    for key, counter in cells:
        into.setdefault(key, Counter()).update(counter)


__all__ = [
    "MAX_LINE_BYTES",
    "PENDING_STAMP",
    "RECENT_KEYS",
    "USAGE_FIELDS",
    "UsageCursor",
    "UsageIncrement",
    "hour_bucket",
    "merge_cells",
    "read_usage_increment",
    "turn_from_line",
]
