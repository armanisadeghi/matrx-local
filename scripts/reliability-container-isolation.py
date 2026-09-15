#!/usr/bin/env python3
"""Exercise a Linux container outer boundary without importing Matrx code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import time
import uuid
from pathlib import Path
from typing import NoReturn


REPO_ROOT = Path(__file__).resolve().parents[1]
IMAGE_ID = "sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285"
TIMEOUT_SECONDS = 25


def fail(message: str) -> NoReturn:
    raise SystemExit(f"reliability-container-isolation: {message}")


def checked(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=TIMEOUT_SECONDS)
    if result.returncode:
        fail((result.stderr.strip() or result.stdout.strip() or "command failed")[:500])
    return result.stdout.strip()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def snapshot(task_root: Path, revision: str, patch: Path | None, patch_sha256: str | None) -> dict[str, object]:
    commit = checked(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=REPO_ROOT)
    archive = task_root / "source.tar"
    with archive.open("wb") as output:
        result = subprocess.run(["git", "archive", "--format=tar", commit], cwd=REPO_ROOT, stdout=output, stderr=subprocess.PIPE, timeout=TIMEOUT_SECONDS)
    if result.returncode:
        fail(result.stderr.decode(errors="replace")[:500])
    source = task_root / "snapshot"
    source.mkdir(mode=0o700)
    with tarfile.open(archive) as tar:
        root = source.resolve()
        for member in tar.getmembers():
            target = (source / member.name).resolve()
            if target != root and root not in target.parents:
                fail(f"archive member escapes snapshot: {member.name!r}")
        tar.extractall(source)
    applied_patch: str | None = None
    if patch is not None:
        if not patch_sha256:
            fail("--patch requires --patch-sha256")
        payload = patch.read_bytes()
        applied_patch = hashlib.sha256(payload).hexdigest()
        if applied_patch.lower() != patch_sha256.lower():
            fail("patch digest does not match --patch-sha256")
        checked(["git", "apply", "--check", "--whitespace=error", str(patch)], cwd=source)
        checked(["git", "apply", "--whitespace=error", str(patch)], cwd=source)
    return {
        "snapshot": source,
        "commit": commit,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "snapshot_sha256": tree_digest(source),
        "patch_sha256": applied_patch,
    }


CHILD_PROBE = """\
import errno, json, socket
from pathlib import Path

allowed = {errno.EROFS, errno.EACCES, errno.EPERM}
result = {'ran': True}
try:
    Path('/container-rootfs-child-control').write_text('forbidden', encoding='utf-8')
except OSError as exc:
    result['rootfs_denied'] = exc.errno in allowed
    result['rootfs_errno'] = exc.errno
else:
    result['rootfs_denied'] = False
    result['rootfs_errno'] = None
try:
    external = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    external.connect(('198.51.100.1', 53))
except OSError as exc:
    result['external_denied'] = exc.errno in {errno.ENETUNREACH, errno.EACCES, errno.EPERM}
    result['external_errno'] = exc.errno
else:
    result['external_denied'] = False
    result['external_errno'] = None
print(json.dumps(result, sort_keys=True))
"""

PROBE = textwrap.dedent(
    f"""\
    import errno
    import json
    import os
    import socket
    import subprocess
    import threading
    from pathlib import Path

    allowed_rootfs = {{errno.EROFS, errno.EACCES, errno.EPERM}}
    root = Path(os.environ['MATRX_HOME_DIR'])
    sentinel = Path(os.environ['ISO_HOST_SENTINEL'])
    root.mkdir(parents=True, exist_ok=True)
    positive = root / 'positive.txt'
    positive.write_text('container-private', encoding='utf-8')
    private_ok = positive.read_text(encoding='utf-8') == 'container-private'

    def denied_rootfs():
        try:
            Path('/container-rootfs-control').write_text('forbidden', encoding='utf-8')
        except OSError as exc:
            return {{'denied': exc.errno in allowed_rootfs, 'errno': exc.errno, 'type': type(exc).__name__}}
        return {{'denied': False, 'errno': None, 'type': None}}

    def denied_external():
        try:
            external = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            external.connect(('198.51.100.1', 53))
        except OSError as exc:
            return {{'denied': exc.errno in {{errno.ENETUNREACH, errno.EACCES, errno.EPERM}}, 'errno': exc.errno, 'type': type(exc).__name__}}
        return {{'denied': False, 'errno': None, 'type': None}}

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    port = server.getsockname()[1]
    def serve():
        connection, _ = server.accept()
        with connection:
            connection.sendall(connection.recv(4))
        server.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
        client.sendall(b'ping')
        loopback_ok = client.recv(4) == b'ping'
    thread.join(timeout=2)

    rootfs = denied_rootfs()
    external = denied_external()
    child = subprocess.run([os.environ.get('PYTHON_BIN', 'python3'), '-I', '-c', {CHILD_PROBE!r}], capture_output=True, text=True, timeout=5)
    try:
        child_result = json.loads(child.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        child_result = {{'ran': False, 'rootfs_denied': False, 'external_denied': False}}
    child_ok = child.returncode == 0 and child_result.get('ran') is True and child_result.get('rootfs_denied') is True and child_result.get('external_denied') is True
    result = {{
        'private_ok': private_ok,
        'rootfs': rootfs,
        'loopback_ok': loopback_ok,
        'external': external,
        'host_sentinel_absent': not sentinel.exists(),
        'child_returncode': child.returncode,
        'child_result': child_result,
        'child_ok': child_ok,
    }}
    result['pass'] = private_ok and rootfs['denied'] and loopback_ok and external['denied'] and result['host_sentinel_absent'] and child_ok
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['pass'] else 1)
    """
)


def image_metadata() -> dict[str, object]:
    raw = checked(["docker", "image", "inspect", IMAGE_ID])
    record = json.loads(raw)[0]
    if record.get("Id") != IMAGE_ID:
        fail("local Python image does not match the required digest")
    if record.get("Os") != "linux" or record.get("Architecture") != "arm64":
        fail("local Python image is not linux/arm64")
    return {"image_id": record["Id"], "image_os": record["Os"], "image_architecture": record["Architecture"]}


def cleanup(name: str) -> dict[str, object]:
    try:
        result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        remaining = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^/{name}$"], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        return {"pass": result.returncode == 0 and remaining.returncode == 0 and not remaining.stdout.strip(),
                "remove_returncode": result.returncode, "inspection_returncode": remaining.returncode,
                "stderr": result.stderr[-1000:]}
    except subprocess.TimeoutExpired:
        return {"pass": False, "timeout": True}


def validate_inspect(inspect: dict[str, object], snapshot_path: Path, sentinel: Path) -> dict[str, bool]:
    host_config = inspect.get("HostConfig", {})
    config = inspect.get("Config", {})
    mounts = inspect.get("Mounts", [])
    if not isinstance(host_config, dict) or not isinstance(config, dict) or not isinstance(mounts, list):
        fail("Docker inspect had an unexpected shape")
    bind_mounts = [mount for mount in mounts if isinstance(mount, dict) and mount.get("Type") == "bind"]
    tmpfs = host_config.get("Tmpfs") or {}
    env = config.get("Env", [])
    if not isinstance(env, list):
        fail("Docker inspect Env is not a list")
    checks = {
        "network_none": host_config.get("NetworkMode") == "none",
        "readonly_root": host_config.get("ReadonlyRootfs") is True,
        "nonroot_user": config.get("User") == "65532:65532",
        "cap_drop_all": "ALL" in (host_config.get("CapDrop") or []),
        "no_new_privileges": "no-new-privileges" in (host_config.get("SecurityOpt") or []),
        "pid_limit": host_config.get("PidsLimit") == 128,
        "memory_limit": host_config.get("Memory") == 4 * 1024 * 1024 * 1024,
        "cpu_limit": host_config.get("NanoCpus") == 2_000_000_000,
        "only_snapshot_bind": len(bind_mounts) == 1 and bind_mounts[0].get("Source") == str(snapshot_path)
        and bind_mounts[0].get("Destination") == "/workspace" and bind_mounts[0].get("RW") is False,
        "private_tmpfs": set(tmpfs) == {"/run/matrx-test", "/tmp"},
        "no_host_or_codex_home_env": all(not item.startswith(("HOME=", "CODEX_HOME=")) for item in env),
        "sentinel_not_mounted": all(mount.get("Source") != str(sentinel) for mount in mounts if isinstance(mount, dict)),
    }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--patch-sha256")
    args = parser.parse_args()
    if args.patch_sha256 and not args.patch:
        fail("--patch-sha256 requires --patch")
    if not shutil.which("docker"):
        fail("docker is unavailable")
    compile(PROBE, "container-probe", "exec")
    compile(CHILD_PROBE, "container-child-probe", "exec")
    task_root = Path(tempfile.mkdtemp(prefix="matrx-container-isolation-")).resolve()
    task_root.chmod(0o700)
    source_info = snapshot(task_root, args.revision, args.patch, args.patch_sha256)
    image = image_metadata()
    sentinel = task_root / "host-only-sentinel"
    sentinel.write_bytes(b"not-mounted")
    name = f"matrx-isolation-{uuid.uuid4().hex[:12]}"
    container_root = "/run/matrx-test"
    invocation = [
        "docker", "create", "--name", name, "--network", "none", "--read-only",
        "--user", "65532:65532", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "128", "--memory", "4g", "--cpus", "2",
        "--tmpfs", f"{container_root}:rw,nosuid,nodev,mode=1777,size=2g",
        "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777,size=1g",
        "--mount", f"type=bind,src={source_info['snapshot']},dst=/workspace,readonly",
        "--workdir", "/workspace",
        "-e", f"MATRX_HOME_DIR={container_root}/home",
        "-e", f"MATRX_USER_DIR={container_root}/user",
        "-e", f"MATRX_TEMP_DIR={container_root}/temp",
        "-e", f"MATRX_DATA_DIR={container_root}/data",
        "-e", f"MATRX_CONFIG_DIR={container_root}/config",
        "-e", f"MATRX_LOG_DIR={container_root}/logs",
        "-e", f"ISO_HOST_SENTINEL={sentinel}",
        "-e", "PYTHON_BIN=python3",
        IMAGE_ID, "python3", "-I", "-c", PROBE,
    ]
    receipt: dict[str, object] = {
        "container_name": name,
        "kind": "container-capability-probe", "timestamp": int(time.time()),
        "commit": source_info["commit"],
        "archive_sha256": source_info["archive_sha256"],
        "snapshot_sha256": source_info["snapshot_sha256"],
        "patch_sha256": source_info["patch_sha256"],
        "snapshot_path": str(source_info["snapshot"]),
        **image,
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "probe_sha256": hashlib.sha256(PROBE.encode()).hexdigest(),
        "container_profile_sha256": hashlib.sha256(json.dumps(invocation[4:], separators=(",", ":")).encode()).hexdigest(),
        "host_sentinel_sha256": hashlib.sha256(sentinel.read_bytes()).hexdigest(),
    }
    try:
        checked(invocation)
        checked(["docker", "start", name])
        inspect = json.loads(checked(["docker", "inspect", name]))[0]
        receipt["inspect"] = {
            "mounts": inspect.get("Mounts"), "host_config": inspect.get("HostConfig"),
            "config_user": inspect.get("Config", {}).get("User"),
            "config_env": inspect.get("Config", {}).get("Env"),
            "network_sandbox_key": inspect.get("NetworkSettings", {}).get("SandboxKey"),
            "state": inspect.get("State"),
        }
        receipt["inspect_validation"] = validate_inspect(inspect, source_info["snapshot"], sentinel)
        waiter = subprocess.run(["docker", "wait", name], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        receipt["wait_returncode"] = waiter.returncode
        receipt["container_returncode"] = int(waiter.stdout.strip()) if waiter.returncode == 0 else None
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        receipt["stdout"] = logs.stdout[-8000:]
        receipt["stderr"] = logs.stderr[-8000:]
        receipt["final_state"] = json.loads(checked(["docker", "inspect", name]))[0].get("State")
        receipt["host_sentinel_unchanged"] = sentinel.read_bytes() == b"not-mounted"
    except subprocess.TimeoutExpired:
        receipt["timeout"] = True
        receipt["host_sentinel_unchanged"] = sentinel.read_bytes() == b"not-mounted"
    finally:
        receipt["cleanup"] = cleanup(name)
    receipt_path = task_root / "probe-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(receipt_path), "container_returncode": receipt.get("container_returncode")}, sort_keys=True))
    validated = all(receipt.get("inspect_validation", {}).values())
    return 0 if receipt.get("container_returncode") == 0 and receipt.get("host_sentinel_unchanged") and validated and receipt["cleanup"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
