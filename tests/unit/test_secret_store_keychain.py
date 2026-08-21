"""Credential storage must fail loudly instead of substituting plaintext."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.common import keychain_helper
from app.services.local_db import secret_store


def test_frozen_keychain_read_uses_timeout_bounded_signed_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict]] = []

    class FakeProcess:
        returncode = 0

        def __init__(self, cmd, **kwargs):
            calls.append((cmd, kwargs))
            os.write(kwargs["pass_fds"][0], b"existing-key")

        def communicate(self, timeout=None):
            assert timeout == keychain_helper.KEYCHAIN_TIMEOUT_SECONDS
            return b"", b""

        def poll(self):
            return self.returncode

    monkeypatch.setattr(keychain_helper.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(keychain_helper.sys, "frozen", True, raising=False)
    monkeypatch.setattr(keychain_helper.sys, "executable", "/signed/matrx-engine")

    assert keychain_helper.read_key_from_helper() == "existing-key"
    assert calls[0][0] == [
        "/signed/matrx-engine",
        keychain_helper.HELPER_ARGUMENT,
    ]
    assert calls[0][1]["stdout"] is subprocess.DEVNULL
    assert calls[0][1]["pass_fds"]
    assert calls[0][1]["env"][keychain_helper.HELPER_FD_ENV]


def test_source_keychain_helper_reexecutes_run_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(keychain_helper.sys, "frozen", raising=False)
    monkeypatch.setattr(keychain_helper.sys, "executable", "/venv/python")

    command = keychain_helper._helper_command()
    assert command[0] == "/venv/python"
    assert Path(command[1]).name == "run.py"
    assert command[2] == keychain_helper.HELPER_ARGUMENT


def test_keychain_timeout_disables_encryption_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secret_store, "_fernet", None)
    monkeypatch.setattr(secret_store, "_fernet_unavailable", False)
    monkeypatch.setattr(secret_store.sys, "platform", "darwin")
    attempts: list[bool] = []

    def timeout():
        attempts.append(True)
        raise TimeoutError("keychain helper timed out")

    monkeypatch.setattr(secret_store, "read_key_from_helper", timeout)

    with pytest.raises(secret_store.SecretEncryptionUnavailableError) as exc:
        secret_store.encryption_backend_or_raise()
    with pytest.raises(secret_store.SecretEncryptionUnavailableError):
        secret_store.encryption_backend_or_raise()
    assert attempts == [True]
    assert secret_store._fernet_unavailable is True
    message = str(exc.value)
    assert "OS keychain" in message
    assert "cryptography plus keyring" in message
    assert "unpersisted" in message


def test_product_code_has_no_isolated_test_plaintext_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secret_store, "_fernet", None)
    monkeypatch.setattr(secret_store, "_fernet_unavailable", False)
    monkeypatch.setattr(
        secret_store,
        "read_key_from_helper",
        lambda: (_ for _ in ()).throw(TimeoutError("unavailable")),
    )
    with pytest.raises(secret_store.SecretEncryptionUnavailableError):
        secret_store.protect("required-secret")
    source = Path(secret_store.__file__).read_text()
    assert "MATRX_ISOLATED_TEST" not in source
    assert "if f is None" not in source


def test_safe_encryption_path_still_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setattr(secret_store, "_fernet", Fernet(Fernet.generate_key()))
    monkeypatch.setattr(secret_store, "_fernet_unavailable", False)
    stored = secret_store.protect("required-secret")
    assert stored and stored.startswith("enc:v1:")
    assert stored != "required-secret"
    assert secret_store.unprotect(stored) == "required-secret"


def test_helper_serializes_read_and_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeLock:
        def __enter__(self):
            events.append("lock")

        def __exit__(self, *_args):
            events.append("unlock")

    class FakeKeyring:
        @staticmethod
        def get_password(_service, _username):
            events.append("read")
            return None

        @staticmethod
        def set_password(_service, _username, _key):
            events.append("write")

    monkeypatch.setattr(keychain_helper, "_exclusive_keychain_lock", FakeLock)
    monkeypatch.setitem(keychain_helper.sys.modules, "keyring", FakeKeyring)
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(keychain_helper, "_validated_output_fd", lambda: write_fd)
    try:
        assert keychain_helper.run_keychain_helper() == 0
        payload = os.read(read_fd, 4096)
    finally:
        os.close(read_fd)

    assert events == ["lock", "read", "write", "unlock"]
    assert payload


def test_helper_refuses_unauthorized_parent_before_touching_keychain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    touched: list[bool] = []

    def refuse():
        raise PermissionError("unauthorized")

    class ForbiddenKeyring:
        def __getattr__(self, _name):
            touched.append(True)
            raise AssertionError("Keychain must not be touched")

    monkeypatch.setattr(keychain_helper, "_validated_output_fd", refuse)
    monkeypatch.setitem(keychain_helper.sys.modules, "keyring", ForbiddenKeyring())

    assert keychain_helper.run_keychain_helper() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "PermissionError" in captured.err
    assert touched == []


def test_helper_rejects_parent_from_another_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psutil

    read_fd, write_fd = os.pipe()
    monkeypatch.setenv(keychain_helper.HELPER_FD_ENV, str(write_fd))
    monkeypatch.setattr(keychain_helper.os, "getppid", lambda: 12345)
    monkeypatch.setattr(keychain_helper.sys, "executable", "/signed/matrx-engine")

    class AttackerProcess:
        def __init__(self, _pid):
            pass

        def exe(self):
            return "/bin/zsh"

    monkeypatch.setattr(psutil, "Process", AttackerProcess)
    try:
        with pytest.raises(PermissionError, match="not the engine"):
            keychain_helper._validated_output_fd()
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_direct_helper_invocation_emits_no_key() -> None:
    run_py = Path(__file__).resolve().parents[2] / "run.py"
    result = subprocess.run(
        [sys.executable, str(run_py), keychain_helper.HELPER_ARGUMENT],
        capture_output=True,
        timeout=5,
    )

    assert result.returncode != 0
    assert result.stdout == b""


def test_run_entrypoint_intercepts_keychain_helper_before_engine_boot() -> None:
    run_source = (Path(__file__).resolve().parents[2] / "run.py").read_text(
        encoding="utf-8"
    )
    helper_guard = run_source.index("if _KEYCHAIN_HELPER_ARGUMENT")
    isolation_guard = run_source.index("IS_DEV_ENGINE =")
    app_import = run_source.index("from app.common.platform_ctx import")

    assert helper_guard < isolation_guard < app_import
