"""Test-only stand-ins for the ports an app injects. Each records what it was given."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from matrx_coding_history.account import AccountSnapshot
from matrx_coding_history.bridge import BridgeRequest
from matrx_coding_history.ports import CloudError

USER = "00000000-0000-4000-8000-000000000001"


async def account_a() -> AccountSnapshot:
    return AccountSnapshot(True, "a" * 64, "a" * 12, "2.1.228", None, account_label="golden@example.com")


class User:
    def __init__(self, user_id: str | None = USER) -> None:
        self.value = user_id

    async def user_id(self) -> str | None:
        return self.value

    async def access_token(self) -> str | None:
        return "token" if self.value else None


class SyncMeta:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def set_last_sync(self, key: str, **fields: Any) -> None:
        self.calls.append((key, fields))


class RecordingSink:
    """Accepts everything; ``fail_for`` raises for envelopes of the named native sessions."""

    def __init__(self, fail_for: dict[str, Exception] | None = None) -> None:
        self.requests: list[BridgeRequest] = []
        self.fail_for = fail_for or {}

    async def enqueue_many(self, requests: Sequence[BridgeRequest], *, enqueue_origin: str) -> dict[str, Any]:
        for request in requests:
            meta = request.source_metadata
            native = str(meta.provider_native_session_id) if meta is not None else None
            if native in self.fail_for:
                raise self.fail_for[native]
        self.requests.extend(requests)
        return {"duplicates_by_index": [False] * len(requests), "pending": len(requests)}


class Ledger:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def attempts(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self.rows.items()}

    async def record_attempt(self, session_key: str, source_state: str | None, error: str | None) -> None:
        row = self.rows.get(session_key)
        attempts = 0 if error is None else (row["attempts"] + 1 if row and row["source_revision"] == source_state else 1)
        self.rows[session_key] = {"session_key": session_key, "source_revision": source_state, "attempts": attempts, "last_error": error}

    async def recent(self, limit: int) -> list[dict[str, Any]]:
        return list(self.rows.values())[:limit]

    async def exhausted(self, max_attempts: int) -> list[dict[str, Any]]:
        return [r for r in self.rows.values() if r["attempts"] >= max_attempts and r["last_error"]]


class Logger:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def _log(self, level: str, msg: str, *args: Any, **_kwargs: Any) -> None:
        self.lines.append((level, msg % args if args else msg))

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log("debug", msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log("info", msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log("warning", msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log("error", msg, *args, **kwargs)


class FakeServer:
    """A ``CloudHttp`` answering like aidream's /coding-sessions/sessions and /bridge."""

    def __init__(self, identities: list[dict[str, Any]] | None = None, page_size: int = 2) -> None:
        self.identities = identities or []
        self.page_size = page_size
        self.posts: list[dict[str, Any]] = []
        self.answer: Any = None

    async def get_json(self, path: str) -> Any:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(path).query)
        start = int(query.get("cursor", ["0"])[0])
        page = self.identities[start : start + self.page_size]
        more = start + self.page_size < len(self.identities)
        return {
            "schema_version": 2,
            "provider": "claude_code",
            "sessions": page,
            "total_count": len(self.identities),
            "page_count": len(page),
            "has_more": more,
            "complete": not more,
            "next_cursor": str(start + self.page_size) if more else None,
        }

    async def post_json(self, path: str, body: Any, *, timeout: float) -> Any:
        assert path == "/coding-sessions/bridge"
        self.posts.append(json.loads(json.dumps(body)))
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return self.answer
        n = 1 if body["action"] == "observe_hook" else len(body["entries"])
        return {
            "schema_version": 1,
            "action": body["action"],
            "provider": body["provider"],
            "fidelity": "event_mirror" if body["action"] == "observe_hook" else "native",
            "session_id": "3f1c3a52-9a2e-4d8e-b6f1-2c4d5e6f7a81",
            "conversation_id": "4f1c3a52-9a2e-4d8e-b6f1-2c4d5e6f7a82",
            "accepted": n,
            "duplicates": 0,
            "conflicts": 0,
        }


def organization_required() -> CloudError:
    return CloudError(409, '{"error": "organization_required"}', body={"error": "organization_required"})
