from __future__ import annotations

import asyncio
from pathlib import Path

from jsonschema import Draft202012Validator

from app.services.artifacts.service import ArtifactService
from app.services.local_db.database import LocalDatabase
from app.services.ai.local_tool_bridge import _convert_result
from app.content_ir import ArtifactAvailability, ScreenshotArtifact
from app.tools.session import ToolSession
from app.tools.types import ToolResult as LocalToolResult


class _FilesClient:
    def __init__(self) -> None:
        self.jwt: str | None = None
        self.uploads: list[dict] = []

    def set_jwt(self, token: str | None) -> None:
        self.jwt = token

    async def upload(self, **kwargs):
        self.uploads.append(kwargs)
        return {
            "file_id": "cloud-file-1",
            "size_bytes": len(kwargs["content"]),
            "checksum": "cloud-checksum",
            "signed_url": "https://files.example/signed",
            "visibility": "private",
        }


def test_advertised_screenshot_output_schemas_compile() -> None:
    from app.tools.catalog import get_by_cloud_name

    sample = {
        "kind": "image_ref",
        "artifact_id": "artifact-1",
        "availability": "sync_pending",
        "file_name": "capture.png",
        "size_bytes": 9,
        "checksum": "checksum",
        "source_width": 20,
        "source_height": 10,
        "capture_source": "desktop",
        "capture": {},
    }
    for name in ("local_screen", "local_browser"):
        schema = get_by_cloud_name(name).output_schema
        assert schema is not None
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(sample)


def test_local_capture_commits_bytes_before_returning(tmp_path: Path) -> None:
    async def run() -> None:
        db = LocalDatabase(path=tmp_path / "matrx.db")
        await db.connect()
        client = _FilesClient()
        service = ArtifactService(
            db=db, root=tmp_path / "artifacts", files_client=client
        )
        artifact, provider_path = await service.create_screenshot(
            content=b"png-bytes",
            width=20,
            height=10,
            capture_source="desktop",
            capture={"monitor": "all"},
            session=ToolSession(),
        )

        assert artifact.availability.value == "sync_pending"
        assert artifact.file_id is None
        assert provider_path is not None
        assert Path(provider_path).read_bytes() == b"png-bytes"
        row = await db.fetchone(
            "SELECT sync_state, local_path FROM local_artifacts WHERE artifact_id=?",
            (artifact.artifact_id,),
        )
        assert row is not None
        assert row["sync_state"] == "sync_pending"
        assert row["local_path"] == provider_path
        assert client.uploads == []
        assert "local_path" not in artifact.model_dump()
        await db.close()

    asyncio.run(run())


def test_cloud_delegated_capture_publishes_before_success(tmp_path: Path) -> None:
    async def run() -> None:
        db = LocalDatabase(path=tmp_path / "matrx.db")
        await db.connect()
        client = _FilesClient()
        service = ArtifactService(
            db=db, root=tmp_path / "artifacts", files_client=client
        )
        artifact, provider_path = await service.create_screenshot(
            content=b"png-bytes",
            width=20,
            height=10,
            capture_source="browser",
            capture={"url": "https://example.com"},
            session=ToolSession(
                execution_origin="cloud_delegated",
                cloud_call_id="call-1",
                cloud_access_token="jwt",
            ),
        )

        assert artifact.availability.value == "cloud_ready"
        assert artifact.file_id == "cloud-file-1"
        assert artifact.media_ref is not None
        assert artifact.media_ref.file_id == "cloud-file-1"
        assert provider_path is None
        assert len(client.uploads) == 1
        assert client.uploads[0]["request_id"] == "call-1"
        assert not (tmp_path / "artifacts").exists()
        await db.close()

    asyncio.run(run())


def test_local_provider_bytes_are_not_durable_tool_output(tmp_path: Path) -> None:
    image_path = tmp_path / "capture.png"
    image_path.write_bytes(b"png-bytes")
    artifact = ScreenshotArtifact(
        artifact_id="artifact-1",
        availability=ArtifactAvailability.SYNC_PENDING,
        file_name="capture.png",
        size_bytes=9,
        checksum="checksum",
        source_width=20,
        source_height=10,
        capture_source="desktop",
    )
    converted = _convert_result(
        LocalToolResult(
            output="captured",
            artifact=artifact,
            provider_image_path=str(image_path),
        ),
        "local_screen",
        "call-1",
        1.0,
    )

    assert converted.output["artifact_id"] == "artifact-1"
    assert converted.to_tool_result_content()["content"][0].base64_data
    dumped = converted.model_dump()
    assert "provider_content" not in dumped
    assert "base64" not in str(dumped).lower()


def test_publication_promotes_chat_result_and_enqueues_outbox(tmp_path: Path) -> None:
    async def run() -> None:
        db = LocalDatabase(path=tmp_path / "matrx.db")
        await db.connect()
        service = ArtifactService(db=db, root=tmp_path / "artifacts")
        await db.execute(
            "INSERT INTO chat.tool_call (id, output, version) VALUES (?, ?, ?)",
            (
                "tool-call-1",
                '{"artifact_id":"artifact-1","availability":"sync_pending"}',
                1,
            ),
        )
        await db.commit()
        artifact = ScreenshotArtifact(
            artifact_id="artifact-1",
            availability=ArtifactAvailability.CLOUD_READY,
            file_name="capture.png",
            size_bytes=9,
            checksum="checksum",
            source_width=20,
            source_height=10,
            capture_source="desktop",
            file_id="cloud-file-1",
            media_ref={"file_id": "cloud-file-1"},
        )
        await service._promote_chat_tool_results(artifact)

        row = await db.fetchone(
            "SELECT output, version FROM chat.tool_call WHERE id='tool-call-1'"
        )
        assert row is not None
        assert '"file_id":"cloud-file-1"' in row["output"]
        assert row["version"] == 2
        queued = await db.fetchone(
            "SELECT entity_type FROM sync_queue WHERE entity_id='tool-call-1'"
        )
        assert queued is not None
        assert queued["entity_type"] == "chat.tool_call"
        await db.close()

    asyncio.run(run())
