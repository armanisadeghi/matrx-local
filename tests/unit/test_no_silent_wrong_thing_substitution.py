from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.local_db import secret_store
from app.services.local_db.database import LocalDatabase
from app.services.local_db.repositories import TokenRepo
from app.tools import tool_schemas


def test_token_repo_reads_the_daemon_grant_without_local_credential_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from app.services import sync_client

        class _Daemon:
            async def access_grant(self) -> tuple[str, str]:
                return (
                    "eyJhbGciOiJub25lIn0.eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMDEiLCJleHAiOjQxMDI0NDQ4MDB9.signature",
                    "00000000-0000-4000-8000-000000000001",
                )

        monkeypatch.setattr(sync_client, "get_sync_client", _Daemon)
        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        try:
            row = await TokenRepo(db).get()
            assert row is not None
            assert row["user_id"] == "00000000-0000-4000-8000-000000000001"
            assert not hasattr(TokenRepo, "save")
            credential_table = await db.fetchone(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_tokens'"
            )
            assert credential_table is None
        finally:
            await db.close()

    asyncio.run(exercise())


def test_tool_annotation_failure_is_actionable_and_safe_path_survives() -> None:
    def bad(value: int) -> None:
        pass
    bad.__annotations__["value"] = "DefinitelyMissingType"

    with pytest.raises(tool_schemas.ToolAnnotationResolutionError) as exc:
        tool_schemas._handler_signature(bad)
    message = str(exc.value)
    assert "DefinitelyMissingType" in message
    assert "Import or define" in message
    assert "string-typed substitution is refused" in message

    def good(value: int) -> None:
        pass

    assert tool_schemas._handler_signature(good).parameters["value"].annotation is int


def test_source_guard_bans_both_wrong_thing_fallbacks() -> None:
    secret_source = Path(secret_store.__file__).read_text()
    schema_source = Path(tool_schemas.__file__).read_text()
    assert "MATRX_ISOLATED_TEST" not in secret_source
    assert "if f is None" not in secret_source
    assert "return inspect.signature(handler)" not in schema_source
