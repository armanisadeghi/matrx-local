"""HTTP client for the matrx-files microservice (files.matrxserver.com).

matrx-files is the ONLY file backend: every byte and every metadata
mutation goes through its API
with the user's Supabase JWT. URLs come from the service's four-flavour
envelope — never hand-constructed. Wire contract:
packages/matrx-files/matrx_files/api/router_files.py (aidream repo) and
common-docs/systems/files/file-service/STATE.md.
"""

from __future__ import annotations

import json
from typing import Any

from matrx_api import CallOptions, MatrxApi, MatrxApiError, Multipart, UserCredential
from matrx_api.generated import files as files_api

from app.common.system_logger import get_logger
from app.services.app_config import get_matrx_files_url

logger = get_logger()

class MatrxFilesHTTPError(RuntimeError):
    """A matrx-files call failed; carries enough context for loud reporting."""

    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body[:500]
        super().__init__(f"{method} {path} -> HTTP {status_code}: {self.body}")

    @property
    def is_auth(self) -> bool:
        return self.status_code == 401

    @property
    def is_permanent(self) -> bool:
        """4xx other than auth/rate-limit — retrying the same payload won't help."""
        return 400 <= self.status_code < 500 and self.status_code not in (401, 429)


class MatrxFilesClient:
    """Thin async wrapper over the matrx-files REST surface."""

    def __init__(self, base_url: str | None = None) -> None:
        # Explicit base_url (tests) wins; otherwise the URL is read per-use
        # from the remote app-config accessor so a config refresh applies on
        # the next request without recreating the client.
        self._base_override = base_url.rstrip("/") if base_url else None
        self._jwt: str | None = None

    @property
    def _base(self) -> str:
        if self._base_override:
            return self._base_override
        return get_matrx_files_url().rstrip("/")

    def set_jwt(self, token: str | None) -> None:
        self._jwt = token

    @property
    def available(self) -> bool:
        return bool(self._base and self._jwt)

    async def _call(
        self,
        method: str,
        label: str,
        operation: Any,
        *,
        request_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> Any:
        """One matrx-files operation through ``matrx_api`` as the signed-in person in their active organization.

        ``method``/``label`` only label a failure. The wire (Bearer JWT, X-Organization-Id, X-Request-Id) is pinned by
        ``tests/unit/test_matrx_files_client_wire_golden.py``.
        """
        if not self._base:
            raise RuntimeError("MATRX_FILES_URL not configured")
        if not self._jwt:
            raise RuntimeError("No JWT set — user must be authenticated")
        # The typed refusal (OrganizationNotResolvedError) travels AS ITSELF - see _auth_headers.
        organization_id = (await self._auth_headers())["X-Organization-Id"]
        api = MatrxApi(
            credentials=UserCredential(self._jwt),
            base_urls={"files": self._base},
            request_id=(lambda: request_id) if request_id else None,
            connect_timeout=5.0,
            total_timeout=timeout,
            retry_attempts=1,
        )
        try:
            async with api:
                payload = await operation(
                    api,
                    options=CallOptions(organization_id=organization_id, headers=extra_headers or None),
                    **kwargs,
                )
        except MatrxApiError as exc:
            if exc.status == 0 and exc.__cause__ is not None:
                raise exc.__cause__ from None  # a transport failure keeps its httpx type for callers
            body = exc.response.text if exc.response is not None else str(exc.server_detail or exc.message)
            raise MatrxFilesHTTPError(method, label, exc.status, body) from exc
        return {} if payload is None else payload

    # ------------------------------------------------------------------
    # Pull — the device-replica sync feed
    # ------------------------------------------------------------------

    async def sync_changes(
        self,
        *,
        cursor: str | None = None,
        since: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """One page of the change feed. Returns {files, next_cursor, has_more,
        server_time}. Pass ``cursor`` from the previous response; ``since``
        only for a raw updated_at watermark entry point."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        elif since:
            params["since"] = since
        return await self._call("GET", "/files/sync/changes", files_api.sync_changes, query=params)

    async def sync_folders(self, *, include_deleted: bool = False) -> dict[str, Any]:
        return await self._call(
            "GET", "/files/sync/folders", files_api.sync_folders, query={"include_deleted": include_deleted}
        )

    # ------------------------------------------------------------------
    # Bytes — URL envelope (the DownloadManager fetches the actual bytes)
    # ------------------------------------------------------------------

    async def get_url_envelope(self, file_id: str) -> dict[str, Any]:
        """{url, cdn_url, download_url} — the DURABLE contract off the record.

        Nothing mints signed URLs anymore (GET /files/{id}/url is deleted);
        the record's url/download_url point at the authenticated
        /files/{id}/download route, fetched with our Bearer JWT.
        """
        record = await self.get_record(file_id)
        return {
            "url": record.get("url"),
            "cdn_url": record.get("cdn_url"),
            "download_url": record.get("download_url"),
        }

    async def _auth_headers(self) -> dict[str, str]:
        """Authorization + organization admission headers for the file service.

        The standalone file service runs the same matrx-connect AuthMiddleware
        as aidream: an authenticated request without X-Organization-Id is
        refused 400 organization_required at the top of the stack. Same
        transport rule as app/services/aidream/client.py — resolve-or-raise,
        never send a request guaranteed to fail.
        """
        headers = {"Authorization": f"Bearer {self._jwt}"}
        from app.services.aidream.organization import (
            resolve_active_organization_id,
        )

        # The typed refusal travels AS ITSELF. Flattening it into a bare
        # RuntimeError here is what made a held upload read as a generic
        # failure upstream: `coding_sessions/artifacts.py` catches
        # `OrganizationNotResolvedError` to raise a one-click blocker, and that
        # handler could never fire because this line had already thrown the
        # type — and `held`, and the remedy — away. `OrganizationNotResolvedError`
        # IS a RuntimeError, so every existing caller still catches it.
        headers["X-Organization-Id"] = await resolve_active_organization_id(
            self._jwt or ""
        )
        return headers

    async def auth_header(self) -> dict[str, str]:
        """Auth + organization headers for direct byte fetches of durable file URLs."""
        return await self._auth_headers()

    async def get_record(self, file_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/files/{file_id}", files_api.files_get_file, path={"file_id": file_id})

    # ------------------------------------------------------------------
    # Push — upload / rename / move / tombstone / restore
    # ------------------------------------------------------------------

    async def upload(
        self,
        *,
        file_path: str,
        content: bytes,
        filename: str,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        intent: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Buffered multipart upload. Same file_path ⇒ the canonical managed
        write version-bumps the existing logical file.

        ``intent`` is the server's strict dedup declaration. Left unset, a
        write whose BYTES the account already holds is aliased onto that
        existing row and this ``file_path`` is recorded nowhere. A caller for
        whom the path IS the point — one placement of one content — sends
        ``intent="force_new_copy"`` with a ``reason`` and gets its own row,
        linked to the canonical content by ``duplicate_of_file_id``.
        """
        return await self._call(
            "POST",
            "/files/upload",
            files_api.upload_file,
            body=Multipart(
                data={
                    "file_path": file_path,
                    **({"intent": intent} if intent else {}),
                    **({"reason": reason} if reason else {}),
                    **(
                        {"metadata_json": json.dumps(metadata, separators=(",", ":"))}
                        if metadata
                        else {}
                    ),
                },
                files={"file": (filename, content, mime_type or "application/octet-stream")},
            ),
            request_id=request_id,
            extra_headers={"X-Idempotency-Key": idempotency_key} if idempotency_key else None,
            timeout=300.0,
        )

    async def patch(
        self,
        file_id: str,
        *,
        name: str | None = None,
        folder: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if folder is not None:
            body["folder"] = folder
        return await self._call(
            "PATCH", f"/files/{file_id}", files_api.update_file_metadata, path={"file_id": file_id}, body=body
        )

    async def soft_delete(self, file_id: str) -> dict[str, Any]:
        return await self._call(
            "DELETE", f"/files/{file_id}", files_api.files_delete_file, path={"file_id": file_id}
        )

    async def restore(self, file_id: str) -> dict[str, Any]:
        return await self._call(
            "POST", f"/files/{file_id}/restore", files_api.restore_file, path={"file_id": file_id}
        )
