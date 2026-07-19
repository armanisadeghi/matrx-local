from __future__ import annotations

from dataclasses import dataclass

import pytest

from app import preflight


@dataclass
class _FakeProcess:
    pid: int
    name: str
    cmdline: list[str]
    env: dict[str, str] | Exception
    username: str = "owner"
    cwd_value: str = "/Users/test/code/matrx-local"
    created_at: float = 1_700_000_000.25
    executable: str | None = None
    parent_pid: int = 1

    @property
    def info(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "name": self.name,
            "cmdline": self.cmdline,
            "username": self.username,
        }

    def environ(self) -> dict[str, str]:
        if isinstance(self.env, Exception):
            raise self.env
        return self.env

    def exe(self) -> str:
        return self.executable or self.cmdline[0]

    def cwd(self) -> str:
        return self.cwd_value

    def create_time(self) -> float:
        return self.created_at

    def ppid(self) -> int:
        return self.parent_pid


def _engine_service() -> preflight.ManagedService:
    return next(service for service in preflight.SERVICES if service.name == "engine")


def _cloudflared_service() -> preflight.ManagedService:
    return next(
        service for service in preflight.SERVICES if service.name == "cloudflared"
    )


def _run_clean(
    monkeypatch: pytest.MonkeyPatch,
    processes: list[_FakeProcess],
    *,
    descendants: dict[int, list[int]] | None = None,
    discovery: dict | None = None,
) -> tuple[preflight.CleanReport, list[int]]:
    terminated: list[int] = []
    monkeypatch.setattr(preflight.psutil, "process_iter", lambda *_args, **_kwargs: processes)
    monkeypatch.setattr(preflight, "_self_pid_chain", lambda: {9999})
    monkeypatch.setattr(preflight, "_current_user", lambda: "owner")
    monkeypatch.setattr(preflight, "_resolve_listening_ports", lambda _found: None)
    monkeypatch.setattr(preflight, "_protected_engine_pids", lambda _found: set())
    monkeypatch.setattr(preflight, "read_discovery_file", lambda: discovery)
    monkeypatch.setattr(preflight, "_ancestor_pids", lambda _pid: set())
    monkeypatch.setattr(
        preflight,
        "_descendant_pids",
        lambda pid: list((descendants or {}).get(pid, [])),
    )
    monkeypatch.setattr(preflight, "_maybe_remove_stale_discovery_file", lambda: False)
    monkeypatch.setattr(preflight.time, "sleep", lambda _seconds: None)

    def terminate(
        pid: int,
        *,
        label: str,
        kill_tree: bool = False,
        expected_identity: preflight.ProcessIdentity | None = None,
    ) -> bool:
        del label, kill_tree, expected_identity
        terminated.append(pid)
        return True

    monkeypatch.setattr(preflight, "_terminate_pid", terminate)
    report = preflight.clean_orphans(
        services=(_engine_service(), _cloudflared_service())
    )
    return report, terminated


@pytest.mark.parametrize(
    ("cmdline", "env"),
    [
        (["uv", "run", "python", "run.py"], {}),
        (
            ["python", "run.py"],
            {"MATRX_HOME_DIR": "/Users/test/.matrx-dev", "MATRX_PORT_BASE": "22240"},
        ),
        (
            ["python", "run.py"],
            {"MATRX_HOME_DIR": "/tmp/matrx-dev-home.private"},
        ),
        (["python", "run.py"], preflight.psutil.AccessDenied(pid=101)),
    ],
)
def test_live_preflight_never_signals_source_run_dev_engine(
    monkeypatch: pytest.MonkeyPatch,
    cmdline: list[str],
    env: dict[str, str] | Exception,
) -> None:
    report, terminated = _run_clean(
        monkeypatch,
        [_FakeProcess(101, "python", cmdline, env)],
    )

    assert terminated == []
    assert report.protected == 1
    assert report.orphans_found == 0


def test_dev_engine_tree_protects_its_cloudflared_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes = [
        _FakeProcess(101, "python", ["python", "run.py"], {}),
        _FakeProcess(
            202,
            "cloudflared",
            ["cloudflared", "tunnel", "--url", "http://127.0.0.1:22240"],
            {},
        ),
    ]

    report, terminated = _run_clean(
        monkeypatch,
        processes,
        descendants={101: [202]},
        discovery={
            "services": {
                "tunnel": {
                    "pid": 202,
                    "process_started_at": 1_700_000_000.25,
                    "executable": "cloudflared",
                }
            }
        },
    )

    assert terminated == []
    assert report.protected == 2
    assert report.orphans_found == 0


def test_unrecorded_cloudflared_is_never_signaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(
        202,
        "cloudflared",
        ["/usr/local/bin/cloudflared", "tunnel", "--url", "http://localhost:9000"],
        {},
        executable="/usr/local/bin/cloudflared",
    )

    report, terminated = _run_clean(monkeypatch, [process], discovery={})

    assert terminated == []
    assert report.orphans_found == 0


def test_exact_discovery_owned_cloudflared_orphan_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(
        202,
        "cloudflared",
        ["/Users/test/.matrx/bin/cloudflared", "tunnel", "--url", "http://127.0.0.1:22140"],
        {},
        executable="/Users/test/.matrx/bin/cloudflared",
    )
    discovery = {
        "services": {
            "tunnel": {
                "pid": 202,
                "process_started_at": process.created_at,
                "executable": process.executable,
            }
        }
    }

    report, terminated = _run_clean(
        monkeypatch,
        [process],
        discovery=discovery,
    )

    assert terminated == [202]
    assert report.orphans_found == 1
    assert report.orphans_killed == 1


def test_windows_cloudflared_executable_matches_owned_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = r"C:\Users\test\.matrx\bin\cloudflared.exe"
    process = _FakeProcess(
        202,
        "cloudflared.exe",
        [executable, "tunnel", "--url", "http://127.0.0.1:22140"],
        {},
        executable=executable,
    )
    discovery = {
        "services": {
            "tunnel": {
                "pid": process.pid,
                "process_started_at": process.created_at,
                "executable": executable,
            }
        }
    }

    report, terminated = _run_clean(
        monkeypatch,
        [process],
        discovery=discovery,
    )

    assert terminated == [202]
    assert report.orphans_killed == 1


def test_pid_reuse_is_revalidated_before_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReusedProcess:
        terminated = False

        def create_time(self) -> float:
            return 1_800_000_000.0

        def exe(self) -> str:
            return "/usr/local/bin/unrelated"

        def terminate(self) -> None:
            self.terminated = True

    reused = ReusedProcess()
    monkeypatch.setattr(preflight.psutil, "Process", lambda _pid: reused)

    result = preflight._terminate_single_pid(
        202,
        label="cloudflared",
        expected_identity=preflight.ProcessIdentity(
            process_started_at=1_700_000_000.25,
            executable="/Users/test/.matrx/bin/cloudflared",
        ),
    )

    assert result is True
    assert reused.terminated is False


def test_exact_identity_does_not_reap_live_engine_cloudflared_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(
        202,
        "cloudflared",
        ["/Users/test/.matrx/bin/cloudflared", "tunnel", "--url", "http://127.0.0.1:22140"],
        {},
        executable="/Users/test/.matrx/bin/cloudflared",
        parent_pid=101,
    )
    monkeypatch.setattr(preflight.psutil, "pid_exists", lambda pid: pid == 101)
    discovery = {
        "services": {
            "tunnel": {
                "pid": process.pid,
                "process_started_at": process.created_at,
                "executable": process.executable,
            }
        }
    }

    report, terminated = _run_clean(
        monkeypatch,
        [process],
        discovery=discovery,
    )

    assert terminated == []
    assert report.orphans_found == 0


@pytest.mark.parametrize(
    "identity_override",
    [
        {"pid": 999},
        {"process_started_at": 1_600_000_000.0},
        {"executable": "/opt/other/cloudflared"},
        {"process_started_at": None},
    ],
)
def test_cloudflared_identity_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    identity_override: dict[str, object],
) -> None:
    process = _FakeProcess(
        202,
        "cloudflared",
        ["/Users/test/.matrx/bin/cloudflared", "tunnel", "--url", "http://127.0.0.1:22140"],
        {},
        executable="/Users/test/.matrx/bin/cloudflared",
    )
    identity: dict[str, object] = {
        "pid": process.pid,
        "process_started_at": process.created_at,
        "executable": process.executable,
    }
    identity.update(identity_override)

    report, terminated = _run_clean(
        monkeypatch,
        [process],
        discovery={"services": {"tunnel": identity}},
    )

    assert terminated == []
    assert report.orphans_found == 0


@pytest.mark.parametrize(
    ("name", "cmdline", "env"),
    [
        ("python", ["python", "run.py"], {"MATRX_LIVE_ENGINE": "1"}),
        ("Matrx Engine", ["/Applications/AI Matrx.app/Matrx Engine"], {}),
        ("matrx-engine", ["/app/bin/matrx-engine"], {}),
    ],
)
def test_live_preflight_still_reaps_true_live_engine_orphans(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    cmdline: list[str],
    env: dict[str, str],
) -> None:
    report, terminated = _run_clean(
        monkeypatch,
        [_FakeProcess(303, name, cmdline, env)],
    )

    assert terminated == [303]
    assert report.protected == 0
    assert report.orphans_found == 1
    assert report.orphans_killed == 1
