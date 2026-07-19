"""Truth-table tests for the canonical image/video runtime API."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


def _status(state: str = "ready", **patch):
    value = {
        "state": state,
        "operation": None,
        "attempt_id": None,
        "runtime_revision": "runtime-1" if state == "ready" else None,
        "required_revision": "runtime-1",
        "stage": state,
        "percent": 100.0 if state == "ready" else 0.0,
        "message": "Runtime ready" if state == "ready" else "Runtime unavailable",
        "failure_code": None,
        "failure_detail": None,
        "repairable": state in {"failed", "rolled_back"},
        "image_available": state == "ready",
        "video_packages_available": state == "ready",
        "active_slot": "runtime-1" if state == "ready" else None,
        "last_known_good_slot": None,
        "candidate_slot": None,
        "package_checks": [],
        "log_lines": [],
    }
    value.update(patch)
    return value


def test_runtime_status_exposes_authoritative_snapshot(monkeypatch) -> None:
    from app.api import image_gen_routes

    monkeypatch.setattr(image_gen_routes, "get_runtime_status", lambda: _status())
    response = asyncio.run(image_gen_routes.media_runtime_status())

    assert response.state == "ready"
    assert response.runtime_revision == response.required_revision
    assert response.image_available is True
    assert response.video_packages_available is True


def test_legacy_install_status_never_calls_failed_runtime_complete(monkeypatch) -> None:
    from app.api import image_gen_routes

    monkeypatch.setattr(
        image_gen_routes,
        "get_runtime_status",
        lambda: _status(
            "failed",
            failure_code="critical_import_failed",
            failure_detail="No module named 'tqdm.contrib.logging'",
        ),
    )
    response = asyncio.run(image_gen_routes.get_install_status())

    assert response.status == "error"
    assert response.already_installed is False
    assert "tqdm.contrib.logging" in (response.error or "")


def test_runtime_ensure_and_repair_return_the_new_attempt(monkeypatch) -> None:
    from app.api import image_gen_routes

    calls: list[str] = []
    current = _status("absent")

    async def ensure():
        calls.append("ensure")
        current.update(
            _status(
                "installing",
                operation="install",
                attempt_id="attempt-install",
                stage="downloading",
                percent=2.0,
            )
        )
        return SimpleNamespace(status="running")

    async def repair():
        calls.append("repair")
        current.update(
            _status(
                "repairing",
                operation="repair",
                attempt_id="attempt-repair",
                stage="preparing",
                percent=1.0,
            )
        )
        return SimpleNamespace(status="running")

    monkeypatch.setattr(image_gen_routes, "ensure_runtime", ensure)
    monkeypatch.setattr(image_gen_routes, "repair_runtime", repair)
    monkeypatch.setattr(image_gen_routes, "get_runtime_status", lambda: current)

    ensured = asyncio.run(image_gen_routes.ensure_media_runtime())
    repaired = asyncio.run(image_gen_routes.repair_media_runtime())

    assert calls == ["ensure", "repair"]
    assert ensured.attempt_id == "attempt-install"
    assert repaired.attempt_id == "attempt-repair"


def test_runtime_stream_emits_same_snapshot_schema(monkeypatch) -> None:
    from app.api import image_gen_routes

    monkeypatch.setattr(image_gen_routes, "get_runtime_status", lambda: _status())

    async def consume() -> dict:
        response = await image_gen_routes.stream_media_runtime()
        chunk = await response.body_iterator.__anext__()
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        assert text.startswith("data: ")
        return json.loads(text.removeprefix("data: ").strip())

    payload = asyncio.run(consume())
    assert payload["state"] == "ready"
    assert payload["required_revision"] == "runtime-1"
