"""Cold-process import regressions for the engine bootstrap boundary."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_isolated(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "MATRX_HOME_DIR": str(tmp_path / "matrx-home"),
            "MATRX_USER_DIR": str(tmp_path / "matrx-user"),
            "LOG_DIR": str(tmp_path / "logs"),
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); {code}",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _assert_isolated_success(code: str, tmp_path: Path) -> None:
    result = _run_isolated(code, tmp_path)
    assert result.returncode == 0, (
        f"isolated import failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def test_common_package_initializer_is_a_leaf(tmp_path: Path) -> None:
    _assert_isolated_success(
        "import app.common; "
        "assert 'app.config' not in sys.modules; "
        "assert 'app.common.fancy_prints' not in sys.modules; "
        "assert 'app.common.system_logger' not in sys.modules; "
        "assert not hasattr(app.common, 'print_link')",
        tmp_path,
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "app.config",
        "app.services.paths.manager",
        "app.services.documents.file_manager",
        "app.common.platform_ctx",
        "app.common.fancy_prints",
        "app.common.system_logger",
    ],
)
def test_module_imports_into_a_cold_process(
    module_name: str, tmp_path: Path
) -> None:
    _assert_isolated_success(f"import {module_name}", tmp_path)
