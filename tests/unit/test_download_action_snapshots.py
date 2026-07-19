from __future__ import annotations

import asyncio
import json

import pytest

from app.services.downloads.manager import DownloadEntry, DownloadManager


def _payload(sse: str) -> dict:
    assert sse.startswith("data: ")
    return json.loads(sse.removeprefix("data: ").strip())


@pytest.mark.parametrize(
    "resolution",
    [
        None,
        {
            "code": "hf_token_missing",
            "title": "Token needed",
            "message": "Add a token.",
            "action_kind": "settings_api_keys",
            "action_label": "Add token",
            "provider": "huggingface",
            "action_url": None,
        },
    ],
)
def test_sse_reconnect_snapshot_includes_resolution_and_explicit_null(resolution):
    async def run() -> dict:
        manager = DownloadManager()
        entry = DownloadEntry(
            id="download-1",
            category="image_gen",
            filename="model",
            display_name="Model",
            urls=["https://example.test/model"],
            status="failed",
            error_msg="blocked",
        )
        entry.set_resolution(resolution)
        manager._entries[entry.id] = entry

        stream = manager.sse_stream()
        try:
            return _payload(await stream.__anext__())
        finally:
            await stream.aclose()

    payload = asyncio.run(run())
    assert "resolution" in payload
    assert payload["resolution"] == resolution
    assert payload["snapshot"] is True
