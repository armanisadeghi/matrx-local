"""Early packaged-smoke exits release the build lock before full cleanup exists."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_missing_no_build_marker_releases_the_preflight_build_lock(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("smoke.sh", "smoke-environment.sh", "smoke-http.sh", "smoke-syncd.sh"):
        shutil.copy2(REPO_ROOT / "scripts" / name, scripts / name)
    target = tmp_path / "desktop" / "src-tauri" / "target"
    target.mkdir(parents=True)

    result = subprocess.run(
        ["bash", str(scripts / "smoke.sh"), "packaged", "--no-build"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not (target / ".matrx-smoke-build.lock").exists()
