from __future__ import annotations

from pathlib import Path

from app.services.optional_packages import core


def test_packages_dir_honors_dev_world_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MATRX_HOME_DIR", str(tmp_path))

    assert core.packages_dir("image-gen-packages") == tmp_path / "image-gen-packages"


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
