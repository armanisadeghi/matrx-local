"""Private, grant-authenticated lifecycle bridge to an admitted extension socket.

This module intentionally supports only lifecycle transport.  It never
materializes credentials, forwards a caller JWT, or dispatches generic tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.api.extension_ws_manager import (
    current_local_browser_registration,
    create_local_browser_future,
    drop_local_browser_future,
    get_registry,
    invalidate_local_browser_registrations,
    send_local_browser_execute,
    send_to_extension_session,
)
from app.common.system_logger import get_logger
from app.services.app_config import get_aidream_server_url
from app.services.cloud_sync.instance_manager import get_instance_manager
from app.services.local_browser_context import (
    BrowserContext,
    FreshContext,
    LocalBrowserContext,
    get_local_browser_context,
)

_MAX_BODY = 32 * 1024
_MAX_GRANT = 8 * 1024
_MAX_REPLY = 4 * 1024
_MAX_INSPECT_REPLY = 8 * 1024
_MAX_COMMAND = 16 * 1024
# One complete local credential journey can synchronously consume all of these
# callbacks from the same source: discover, admit, inspect (renew + approve),
# two password pages (renew + approve each), the two custody renewals between
# pages, authenticator (renew + approve), and cleanup.  Keep that finite
# journey admissible before grant verification; sustained abuse remains limited
# by the unchanged refill rates and the concurrency gates below.
_CANONICAL_CREDENTIAL_CALLBACK_BURST = 13
_OPERATIONS = frozenset({"discover", "admit", "approve", "renew", "cleanup"})
_REASONS = frozenset(
    {
        "context_unavailable",
        "registration_unavailable",
        "authority_refused",
        "rate_limited",
        "transport_unavailable",
        "binding_changed",
        "invalid_request",
        "retry_conflict",
        "discovery_refresh_required",
    }
)
_RESULT_REASONS = frozenset(
    {
        "none",
        "unsafe_destination",
        "no_matching_login",
        "field_unavailable",
        "form_changed",
        "needs_mfa",
        "captcha_or_takeover",
        "credentials_rejected",
        "deadline_exceeded",
        "binding_changed",
        "tab_lost",
        "configuration_error",
    }
)
_RECEIPTS = {
    "discover": frozenset({"accepted"}),
    "admit": frozenset({"created", "cancelled", "failed"}),
    "renew": frozenset({"accepted"}),
    "cleanup": frozenset({"closed", "already_absent", "unconfirmed"}),
}
logger = get_logger()


class TransportRefusal(Exception):
    def __init__(self, reason: str, status_code: int = 403) -> None:
        self.reason, self.status_code = reason, status_code


def _lifecycle_diagnostic(stage: str) -> None:
    """Emit only a fixed transport-stage label; lifecycle material is private."""
    logger.warning("[local_browser_transport] lifecycle_diagnostic stage=%s", stage)


def private_path(path: str) -> bool:
    return path == "/local-browser/execute"


def protected_private_path(path: str) -> bool:
    """Both spellings must be private; only the canonical one is executable."""
    return path in {"/local-browser/execute", "/local-browser/execute/"}


def _bounded_json(raw: bytes) -> dict[str, str]:
    if len(raw) > _MAX_BODY:
        raise TransportRefusal("invalid_request", 413)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise TransportRefusal("invalid_request", 400) from exc
    if not isinstance(value, dict) or _depth(value) > 8:
        raise TransportRefusal("invalid_request", 400)
    grant, operation = value.get("grant"), value.get("operation")
    if (
        not isinstance(grant, str)
        or not isinstance(operation, str)
        or operation not in _OPERATIONS
    ):
        raise TransportRefusal("invalid_request", 400)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in grant):
        raise TransportRefusal("invalid_request", 400)
    if not grant or len(grant.encode("utf-8")) > _MAX_GRANT:
        raise TransportRefusal("invalid_request", 400)
    if operation == "approve":
        command_json = value.get("command_json")
        if set(value) != {"grant", "operation", "command_json"} or not isinstance(
            command_json, str
        ):
            raise TransportRefusal("invalid_request", 400)
        if (
            any(0xD800 <= ord(char) <= 0xDFFF for char in command_json)
            or not command_json
        ):
            raise TransportRefusal("invalid_request", 400)
        if len(command_json.encode("utf-8")) > _MAX_COMMAND:
            raise TransportRefusal("invalid_request", 400)
        return {"grant": grant, "operation": operation, "command_json": command_json}
    if set(value) != {"grant", "operation"}:
        raise TransportRefusal("invalid_request", 400)
    return {"grant": grant, "operation": operation}


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate key")
        output[key] = value
    return output


def _depth(value: Any, current: int = 0) -> int:
    if not isinstance(value, (dict, list)):
        return current
    if not value:
        return current + 1
    return (
        max(
            _depth(child, current + 1)
            for child in value.values()
            if isinstance(value, dict)
        )
        if isinstance(value, dict)
        else max(_depth(child, current + 1) for child in value)
    )


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
        self.tokens = min(
            self.capacity, self.tokens + (now - self.seen) * self.refill_per_second
        )
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
        self._global_bucket = _Bucket(_CANONICAL_CREDENTIAL_CALLBACK_BURST, 1.0)
        self._buckets: dict[str, _Bucket] = {
            "unknown": _Bucket(_CANONICAL_CREDENTIAL_CALLBACK_BURST, 0.5)
        }

    def _address_gate(self, address: str) -> asyncio.Semaphore | None:
        if address not in self._per_address:
            if len(self._per_address) >= 64:
                return None
            self._per_address[address] = asyncio.Semaphore(2)
        return self._per_address[address]

    async def __aenter__(self) -> "CallbackLimits":
        raise RuntimeError("use acquire")

    async def acquire(self, address: str) -> "CapacityLease":
        key = address if address and len(address) <= 128 else "unknown"
        bucket = (
            self._buckets.setdefault(
                key, _Bucket(_CANONICAL_CREDENTIAL_CALLBACK_BURST, 0.5)
            )
            if len(self._buckets) < 65 or key == "unknown"
            else self._buckets["unknown"]
        )
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
        return CapacityLease(self.global_gate, address_gate)


@dataclass
class CapacityLease:
    global_gate: asyncio.Semaphore
    address_gate: asyncio.Semaphore
    released: bool = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.address_gate.release()
            self.global_gate.release()


_LIMITS = CallbackLimits()
_subscription_context: LocalBrowserContext | None = None
_unsubscribe: Any = None
_subscription_manager: Any = None
_unsubscribe_identity: Any = None
_observed_device_identity: Any = None


def _on_context_change(context: BrowserContext) -> None:
    """Synchronous listener called inside LocalBrowserContext's lock."""
    registrations = invalidate_local_browser_registrations("binding_changed")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for registration in registrations:
        loop.create_task(
            send_to_extension_session(
                registration.session_id,
                {
                    "type": "local_browser.invalidate",
                    "version": 1,
                    "reason": "binding_changed",
                },
            )
        )
    if context.organization_id is not None:
        for session_id in get_registry().session_ids:
            loop.create_task(
                send_to_extension_session(
                    session_id,
                    {
                        "type": "local_browser.register_required",
                        "version": 1,
                        "engine_boot_id": context.engine_boot_id,
                        "revision": context.revision,
                    },
                )
            )


async def _notify_identity_ready() -> None:
    fresh = await get_local_browser_context().refresh()
    if fresh is None or fresh.context.organization_id is None:
        return
    for session_id in get_registry().session_ids:
        await send_to_extension_session(
            session_id,
            {
                "type": "local_browser.register_required",
                "version": 1,
                "engine_boot_id": fresh.context.engine_boot_id,
                "revision": fresh.context.revision,
            },
        )


def _on_device_identity_change(identity: Any) -> None:
    """Synchronous device fence; asynchronous readiness notification follows."""
    # A removal or replacement makes every current selection unsafe. A first
    # successful registration has no local binding to retain, and only wakes
    # ready sockets so they can register once without polling.
    global _observed_device_identity
    changed = (
        _observed_device_identity is not None and _observed_device_identity != identity
    )
    _observed_device_identity = identity
    if identity is None or changed:
        registrations = invalidate_local_browser_registrations("binding_changed")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for registration in registrations:
            loop.create_task(
                send_to_extension_session(
                    registration.session_id,
                    {
                        "type": "local_browser.invalidate",
                        "version": 1,
                        "reason": "binding_changed",
                    },
                )
            )
        if identity is not None:
            loop.create_task(_notify_identity_ready())
        return
    try:
        asyncio.get_running_loop().create_task(_notify_identity_ready())
    except RuntimeError:
        return


def ensure_context_subscription(context: LocalBrowserContext | None = None) -> None:
    global \
        _subscription_context, \
        _unsubscribe, \
        _subscription_manager, \
        _unsubscribe_identity
    current = context or get_local_browser_context()
    if current is not _subscription_context:
        if _unsubscribe is not None:
            _unsubscribe()
        _subscription_context = current
        _unsubscribe = current.subscribe(_on_context_change)
    manager = get_instance_manager()
    if manager is not _subscription_manager:
        if _unsubscribe_identity is not None:
            _unsubscribe_identity()
        _subscription_manager = manager
        _unsubscribe_identity = manager.subscribe_registered_device_identity(
            _on_device_identity_change
        )


async def install_transport_subscriptions() -> None:
    """Install before the app serves sockets; seed identity without polling."""
    ensure_context_subscription()
    try:
        await get_instance_manager().registered_device_identity()
    except Exception:
        return


def uninstall_transport_subscriptions() -> None:
    global \
        _subscription_context, \
        _unsubscribe, \
        _subscription_manager, \
        _unsubscribe_identity, \
        _observed_device_identity
    if _unsubscribe is not None:
        _unsubscribe()
    if _unsubscribe_identity is not None:
        _unsubscribe_identity()
    _subscription_context = None
    _unsubscribe = None
    _subscription_manager = None
    _unsubscribe_identity = None
    _observed_device_identity = None


async def read_execute_body(request: Any) -> dict[str, str]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY:
            raise TransportRefusal("invalid_request", 413)
        chunks.append(chunk)
    raw = b"".join(chunks)
    # Kept only for this request's in-process replay comparison; never logged.
    request.state.local_browser_execute_raw = raw
    return _bounded_json(raw)


def _base_url() -> str:
    url = get_aidream_server_url().rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise TransportRefusal("transport_unavailable", 503)
    return url


def _uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        return None
    return canonical if canonical == value else None


def valid_local_browser_document_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return 0 < size <= 128


def _safe_urlsplit(value: object):  # noqa: ANN201
    """Parse an untrusted URL without letting malformed IPv6 escape this boundary."""
    if not isinstance(value, str):
        return None
    try:
        return urlsplit(value)
    except ValueError:
        return None


def _strict_response_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (RecursionError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise TransportRefusal("transport_unavailable", 503) from exc
    if not isinstance(value, dict):
        raise TransportRefusal("authority_refused", 403)
    for item in value.values():
        if isinstance(item, str) and any(
            0xD800 <= ord(char) <= 0xDFFF for char in item
        ):
            raise TransportRefusal("authority_refused", 403)
    return value


async def _read_response(response: httpx.Response) -> bytes:
    raw = bytearray()
    async for chunk in response.aiter_bytes():
        if len(raw) + len(chunk) > _MAX_REPLY:
            raise TransportRefusal("transport_unavailable", 503)
        raw.extend(chunk)
    return bytes(raw)


async def _verify(
    fresh: FreshContext, device_id: str, registration: Any, request: dict[str, str]
) -> dict[str, Any]:
    payload = {
        "grant": request["grant"],
        "operation": request["operation"],
        "app_instance_id": device_id,
    }
    if request["operation"] == "approve":
        payload["command_json"] = request["command_json"]
    headers = {
        "Authorization": f"Bearer {fresh.daemon_jwt}",
        "X-Organization-Id": fresh.context.organization_id or "",
        "Cache-Control": "no-store",
        "Accept-Encoding": "identity",
    }
    try:
        async with asyncio.timeout(5.0):
            async with httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(5.0),
                headers=headers,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{_base_url()}/browser-manager/local/transport/verify",
                    json=payload,
                ) as response:
                    if (
                        response.headers.get("content-encoding", "identity")
                        .strip()
                        .lower()
                        != "identity"
                    ):
                        raise TransportRefusal("transport_unavailable", 503)
                    raw = await _read_response(response)
    except Exception as exc:
        raise TransportRefusal("transport_unavailable", 503) from exc
    if response.headers.get("cache-control", "").lower().find("no-store") < 0:
        raise TransportRefusal("transport_unavailable", 503)
    value = _strict_response_json(raw)
    if response.status_code != 200:
        raise TransportRefusal("authority_refused", 403)
    required = {
        "status",
        "operation",
        "run_id",
        "app_instance_id",
        "controller_revision",
        "jti",
        "expires_at_ms",
    }
    if request["operation"] != "discover":
        required |= {"extension_generation", "connection_id"}
    if request["operation"] == "approve":
        required |= {
            "actor_id",
            "organization_id",
            "profile_id",
            "admission_id",
            "command_id",
            "sequence",
            "command_digest",
            "approval_id",
            "deadline_ms",
        }
    if (
        set(value) != required
        or value.get("status") != "accepted"
        or value.get("operation") != request["operation"]
    ):
        raise TransportRefusal("authority_refused", 403)
    if (
        _uuid(value.get("run_id")) is None
        or value.get("app_instance_id") != device_id
        or _uuid(device_id) is None
        or _uuid(value.get("jti")) is None
    ):
        raise TransportRefusal("authority_refused", 403)
    expiry = value.get("expires_at_ms")
    revision = value.get("controller_revision")
    if (
        type(expiry) is not int
        or type(revision) is not int
        or not 0 <= revision <= 9_007_199_254_740_991
        or not 0 <= expiry <= 9_007_199_254_740_991
        or expiry <= int(time.time() * 1000)
    ):
        raise TransportRefusal("authority_refused", 403)
    if request["operation"] != "discover" and (
        value.get("extension_generation") != registration.extension_generation
        or value.get("connection_id") != registration.connection_id
    ):
        raise TransportRefusal("binding_changed", 409)
    if request["operation"] == "approve":
        if any(
            _uuid(value.get(name)) is None
            for name in (
                "actor_id",
                "organization_id",
                "profile_id",
                "admission_id",
                "command_id",
                "approval_id",
            )
        ):
            raise TransportRefusal("authority_refused", 403)
        if (
            value.get("approval_id") != value.get("jti")
            or value.get("deadline_ms") != expiry
        ):
            raise TransportRefusal("authority_refused", 403)
        if type(value.get("sequence")) is not int or value["sequence"] < 1:
            raise TransportRefusal("authority_refused", 403)
        digest = value.get("command_digest")
        if (
            not isinstance(digest, str)
            or _command_digest(request["command_json"]) != digest
        ):
            raise TransportRefusal("authority_refused", 403)
    return value


def _same(
    fresh: FreshContext, later: FreshContext | None, device: Any, device_id: str
) -> bool:
    return (
        later is not None
        and later.owner == fresh.owner
        and later.daemon_jwt == fresh.daemon_jwt
        and later.context == fresh.context
        and later.context.organization_id is not None
        and device is not None
        and device.app_instance_id == device_id
    )


@dataclass(frozen=True)
class _ReplayIdentity:
    owner: tuple[str, str]
    engine_boot_id: str
    revision: int
    organization_id: str
    device_id: str
    extension_generation: str
    connection_id: str
    controller_revision: int


@dataclass
class _ReplayEntry:
    raw_digest: bytes
    identity: _ReplayIdentity
    expires_at_ms: int
    future: asyncio.Future[dict[str, Any]]
    inspect: bool = False


@dataclass(frozen=True)
class _InspectTombstone:
    raw_digest: bytes
    identity: _ReplayIdentity
    expires_at_ms: int


class _ReplayTable:
    def __init__(self) -> None:
        self._entries: dict[str, _ReplayEntry] = {}
        self._inspect_tombstones: dict[str, _InspectTombstone] = {}
        self._lock = asyncio.Lock()

    async def join_or_create(
        self,
        *,
        jti: str,
        raw: bytes,
        identity: _ReplayIdentity,
        expires_at_ms: int,
        inspect: bool = False,
    ) -> tuple[_ReplayEntry, bool]:
        digest = hashlib.sha256(raw).digest()
        now = int(time.time() * 1000)
        async with self._lock:
            for stale_jti, entry in list(self._entries.items()):
                if entry.expires_at_ms <= now:
                    self._entries.pop(stale_jti, None)
            for stale_jti, entry in list(self._inspect_tombstones.items()):
                if entry.expires_at_ms <= now:
                    self._inspect_tombstones.pop(stale_jti, None)
            tombstone = self._inspect_tombstones.get(jti)
            if tombstone is not None:
                if (
                    tombstone.raw_digest != digest
                    or tombstone.identity != identity
                    or tombstone.expires_at_ms != expires_at_ms
                ):
                    raise TransportRefusal("retry_conflict", 409)
                raise TransportRefusal("discovery_refresh_required", 409)
            existing = self._entries.get(jti)
            if existing is not None:
                if (
                    existing.raw_digest != digest
                    or existing.identity != identity
                    or existing.expires_at_ms != expires_at_ms
                    or existing.inspect != inspect
                ):
                    raise TransportRefusal("retry_conflict", 409)
                return existing, False
            future: asyncio.Future[dict[str, Any]] = (
                asyncio.get_running_loop().create_future()
            )
            entry = _ReplayEntry(
                digest, identity, expires_at_ms, future, inspect=inspect
            )
            self._entries[jti] = entry
            return entry, True

    async def consume_inspect(self, entry: _ReplayEntry) -> None:
        """Tombstone before publishing a one-shot inspect result."""
        async with self._lock:
            for jti, candidate in list(self._entries.items()):
                if candidate is entry:
                    self._entries.pop(jti, None)
                    self._inspect_tombstones[jti] = _InspectTombstone(
                        entry.raw_digest, entry.identity, entry.expires_at_ms
                    )
                    break

    async def discard(self, entry: _ReplayEntry) -> None:
        async with self._lock:
            for jti, candidate in list(self._entries.items()):
                if candidate is entry:
                    self._entries.pop(jti, None)
                    break


_REPLAYS = _ReplayTable()


def _identity(
    fresh: FreshContext, device_id: str, registration: Any, controller_revision: int
) -> _ReplayIdentity:
    assert fresh.context.organization_id is not None
    return _ReplayIdentity(
        owner=fresh.owner,
        engine_boot_id=fresh.context.engine_boot_id,
        revision=fresh.context.revision,
        organization_id=fresh.context.organization_id,
        device_id=device_id,
        extension_generation=registration.extension_generation,
        connection_id=registration.connection_id,
        controller_revision=controller_revision,
    )


def _remaining_seconds(expires_at_ms: int) -> float:
    return (expires_at_ms - int(time.time() * 1000)) / 1000


async def _assert_current(
    fresh: FreshContext,
    device_id: str,
    registration: Any,
    expires_at_ms: int,
) -> None:
    if _remaining_seconds(expires_at_ms) <= 0:
        raise TransportRefusal("authority_refused", 403)
    later = await get_local_browser_context().refresh()
    latest_device = await get_instance_manager().registered_device_identity()
    if not _same(fresh, later, latest_device, device_id):
        raise TransportRefusal("binding_changed", 409)
    assert fresh.context.organization_id is not None
    current = current_local_browser_registration(
        engine_boot_id=fresh.context.engine_boot_id,
        revision=fresh.context.revision,
        owner=fresh.owner,
        organization_id=fresh.context.organization_id,
        device_id=device_id,
    )
    if current != registration or _remaining_seconds(expires_at_ms) <= 0:
        raise TransportRefusal("binding_changed", 409)


def _inspect_command(command_json: str) -> bool:
    try:
        value = json.loads(command_json, object_pairs_hook=_no_duplicates)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(value, dict)
        and set(value) == {"operation"}
        and value.get("operation") == "inspect_login"
    )


def _document(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != {"url", "document_id"}:
        return None
    url, document_id = value.get("url"), value.get("document_id")
    if (
        not isinstance(url, str)
        or not valid_local_browser_document_id(document_id)
    ):
        return None
    parsed = _safe_urlsplit(url)
    if (
        parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return {"url": url, "document_id": document_id}


def _command_digest(command_json: str) -> str:
    return hashlib.sha256(
        b"matrx.local-browser.command.v1\n" + command_json.encode("utf-8")
    ).hexdigest()


def valid_terminal_receipt(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"command_id", "operation", "outcome", "reason"} | (
        {"data"} if "data" in value else set()
    )
    if set(value) != required or _uuid(value.get("command_id")) is None:
        return False
    operation, outcome, reason = (
        value.get("operation"),
        value.get("outcome"),
        value.get("reason"),
    )
    if (
        operation not in {"navigate", "inspect_login", "vault_login", "authenticator"}
        or outcome not in {"completed", "refused", "cancelled", "outcome_unknown"}
        or reason not in _RESULT_REASONS
    ):
        return False
    if outcome != "completed":
        return "data" not in value
    data = value.get("data")
    shapes = {
        "navigate": {"origin"},
        "inspect_login": {"origin", "form", "challenge"},
        "vault_login": {"filled", "submitted", "verification"},
        "authenticator": {"filled", "submitted", "challenge_detected", "verification"},
    }
    if reason != "none" or not isinstance(data, dict) or set(data) != shapes[operation]:
        return False
    if operation in {"navigate", "inspect_login"}:
        origin = data.get("origin")
        parsed = _safe_urlsplit(origin)
        if (
            parsed is None
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
    if operation == "inspect_login":
        return data.get("form") in {
            "login",
            "username_first",
            "password_change",
            "none",
            "ambiguous",
        } and data.get("challenge") in {"none", "mfa", "captcha", "unknown"}
    if operation == "vault_login":
        return (
            type(data.get("filled")) is bool
            and type(data.get("submitted")) is bool
            and data.get("verification")
            in {
                "unverified",
                "verified",
                "needs_mfa",
                "credentials_rejected",
                "captcha_or_takeover",
            }
        )
    return operation != "authenticator" or (
        all(
            type(data.get(key)) is bool
            for key in {"filled", "submitted", "challenge_detected"}
        )
        and data.get("verification")
        in {
            "unverified",
            "verified",
            "needs_mfa",
            "credentials_rejected",
            "captcha_or_takeover",
        }
    )


def _approve_result(result: object, *, inspect: bool) -> dict[str, Any] | None:
    if not isinstance(result, dict) or result.get("operation") != "approve":
        return None
    if (
        result.get("status") == "refused"
        and set(result) == {"status", "operation", "reason"}
        and result.get("reason") in _REASONS
    ):
        return {"status": "refused", "operation": "approve", "reason": result["reason"]}
    if result.get("status") != "acknowledged" or "terminal_receipt" not in result:
        return None
    allowed = {"status", "operation", "terminal_receipt"}
    document = result.get("document")
    if document is not None:
        allowed.add("document")
    if set(result) != allowed or not valid_terminal_receipt(result["terminal_receipt"]):
        return None
    if inspect:
        clean_document = _document(document)
        if clean_document is None:
            return None
        return {
            "status": "acknowledged",
            "operation": "approve",
            "terminal_receipt": result["terminal_receipt"],
            "document": clean_document,
        }
    if document is not None:
        return None
    return {
        "status": "acknowledged",
        "operation": "approve",
        "terminal_receipt": result["terminal_receipt"],
    }


async def _dispatch(
    registration: Any, request: dict[str, str], entry: _ReplayEntry
) -> dict[str, Any]:
    if _remaining_seconds(entry.expires_at_ms) <= 0:
        raise TransportRefusal("authority_refused", 403)
    call_id = str(uuid.uuid4())
    future = create_local_browser_future(registration, call_id)
    if future is None:
        raise TransportRefusal("binding_changed", 409)
    frame: dict[str, Any] = {
        "type": "local_browser.execute",
        "version": 1,
        "call_id": call_id,
        "operation": request["operation"],
        "grant": request["grant"],
    }
    if request["operation"] == "approve":
        frame["command_json"] = request["command_json"]
    sent = await send_local_browser_execute(registration, frame)
    if not sent:
        drop_local_browser_future(call_id)
        raise TransportRefusal("binding_changed", 409)
    try:
        remaining = _remaining_seconds(entry.expires_at_ms)
        if remaining <= 0:
            raise TransportRefusal("authority_refused", 403)
        result = await asyncio.wait_for(future, timeout=remaining)
    except asyncio.TimeoutError as exc:
        _lifecycle_diagnostic("dispatch_wait_timeout")
        raise TransportRefusal("transport_unavailable", 503) from exc
    except ConnectionError as exc:
        _lifecycle_diagnostic("dispatch_wait_connection")
        raise TransportRefusal("transport_unavailable", 503) from exc
    finally:
        drop_local_browser_future(call_id)
    if not isinstance(result, dict) or result.get("operation") != request["operation"]:
        _lifecycle_diagnostic("dispatch_result_envelope")
        raise TransportRefusal("transport_unavailable", 503)
    if request["operation"] == "approve":
        approved = _approve_result(result, inspect=entry.inspect)
        if approved is None:
            _lifecycle_diagnostic("dispatch_approve_result")
            raise TransportRefusal("transport_unavailable", 503)
        encoded = json.dumps(
            approved, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        if len(encoded) > (_MAX_INSPECT_REPLY if entry.inspect else _MAX_REPLY):
            _lifecycle_diagnostic("dispatch_reply_oversize")
            raise TransportRefusal("transport_unavailable", 503)
        return approved
    if (
        result.get("status") == "acknowledged"
        and result.get("receipt") in _RECEIPTS[request["operation"]]
    ):
        return {
            "status": "acknowledged",
            "operation": request["operation"],
            "receipt": result["receipt"],
        }
    if result.get("status") == "refused" and result.get("reason") in _REASONS:
        return {
            "status": "refused",
            "operation": request["operation"],
            "reason": result["reason"],
        }
    _lifecycle_diagnostic("dispatch_result_shape")
    raise TransportRefusal("transport_unavailable", 503)


async def _join_replay(
    entry: _ReplayEntry,
    fresh: FreshContext,
    device_id: str,
    registration: Any,
) -> dict[str, str]:
    remaining = _remaining_seconds(entry.expires_at_ms)
    if remaining <= 0:
        raise TransportRefusal("authority_refused", 403)
    try:
        result = await asyncio.wait_for(asyncio.shield(entry.future), timeout=remaining)
    except asyncio.TimeoutError as exc:
        raise TransportRefusal("authority_refused", 403) from exc
    await _assert_current(fresh, device_id, registration, entry.expires_at_ms)
    return result


def _settle_failure(entry: _ReplayEntry, reason: str = "transport_unavailable") -> None:
    if not entry.future.done():
        entry.future.set_exception(TransportRefusal(reason, 503))
        entry.future.exception()


async def _run_replay(
    entry: _ReplayEntry,
    registration: Any,
    request: dict[str, str],
    fresh: FreshContext,
    device_id: str,
    capacity: CapacityLease,
) -> None:
    """Keep the one socket dispatch alive if its initiating HTTP call leaves."""
    try:
        result = await _dispatch(registration, request, entry)
        await _assert_current(fresh, device_id, registration, entry.expires_at_ms)
        if entry.inspect:
            await _REPLAYS.consume_inspect(entry)
        if not entry.future.done():
            entry.future.set_result(result)
    except asyncio.CancelledError:
        await _REPLAYS.discard(entry)
        _settle_failure(entry)
        raise
    except TransportRefusal as refusal:
        await _REPLAYS.discard(entry)
        if not entry.future.done():
            entry.future.set_exception(refusal)
            entry.future.exception()
    except BaseException:
        _lifecycle_diagnostic("replay_unexpected")
        await _REPLAYS.discard(entry)
        _settle_failure(entry)
    finally:
        capacity.release()


async def execute_lifecycle(
    request: dict[str, str], *, address: str = "unknown", raw_bytes: bytes | None = None
) -> dict[str, str]:
    ensure_context_subscription()
    capacity = await _LIMITS.acquire(address)
    handed_to_replay = False
    try:
        context = get_local_browser_context()
        fresh = await context.refresh()
        device = await get_instance_manager().registered_device_identity()
        if (
            fresh is None
            or fresh.context.organization_id is None
            or device is None
            or device.user_id != fresh.owner[0]
        ):
            raise TransportRefusal("context_unavailable", 401)
        registration = current_local_browser_registration(
            engine_boot_id=fresh.context.engine_boot_id,
            revision=fresh.context.revision,
            owner=fresh.owner,
            organization_id=fresh.context.organization_id,
            device_id=device.app_instance_id,
        )
        if registration is None:
            raise TransportRefusal("registration_unavailable", 409)
        accepted = await _verify(fresh, device.app_instance_id, registration, request)
        await _assert_current(
            fresh, device.app_instance_id, registration, accepted["expires_at_ms"]
        )
        entry, creator = await _REPLAYS.join_or_create(
            jti=accepted["jti"],
            raw=raw_bytes
            if raw_bytes is not None
            else json.dumps(request, separators=(",", ":")).encode(),
            identity=_identity(
                fresh,
                device.app_instance_id,
                registration,
                accepted["controller_revision"],
            ),
            expires_at_ms=accepted["expires_at_ms"],
            inspect=request["operation"] == "approve"
            and _inspect_command(request["command_json"]),
        )
        if not creator:
            capacity.release()
            return await _join_replay(
                entry, fresh, device.app_instance_id, registration
            )
        try:
            asyncio.create_task(
                _run_replay(
                    entry,
                    registration,
                    request,
                    fresh,
                    device.app_instance_id,
                    capacity,
                ),
                name="local-browser-lifecycle-replay",
            )
            handed_to_replay = True
        except BaseException:
            _settle_failure(entry)
            raise
        return await _join_replay(entry, fresh, device.app_instance_id, registration)
    finally:
        if not handed_to_replay:
            capacity.release()
