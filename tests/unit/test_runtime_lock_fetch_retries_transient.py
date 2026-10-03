"""A transient TLS timeout must not abort the release's runtime-lock refresh.

Hosted release 37153511268 died on one `_ssl.c: The handshake operation timed
out` from an index read. `_fetch` retries transient failures and passes any
definitive 4xx straight through.
"""

from __future__ import annotations

import importlib.util
import io
import ssl
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "generate-runtime-locks.py"
_spec = importlib.util.spec_from_file_location("generate_runtime_locks", _PATH)
locks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(locks)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(failures: list[BaseException], body: bytes = b"ok"):
    calls = {"n": 0}

    def open_(url, timeout):
        calls["n"] += 1
        if failures:
            raise failures.pop(0)
        return _Response(body)

    return open_, calls


def test_handshake_timeout_then_success_is_retried():
    opener, calls = _opener(
        [urllib.error.URLError(ssl.SSLError("The handshake operation timed out"))]
    )
    sleeps: list[float] = []
    assert locks._fetch("https://x", sleep=sleeps.append, opener=opener) == b"ok"
    assert calls["n"] == 2
    assert sleeps == [locks.FETCH_BACKOFF_SECONDS]


def test_server_error_is_retried():
    err = urllib.error.HTTPError("https://x", 503, "unavailable", {}, None)
    opener, calls = _opener([err, err])
    assert locks._fetch("https://x", sleep=lambda _s: None, opener=opener) == b"ok"
    assert calls["n"] == 3


def test_not_found_is_definitive_and_not_retried():
    err = urllib.error.HTTPError("https://x", 404, "missing", {}, None)
    opener, calls = _opener([err])
    with pytest.raises(urllib.error.HTTPError):
        locks._fetch("https://x", sleep=lambda _s: None, opener=opener)
    assert calls["n"] == 1


def test_exhausted_attempts_fail_loudly_naming_every_error():
    failures = [TimeoutError(f"t{i}") for i in range(locks.FETCH_ATTEMPTS)]
    opener, calls = _opener(failures)
    with pytest.raises(RuntimeError) as info:
        locks._fetch("https://x", sleep=lambda _s: None, opener=opener)
    assert calls["n"] == locks.FETCH_ATTEMPTS
    assert f"attempt {locks.FETCH_ATTEMPTS}" in str(info.value)
