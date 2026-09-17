"""Private packaged-smoke sync-daemon cleanup uses only its own boundary."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SYNC_HELPER = REPO_ROOT / "scripts" / "smoke-syncd.sh"
CONTROL = "private-test-control-token"


def _free_dev_port() -> int:
    for port in range(22260, 22280):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("the private dev daemon band is occupied")


class _ShutdownHandler(BaseHTTPRequestHandler):
    home: Path
    child: subprocess.Popen[str]
    requests: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        type(self).requests.append(self.path)
        if self.path != "/v1/shutdown" or self.headers.get("Authorization") != f"Bearer {CONTROL}":
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(202)
        self.end_headers()
        type(self).child.terminate()
        type(self).child.wait(timeout=1)
        (type(self).home / "run" / "syncd.sock").unlink()
        (type(self).home / "syncd.json").unlink()
        (type(self).home / "syncd.token").unlink()

    def log_message(self, *_args: object) -> None:
        pass


def _write_private_daemon_boundary(home: Path, port: int, pid: int, world: str = "dev") -> None:
    (home / "run").mkdir(parents=True)
    (home / "run" / "syncd.sock").touch()
    (home / "syncd.token").write_text(f"{CONTROL}\nprivate-read-token\n", encoding="utf-8")
    (home / "syncd.json").write_text(
        json.dumps(
            {
                "world": world,
                "pid": pid,
                "tcp_port": port,
                "socket_path": str(home / "run" / "syncd.sock"),
            }
        ),
        encoding="utf-8",
    )


def _run_cleanup(
    private_home: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    command = f'source "{SYNC_HELPER}"; smoke_shutdown_private_syncd "{private_home}" 3'
    return subprocess.run(
        ["bash", "-c", command], text=True, capture_output=True, check=False, env=env
    )


@pytest.mark.skipif(os.name == "nt", reason="the controlled child uses POSIX signals")
def test_private_smoke_cleanup_posts_only_to_its_dev_boundary(tmp_path: Path) -> None:
    port = _free_dev_port()
    child = subprocess.Popen(["sh", "-c", "sleep 30"], text=True)
    _ShutdownHandler.home = tmp_path
    _ShutdownHandler.child = child
    _ShutdownHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", port), _ShutdownHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _write_private_daemon_boundary(tmp_path, port, child.pid)
    try:
        result = _run_cleanup(tmp_path)
    finally:
        server.shutdown()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=1)

    assert result.returncode == 0, result.stderr
    assert _ShutdownHandler.requests == ["/v1/shutdown"]
    assert not (tmp_path / "syncd.json").exists()
    assert not (tmp_path / "run" / "syncd.sock").exists()
    assert not (tmp_path / "syncd.token").exists()
    assert CONTROL not in result.stdout
    assert CONTROL not in result.stderr


def test_private_smoke_cleanup_refuses_a_live_world_before_any_request(tmp_path: Path) -> None:
    port = _free_dev_port()
    _write_private_daemon_boundary(tmp_path, port, os.getpid(), world="live")

    result = _run_cleanup(tmp_path)

    assert result.returncode != 0
    assert (tmp_path / "syncd.json").exists()
    assert (tmp_path / "run" / "syncd.sock").exists()


def test_private_smoke_cleanup_refuses_a_symlink_to_the_live_home(tmp_path: Path) -> None:
    user_home = tmp_path / "user-home"
    live_home = user_home / ".matrx"
    _write_private_daemon_boundary(live_home, _free_dev_port(), os.getpid())
    alias = tmp_path / "private-alias"
    alias.symlink_to(live_home, target_is_directory=True)

    result = _run_cleanup(alias, env={**os.environ, "HOME": str(user_home)})

    assert result.returncode != 0
    assert (live_home / "syncd.json").exists()
    assert "non-private home" in result.stderr


def test_private_smoke_cleanup_rejects_leftover_socket_or_token_without_discovery(
    tmp_path: Path,
) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "syncd.sock").touch()
    (tmp_path / "syncd.token").write_text(f"{CONTROL}\nprivate-read-token\n", encoding="utf-8")

    result = _run_cleanup(tmp_path)

    assert result.returncode != 0
    assert "incomplete private daemon boundary" in result.stderr


def _exit_trap_source() -> str:
    source = (REPO_ROOT / "scripts" / "smoke.sh").read_text(encoding="utf-8")
    match = re.search(r"^smoke_on_exit\(\) \{\n.*?^}\n", source, flags=re.MULTILINE | re.DOTALL)
    assert match is not None
    return match.group(0)


def test_exit_cleanup_failure_changes_a_successful_shell_exit_to_failure() -> None:
    command = f'''
SMOKE_APP_QUIESCED=1
smoke_stop_owned_app() {{ printf 'app\\n'; return 0; }}
smoke_cleanup_private_syncd() {{ printf 'daemon\\n'; return 1; }}
smoke_release_build_lock() {{ printf 'lock\\n'; }}
{_exit_trap_source()}
trap smoke_on_exit EXIT
exit 0
'''

    result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["app", "daemon", "lock"]


def test_exit_after_confirmed_forced_app_stop_still_cleans_the_private_daemon() -> None:
    command = f'''
SMOKE_APP_QUIESCED=0
smoke_stop_owned_app() {{ SMOKE_APP_QUIESCED=1; printf 'forced-app-exit\\n'; return 1; }}
smoke_cleanup_private_syncd() {{ printf 'daemon-cleanup\\n'; return 0; }}
smoke_release_build_lock() {{ printf 'lock\\n'; }}
{_exit_trap_source()}
trap smoke_on_exit EXIT
exit 0
'''

    result = subprocess.run(["bash", "-c", command], text=True, capture_output=True, check=False)

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["forced-app-exit", "daemon-cleanup", "lock"]
