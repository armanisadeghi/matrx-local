from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_HELPER = REPO_ROOT / "scripts" / "smoke-environment.sh"
TEST_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _resolved_environment(smoke_home: Path) -> dict[str, str]:
    command = f'''
source "{ENV_HELPER}"
smoke_build_isolated_env "{smoke_home}" "{smoke_home}/matrx" 24000 run-1 "{TEST_KEY}"
env "${{SMOKE_ISOLATED_ENV[@]}}" python3 - <<'PY'
import json, os
print(json.dumps({{key: os.environ.get(key) for key in (
    "HOME", "MATRX_HOME_DIR", "XDG_CONFIG_HOME", "MATRX_ISOLATED_TEST",
    "TEST_MODE", "MATRX_ISOLATED_DB_ENCRYPTION_KEY"
)}}))
PY
'''
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_macos_smoke_keeps_home_isolated_and_uses_run_scoped_encryption(
    tmp_path: Path,
) -> None:
    resolved = _resolved_environment(tmp_path)

    assert resolved["HOME"] == str(tmp_path)
    assert resolved["MATRX_HOME_DIR"] == str(tmp_path / "matrx")
    assert resolved["XDG_CONFIG_HOME"] == str(tmp_path / ".config")
    assert resolved["MATRX_ISOLATED_TEST"] == "1"
    assert resolved["TEST_MODE"] == "1"
    assert resolved["MATRX_ISOLATED_DB_ENCRYPTION_KEY"] == TEST_KEY


def test_non_macos_smoke_keeps_whole_home_isolation(tmp_path: Path) -> None:
    resolved = _resolved_environment(tmp_path)

    assert resolved["HOME"] == str(tmp_path)
    assert resolved["MATRX_HOME_DIR"] == str(tmp_path / "matrx")


def test_isolated_encryption_key_shape_is_accepted() -> None:
    command = (
        f"source {shlex.quote(str(ENV_HELPER))}; "
        f"smoke_verify_isolated_encryption_environment {TEST_KEY}"
    )
    result = subprocess.run(["bash", "-c", command], check=False)
    assert result.returncode == 0


def test_invalid_isolated_encryption_key_shape_is_rejected() -> None:
    command = (
        f"source {shlex.quote(str(ENV_HELPER))}; "
        "smoke_verify_isolated_encryption_environment too-short"
    )
    result = subprocess.run(["bash", "-c", command], check=False)
    assert result.returncode != 0


def test_packaged_smoke_webview_is_incognito() -> None:
    command = f"source {shlex.quote(str(ENV_HELPER))}; smoke_tauri_config"
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)
    assert config["app"]["windows"][0]["incognito"] is True
    assert config["bundle"]["createUpdaterArtifacts"] is False


def test_smoke_build_lock_refuses_a_concurrent_owner(tmp_path: Path) -> None:
    lock_dir = tmp_path / "missing-parent" / "build.lock"
    command = f'''
source "{ENV_HELPER}"
smoke_acquire_build_lock "{lock_dir}"
if smoke_acquire_build_lock "{lock_dir}"; then exit 9; fi
smoke_release_build_lock
test ! -e "{lock_dir}"
'''
    subprocess.run(["bash", "-c", command], check=True)


def test_smoke_holds_build_lock_through_artifact_consumption() -> None:
    source = (REPO_ROOT / "scripts" / "smoke.sh").read_text(encoding="utf-8")
    early_no_build = source.index('if [ "$NO_BUILD" -eq 1 ]')
    early_acquire = source.index("smoke_acquire_build_lock", early_no_build)
    marker_read = source.index('SMOKE_ENGINE_PORT_BASE="$(tr', early_no_build)
    packaged = source[source.index("run_packaged() {") : source.index('case "$MODE" in')]
    acquire = packaged.index("smoke_acquire_build_lock")
    build_branch = packaged.index('if [ "$NO_BUILD" -eq 0 ]')
    find_binary = packaged.index("bin=\"$(find_app_binary)\"")
    launch = packaged.index('env "${SMOKE_ISOLATED_ENV[@]}" "$bin"')
    shutdown = packaged.index('terminate_pid "$pid"')
    release = packaged.rindex("smoke_release_build_lock")

    assert early_acquire < marker_read
    assert acquire < build_branch < find_binary < launch < shutdown < release
