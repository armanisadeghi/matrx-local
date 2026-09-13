"""Process boundary for the Claude Code session-index scan.

Reading Claude's session index is ~63,000 small JSON files (rglob + lstat +
``json.loads`` each, 56 s warm on this Mac, measured 2026-09-13). It ran in the
engine's own process via ``asyncio.to_thread``, and on 2026-09-13 the engine
declared a healthy Chromium dead during exactly that window: the browser pool's
30 s launch bound expired 47 s after Phase 3 began, at the same millisecond as
"Phase 2h.2: Claude session index warmed ✓", while the same browser launched in
3.7 s from a terminal. ``/setup/status`` and ``/devices/permissions`` took
10-20 s in the same window.

**What is proven and what is not.** A benchmark of the two paths on a warm cache
(2026-09-13) measured a worst event-loop lag of 45 ms for the thread and 51 ms
for the subprocess, so the thread's GIL contention alone does NOT reproduce the
17 s timer delay seen live — a cold read of 63,000 files, with its thread-pool
and disk contention, is a different animal from the warm one that can be
measured on demand. What a separate process buys is certain: none of that work
— GIL, allocator, thread pool or disk queue — happens inside the engine at all,
so it cannot be a suspect again. The honest browser state (one retry before any
accusation) is in ``app/services/scraper/browser_runtime.py``, and it is what
makes the screen correct regardless of which contention it was.

This module is the same shape as :mod:`app.common.keychain_helper`: a public
argv flag that ``run.py`` dispatches BEFORE any application import, so the
helper never boots an engine, never binds a port, and never touches runtime
state (Hard Rule 9 — a dev engine's world is inherited through the environment,
and nothing here creates a home directory).

The result crosses back as pickle on stdout. That is our own signed executable
talking to itself over a private pipe — the entries are frozen dataclasses
(:class:`~app.services.coding_sessions.claude_session_index.ClaudeSessionIndexEntry`)
carrying ``Path`` objects, which JSON cannot round-trip. The payload is framed
with a magic prefix and an explicit length so a stray line on the child's stdout
(a third-party import banner, a warning) can never be mistaken for data.
"""

from __future__ import annotations

import importlib
import pickle
import struct
import sys
import types
from pathlib import Path
from typing import Any

HELPER_ARGUMENT = "--matrx-claude-index-helper-v1"

# Measured 2026-09-13 on this Mac: 63,419 records, 1,917 conversations, 56 s
# warm. A cold cache is slower, so the bound is generous on purpose — it exists
# only so a wedged child (a stalled network home, a paused disk) can never hold
# a caller forever. It is not a performance expectation, and exceeding it costs
# correctness nothing: the caller falls back to the in-process scan.
HELPER_TIMEOUT_SECONDS = 240.0

# Framing: magic, then an 8-byte big-endian payload length, then the pickle.
PAYLOAD_MAGIC = b"\x00MATRX-CLAUDE-INDEX-1\x00"
_LENGTH_STRUCT = struct.Struct(">Q")
# A pickled index of this Mac's 1,800 conversations is ~1 MB. 64 MB is a sanity
# ceiling that refuses a corrupt length header instead of allocating on it.
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024

_INDEX_MODULE_NAME = "app.services.coding_sessions.claude_session_index"
_INDEX_PACKAGE_NAME = "app.services.coding_sessions"


def helper_command(root: Path) -> list[str]:
    """The argv that runs this helper as a short-lived copy of the engine."""
    if getattr(sys, "frozen", False):
        return [sys.executable, HELPER_ARGUMENT, str(root)]
    run_py = Path(__file__).resolve().parents[2] / "run.py"
    return [sys.executable, str(run_py), HELPER_ARGUMENT, str(root)]


def encode_payload(result: tuple[dict[str, Any], dict[str, int]]) -> bytes:
    """Frame one ``(entries, totals)`` result for the pipe."""
    blob = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
    return PAYLOAD_MAGIC + _LENGTH_STRUCT.pack(len(blob)) + blob


def decode_payload(stream: bytes) -> tuple[dict[str, Any], dict[str, int]]:
    """Recover the result from the child's stdout, ignoring anything around it.

    Raises ``ValueError`` when no intact payload is present — callers treat that
    exactly like a non-zero exit: fall back to the in-process read.
    """
    start = stream.rfind(PAYLOAD_MAGIC)
    if start < 0:
        raise ValueError("session-index helper produced no framed payload")
    header = start + len(PAYLOAD_MAGIC)
    if len(stream) < header + _LENGTH_STRUCT.size:
        raise ValueError("session-index helper payload header is truncated")
    (length,) = _LENGTH_STRUCT.unpack_from(stream, header)
    if length > MAX_PAYLOAD_BYTES:
        raise ValueError("session-index helper payload length is implausible")
    body = stream[header + _LENGTH_STRUCT.size : header + _LENGTH_STRUCT.size + length]
    if len(body) != length:
        raise ValueError("session-index helper payload is truncated")
    result = pickle.loads(body)
    if (
        not isinstance(result, tuple)
        or len(result) != 2
        or not isinstance(result[0], dict)
        or not isinstance(result[1], dict)
    ):
        raise ValueError("session-index helper payload has the wrong shape")
    return result


def _load_index_module() -> types.ModuleType:
    """Import the index reader WITHOUT executing its package initializer.

    ``app.services.coding_sessions.__init__`` eagerly imports the bridge outbox
    and the capture reconciler, which pull in ``app.config``, the local database
    and the HTTP clients — engine machinery this short-lived process must never
    touch. Standing in a bare parent module keeps the helper at ~100 imported
    modules and ~0.06 s, and leaves the class's ``__module__`` at its canonical
    name so the parent unpickles the real dataclass.
    """
    existing = sys.modules.get(_INDEX_MODULE_NAME)
    if existing is not None:
        return existing
    if _INDEX_PACKAGE_NAME not in sys.modules:
        stub = types.ModuleType(_INDEX_PACKAGE_NAME)
        # Frozen builds resolve submodules through PyInstaller's name-keyed
        # importer and ignore this path; a source run needs the real directory.
        stub.__path__ = [str(Path(__file__).resolve().parents[1] / "services" / "coding_sessions")]
        sys.modules[_INDEX_PACKAGE_NAME] = stub
    try:
        return importlib.import_module(_INDEX_MODULE_NAME)
    except Exception:
        # Never let the isolation trick be the reason a scan fails: drop the
        # stand-in and take the ordinary (heavier) import path.
        sys.modules.pop(_INDEX_PACKAGE_NAME, None)
        return importlib.import_module(_INDEX_MODULE_NAME)


def run_claude_index_helper() -> int:
    """Scan one session-index root and write the result to stdout. Exit code."""
    try:
        arguments = sys.argv[1:]
        position = arguments.index(HELPER_ARGUMENT)
        remainder = [value for value in arguments[position + 1 :] if value]
        if not remainder:
            raise ValueError("no session-index root was given")
        root = Path(remainder[0]).expanduser()

        index_module = _load_index_module()
        result = index_module.read_session_index(root)

        stdout = sys.stdout.buffer
        stdout.write(encode_payload(result))
        stdout.flush()
        return 0
    except Exception as exc:  # noqa: BLE001 — the exit code is the contract
        sys.stderr.write(f"claude index helper failed: {type(exc).__name__}: {exc}\n")
        sys.stderr.flush()
        return 1


__all__ = [
    "HELPER_ARGUMENT",
    "HELPER_TIMEOUT_SECONDS",
    "MAX_PAYLOAD_BYTES",
    "PAYLOAD_MAGIC",
    "decode_payload",
    "encode_payload",
    "helper_command",
    "run_claude_index_helper",
]
