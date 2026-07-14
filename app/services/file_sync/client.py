"""HTTP client for the matrx-files microservice (files.matrxserver.com).

matrx-files is the ONLY file backend (docs/handoffs/file-sync-system.md
§Contracts): every byte and every metadata mutation goes through its API
with the user's Supabase JWT. URLs come from the service's four-flavour
envelope — never hand-constructed. Wire contract:
packages/matrx-files/matrx_files/api/router_files.py (aidream repo) and
common-docs/matrx-files-service/FEATURE.md.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.common.system_logger import get_logger
from app.config import MATRX_FILES_URL

logger = get_logger()

_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


class FileSyncHTTPError(RuntimeError):
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
        self._base = (base_url or MATRX_FILES_URL).rstrip("/")
        self._jwt: str | None = None

    def set_jwt(self, token: str | None) -> None:
        self._jwt = token

    @property
    def available(self) -> bool:
        return bool(self._base and self._jwt)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        if not self._base:
            raise RuntimeError("MATRX_FILES_URL not configured")
        if not self._jwt:
            raise RuntimeError("No JWT set — user must be authenticated")
        headers = {"Authorization": f"Bearer {self._jwt}"}
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            resp = await client.request(
                method, url, params=params, json=json_body, data=data,
                files=files, headers=headers,
            )
        if resp.status_code >= 400:
            raise FileSyncHTTPError(method.upper(), path, resp.status_code, resp.text)
        if resp.status_code == 204 or not resp.text:
            return {}
        return resp.json()

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
        return await self._request("GET", "/files/sync/changes", params=params)

    async def sync_folders(self, *, include_deleted: bool = False) -> dict[str, Any]:
        return await self._request(
            "GET", "/files/sync/folders", params={"include_deleted": include_deleted}
        )

    # ------------------------------------------------------------------
    # Bytes — URL envelope (the DownloadManager fetches the actual bytes)
    # ------------------------------------------------------------------

    async def get_url_envelope(self, file_id: str) -> dict[str, Any]:
        """{url, cdn_url, signed_url, download_url} — mint fresh right before
        enqueueing a download; signed URLs expire."""
        return await self._request("GET", f"/files/{file_id}/url")

    async def get_record(self, file_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/files/{file_id}")

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
        visibility: str = "private",
    ) -> dict[str, Any]:
        """Buffered multipart upload. Same file_path ⇒ the canonical managed
        write version-bumps the existing logical file."""
        return await self._request(
            "POST",
            "/files/upload",
            data={"file_path": file_path, "visibility": visibility},
            files={"file": (filename, content, mime_type or "application/octet-stream")},
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
        return await self._request("PATCH", f"/files/{file_id}", json_body=body)

    async def soft_delete(self, file_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/files/{file_id}")

    async def restore(self, file_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/files/{file_id}/restore")
