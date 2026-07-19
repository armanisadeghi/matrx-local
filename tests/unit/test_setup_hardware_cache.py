from __future__ import annotations

import builtins

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
