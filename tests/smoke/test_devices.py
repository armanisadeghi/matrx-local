"""
Device/permission endpoint smoke tests.

All /devices/* paths are unconditionally public (AuthMiddleware skips them),
so no token is required.
"""

from __future__ import annotations

import os
import sys

import httpx
import pytest

# These probe real display / audio / network hardware via the OS. On a headless
# CI runner there is no display or audio device, so the endpoints can't return
# meaningful data. CI sets MATRX_CI_HEADLESS=1 to skip them; they still run on
# developer machines and hardware-backed runners.
_headless = pytest.mark.skipif(
    os.environ.get("MATRX_CI_HEADLESS") == "1",
    reason="requires real display/audio/network hardware (headless CI)",
)


def test_devices_permissions(http_public: httpx.Client) -> None:
    """GET /devices/permissions returns a list of permission objects."""
    r = http_public.get("/devices/permissions")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, (list, dict)), (
        f"Expected list or dict from /devices/permissions, got {type(data).__name__}"
    )


def test_devices_system(http_public: httpx.Client) -> None:
    """GET /devices/system returns 200 with a structured response."""
    r = http_public.get("/devices/system")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict)


@_headless
def test_devices_screens(http_public: httpx.Client) -> None:
    """GET /devices/screens returns 200 (may be empty list on headless)."""
    r = http_public.get("/devices/screens")
    assert r.status_code in (200, 503), (
        f"Unexpected status from /devices/screens: {r.status_code} {r.text}"
    )


@_headless
def test_devices_audio(http_public: httpx.Client) -> None:
    """GET /devices/audio returns 200 with list of audio devices."""
    r = http_public.get("/devices/audio")
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, (list, dict)), (
        f"Expected list or dict from /devices/audio, got {type(data).__name__}"
    )


@_headless
def test_devices_network(http_public: httpx.Client) -> None:
    """GET /devices/network returns 200."""
    r = http_public.get("/devices/network")
    assert r.status_code == 200, r.text


@pytest.mark.macos_only
def test_devices_wifi(http_public: httpx.Client) -> None:
    """GET /devices/wifi returns 200 on macOS."""
    r = http_public.get("/devices/wifi")
    assert r.status_code in (200, 503), r.text


@pytest.mark.macos_only
def test_devices_bluetooth(http_public: httpx.Client) -> None:
    """GET /devices/bluetooth returns 200 on macOS."""
    r = http_public.get("/devices/bluetooth")
    assert r.status_code in (200, 503), r.text


@pytest.mark.macos_only
def test_devices_location(http_public: httpx.Client) -> None:
    """GET /devices/location returns 200 on macOS."""
    r = http_public.get("/devices/location")
    assert r.status_code in (200, 503), r.text
