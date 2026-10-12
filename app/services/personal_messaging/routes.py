"""Typed same-machine personal WhatsApp customer routes; no native token leaves device."""

from __future__ import annotations

import asyncio
from urllib.parse import quote
from collections.abc import Awaitable
from ipaddress import ip_address
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.remote_auth import (
    headers_indicate_tunnel,
    is_instance_owner,
    verify_supabase_token_result,
)
from app.services.cloud_sync.instance_manager import get_instance_manager
from .beeper import BeeperFailure
from .coordinator import PersonalMessagingCoordinator
from .models import GrantConflict, GrantUnavailable
from .live import LiveActorUnverified, live_frames
from .outbox import OutboxEntry
from .public import (
    PublicGrantStatus,
    PersonalConsentStart,
    PersonalConsentRequest,
    PersonalSendRequest,
    PersonalSendBinding,
    PersonalActivation,
    PersonalAccountSummary,
    PersonalChatPage,
    PersonalMessagePage,
    PersonalMessageLookup,
    PersonalLiveFrame,
)
from app.services.sync_client import get_sync_client

router = APIRouter(prefix="/personal-messaging/beeper", tags=["personal-messaging"])
_PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class AccountSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_ref: str = Field(min_length=1, max_length=1024)


class ChatSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: str = Field(min_length=1, max_length=1024)
    selected: bool = True


class NativeCleanupConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    client_label: str = Field(min_length=1, max_length=100)
    approval_removed: bool


def direct_callback_path(path: str, method: str) -> bool:
    # AuthMiddleware dispatches these two exact routes to their own stricter
    # loopback/state checks. Neither is a general unauthenticated surface.
    return method == "GET" and path in {
        "/personal-messaging/beeper/callback",
        "/personal-messaging/beeper/finish",
    }


def _same_machine(request: Request, *, callback: bool = False) -> int:
    address = request.client.host if request.client else ""
    try:
        local = ip_address(address).is_loopback
    except ValueError:
        local = False
    server = request.scope.get("server")
    if (
        not local
        or headers_indicate_tunnel(request.headers)
        or any(
            request.headers.get(k)
            for k in ("forwarded", "x-forwarded-host", "x-forwarded-for")
        )
        or not server
        or not isinstance(server[1], int)
        or not 1 <= server[1] <= 65535
    ):
        raise HTTPException(403, "Use personal WhatsApp on this computer.")
    if callback and request.headers.get("host") != f"127.0.0.1:{server[1]}":
        raise HTTPException(403, "This callback does not belong to the local engine.")
    return server[1]


async def _device_service(
    request: Request, actor_id: UUID
) -> PersonalMessagingCoordinator:
    port = _same_machine(request)
    identity = await get_instance_manager().registered_device_identity()
    if (
        not identity
        or identity.user_id != str(actor_id)
        or not await is_instance_owner(str(actor_id))
    ):
        raise HTTPException(403, "Sign in as this computer's registered owner.")
    device = UUID(identity.app_instance_id)

    async def fence() -> None:
        current = await get_instance_manager().registered_device_identity()
        if (
            not current
            or current.user_id != str(actor_id)
            or current.app_instance_id != str(device)
            or current.instance_id != identity.instance_id
            or not await is_instance_owner(str(actor_id))
        ):
            raise GrantUnavailable(
                "The registered device owner changed. Sign in again."
            )

    return PersonalMessagingCoordinator(
        actor_id=actor_id, app_instance_id=device, engine_port=port, fence=fence
    )


async def owner_service(request: Request) -> PersonalMessagingCoordinator:
    _same_machine(request)
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in to AI Matrx.")
    result = await verify_supabase_token_result(authorization[7:].strip())
    if result.status != "verified" or result.user is None or result.user.is_anon:
        raise HTTPException(
            503 if result.status in ("unavailable", "unconfigured") else 401,
            "The account could not be verified.",
        )
    return await _device_service(request, UUID(result.user.user_id))


async def _response(
    operation: Awaitable, schema: type[BaseModel], *, many: bool = False
) -> JSONResponse:
    try:
        value = await operation
        if many:
            if not isinstance(value, list):
                raise ValueError("Expected a list response")
            body = [schema.model_validate(row).model_dump(mode="json") for row in value]
        else:
            body = schema.model_validate(value).model_dump(mode="json")
        return JSONResponse(body, headers=_PRIVATE)
    except BeeperFailure as failure:
        return JSONResponse(
            {"error": failure.code, "message": str(failure)},
            status_code=409,
            headers=_PRIVATE,
        )
    except GrantConflict:
        return JSONResponse(
            {
                "error": "connection_changed",
                "message": "Connection changed. Reconcile before continuing.",
            },
            status_code=409,
            headers=_PRIVATE,
        )
    except GrantUnavailable as failure:
        return JSONResponse(
            {"error": "connection_unavailable", "message": str(failure)},
            status_code=409,
            headers=_PRIVATE,
        )
    except Exception:
        # No HTTP client exception or credential/body appears in diagnostics.
        return JSONResponse(
            {
                "error": "device_unavailable",
                "message": "Personal WhatsApp is unavailable. Retry.",
            },
            status_code=503,
            headers=_PRIVATE,
        )


@router.get("/status", response_model=PublicGrantStatus)
async def status(service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.status(), PublicGrantStatus)


@router.post("/start", response_model=PersonalConsentStart)
async def start(body: PersonalConsentRequest, service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.start(request_profile=body.request_profile, max_duration_seconds=body.max_duration_seconds), PersonalConsentStart)


@router.get("/callback", include_in_schema=False)
async def callback(request: Request):
    _same_machine(request, callback=True)
    outcome = "failed"
    try:
        access = await get_sync_client().access_grant()
        if not access:
            raise GrantUnavailable("Device owner is signed out.")
        verified = await verify_supabase_token_result(access[0])
        if (
            verified.status != "verified"
            or not verified.user
            or verified.user.is_anon
            or verified.user.user_id != access[1]
        ):
            raise GrantUnavailable("Device owner could not be verified.")
        service = await _device_service(request, UUID(access[1]))
        pairs = list(request.query_params.multi_items())
        if len(pairs) != 2 or {k for k, _ in pairs} != {"state", "code"}:
            raise GrantUnavailable("Native consent was not completed.")
        state, code = request.query_params["state"], request.query_params["code"]
        if not state or len(state) > 256 or not code or len(code) > 8192:
            raise GrantUnavailable("Native callback was invalid.")
        await service.callback(state=SecretStr(state), code=SecretStr(code))
        outcome = "complete"
    except Exception:
        # Never echo the code, state, private account or provider exception.
        pass
    return RedirectResponse(
        "/personal-messaging/beeper/finish?result=" + outcome,
        status_code=303,
        headers=_PRIVATE,
    )


@router.get("/finish", include_in_schema=False)
async def finish(request: Request):
    _same_machine(request, callback=True)
    complete = request.query_params.get("result") == "complete"
    text = (
        "WhatsApp consent saved. Return to AI Matrx to select your account."
        if complete
        else "WhatsApp consent was not completed. Return to AI Matrx to reconcile or reconnect."
    )
    return HTMLResponse(
        "<!doctype html><html><head><title>AI Matrx WhatsApp</title></head><body><p>"
        + text
        + "</p></body></html>",
        headers={
            **_PRIVATE,
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        },
    )


@router.get("/accounts", response_model=list[PersonalAccountSummary])
async def accounts(service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.accounts(), PersonalAccountSummary, many=True)


@router.post("/select-account", response_model=PersonalActivation)
async def select_account(
    selection: AccountSelection,
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    return await _response(
        service.select_account(selection.account_ref), PersonalActivation
    )


@router.get("/chats", response_model=PersonalChatPage)
async def chats(
    cursor: str | None = None,
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    return await _response(service.chats(cursor=cursor), PersonalChatPage)


@router.post("/select-chat", response_model=PublicGrantStatus)
async def select_chat(
    selection: ChatSelection,
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    return await _response(
        service.select_chat(selection.chat_id, selected=selection.selected),
        PublicGrantStatus,
    )


@router.get("/messages", response_model=PersonalMessagePage)
async def messages(
    chat_id: str,
    cursor: str | None = None,
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    return await _response(
        service.messages(chat_id, cursor=cursor), PersonalMessagePage
    )


class PersonalNDJSONResponse(StreamingResponse):
    media_type = "application/x-ndjson"


@router.get(
    "/events",
    response_model=PersonalLiveFrame,
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "application/x-ndjson": {
                    "schema": {"$ref": "#/components/schemas/PersonalLiveFrame"}
                }
            }
        }
    },
)
async def events(
    request: Request,
    chat_id: str,
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    original = SecretStr(request.headers.get("authorization", "")[7:].strip())

    async def authorize():
        result = await verify_supabase_token_result(original.get_secret_value())
        if result.status != "verified" or result.user is None:
            raise LiveActorUnverified("The caller token needs renewal.")
        if result.user.is_anon or result.user.user_id != str(service.actor_id):
            raise GrantUnavailable("The signed-in account changed. Reconnect.")
        await service.fence()

    # Admit active selected consent before headers; recheck throughout the stream.
    try:
        async with asyncio.timeout(4):
            await authorize()
            async with service.lock:
                grant = await service._ready()
                if chat_id not in grant.selected_chat_ids:
                    raise GrantUnavailable("Select this WhatsApp conversation first.")
                await service.fence()
    except (BeeperFailure, GrantUnavailable, GrantConflict) as error:
        return await _response(_raise_operation(error), PublicGrantStatus)
    except LiveActorUnverified:
        return JSONResponse(
            {"message": "The signed-in account could not be verified. Retry."},
            status_code=503,
            headers=_PRIVATE,
        )
    except TimeoutError:
        return JSONResponse(
            {"message": "WhatsApp updates are unavailable. Retry."},
            status_code=503,
            headers=_PRIVATE,
        )
    # Retain/reverify the original bearer; the native provider token stays sealed.
    return PersonalNDJSONResponse(
        live_frames(service, chat_id, authorize, initial_snapshot=grant),
        headers=_PRIVATE,
    )


@router.get("/message", response_model=PersonalMessageLookup)
async def message(
    chat_id: str,
    message_id: str,
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    return await _response(service.message(chat_id, message_id), PersonalMessageLookup)


async def _raise_operation(error: Exception):
    raise error


@router.get(
    "/attachment",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def attachment(
    request: Request,
    chat_id: str,
    message_id: str,
    attachment_index: int = Query(ge=0, le=100),
    revision: str = Query(pattern=r"^[0-9a-f]{64}$"),
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    async def wait_disconnected():
        while not await request.is_disconnected():
            await asyncio.sleep(0.25)

    operation = asyncio.create_task(
        service.attachment(chat_id, message_id, attachment_index, revision)
    )
    disconnected = asyncio.create_task(wait_disconnected())
    try:
        done, _ = await asyncio.wait(
            {operation, disconnected}, return_when=asyncio.FIRST_COMPLETED
        )
        if disconnected in done:
            raise HTTPException(499, "Attachment request was cancelled.")
        asset = await operation
        return Response(
            asset.data,
            media_type=asset.mime_type,
            headers={
                **_PRIVATE,
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(asset.name, safe=""),
                "X-Content-Type-Options": "nosniff",
            },
        )
    except (BeeperFailure, GrantUnavailable, GrantConflict) as error:
        return await _response(_raise_operation(error), PublicGrantStatus)
    finally:
        for task in (operation, disconnected):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation, disconnected, return_exceptions=True)


@router.post("/reconcile", response_model=PersonalActivation)
async def reconcile(service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.reconcile(), PersonalActivation)


@router.post("/refresh", response_model=PublicGrantStatus)
async def refresh(service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.refresh(), PublicGrantStatus)


@router.post("/disconnect", response_model=PublicGrantStatus)
async def disconnect(service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.disconnect(), PublicGrantStatus)


@router.post("/confirm-native-cleanup", response_model=PublicGrantStatus)
async def confirm_native_cleanup(
    confirmation: NativeCleanupConfirmation,
    service: PersonalMessagingCoordinator = Depends(owner_service),
):
    if confirmation.approval_removed is not True:
        raise HTTPException(
            409, "Remove the named approval in Beeper before confirming."
        )
    return await _response(
        service.confirm_native_cleanup(
            expected_revision=confirmation.expected_revision,
            client_label=confirmation.client_label,
        ),
        PublicGrantStatus,
    )


@router.post("/send", response_model=OutboxEntry)
async def send(body: PersonalSendRequest, service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.send(body.chat_id, body.client_id, body.text, body.grant_id, body.generation), OutboxEntry)


@router.get("/outbox", response_model=list[OutboxEntry])
async def outbox(chat_id: str, service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.outbox_status(chat_id), OutboxEntry, many=True)


@router.post("/cancel-unsubmitted", response_model=OutboxEntry)
async def cancel_unsubmitted(body: PersonalSendBinding, service: PersonalMessagingCoordinator = Depends(owner_service)):
    return await _response(service.cancel_unsubmitted(body.chat_id, body.client_id, body.grant_id, body.generation), OutboxEntry)
