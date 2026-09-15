#!/usr/bin/env python3
"""Create and exercise a small, outer filesystem/network test boundary.

This launcher is intentionally stdlib-only.  It creates a clean tracked-source
snapshot and runs one Python subprocess under macOS Seatbelt.  It does not
import Matrx code, start the application, load dotenv, or inspect user data.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
from pathlib import Path
from typing import NoReturn


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE = REPO_ROOT / "scripts" / "reliability-isolation.sb"
TIMEOUT_SECONDS = 20
RUNTIME_PREFIXES = (
    Path("/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework"),
    Path("/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework"),
)


def fail(message: str) -> NoReturn:
    raise SystemExit(f"reliability-isolation: {message}")


def run_checked(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        fail(f"{' '.join(command[:3])} failed: {detail[:500]}")
    return completed.stdout.strip()


def selected_python_runtime() -> tuple[Path, Path, str]:
    """Resolve xcrun once outside Seatbelt; never execute xcrun while confined."""
    selected = Path(run_checked(["xcrun", "--find", "python3"])).resolve(strict=True)
    for prefix in RUNTIME_PREFIXES:
        try:
            selected.relative_to(prefix)
            runtime = selected.parents[1]
            return selected, runtime, hashlib.sha256(selected.read_bytes()).hexdigest()
        except ValueError:
            continue
    fail(f"xcrun selected an unapproved Python runtime: {selected}")


def require_outer_tools() -> tuple[Path, Path, str]:
    if sys.platform != "darwin":
        fail("this bootstrap currently supports macOS sandbox-exec only")
    if not Path("/usr/bin/sandbox-exec").is_file():
        fail("/usr/bin/sandbox-exec is unavailable")
    if not PROFILE.is_file():
        fail(f"profile is missing: {PROFILE}")
    return selected_python_runtime()


def tree_digest(root: Path) -> str:
    """Digest the extracted snapshot after any explicitly reviewed patch."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def create_snapshot(
    task_root: Path, *, revision: str, patch: Path | None, patch_sha256: str | None
) -> tuple[Path, str, str, str, str | None]:
    """Archive one committed revision, optionally applying a hash-bound patch."""
    commit = run_checked(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=REPO_ROOT)
    archive = task_root / "snapshot.tar"
    with archive.open("wb") as output:
        completed = subprocess.run(
            ["git", "archive", "--format=tar", commit],
            cwd=REPO_ROOT,
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT_SECONDS,
        )
    if completed.returncode:
        fail(f"git archive failed: {completed.stderr.decode(errors='replace')[:500]}")
    archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    snapshot = task_root / "snapshot"
    snapshot.mkdir(mode=0o700)
    with tarfile.open(archive) as tar:
        # This archive was created locally from HEAD, but retain a traversal
        # guard and support the macOS-provided Python 3.9 (which has no
        # TarFile.extractall(filter=...) argument).
        root = snapshot.resolve()
        for member in tar.getmembers():
            destination = (snapshot / member.name).resolve()
            if destination != root and root not in destination.parents:
                fail(f"git archive member escapes snapshot: {member.name!r}")
        tar.extractall(snapshot)
    applied_patch_digest: str | None = None
    if patch is not None:
        if not patch_sha256:
            fail("--patch requires --patch-sha256")
        patch_bytes = patch.read_bytes()
        applied_patch_digest = hashlib.sha256(patch_bytes).hexdigest()
        if applied_patch_digest.lower() != patch_sha256.lower():
            fail("supplied patch SHA-256 does not match --patch-sha256")
        run_checked(["git", "apply", "--check", "--whitespace=error", str(patch)], cwd=snapshot)
        run_checked(["git", "apply", "--whitespace=error", str(patch)], cwd=snapshot)
    return snapshot, commit, tree_digest(snapshot), archive_digest, applied_patch_digest


def private_environment(run_root: Path, port: int) -> dict[str, str]:
    """Do not inherit credentials, proxies, HOME, or arbitrary MATRX overrides."""
    user_root = run_root / "user"
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        # Keep the bootstrap deterministic and avoid an unnecessary entropy
        # device dependency inside the default-deny profile.
        "PYTHONHASHSEED": "0",
        "TMPDIR": str(run_root / "tmp"),
        "MATRX_ISOLATED_TEST": "1",
        "TEST_MODE": "1",
        "MATRX_HOME_DIR": str(run_root / "matrx-home"),
        "MATRX_TEMP_DIR": str(run_root / "temp"),
        "MATRX_DATA_DIR": str(run_root / "data"),
        "MATRX_CONFIG_DIR": str(run_root / "config"),
        "MATRX_LOG_DIR": str(run_root / "logs"),
        "LOG_DIR": str(run_root / "logs"),
        "MATRX_USER_DIR": str(user_root),
        "MATRX_NOTES_DIR": str(user_root / "Notes"),
        "MATRX_FILES_DIR": str(user_root / "Files"),
        "MATRX_CODE_DIR": str(user_root / "Code"),
        "MATRX_WORKSPACES_DIR": str(run_root / "workspaces"),
        "MATRX_AGENT_DATA_DIR": str(run_root / "agent-data"),
        "MATRX_PORT": str(port),
        "MATRX_PORT_BASE": str(port),
        "MATRX_SKIP_ORPHAN_SCAN": "1",
        "MATRX_INSTANCE_SALT": f"reliability-{run_root.name}",
        "MATRX_CLOUD_PARTICIPATION": "0",
        "TUNNEL_ENABLED": "0",
    }


def sandbox_command(
    *, snapshot: Path, run_root: Path, live_home: Path, python_runtime: Path, command: list[str]
) -> list[str]:
    python_bin_dir = Path(command[0]).parent
    return [
        "/usr/bin/sandbox-exec",
        "-D",
        f"SNAPSHOT={snapshot}",
        "-D",
        f"RUN_ROOT={run_root}",
        "-D",
        f"LIVE_HOME={live_home}",
        "-D",
        f"KEYCHAIN_DIR={live_home / 'Library' / 'Keychains'}",
        "-D",
        f"PYTHON_RUNTIME={python_runtime}",
        "-D",
        f"PYTHON_BIN_DIR={python_bin_dir}",
        "-D",
        f"PYTHON_RUNTIME_PARENT={python_runtime.parent}",
        "-D",
        f"PYTHON_RUNTIME_GRANDPARENT={python_runtime.parent.parent}",
        "-f",
        str(PROFILE),
        *command,
    ]


def run_sandboxed(
    command: list[str], *, cwd: Path, env: dict[str, str], capture_output: bool
) -> subprocess.CompletedProcess[str]:
    """Apply the timeout to the entire confined process group."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(command, TIMEOUT_SECONDS, stdout, stderr) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def receipt_context(context: dict[str, object]) -> dict[str, object]:
    return {key: str(value) if isinstance(value, Path) else value for key, value in context.items()}


def make_context(*, revision: str, patch: Path | None, patch_sha256: str | None) -> dict[str, object]:
    python, python_runtime, python_digest = require_outer_tools()
    # Seatbelt evaluates the canonical vnode path; /var is a /private/var
    # alias on macOS and would otherwise make an exact run-root rule miss.
    task_root = Path(tempfile.mkdtemp(prefix="matrx-reliability-isolation-")).resolve()
    task_root.chmod(0o700)
    run_root = task_root / "run"
    run_root.mkdir(mode=0o700)
    for name in ("tmp", "matrx-home", "temp", "data", "config", "logs", "user", "workspaces", "agent-data"):
        (run_root / name).mkdir(mode=0o700, exist_ok=True)
    snapshot, commit, digest, archive_digest, applied_patch_digest = create_snapshot(
        task_root, revision=revision, patch=patch, patch_sha256=patch_sha256
    )
    # 20-port blocks stay outside live and ordinary dev ranges.  This is an
    # identity marker only in the bootstrap because Seatbelt denies networking.
    port = 23000 + (int.from_bytes(os.urandom(2), "big") % 2100) * 20
    return {
        "task_root": task_root,
        "run_root": run_root,
        "snapshot": snapshot,
        "commit": commit,
        "snapshot_sha256": digest,
        "archive_sha256": archive_digest,
        "patch_sha256": applied_patch_digest,
        "port": port,
        "live_home": Path.home().resolve(),
        "python": python,
        "python_runtime": python_runtime,
        "python_sha256": python_digest,
        "profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
        "launcher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


CHILD_PROBE = """\
import errno
import json
import os
from pathlib import Path

target = Path(os.environ['ISO_DENIED_SIBLING'])
result = {'ran': True}
try:
    target.write_text('child', encoding='utf-8')
except OSError as exc:
    result.update(denied=exc.errno in {errno.EPERM, errno.EACCES}, errno=exc.errno, type=type(exc).__name__)
else:
    result.update(denied=False, errno=None, type=None)
print(json.dumps(result, sort_keys=True))
"""

PROBE = textwrap.dedent(
    f"""\
    import errno
    import json
    import os
    import socket
    import subprocess
    import sys
    from pathlib import Path

    root = Path(os.environ['ISO_RUN_ROOT'])
    sibling = Path(os.environ['ISO_DENIED_SIBLING'])
    alias = Path(os.environ['ISO_DENIED_ALIAS'])
    positive = root / 'positive-control.txt'
    positive.write_text('outer-boundary', encoding='utf-8')
    positive_ok = positive.read_text(encoding='utf-8') == 'outer-boundary'
    allowed_errnos = {{errno.EPERM, errno.EACCES}}

    def denied(action):
        try:
            action()
        except OSError as exc:
            return {{'denied': exc.errno in allowed_errnos, 'errno': exc.errno, 'type': type(exc).__name__}}
        return {{'denied': False, 'errno': None, 'type': None}}

    sibling_read = denied(lambda: sibling.read_text(encoding='utf-8'))
    sibling_write = denied(lambda: sibling.write_text('forbidden', encoding='utf-8'))
    alias_read = denied(lambda: alias.read_text(encoding='utf-8'))
    alias_write = denied(lambda: alias.write_text('forbidden-through-alias', encoding='utf-8'))

    def bind_loopback():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', 0))
        finally:
            sock.close()

    network = denied(bind_loopback)
    child = subprocess.run([sys.executable, '-c', {CHILD_PROBE!r}], capture_output=True, text=True, timeout=5)
    try:
        child_result = json.loads(child.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        child_result = {{'ran': False, 'denied': False, 'errno': None}}
    child_denied = child.returncode == 0 and child_result.get('ran') is True and child_result.get('denied') is True and child_result.get('errno') in {{errno.EPERM, errno.EACCES}}
    result = {{
        'positive_control': positive_ok,
        'sibling_read': sibling_read,
        'sibling_write': sibling_write,
        'alias_read': alias_read,
        'alias_write': alias_write,
        'network': network,
        'child_denied': child_denied,
        'child_returncode': child.returncode,
        'child_result': child_result,
    }}
    result['pass'] = positive_ok and sibling_read['denied'] and sibling_write['denied'] and alias_read['denied'] and alias_write['denied'] and network['denied'] and child_denied
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['pass'] else 1)
    """
)


def probe(context: dict[str, object]) -> int:
    task_root = context["task_root"]
    run_root = context["run_root"]
    assert isinstance(task_root, Path) and isinstance(run_root, Path)
    sibling = task_root / "denied-sibling"
    sibling.mkdir(mode=0o700)
    forbidden = sibling / "blocked.txt"
    original_forbidden_bytes = b"must-remain-unchanged"
    forbidden.write_bytes(original_forbidden_bytes)
    alias = run_root / "forbidden-sibling-alias"
    alias.symlink_to(forbidden)
    env = private_environment(run_root, int(context["port"]))
    env.update({"ISO_RUN_ROOT": str(run_root), "ISO_DENIED_SIBLING": str(forbidden), "ISO_DENIED_ALIAS": str(alias)})
    command = sandbox_command(
        snapshot=context["snapshot"], run_root=run_root, live_home=context["live_home"],
        python_runtime=context["python_runtime"], command=[str(context["python"]), "-I", "-c", PROBE],
    )
    try:
        completed = run_sandboxed(command, cwd=context["snapshot"], env=env, capture_output=True)
        payload = {
            "kind": "capability-probe",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "timestamp": int(time.time()),
            "forbidden_sibling_unchanged": forbidden.read_bytes() == original_forbidden_bytes,
            **receipt_context(context),
        }
    except subprocess.TimeoutExpired as exc:
        payload = {"kind": "capability-probe", "timeout": True, "stderr": str(exc), "forbidden_sibling_unchanged": forbidden.read_bytes() == original_forbidden_bytes, **receipt_context(context)}
    (task_root / "probe-result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(task_root / "probe-result.json"), "returncode": payload.get("returncode")}, sort_keys=True))
    return 0 if payload.get("returncode") == 0 else 1


def run_lane(context: dict[str, object], python_args: list[str]) -> int:
    if python_args[:1] == ["--"]:
        python_args = python_args[1:]
    if not python_args:
        fail("run requires arguments after --, for example: -- -c 'print(1)'")
    run_root = context["run_root"]
    assert isinstance(run_root, Path)
    env = private_environment(run_root, int(context["port"]))
    command = sandbox_command(
        snapshot=context["snapshot"], run_root=run_root, live_home=context["live_home"],
        python_runtime=context["python_runtime"], command=[str(context["python"]), "-I", *python_args],
    )
    try:
        completed = run_sandboxed(command, cwd=context["snapshot"], env=env, capture_output=False)
    except subprocess.TimeoutExpired:
        fail(f"lane exceeded {TIMEOUT_SECONDS}s and was terminated")
    print(json.dumps({"task_root": str(context["task_root"]), "returncode": completed.returncode}, sort_keys=True))
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="HEAD", help="explicit committed Git revision (default: HEAD)")
    parser.add_argument("--patch", type=Path, help="an explicitly reviewed patch to apply to the archive")
    parser.add_argument("--patch-sha256", help="SHA-256 required for --patch")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("probe", help="run only disposable positive and negative controls")
    lane = subcommands.add_parser("run", help="run a bounded pure-Python resolver/unit subprocess")
    lane.add_argument("python_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.patch_sha256 and not args.patch:
        fail("--patch-sha256 requires --patch")
    context = make_context(revision=args.revision, patch=args.patch, patch_sha256=args.patch_sha256)
    if args.command == "probe":
        return probe(context)
    return run_lane(context, args.python_args)


if __name__ == "__main__":
    raise SystemExit(main())
