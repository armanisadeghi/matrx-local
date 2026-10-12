"""Provider contract guards; real customer media proof is a separate gate."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from app.services.personal_messaging import beeper, media
from test_personal_messaging_beeper import grant, native_client


def message(**updates):
    return beeper.NativeMessage.model_validate(
        {
            "id": "sandbox-message",
            "accountID": "whatsapp",
            "chatID": "sandbox",
            "timestamp": datetime.now(UTC).isoformat(),
            "senderID": "sender",
            "attachments": [
                {
                    "id": "mxc://sandbox.test/media",
                    "fileName": "test.png",
                    "mimeType": "image/png",
                    "srcURL": "file:///private/never-expose",
                }
            ],
            **updates,
        }
    )


def test_projection_keeps_opaque_revision_but_no_private_media_addresses():
    value = message()
    projection = media.project_message(value, grant(), "sandbox")
    assert projection["attachments"][0]["available"]
    assert len(projection["attachments"][0]["revision"]) == 64
    assert "mxc://" not in str(projection) and "file:///" not in str(projection)
    altered = value.model_copy(
        update={"attachments": [{"id": "mxc://sandbox.test/replacement"}]}
    )
    assert media.attachment_revision(value, 0) != media.attachment_revision(altered, 0)
    assert media.project_message(message(isHidden=True), grant(), "sandbox") is None
    assert (
        media.project_message(message(isDeleted=True), grant(), "sandbox")[
            "attachments"
        ]
        == []
    )


@pytest.mark.parametrize(
    "source",
    [
        "file:///etc/passwd",
        "https://private.test/media",
        "http://127.0.0.1:8000",
        "mxc://sandbox.test/asset?token=private",
        "mxc://sandbox.test/asset#secret",
        None,
    ],
)
def test_unavailable_source_never_opens_provider_http(monkeypatch, source):
    calls = []
    native_client(monkeypatch, lambda r: calls.append(r))
    with pytest.raises(beeper.BeeperFailure):
        asyncio.run(
            media.read_attachment(
                beeper.DesktopApi(), grant(), message(attachments=[{"id": source}]), 0
            )
        )
    assert calls == []


def test_asset_uses_exact_fixed_origin_and_authenticated_bytes(monkeypatch):
    def handler(request):
        assert (
            request.url.path == "/v1/assets/serve"
            and request.url.host == "127.0.0.1"
            and request.url.port == 23373
        )
        assert request.url.params["url"] == "mxc://sandbox.test/media"
        assert request.headers["authorization"] == "Bearer fixture-token"
        return httpx.Response(
            200, content=b"sandbox-bytes", headers={"content-type": "image/png"}
        )

    native_client(monkeypatch, handler)
    asset = asyncio.run(
        media.read_attachment(beeper.DesktopApi(), grant(), message(), 0)
    )
    assert (
        asset.data == b"sandbox-bytes"
        and asset.mime_type == "image/png"
        and asset.name == "test.png"
    )
    assert "sandbox-bytes" not in repr(asset)


@pytest.mark.parametrize("advertised", [False, True])
def test_oversized_asset_refuses_before_response(monkeypatch, advertised):
    native_client(
        monkeypatch,
        lambda r: httpx.Response(
            200,
            content=b"123456789",
            headers={"content-length": "999"} if advertised else {},
        ),
    )
    with pytest.raises(beeper.BeeperFailure) as error:
        asyncio.run(
            media.read_attachment(
                beeper.DesktopApi(), grant(), message(), 0, max_bytes=4
            )
        )
    assert error.value.code == "attachment_too_large"


@pytest.mark.parametrize(
    "condition", ["404", "hidden", "deleted", "wrong-chat", "wrong-account", "wrong-id"]
)
def test_lookup_null_is_exact_and_cross_binding_refuses(monkeypatch, condition):
    def handler(request):
        if request.url.path == "/v1/accounts":
            return httpx.Response(
                200,
                json=[
                    {
                        "accountID": "whatsapp",
                        "status": "connected",
                        "user": {"id": "native-owner"},
                        "bridge": {"id": "wa", "type": "whatsapp", "provider": "cloud"},
                    }
                ],
            )
        if request.url.path == "/v1/chats/sandbox":
            return httpx.Response(
                200, json={"id": "sandbox", "accountID": "whatsapp", "title": "Sandbox"}
            )
        assert request.url.path == "/v1/chats/sandbox/messages/sandbox-message"
        if condition == "404":
            return httpx.Response(404)
        updates = {
            "hidden": {"isHidden": True},
            "deleted": {"isDeleted": True},
            "wrong-chat": {"chatID": "unselected"},
            "wrong-account": {"accountID": "different"},
            "wrong-id": {"id": "different"},
        }
        return httpx.Response(
            200, json=message(**updates[condition]).model_dump(mode="json")
        )

    native_client(monkeypatch, handler)
    operation = media.retrieve_message(
        beeper.DesktopApi(), grant(), "sandbox", "sandbox-message"
    )
    if condition.startswith("wrong-"):
        with pytest.raises(beeper.BeeperFailure):
            asyncio.run(operation)
    else:
        assert asyncio.run(operation) is None


@pytest.mark.parametrize("change", ["disconnect", "edit"])
def test_download_never_blocks_retirement_and_never_returns_stale_bytes(
    tmp_path, monkeypatch, change
):
    from app.services.personal_messaging import coordinator
    from app.services.personal_messaging.models import GrantConflict, GrantUnavailable
    from test_personal_messaging_coordinator import scenario, connect_selected

    async def run(service, repo, remote):
        await connect_selected(service)
        started, release = asyncio.Event(), asyncio.Event()
        edited = False

        async def lookup(api, grant, chat_id, message_id):
            assert chat_id == "sandbox" and message_id == "sandbox-message"
            return message(
                attachments=[
                    {
                        "id": "mxc://sandbox.test/new"
                        if edited
                        else "mxc://sandbox.test/media"
                    }
                ]
            )

        async def download(api, grant, value, index):
            started.set()
            await release.wait()
            return media.NativeAttachment(b"never-stale", "image/png", "sandbox.png")

        monkeypatch.setattr(coordinator, "retrieve_message", lookup)
        monkeypatch.setattr(coordinator, "read_attachment", download)
        value = await lookup(None, None, "sandbox", "sandbox-message")
        read = asyncio.create_task(
            service.attachment(
                "sandbox", "sandbox-message", 0, media.attachment_revision(value, 0)
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        if change == "disconnect":
            result = await asyncio.wait_for(service.disconnect(), 2)
            assert result["status"] == "retired"
        else:
            edited = True
        release.set()
        with pytest.raises((GrantConflict, GrantUnavailable, beeper.BeeperFailure)):
            await read
        assert (await service.status())["status"] == (
            "retired" if change == "disconnect" else "active"
        )

    asyncio.run(scenario(tmp_path, monkeypatch, run))


def test_client_disconnect_cancels_download_task_before_bytes_escape():
    from app.services.personal_messaging import routes
    from fastapi import HTTPException

    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()

        class Request:
            async def is_disconnected(self):
                await started.wait()
                return True

        class Service:
            async def attachment(self, *args):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(
                routes.attachment(
                    Request(), "sandbox", "sandbox-message", 0, "a" * 64, Service()
                ),
                2,
            )
        assert error.value.status_code == 499 and cancelled.is_set()

    asyncio.run(run())

@pytest.mark.parametrize("declared,expected", [("image/png", "image/png"), ("text/html", "application/octet-stream"), ({"invalid": "shape"}, "application/octet-stream")])
def test_generic_asset_mime_uses_only_selected_passive_attachment_type(monkeypatch, declared, expected):
    native_client(monkeypatch, lambda r: httpx.Response(200, content=b"sandbox-asset", headers={"content-type": "application/octet-stream"}))
    asset = asyncio.run(media.read_attachment(beeper.DesktopApi(), grant(), message(attachments=[{"id": "mxc://sandbox.test/media", "mimeType": declared}]), 0))
    assert asset.mime_type == expected
