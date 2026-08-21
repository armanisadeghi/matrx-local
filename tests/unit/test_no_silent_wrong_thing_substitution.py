from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.local_db import secret_store
from app.services.local_db.database import LocalDatabase
from app.services.local_db.repositories import TokenRepo
from app.tools import tool_schemas


def test_real_token_write_refuses_plaintext_when_encryption_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        db = LocalDatabase(tmp_path / "matrx.db")
        await db.connect()
        monkeypatch.setattr(secret_store, "_fernet", None)
        monkeypatch.setattr(secret_store, "_fernet_unavailable", True)
        try:
            with pytest.raises(secret_store.SecretEncryptionUnavailableError) as exc:
                await TokenRepo(db).save("access-secret", "user-1", "refresh-secret")
            assert "OS keychain" in str(exc.value)
            assert "plaintext storage is refused" in str(exc.value)
            row = await db.fetchone("SELECT count(*) AS n FROM auth_tokens")
            assert row and row["n"] == 0
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
