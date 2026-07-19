"""Timeout boundary for macOS Keychain access by the frozen engine.

The macOS ``keyring`` backend can wait for user interaction inside
``SecItemCopyMatching`` while retaining the Python interpreter lock. Running
that backend in a short-lived copy of the signed engine preserves the Keychain
ACL identity and lets the parent terminate a blocked request safely.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

HELPER_ARGUMENT = "--matrx-keychain-helper-v1"
HELPER_FD_ENV = "MATRX_KEYCHAIN_HELPER_FD"
KEYRING_SERVICE = "matrx-local"
KEYRING_USERNAME = "db-encryption-key"
# A frozen one-file helper spends roughly 3-4 seconds in the PyInstaller
# bootstrap before ``run.py`` can dispatch the helper argument.  Five seconds
# therefore left less than two seconds for the actual Keychain call and caused
# healthy packaged installs to fall back to plaintext on a cold launch.  Keep
# the boundary finite (the backend can still wait forever in
# SecItemCopyMatching), but allow enough time for both bootstrap and a normal
# Keychain round trip.
KEYCHAIN_TIMEOUT_SECONDS = 15


@contextmanager
def _exclusive_keychain_lock() -> Iterator[None]:
    """Serialize first-use DEK creation across every engine process."""
    import fcntl
    import os

    lock_dir = Path.home() / "Library" / "Caches" / "com.aimatrx.desktop"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = lock_dir / ".keychain-dek.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _helper_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, HELPER_ARGUMENT]
    run_py = Path(__file__).resolve().parents[2] / "run.py"
    return [sys.executable, str(run_py), HELPER_ARGUMENT]


def read_key_from_helper() -> str:
    """Return the DEK over a private inherited pipe with a bounded wait."""
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        env = os.environ.copy()
        env[HELPER_FD_ENV] = str(write_fd)
        process = subprocess.Popen(
            _helper_command(),
            env=env,
            pass_fds=(write_fd,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        os.close(write_fd)
        write_fd = -1
        try:
            _, stderr = process.communicate(timeout=KEYCHAIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise TimeoutError("macOS Keychain helper timed out") from exc

        with os.fdopen(read_fd, "rb") as pipe:
            payload = pipe.read(4097)
        read_fd = -1
        if process.returncode == 0 and 0 < len(payload) <= 4096:
            return payload.decode("ascii").strip()
        detail = stderr.decode("utf-8", errors="replace").strip()[:500]
        raise RuntimeError(
            f"macOS Keychain helper failed: {detail or f'exit status {process.returncode}'}"
        )
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        if read_fd >= 0:
            os.close(read_fd)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()


def _validated_output_fd() -> int:
    """Authorize the helper's real parent and its inherited one-way pipe."""
    import psutil

    raw_fd = os.environ.get(HELPER_FD_ENV, "")
    try:
        output_fd = int(raw_fd)
    except ValueError as exc:
        raise PermissionError("missing helper output channel") from exc
    if output_fd <= 2 or not stat.S_ISFIFO(os.fstat(output_fd).st_mode):
        raise PermissionError("invalid helper output channel")

    parent_pid = os.getppid()
    if parent_pid <= 1:
        raise PermissionError("helper has no authorized parent")
    parent_executable = Path(psutil.Process(parent_pid).exe()).resolve()
    current_executable = Path(sys.executable).resolve()
    if parent_executable != current_executable:
        raise PermissionError("helper parent is not the engine executable")
    return output_fd


def run_keychain_helper() -> int:
    """Read/create the DEK for an authenticated parent engine only."""
    output_fd: int | None = None
    try:
        # Validate before importing or touching Keychain. The public argv flag
        # alone must never become a raw-key export oracle.
        output_fd = _validated_output_fd()
        import keyring
        from cryptography.fernet import Fernet

        # The lock spans read + possible create. Without it, two overlapping
        # engines can both observe a missing item, generate different DEKs,
        # and make the first process's ciphertext permanently unreadable.
        with _exclusive_keychain_lock():
            key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
            if not key:
                key = Fernet.generate_key().decode("ascii")
                keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
        payload = key.encode("ascii")
        while payload:
            written = os.write(output_fd, payload)
            payload = payload[written:]
        return 0
    except Exception as exc:
        # Never print credentials or exception values: backend errors can
        # include command arguments or other environment details.
        sys.stderr.write(f"keychain helper failed: {type(exc).__name__}\n")
        sys.stderr.flush()
        return 1
    finally:
        if output_fd is not None:
            try:
                os.close(output_fd)
            except OSError:
                pass
