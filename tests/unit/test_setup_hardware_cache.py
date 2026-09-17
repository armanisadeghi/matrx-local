from __future__ import annotations

import builtins
import asyncio
import threading

import pytest

from app.api import hardware_routes, setup_routes
from app.services.hardware import detector


@pytest.mark.anyio
async def test_setup_status_uses_cached_gpu_without_reprobing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def permissions() -> setup_routes.ComponentStatus:
        return setup_routes.ComponentStatus(
            id="permissions",
            label="Permissions",
            description="test",
            status="ready",
        )

    monkeypatch.setattr(setup_routes, "_check_permissions", permissions)
    monkeypatch.setattr(
        hardware_routes,
        "_cached_profile",
        {"gpus": [{"name": "Test GPU", "backend": "metal"}]},
    )
    monkeypatch.setattr(
        detector,
        "_detect_gpus",
        lambda: pytest.fail("setup status must not launch a fresh GPU probe"),
    )

    result = await setup_routes.get_setup_status()

    assert result.gpu_name == "Test GPU"


def test_core_package_probe_reports_native_runtime_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def import_with_missing_portaudio(name: str, *args, **kwargs):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_missing_portaudio)

    result = setup_routes._check_core_packages()

    assert result.status == "error"
    assert "Audio I/O" in (result.detail or "")


@pytest.mark.anyio
async def test_setup_status_offloads_package_checks_but_keeps_permissions_on_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first native package import must not starve other setup requests."""
    loop_thread = threading.get_ident()
    package_started = threading.Event()
    release_package_check = threading.Event()
    package_threads: list[int] = []
    permission_threads: list[int] = []

    def ready(component_id: str) -> setup_routes.ComponentStatus:
        return setup_routes.ComponentStatus(
            id=component_id,
            label=component_id,
            description="test",
            status="ready",
        )

    def blocked_core_packages() -> setup_routes.ComponentStatus:
        package_threads.append(threading.get_ident())
        package_started.set()
        assert release_package_check.wait(1)
        return ready("core_packages")

    async def permissions() -> setup_routes.ComponentStatus:
        permission_threads.append(threading.get_ident())
        return ready("permissions")

    monkeypatch.setattr(setup_routes, "_check_gpu", lambda: (False, None))
    monkeypatch.setattr(setup_routes, "_check_core_packages", blocked_core_packages)
    monkeypatch.setattr(
        setup_routes,
        "_check_playwright_browsers",
        lambda: ready("browser_engine"),
    )
    monkeypatch.setattr(
        setup_routes,
        "_check_storage_directories",
        lambda: ready("storage_dirs"),
    )
    monkeypatch.setattr(setup_routes, "_check_tts", lambda: ready("tts_model"))
    monkeypatch.setattr(setup_routes, "_check_cloudflared", lambda: ready("cloudflared"))
    monkeypatch.setattr(
        setup_routes,
        "_check_transcription",
        lambda: ready("transcription"),
    )
    monkeypatch.setattr(setup_routes, "_check_permissions", permissions)

    status_task = asyncio.create_task(setup_routes.get_setup_status())
    assert await asyncio.to_thread(package_started.wait, 1)

    heartbeat_ran = False

    async def heartbeat() -> None:
        nonlocal heartbeat_ran
        heartbeat_ran = True

    await heartbeat()
    assert heartbeat_ran
    assert not status_task.done()

    release_package_check.set()
    result = await status_task

    assert result.setup_complete is True
    assert package_threads
    assert package_threads[0] != loop_thread
    assert permission_threads == [loop_thread]
