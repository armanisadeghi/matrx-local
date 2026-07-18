"""Durable local screenshot artifacts with independent cloud reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.common.system_logger import get_logger
from app.services.file_sync.client import MatrxFilesClient
from app.services.local_db.database import LocalDatabase, get_db
from app.services.local_db.repositories import TokenRepo
from app.services.paths.manager import safe_dir
from app.content_ir.media import (
    ArtifactAvailability,
    MediaRefValue,
    ScreenshotArtifact,
)

if TYPE_CHECKING:
    from app.tools.session import ToolSession

logger = get_logger()

_PUBLISH_INTERVAL_SECONDS = 15.0
_BATCH_SIZE = 20


class ArtifactService:
    """Owns screenshot bytes from capture through cloud publication."""

    def __init__(
        self,
        *,
        db: LocalDatabase | None = None,
        root: Path | None = None,
        files_client: MatrxFilesClient | None = None,
    ) -> None:
        self._db = db or get_db()
        self._root_override = root
        self._client = files_client or MatrxFilesClient()
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    @property
    def root(self) -> Path:
        root = self._root_override or (safe_dir("data") / "artifacts" / "screenshots")
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start_background(self) -> None:
        if self.active:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._publisher_loop(), name="artifact-cloud-publisher"
        )

    async def stop_background(self) -> None:
        self._stopping = True
        self._wake.set()
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def create_screenshot(
        self,
        *,
        content: bytes,
        width: int,
        height: int,
        capture_source: str,
        capture: dict[str, Any] | None,
        session: "ToolSession",
    ) -> tuple[ScreenshotArtifact, str | None]:
        """Persist/publish one capture and return its Content IR value.

        The second tuple item is a process-private local path for provider
        adapters. It is intentionally excluded from the semantic result.
        """

        artifact_id = str(uuid.uuid4())
        checksum = hashlib.sha256(content).hexdigest()
        file_name = f"screenshot-{artifact_id}.png"
        capture_value = capture or {}

        if session.execution_origin == "cloud_delegated":
            try:
                artifact = await self._publish_bytes(
                    artifact_id=artifact_id,
                    content=content,
                    file_name=file_name,
                    width=width,
                    height=height,
                    capture_source=capture_source,
                    capture=capture_value,
                    checksum=checksum,
                    jwt=session.cloud_access_token,
                    request_id=session.cloud_call_id,
                )
            except Exception as exc:
                # The delegated caller cannot consume a machine-local URL, but
                # retaining the bytes makes the failure recoverable and avoids
                # throwing away a completed capture.
                local_path = await asyncio.to_thread(
                    self._write_local,
                    artifact_id,
                    content,
                    file_name,
                )
                artifact = self._pending_value(
                    artifact_id=artifact_id,
                    file_name=file_name,
                    size_bytes=len(content),
                    checksum=checksum,
                    width=width,
                    height=height,
                    capture_source=capture_source,
                    capture=capture_value,
                    availability=ArtifactAvailability.SYNC_FAILED,
                )
                await self._insert_artifact(
                    artifact, local_path=local_path, sync_error=str(exc)
                )
                self.wake()
                logger.warning(
                    "[artifacts] delegated screenshot %s retained locally after "
                    "cloud publication failed: %s",
                    artifact_id,
                    exc,
                )
                return artifact, local_path
            # Cloud delegation's success boundary is the acknowledged file,
            # not the optional local bookkeeping row. A damaged local DB must
            # not turn an already-portable result into a false cloud failure.
            try:
                await self._insert_artifact(artifact, local_path=None)
            except Exception:
                logger.warning(
                    "[artifacts] cloud-ready screenshot %s could not be cached "
                    "in the local sidecar",
                    artifact_id,
                    exc_info=True,
                )
            return artifact, None

        local_path = await asyncio.to_thread(
            self._write_local, artifact_id, content, file_name
        )
        artifact = self._pending_value(
            artifact_id=artifact_id,
            file_name=file_name,
            size_bytes=len(content),
            checksum=checksum,
            width=width,
            height=height,
            capture_source=capture_source,
            capture=capture_value,
            availability=ArtifactAvailability.SYNC_PENDING,
        )
        await self._insert_artifact(artifact, local_path=local_path)
        self.wake()
        return artifact, local_path

    async def get(self, artifact_id: str) -> ScreenshotArtifact | None:
        row = await self._db.fetchone(
            "SELECT * FROM local_artifacts WHERE artifact_id = ?", (artifact_id,)
        )
        return self._row_to_artifact(dict(row)) if row else None

    async def local_path(self, artifact_id: str) -> Path | None:
        row = await self._db.fetchone(
            "SELECT local_path FROM local_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        if not row or not row["local_path"]:
            return None
        path = Path(str(row["local_path"]))
        try:
            path.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError):
            logger.error("[artifacts] rejected out-of-root path for %s", artifact_id)
            return None
        return path if path.is_file() else None

    async def sync_pending(self) -> int:
        token_row = await TokenRepo(self._db).get()
        jwt = token_row.get("access_token") if token_row else None
        if not jwt:
            return 0
        self._client.set_jwt(jwt)
        rows = await self._db.fetchall(
            "SELECT * FROM local_artifacts "
            "WHERE sync_state IN ('sync_pending', 'sync_failed') "
            "AND local_path IS NOT NULL ORDER BY created_at LIMIT ?",
            (_BATCH_SIZE,),
        )
        published = 0
        for raw in rows:
            row = dict(raw)
            try:
                path = Path(row["local_path"])
                content = await asyncio.to_thread(path.read_bytes)
                artifact = await self._publish_bytes(
                    artifact_id=row["artifact_id"],
                    content=content,
                    file_name=row["file_name"],
                    width=int(row["source_width"]),
                    height=int(row["source_height"]),
                    capture_source=row["capture_source"],
                    capture=json.loads(row["capture_json"] or "{}"),
                    checksum=row["checksum"],
                    jwt=jwt,
                    request_id=f"artifact:{row['artifact_id']}",
                )
                await self._mark_published(artifact)
                published += 1
            except Exception as exc:
                await self._mark_failed(row["artifact_id"], exc)
                logger.info(
                    "[artifacts] publication deferred for %s: %s",
                    row["artifact_id"],
                    exc,
                )
        return published

    def _write_local(self, artifact_id: str, content: bytes, file_name: str) -> str:
        today = datetime.now(UTC)
        directory = self.root / f"{today:%Y}" / f"{today:%m}" / f"{today:%d}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / file_name
        fd, temp_name = tempfile.mkstemp(prefix=f".{artifact_id}-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return str(target)

    async def _publish_bytes(
        self,
        *,
        artifact_id: str,
        content: bytes,
        file_name: str,
        width: int,
        height: int,
        capture_source: str,
        capture: dict[str, Any],
        checksum: str,
        jwt: str | None,
        request_id: str | None,
    ) -> ScreenshotArtifact:
        if not jwt:
            raise RuntimeError("cloud publication requires an authenticated user")
        self._client.set_jwt(jwt)
        response = await self._client.upload(
            file_path=f"system-files/screenshots/{artifact_id}.png",
            content=content,
            filename=file_name,
            mime_type="image/png",
            visibility="private",
            metadata={
                "kind": "screenshot",
                "artifact_id": artifact_id,
                "capture_source": capture_source,
                "width": width,
                "height": height,
                "checksum": checksum,
                "capture": capture,
            },
            request_id=request_id,
            idempotency_key=f"screenshot:{artifact_id}",
        )
        file_id = response.get("file_id")
        if not file_id:
            raise RuntimeError("matrx-files upload returned no file_id")
        return ScreenshotArtifact(
            artifact_id=artifact_id,
            availability=ArtifactAvailability.CLOUD_READY,
            media_type="image/png",
            file_name=file_name,
            size_bytes=int(response.get("size_bytes") or len(content)),
            checksum=str(response.get("checksum") or checksum),
            source_width=width,
            source_height=height,
            capture_source=capture_source,
            file_id=str(file_id),
            media_ref=MediaRefValue(file_id=str(file_id)),
            url=response.get("url"),
            cdn_url=response.get("cdn_url"),
            signed_url=response.get("signed_url"),
            download_url=response.get("download_url"),
            visibility=response.get("visibility") or "private",
            capture=capture,
        )

    def _pending_value(
        self,
        *,
        artifact_id: str,
        file_name: str,
        size_bytes: int,
        checksum: str,
        width: int,
        height: int,
        capture_source: str,
        capture: dict[str, Any],
        availability: ArtifactAvailability,
    ) -> ScreenshotArtifact:
        return ScreenshotArtifact(
            artifact_id=artifact_id,
            availability=availability,
            file_name=file_name,
            size_bytes=size_bytes,
            checksum=checksum,
            source_width=width,
            source_height=height,
            capture_source=capture_source,
            capture=capture,
        )

    async def _insert_artifact(
        self,
        artifact: ScreenshotArtifact,
        *,
        local_path: str | None,
        sync_error: str | None = None,
    ) -> None:
        await self._db.execute(
            """INSERT INTO local_artifacts (
                 artifact_id, kind, local_path, media_type, file_name, size_bytes,
                 checksum, source_width, source_height, capture_source,
                 capture_json, sync_state, cloud_file_id, url, cdn_url,
                 signed_url, download_url, visibility, sync_error, published_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(artifact_id) DO UPDATE SET
                 local_path=COALESCE(excluded.local_path, local_artifacts.local_path),
                 sync_state=excluded.sync_state,
                 cloud_file_id=excluded.cloud_file_id,
                 url=excluded.url, cdn_url=excluded.cdn_url,
                 signed_url=excluded.signed_url, download_url=excluded.download_url,
                 sync_error=excluded.sync_error,
                 published_at=excluded.published_at,
                 updated_at=datetime('now')""",
            (
                artifact.artifact_id,
                artifact.kind,
                local_path,
                artifact.media_type,
                artifact.file_name,
                artifact.size_bytes,
                artifact.checksum,
                artifact.source_width,
                artifact.source_height,
                artifact.capture_source,
                json.dumps(artifact.capture, separators=(",", ":")),
                artifact.availability.value,
                artifact.file_id,
                artifact.url,
                artifact.cdn_url,
                artifact.signed_url,
                artifact.download_url,
                artifact.visibility,
                sync_error,
                datetime.now(UTC).isoformat()
                if artifact.availability == ArtifactAvailability.CLOUD_READY
                else None,
            ),
        )
        await self._db.commit()

    async def _mark_published(self, artifact: ScreenshotArtifact) -> None:
        await self._db.execute(
            """UPDATE local_artifacts SET
                 sync_state='cloud_ready', cloud_file_id=?, url=?, cdn_url=?,
                 signed_url=?, download_url=?, visibility=?, size_bytes=?,
                 checksum=?, sync_error=NULL, published_at=?, updated_at=datetime('now')
               WHERE artifact_id=?""",
            (
                artifact.file_id,
                artifact.url,
                artifact.cdn_url,
                artifact.signed_url,
                artifact.download_url,
                artifact.visibility,
                artifact.size_bytes,
                artifact.checksum,
                datetime.now(UTC).isoformat(),
                artifact.artifact_id,
            ),
        )
        await self._db.commit()
        await self._promote_chat_tool_results(artifact)

    async def _promote_chat_tool_results(self, artifact: ScreenshotArtifact) -> None:
        """Replace pending refs in the local chat mirror and enqueue them.

        A stable artifact row alone is not enough: the cloud conversation must
        eventually receive the newly available ``file_id`` too. This pass is
        intentionally artifact-id based, so it works regardless of whether a
        logger wrapped the Content IR value in another result envelope.
        """

        from app.services.local_db.outbox import enqueue_change

        rows = await self._db.fetchall(
            "SELECT id, output FROM chat.tool_call "
            "WHERE output IS NOT NULL AND output LIKE ?",
            (f"%{artifact.artifact_id}%",),
        )
        replacement = artifact.model_dump(mode="json", exclude_none=True)

        def replace(value: Any) -> tuple[Any, bool]:
            if isinstance(value, dict):
                if value.get("artifact_id") == artifact.artifact_id:
                    return {**value, **replacement}, True
                changed = False
                result: dict[str, Any] = {}
                for key, child in value.items():
                    result[key], child_changed = replace(child)
                    changed = changed or child_changed
                return result, changed
            if isinstance(value, list):
                changed = False
                result_list: list[Any] = []
                for child in value:
                    new_child, child_changed = replace(child)
                    result_list.append(new_child)
                    changed = changed or child_changed
                return result_list, changed
            return value, False

        for row in rows:
            try:
                parsed = json.loads(row["output"])
            except (TypeError, json.JSONDecodeError):
                continue
            promoted, changed = replace(parsed)
            if not changed:
                continue
            serialized = json.dumps(promoted, separators=(",", ":"), default=str)
            await self._db.execute(
                "UPDATE chat.tool_call SET output=?, output_chars=?, "
                "updated_at=datetime('now'), version=COALESCE(version, 0)+1 "
                "WHERE id=?",
                (serialized, len(serialized), row["id"]),
            )
            await enqueue_change(
                "chat", "tool_call", str(row["id"]), self._db, commit=False
            )
        await self._db.commit()

    async def _mark_failed(self, artifact_id: str, exc: Exception) -> None:
        await self._db.execute(
            """UPDATE local_artifacts SET sync_state='sync_failed',
                 sync_attempts=sync_attempts+1, sync_error=?,
                 updated_at=datetime('now') WHERE artifact_id=?""",
            (str(exc)[:1000], artifact_id),
        )
        await self._db.commit()

    async def _publisher_loop(self) -> None:
        while not self._stopping:
            try:
                await self.sync_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("[artifacts] publisher tick failed", exc_info=True)
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=_PUBLISH_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    def _row_to_artifact(self, row: dict[str, Any]) -> ScreenshotArtifact:
        file_id = row.get("cloud_file_id")
        return ScreenshotArtifact(
            artifact_id=row["artifact_id"],
            availability=row["sync_state"],
            media_type=row["media_type"],
            file_name=row["file_name"],
            size_bytes=row["size_bytes"],
            checksum=row["checksum"],
            source_width=row["source_width"],
            source_height=row["source_height"],
            capture_source=row["capture_source"],
            file_id=file_id,
            media_ref=MediaRefValue(file_id=file_id) if file_id else None,
            url=row.get("url"),
            cdn_url=row.get("cdn_url"),
            signed_url=row.get("signed_url"),
            download_url=row.get("download_url"),
            visibility=row.get("visibility") or "private",
            capture=json.loads(row.get("capture_json") or "{}"),
        )


_instance: ArtifactService | None = None


def get_artifact_service() -> ArtifactService:
    global _instance
    if _instance is None:
        _instance = ArtifactService()
    return _instance
