"""Private, grant-authenticated lifecycle bridge to an admitted extension socket.

This module intentionally supports only lifecycle transport.  It never
materializes credentials, forwards a caller JWT, or dispatches generic tools.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.api.extension_ws_manager import (
    current_local_browser_registration, create_local_browser_future,
    drop_local_browser_future, get_registry, invalidate_local_browser_registrations,
    send_local_browser_execute, send_to_extension_session,
)
from app.services.app_config import get_aidream_server_url
from app.services.cloud_sync.instance_manager import get_instance_manager
from app.services.local_browser_context import BrowserContext, FreshContext, LocalBrowserContext, get_local_browser_context

_MAX_BODY = 32 * 1024
_MAX_GRANT = 8 * 1024
_MAX_REPLY = 4 * 1024
_OPERATIONS = frozenset({"discover", "admit", "renew", "cleanup"})
_REASONS = frozenset({"context_unavailable", "registration_unavailable", "authority_refused", "rate_limited", "transport_unavailable", "binding_changed", "invalid_request"})


class TransportRefusal(Exception):
    def __init__(self, reason: str, status_code: int = 403) -> None:
        self.reason, self.status_code = reason, status_code


def private_path(path: str) -> bool:
    return path == "/local-browser/execute"


def _bounded_json(raw: bytes) -> dict[str, str]:
    if len(raw) > _MAX_BODY:
        raise TransportRefusal("invalid_request", 413)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=lambda pairs: _no_duplicates(pairs))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise TransportRefusal("invalid_request", 400) from exc
    if not isinstance(value, dict) or set(value) != {"grant", "operation"}:
        raise TransportRefusal("invalid_request", 400)
    grant, operation = value.get("grant"), value.get("operation")
    if not isinstance(grant, str) or not isinstance(operation, str) or operation not in _OPERATIONS:
        raise TransportRefusal("invalid_request", 400)
    if not grant or len(grant.encode("utf-8")) > _MAX_GRANT:
        raise TransportRefusal("invalid_request", 400)
    # JSON parser has already rejected invalid Unicode. Keep the structural
    # depth closed even if a future schema grows nested fields.
    return {"grant": grant, "operation": operation}


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate key")
        output[key] = value
    return output


@dataclass
class _Bucket:
    capacity: float
    refill_per_second: float
    tokens: float | None = None
    seen: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.tokens is None:
            self.tokens = self.capacity

    def take(self, now: float) -> bool:
        assert self.tokens is not None
        self.tokens = min(self.capacity, self.tokens + (now - self.seen) * self.refill_per_second)
        self.seen = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class CallbackLimits:
    """Bounded callback admission; queues are never accumulated."""
    def __init__(self) -> None:
        self.global_gate = asyncio.Semaphore(4)
        self._per_address: dict[str, asyncio.Semaphore] = {}
        self._global_bucket = _Bucket(8.0, 1.0)
        self._buckets: dict[str, _Bucket] = {"unknown": _Bucket(4.0, 0.5)}

    def _address_gate(self, address: str) -> asyncio.Semaphore | None:
        if address not in self._per_address:
            if len(self._per_address) >= 64:
                return None
            self._per_address[address] = asyncio.Semaphore(2)
        return self._per_address[address]

    async def __aenter__(self) -> "CallbackLimits":
        raise RuntimeError("use acquire")

    async def acquire(self, address: str) -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
        key = address if address and len(address) <= 128 else "unknown"
        bucket = self._buckets.setdefault(key, _Bucket(4.0, 0.5)) if len(self._buckets) < 65 or key == "unknown" else self._buckets["unknown"]
        now = time.monotonic()
        if not self._global_bucket.take(now) or not bucket.take(now):
            raise TransportRefusal("rate_limited", 429)
        address_gate = self._address_gate(key)
        if address_gate is None or self.global_gate.locked() or address_gate.locked():
            raise TransportRefusal("rate_limited", 429)
        await self.global_gate.acquire()
        try:
            await address_gate.acquire()
        except BaseException:
            self.global_gate.release()
            raise
        return self.global_gate, address_gate


_LIMITS = CallbackLimits()
_subscription_context: LocalBrowserContext | None = None
_unsubscribe: Any = None


def _on_context_change(context: BrowserContext) -> None:
    """Synchronous listener called inside LocalBrowserContext's lock."""
    registrations = invalidate_local_browser_registrations("binding_changed")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for registration in registrations:
        loop.create_task(send_to_extension_session(registration.session_id, {"type": "local_browser.invalidate", "version": 1, "reason": "binding_changed"}))
    if context.organization_id is not None:
        for session_id in get_registry().session_ids:
            loop.create_task(send_to_extension_session(session_id, {"type": "local_browser.register_required", "version": 1, "engine_boot_id": context.engine_boot_id, "revision": context.revision}))


def ensure_context_subscription(context: LocalBrowserContext | None = None) -> None:
    global _subscription_context, _unsubscribe
    current = context or get_local_browser_context()
    if current is _subscription_context:
        return
    if _unsubscribe is not None:
        _unsubscribe()
    _subscription_context = current
    _unsubscribe = current.subscribe(_on_context_change)


async def read_execute_body(request: Any) -> dict[str, str]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY:
            raise TransportRefusal("invalid_request", 413)
        chunks.append(chunk)
    return _bounded_json(b"".join(chunks))


def _base_url() -> str:
    url = get_aidream_server_url().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TransportRefusal("transport_unavailable", 503)
    return url


async def _verify(fresh: FreshContext, device_id: str, registration: Any, request: dict[str, str]) -> dict[str, Any]:
    payload = {
        "grant": request["grant"], "operation": request["operation"], "device_id": device_id,
        "engine_boot_id": fresh.context.engine_boot_id, "revision": fresh.context.revision,
        "extension_generation": registration.extension_generation, "connection_id": registration.connection_id,
    }
    headers = {"Authorization": f"Bearer {fresh.daemon_jwt}", "X-Organization-Id": fresh.context.organization_id or "", "Cache-Control": "no-store"}
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(5.0), headers=headers) as client:
            response = await client.post(f"{_base_url()}/browser-manager/local/transport/verify", json=payload)
            raw = await response.aread()
    except Exception as exc:
        raise TransportRefusal("transport_unavailable", 503) from exc
    if len(raw) > _MAX_REPLY or response.headers.get("cache-control", "").lower().find("no-store") < 0:
        raise TransportRefusal("transport_unavailable", 503)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise TransportRefusal("transport_unavailable", 503)
    if response.status_code != 200 or not isinstance(value, dict) or value.get("status") != "acknowledged":
        raise TransportRefusal("authority_refused", 403)
    return value


def _same(fresh: FreshContext, later: FreshContext | None, device: Any, device_id: str) -> bool:
    return later is not None and later.owner == fresh.owner and later.daemon_jwt == fresh.daemon_jwt and later.context == fresh.context and later.context.organization_id is not None and device is not None and device.app_instance_id == device_id


async def execute_lifecycle(request: dict[str, str], *, address: str = "unknown") -> dict[str, str]:
    ensure_context_subscription()
    global_gate, address_gate = await _LIMITS.acquire(address)
    try:
        context = get_local_browser_context()
        fresh = await context.refresh()
        device = await get_instance_manager().registered_device_identity()
        if fresh is None or fresh.context.organization_id is None or device is None or device.user_id != fresh.owner[0]:
            raise TransportRefusal("context_unavailable", 401)
        registration = current_local_browser_registration(engine_boot_id=fresh.context.engine_boot_id, revision=fresh.context.revision, owner=fresh.owner, organization_id=fresh.context.organization_id, device_id=device.app_instance_id)
        if registration is None:
            raise TransportRefusal("registration_unavailable", 409)
        await _verify(fresh, device.app_instance_id, registration, request)
        later = await context.refresh()
        latest_device = await get_instance_manager().registered_device_identity()
        if not _same(fresh, later, latest_device, device.app_instance_id):
            raise TransportRefusal("binding_changed", 409)
        # Select afresh after every await; no old tuple can dispatch.
        registration = current_local_browser_registration(engine_boot_id=fresh.context.engine_boot_id, revision=fresh.context.revision, owner=fresh.owner, organization_id=fresh.context.organization_id, device_id=device.app_instance_id)
        if registration is None:
            raise TransportRefusal("registration_unavailable", 409)
        call_id = str(uuid.uuid4())
        future = create_local_browser_future(registration, call_id)
        if future is None:
            raise TransportRefusal("binding_changed", 409)
        sent = await send_local_browser_execute(registration, {"type": "local_browser.execute", "version": 1, "call_id": call_id, "operation": request["operation"], "grant": request["grant"]})
        if not sent:
            drop_local_browser_future(call_id)
            raise TransportRefusal("binding_changed", 409)
        try:
            result = await asyncio.wait_for(future, timeout=5.0)
        except (asyncio.TimeoutError, ConnectionError) as exc:
            raise TransportRefusal("transport_unavailable", 503) from exc
        finally:
            drop_local_browser_future(call_id)
        if not isinstance(result, dict) or result.get("operation") != request["operation"]:
            raise TransportRefusal("transport_unavailable", 503)
        if result.get("status") == "acknowledged":
            return {"status": "acknowledged", "operation": request["operation"], "receipt": _receipt(request["operation"])}
        return {"status": "refused", "operation": request["operation"], "reason": "authority_refused"}
    finally:
        address_gate.release()
        global_gate.release()


def _receipt(operation: str) -> str:
    return {"discover": "accepted", "admit": "created", "renew": "accepted", "cleanup": "closed"}[operation]
