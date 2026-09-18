"""Per-turn token usage read from Claude Code's own transcripts.

Every assistant turn Claude Code writes to ``~/.claude/projects/**/*.jsonl``
carries ``message.usage`` (input, output, cache-creation and cache-read
tokens), ``message.model`` and a timestamp. That is the local truth of what
this Mac spent — no account API, no guesswork.

Two facts about those files decide the shape of this module:

* **One message is written many times, and not only in one run.** A streamed
  reply lands as one line per content block, all carrying the same
  ``message.id``/``requestId`` and the same usage (measured 2026-09-17: 4,698
  assistant lines for 1,700 distinct messages, one message repeated 14
  times). Claude ALSO re-writes an earlier block of assistant lines much
  later in a long session: in the real 52.6 MB transcript 97ce06fb… 205
  messages reappear with more than 64 other messages in between (measured
  2026-09-18, lane CS-33). So the repeats are NOT contiguous, and a dedupe
  that remembers only the last N messages counts those a second time — it
  inflated that one session by 208 requests and 105 million cache-read
  tokens. A turn is identified by that pair and counted once per SESSION:
  the reader is handed every key the session has already contributed and
  reports back the keys this read added, which the store persists beside the
  session's usage rows.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

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
    """Where the reader stopped in one transcript, persisted between refreshes.

    Position only: the keys already counted for the session are held beside
    its usage rows (``transcript_usage_key``) and handed to
    :func:`read_usage_increment` as ``seen_keys``, so one refresh never holds
    more than the session it is reading.
    """

    offset: int = 0
    size: int = 0
    mtime_ns: int = 0


@dataclass
class UsageIncrement:
    """What one bounded read of one transcript's tail produced."""

    # (hour bucket ISO, model) -> counters incl. "requests"
    cells: dict[tuple[str, str], Counter[str]]
    cursor: UsageCursor
    # The (message id, request id) keys this read counted for the first time,
    # in the order they appeared. The caller persists them with the session so
    # the next refresh — and a repeat written thousands of lines later — still
    # counts the message once.
    new_keys: list[str]
    # True when the file shrank or was rewritten and the session's stored
    # usage AND keys must be replaced by this increment rather than added to.
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
    seen_keys: Iterable[str] | None = None,
) -> UsageIncrement:
    """Read the bytes of ``path`` that appeared since ``cursor``.

    ``size``/``mtime_ns`` are the stamp the caller's walk already took, so the
    cursor records exactly the file the caller saw. A partial trailing line
    (Claude mid-write) is left for the next refresh. ``byte_budget`` bounds
    this read; when it stops the file short, ``truncated`` is True and the
    cursor points at the last complete line consumed.

    ``seen_keys`` is every key this session has already been counted for —
    the whole session, never a tail of it. A rewritten (shorter) file starts
    over and ignores them, because its stored usage is replaced too.
    """
    previous = cursor or UsageCursor()
    restarted = previous.offset > size
    start = 0 if restarted else previous.offset
    seen: set[str] = set() if (restarted or seen_keys is None) else set(seen_keys)
    added: list[str] = []
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
            added.append(key)
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
    )
    return UsageIncrement(
        cells=cells,
        cursor=next_cursor,
        new_keys=added,
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
    "USAGE_FIELDS",
    "UsageCursor",
    "UsageIncrement",
    "hour_bucket",
    "merge_cells",
    "read_usage_increment",
    "turn_from_line",
]
