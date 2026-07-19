from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from app.services.optional_packages import core


def test_packages_dir_honors_dev_world_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MATRX_HOME_DIR", str(tmp_path))

    assert core.packages_dir("image-gen-packages") == tmp_path / "image-gen-packages"


def test_packages_dir_contains_escaped_dev_symlink_without_mutating_it(
    monkeypatch, tmp_path: Path
) -> None:
    dev_home = tmp_path / "dev"
    live_packages = tmp_path / "live" / "image-gen-packages"
    dev_home.mkdir()
    live_packages.mkdir(parents=True)
    sentinel = live_packages / ".install-complete"
    sentinel.write_text("live", encoding="utf-8")
    escaped = dev_home / "image-gen-packages"
    escaped.symlink_to(live_packages, target_is_directory=True)
    monkeypatch.setenv("MATRX_HOME_DIR", str(dev_home))

    resolved = core.packages_dir("image-gen-packages")

    assert resolved == dev_home / ".isolated-packages" / "image-gen-packages"
    assert resolved.resolve(strict=False).is_relative_to(dev_home.resolve())
    assert escaped.is_symlink()
    assert escaped.resolve() == live_packages.resolve()
    assert sentinel.read_text(encoding="utf-8") == "live"


def test_packages_dir_uses_platform_live_home_without_isolation_override(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MATRX_HOME_DIR", raising=False)

    result = core.packages_dir("image-gen-packages")

    if core.sys.platform == "win32":
        expected = Path(
            core.os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        ) / "AI Matrx" / "image-gen-packages"
    else:
        expected = Path.home() / ".matrx" / "image-gen-packages"
    assert result == expected


def test_packages_dir_uses_windows_live_home_without_isolation_override(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MATRX_HOME_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(core.sys, "platform", "win32")

    assert core.packages_dir("image-gen-packages") == (
        tmp_path / "AI Matrx" / "image-gen-packages"
    )


def test_packages_dir_rejects_relative_escape(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MATRX_HOME_DIR", str(tmp_path))

    try:
        core.packages_dir("../live-packages")
    except ValueError as exc:
        assert "basename" in str(exc)
    else:
        raise AssertionError("relative package path escape was accepted")


def test_install_command_prefers_interpreter_pip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core, "_python_has_pip", lambda _python: True)

    command = core._install_command("/python", tmp_path)

    assert command[:4] == ["/python", "-m", "pip", "install"]
    assert command[command.index("--target") + 1] == str(tmp_path)


def test_install_command_uses_uv_for_pipless_uv_environment(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(core, "_python_has_pip", lambda _python: False)
    monkeypatch.setattr(
        core.shutil, "which", lambda name: "/tools/uv" if name == "uv" else None
    )

    command = core._install_command("/uv-venv/python", tmp_path)

    assert command[:3] == ["/tools/uv", "pip", "install"]
    assert command[command.index("--python") + 1] == "/uv-venv/python"
    assert command[command.index("--target") + 1] == str(tmp_path)


def test_pip_process_is_reaped_when_engine_shutdown_is_requested(
    monkeypatch, tmp_path: Path
) -> None:
    cancel = threading.Event()
    progress = core.InstallProgress()
    monkeypatch.setattr(core, "find_python", lambda: sys.executable)
    monkeypatch.setattr(
        core,
        "_install_command",
        lambda _python, _target: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(core.InstallCancelledError, match="cancelled"):
            core.run_pip_streaming(
                ["ignored-argument"],
                tmp_path,
                progress,
                cancel_event=cancel,
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 5.0
