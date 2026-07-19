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
        return self.cmdline[0]

    def cwd(self) -> str:
        return self.cwd_value


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
) -> tuple[preflight.CleanReport, list[int]]:
    terminated: list[int] = []
    monkeypatch.setattr(preflight.psutil, "process_iter", lambda *_args, **_kwargs: processes)
    monkeypatch.setattr(preflight, "_self_pid_chain", lambda: {9999})
    monkeypatch.setattr(preflight, "_current_user", lambda: "owner")
    monkeypatch.setattr(preflight, "_resolve_listening_ports", lambda _found: None)
    monkeypatch.setattr(preflight, "_protected_engine_pids", lambda _found: set())
    monkeypatch.setattr(preflight, "_ancestor_pids", lambda _pid: set())
    monkeypatch.setattr(
        preflight,
        "_descendant_pids",
        lambda pid: list((descendants or {}).get(pid, [])),
    )
    monkeypatch.setattr(preflight, "_maybe_remove_stale_discovery_file", lambda: False)
    monkeypatch.setattr(preflight.time, "sleep", lambda _seconds: None)

    def terminate(pid: int, *, label: str, kill_tree: bool = False) -> bool:
        del label, kill_tree
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
        monkeypatch, processes, descendants={101: [202]}
    )

    assert terminated == []
    assert report.protected == 2
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
