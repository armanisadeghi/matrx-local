"""Regression coverage for authoritative non-tool remediation routes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import image_gen_routes, permissions_routes, settings_routes, setup_routes
from app.services.action_needed import (
    ActionNeeded,
    ActionNeededAction,
    ActionNeededKind,
)
from app.services.action_needed.registry import get_action_needed_registry
from app.services.permissions.checker import PermissionResult, PermissionStatus
from app.tools.types import ToolResult, ToolResultType


def _provider_requirement(provider: str = "huggingface") -> ActionNeeded:
    return ActionNeeded(
        fingerprint=f"api-key:{provider}",
        code="api_key_missing",
        kind=ActionNeededKind.API_KEY,
        feature="test",
        title="Add an API key",
        message="A key is required.",
        action=ActionNeededAction(
            kind="settings_api_keys",
            label="Add key",
            provider=provider,
        ),
        source="test.route",
    )


async def _active_fingerprints() -> set[str]:
    snapshots = await get_action_needed_registry().snapshots()
    return {
        str(item["fingerprint"])
        for snapshot in snapshots
        for item in snapshot.get("items", [])
    }


def test_device_tool_adapter_preserves_and_reconciles_action() -> None:
    async def exercise() -> None:
        registry = get_action_needed_registry()
        await registry.reset()
        requirement = _provider_requirement()

        blocked = await permissions_routes._tool_result_payload(
            ToolResult(
                type=ToolResultType.ERROR,
                output="blocked",
                action_needed=requirement,
            ),
            operation_key="devices.test",
        )
        assert blocked["action_needed"]["fingerprint"] == requirement.fingerprint
        assert requirement.fingerprint in await _active_fingerprints()

        recovered = await permissions_routes._tool_result_payload(
            ToolResult(output="ready"), operation_key="devices.test"
        )
        assert recovered["action_needed"] is None
        assert requirement.fingerprint not in await _active_fingerprints()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("route", "route_request"),
    [
        (permissions_routes.capture_photo, permissions_routes.CapturePhotoRequest()),
        (permissions_routes.record_video, permissions_routes.RecordVideoRequest()),
    ],
)
def test_camera_routes_preflight_without_touching_capture(
    monkeypatch: pytest.MonkeyPatch,
    route: object,
    route_request: object,
) -> None:
    async def denied() -> PermissionResult:
        return PermissionResult(
            permission="camera",
            status=PermissionStatus.NOT_DETERMINED,
            user_instructions="Allow camera access.",
        )

    async def must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("capture was touched before permission was granted")

    monkeypatch.setitem(permissions_routes.PERMISSION_CHECKERS, "camera", denied)
    monkeypatch.setattr(permissions_routes, "_run", must_not_run)

    async def exercise() -> None:
        await get_action_needed_registry().reset()
        response = await route(route_request)  # type: ignore[operator]
        assert response["type"] == "error"
        assert response["action_needed"]["code"] == "camera_required"
        assert "os-permission:camera" in next(iter(await _active_fingerprints()))

    asyncio.run(exercise())


def test_screen_recording_preflight_does_not_enumerate_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def denied() -> PermissionResult:
        return PermissionResult(
            permission="screen_recording",
            status=PermissionStatus.DENIED,
            user_instructions="Allow screen recording.",
        )

    async def must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ffmpeg was invoked during permission preflight")

    monkeypatch.setitem(permissions_routes.PLATFORM, "is_mac", True)
    monkeypatch.setitem(
        permissions_routes.PERMISSION_CHECKERS, "screen_recording", denied
    )
    monkeypatch.setattr(permissions_routes, "_run", must_not_run)

    async def exercise() -> None:
        await get_action_needed_registry().reset()
        response = await permissions_routes.record_screen(
            permissions_routes.RecordScreenRequest(duration_seconds=1)
        )
        assert response["action_needed"]["code"] == "screen_recording_required"

    asyncio.run(exercise())


def test_setup_permissions_use_canonical_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def canonical_results() -> list[dict[str, object]]:
        return [
            {"permission": "microphone", "status": "granted", "deep_link": ""},
            {
                "permission": "camera",
                "status": "not_determined",
                "deep_link": "settings://camera",
            },
            {"permission": "bluetooth", "status": "unavailable", "deep_link": ""},
        ]

    monkeypatch.setattr(setup_routes, "check_all_permissions", canonical_results)
    result = asyncio.run(setup_routes._check_permissions())
    assert result.status == "warning"
    assert "camera" in (result.detail or "")
    assert "not_determined" in (result.detail or "")
    assert result.deep_link == "settings://camera"


def test_successful_permission_request_resolves_every_matching_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def granted(name: str) -> PermissionResult:
        return PermissionResult(permission=name, status=PermissionStatus.GRANTED)

    monkeypatch.setattr(permissions_routes, "request_engine_permission", granted)

    async def exercise() -> None:
        registry = get_action_needed_registry()
        await registry.reset()
        first = permissions_routes.os_permission_needed(
            feature="contacts-search",
            permission_key="contacts",
            source="devices.contacts",
        )
        second = permissions_routes.os_permission_needed(
            feature="contacts-create",
            permission_key="contacts",
            source="devices.contacts",
        )
        unrelated = permissions_routes.os_permission_needed(
            feature="calendar-list",
            permission_key="calendar",
            source="devices.calendar",
        )
        await registry.reconcile_operation("contacts.search", first)
        await registry.reconcile_operation("contacts.create", second)
        await registry.reconcile_operation("calendar.list", unrelated)

        response = await permissions_routes.request_permission("contacts")

        assert response["status"] == "granted"
        active = await _active_fingerprints()
        assert first.fingerprint not in active
        assert second.fingerprint not in active
        assert unrelated.fingerprint in active

    asyncio.run(exercise())


@pytest.mark.parametrize("bulk", [False, True])
def test_permission_recheck_resolves_backend_requirement(
    monkeypatch: pytest.MonkeyPatch,
    bulk: bool,
) -> None:
    async def granted() -> PermissionResult:
        return PermissionResult(
            permission="contacts", status=PermissionStatus.GRANTED
        )

    async def all_granted() -> list[dict[str, object]]:
        return [
            {"permission": "contacts", "status": "granted"},
            {"permission": "camera", "status": "denied"},
        ]

    monkeypatch.setitem(permissions_routes.PERMISSION_CHECKERS, "contacts", granted)
    monkeypatch.setattr(permissions_routes, "check_all_permissions", all_granted)

    async def exercise() -> None:
        registry = get_action_needed_registry()
        await registry.reset()
        requirement = permissions_routes.os_permission_needed(
            feature="contacts-search",
            permission_key="contacts",
            source="devices.contacts",
        )
        await registry.reconcile_operation("contacts.search", requirement)

        if bulk:
            permissions_routes._invalidate_permissions_cache()
            response = await permissions_routes.get_permissions(force_refresh=True)
            assert response["permissions"][0]["status"] == "granted"
        else:
            response = await permissions_routes.get_permission("contacts")
            assert response["status"] == "granted"

        assert requirement.fingerprint not in await _active_fingerprints()

    asyncio.run(exercise())


def test_cached_permission_grant_cannot_erase_a_newer_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def all_granted() -> list[dict[str, object]]:
        return [{"permission": "contacts", "status": "granted"}]

    monkeypatch.setattr(permissions_routes, "check_all_permissions", all_granted)

    async def exercise() -> None:
        registry = get_action_needed_registry()
        await registry.reset()
        permissions_routes._invalidate_permissions_cache()
        fresh = await permissions_routes.get_permissions(force_refresh=True)
        assert fresh["cached"] is False

        newer_denial = permissions_routes.os_permission_needed(
            feature="contacts-search",
            permission_key="contacts",
            source="devices.contacts",
        )
        await registry.reconcile_operation("contacts.search", newer_denial)

        cached = await permissions_routes.get_permissions(force_refresh=False)
        assert cached["cached"] is True
        assert newer_denial.fingerprint in await _active_fingerprints()

    asyncio.run(exercise())


def test_custom_registration_missing_token_is_structured_and_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.media_gen.paths.read_hf_token", lambda: None)
    entry = image_gen_routes.CustomModelEntry(
        model_id="custom/acme-model",
        name="Acme Model",
        source="hf",
        source_ref="acme/model",
        family="sdxl",
        format="diffusers",
        requires_hf_token=True,
    )

    async def exercise() -> None:
        await get_action_needed_registry().reset()
        with pytest.raises(HTTPException) as raised:
            await image_gen_routes.register_custom_model_route(entry)
        assert raised.value.status_code == 409
        detail = raised.value.detail
        assert detail["action_needed"]["action"]["provider"] == "huggingface"
        assert detail["action_needed"]["fingerprint"] in await _active_fingerprints()

    asyncio.run(exercise())


@pytest.mark.parametrize("verdict", ["valid", "unsupported"])
def test_stored_key_validation_resolves_provider_requirements(
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
) -> None:
    async def validate_stored(_provider: str) -> SimpleNamespace:
        return SimpleNamespace(
            provider="huggingface",
            verdict=verdict,
            message="ok",
            account=None,
            status_code=None,
        )

    class FakeRepo:
        async def record_validation(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        "app.services.ai.key_validation.validate_stored_key", validate_stored
    )
    monkeypatch.setattr(settings_routes, "ApiKeysRepo", FakeRepo)

    async def exercise() -> None:
        registry = get_action_needed_registry()
        await registry.reset()
        requirement = _provider_requirement()
        await registry.reconcile_operation("test.provider", requirement)
        result = await settings_routes.validate_api_key("huggingface")
        assert result.verdict == verdict
        assert requirement.fingerprint not in await _active_fingerprints()

    asyncio.run(exercise())
