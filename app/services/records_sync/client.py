"""Client for the custom record store's doors.

THE DOORS ARE THE ONLY SURFACE.  Every read and write in this module names one
of the store's own functions —  ``custom.read_records``, ``custom.read_record``,
``custom.anon_capture``, ``custom.record_update``, ``custom.record_write``,
``custom.record_delete``, ``custom.table_declare`` — and nothing here ever
selects from ``custom.record`` or any other table in that schema.  The store
enforces this too: ``authenticated`` holds EXECUTE on the doors and no table
privilege at all, so a direct read answers ``permission denied for table
record``.

WHY THIS REPO HAS ITS OWN CLIENT (and does not consume ``matrx-records``)
------------------------------------------------------------------------
The platform's Python store client (``aidream/packages/matrx-records``) reaches
the doors through ``matrx-orm``'s ``call_function`` / ``rls_session`` — a direct
Postgres connection with a server-side principal.  A desktop client holds no
database credentials and no service role; its only wire is PostgREST with the
publishable key and the user's JWT (CLAUDE.md, "Configuration posture").  This
repo's ``pyproject.toml`` additionally forbids a path dependency on a sibling
repo's package, so a TEMPORARY source dependency was not available either.  The
thin client below therefore speaks the same doors over the client wire.  If
``matrx-records`` ever ships a transport-agnostic store client on PyPI, delete
this file and swap in ``from matrx_records import RecordStore``.

THE TRANSPORT SEAM
------------------
``CustomStoreClient`` takes a transport so the wire is replaceable without the
engine knowing.  The shipped transport is PostgREST.  Verification lanes inject
their own (the campaign has not yet added ``custom`` to PostgREST's exposed
schemas — until it does, the HTTP wire answers PGRST106 and this client says so
out loud rather than degrading).
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from app.common.system_logger import get_logger
from app.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL

logger = get_logger()

_REST_BASE = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""

# The record store's kernels.  A client never invents these — they are the
# store's own fixed ids.
TABLE_KERNEL_ID = "11111111-0000-4000-8000-000000000001"
FIELD_KERNEL_ID = "11111111-0000-4000-8000-000000000002"
HOME_KERNEL_ID = "11111111-0000-4000-8000-000000000005"


class RecordsStoreError(RuntimeError):
    """A door refused, or the wire failed.  Carries enough to report loudly."""

    def __init__(
        self,
        function: str,
        status_code: int,
        body: str,
        *,
        code: str | None = None,
        details: Any = None,
    ) -> None:
        self.function = function
        self.status_code = status_code
        self.body = body[:800]
        self.code = code
        self.details = details
        super().__init__(f"custom.{function} -> HTTP {status_code} [{code}]: {self.body}")

    @property
    def is_auth(self) -> bool:
        return self.status_code == 401

    @property
    def is_transport(self) -> bool:
        """No HTTP response at all — offline, DNS, a dropped connection."""
        return self.status_code == 0

    @property
    def is_conflict(self) -> bool:
        """The store's optimistic-concurrency refusal (``record_update``)."""
        return self.code == "PT409"

    @property
    def is_unreachable_schema(self) -> bool:
        """PostgREST does not expose ``custom`` yet — an absent feature, not a bug."""
        return self.code == "PGRST106"

    @property
    def is_door_refused(self) -> bool:
        """This role may not call that function here (grant missing, or unknown)."""
        return self.code in ("42501", "PGRST202") or self.status_code in (403, 404)

    @property
    def is_permanent(self) -> bool:
        return (
            400 <= self.status_code < 500
            and self.status_code not in (401, 429)
            and not self.is_conflict
        )

    @property
    def current_version(self) -> int | None:
        """The version that won, when the store refused with PT409."""
        if isinstance(self.details, dict):
            value = self.details.get("current_version")
            if isinstance(value, int):
                return value
        return None


class DoorTransport(Protocol):
    """One call to one database function, in one schema."""

    async def call(self, schema: str, function: str, args: dict[str, Any]) -> Any: ...


class PostgrestDoorTransport:
    """The shipped wire: PostgREST RPC, publishable key + the user's JWT, RLS.

    ``Content-Profile`` selects the schema — a bare ``/rest/v1/rpc/<fn>`` call
    resolves against ``api`` on this database (app/config.py).
    """

    def __init__(self) -> None:
        self._jwt: str | None = None

    def set_jwt(self, token: str | None) -> None:
        self._jwt = token

    @property
    def available(self) -> bool:
        return bool(_REST_BASE and self._jwt)

    async def call(self, schema: str, function: str, args: dict[str, Any]) -> Any:
        if not _REST_BASE:
            raise RuntimeError("SUPABASE_URL not configured")
        if not self._jwt:
            raise RuntimeError("No JWT set — user must be authenticated")
        headers = {
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._jwt}",
            "Content-Profile": schema,
            "Accept-Profile": schema,
        }
        url = f"{_REST_BASE}/rpc/{function}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                resp = await client.post(url, json=args, headers=headers)
        except httpx.TransportError:
            # No HTTP response to classify.  Status 0 means exactly that, and
            # it stays retryable — an offline device is a normal state here.
            raise RecordsStoreError(function, 0, "transport failure (no HTTP response)") from None
        if resp.status_code >= 400:
            code = None
            details = None
            try:
                payload = resp.json()
                code = payload.get("code")
                raw = payload.get("details")
                details = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
                pass
            raise RecordsStoreError(function, resp.status_code, resp.text, code=code, details=details)
        if resp.status_code == 204 or not resp.text:
            return None
        return resp.json()


class CustomStoreClient:
    """The eight doors this desktop needs, and nothing else."""

    def __init__(self, transport: DoorTransport | None = None) -> None:
        self._http = PostgrestDoorTransport()
        self._transport: DoorTransport = transport or self._http

    def set_jwt(self, token: str | None) -> None:
        self._http.set_jwt(token)

    def set_transport(self, transport: DoorTransport) -> None:
        self._transport = transport

    @property
    def available(self) -> bool:
        return self._transport is not self._http or self._http.available

    # -- the switch ----------------------------------------------------

    async def store_is_open(self, organization_id: str) -> bool:
        """Is the record store switched on for this organization?

        ``custom.store_is_open(org)`` is the store's own switch door and is what
        this asks (it is EXECUTE-granted to ``authenticated`` and declared in
        ``platform.client_callable_door`` since 2026-09-18).

        If a database has not taken that grant yet the door answers 42501, and
        this falls back — LOUDLY, never silently — to the knob the door itself
        reads, ``platform.knob_resolve('custom','system_enabled', org)``, which
        is the whole body of ``store_is_open``. A switch this client cannot read
        at all is CLOSED, never open.
        """
        try:
            value = await self._transport.call(
                "custom", "store_is_open", {"p_organization_id": organization_id}
            )
        except RecordsStoreError as exc:
            if not exc.is_door_refused:
                raise
            logger.error(
                "[records_sync] custom.store_is_open is not callable here (%s) — reading the "
                "knob it reads instead: platform.knob_resolve('custom','system_enabled')",
                exc.code or exc.status_code,
            )
            value = await self._transport.call(
                "platform",
                "knob_resolve",
                {
                    "p_feature": "custom",
                    "p_key": "system_enabled",
                    "p_organization_id": organization_id,
                },
            )
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() == "true"
        return False

    # -- reads (ONLY through the read doors) ---------------------------

    async def read_records(
        self,
        organization_id: str,
        table_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._transport.call(
            "custom",
            "read_records",
            {
                "p_organization_id": organization_id,
                "p_table_id": table_id,
                "p_limit": limit,
                "p_offset": offset,
            },
        )
        return list(rows or [])

    async def read_record(self, organization_id: str, record_id: str) -> dict[str, Any] | None:
        doc = await self._transport.call(
            "custom",
            "read_record",
            {"p_organization_id": organization_id, "p_record_id": record_id},
        )
        return doc if isinstance(doc, dict) else None

    # -- writes --------------------------------------------------------

    async def anon_capture(
        self,
        organization_id: str,
        client_key: str,
        table_id: str,
        payload: dict[str, Any],
        *,
        device: str | None = None,
        captured_at: str | None = None,
    ) -> str:
        """DOOR-21: the deferred write, idempotent on a client-minted key.

        The device mints ``client_key`` BEFORE the first attempt, so every
        replay of the same key returns the same record id and increments
        ``custom.anon_replay.replays`` instead of writing a second record.
        """
        record_id = await self._transport.call(
            "custom",
            "anon_capture",
            {
                "p_organization_id": organization_id,
                "p_client_key": client_key,
                "p_table_id": table_id,
                "p_payload": payload,
                "p_device": device,
                "p_captured_at": captured_at,
            },
        )
        return str(record_id)

    async def record_update(
        self,
        organization_id: str,
        record_id: str,
        patch: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> int:
        """Patch a record; returns the new version.  PT409 = someone else won."""
        version = await self._transport.call(
            "custom",
            "record_update",
            {
                "p_organization_id": organization_id,
                "p_record_id": record_id,
                "p_patch": patch,
                "p_expected_version": expected_version,
            },
        )
        return int(version)

    async def record_write(
        self, organization_id: str, table_id: str, data: dict[str, Any]
    ) -> str:
        record_id = await self._transport.call(
            "custom",
            "record_write",
            {"p_organization_id": organization_id, "p_table_id": table_id, "p_data": data},
        )
        return str(record_id)

    async def record_delete(self, organization_id: str, record_id: str) -> Any:
        return await self._transport.call(
            "custom",
            "record_delete",
            {"p_organization_id": organization_id, "p_record_id": record_id},
        )

    async def table_declare(self, organization_id: str, spec: dict[str, Any]) -> str:
        table_id = await self._transport.call(
            "custom",
            "table_declare",
            {"p_organization_id": organization_id, "p_spec": spec},
        )
        return str(table_id)
