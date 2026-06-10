"""Execution tools — Shell (foreground + background), BashOutput, TaskStop.

Fully cross-platform: uses bash/zsh on macOS/Linux, PowerShell on Windows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.tools.session import BackgroundShell, ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

MAX_OUTPUT_LENGTH = 30_000
DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000
CWD_SENTINEL = "___MATRX_CWD_SENTINEL_9f8a7b___"

def _get_shell() -> str:
    if PLATFORM["is_windows"]:
        return CAPABILITIES["powershell_path"]
    return CAPABILITIES["shell_path"] or "/bin/sh"


def _quote(value: str) -> str:
    if PLATFORM["is_windows"]:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    import shlex
    return shlex.quote(value)


def _wrap_foreground(command: str, cwd: str) -> str:
    if PLATFORM["is_windows"]:
        return (
            f"Set-Location -LiteralPath {_quote(cwd)}; "
            f"$ErrorActionPreference='Continue'; "
            f"{command}; "
            f"$__ec = $LASTEXITCODE; "
            f"Write-Output '{CWD_SENTINEL}'; "
            f"(Get-Location).Path; "
            f"exit $__ec"
        )
    return (
        f"cd {_quote(cwd)} && "
        f"{{ {command} ; }}; "
        f"__exit_code=$?; "
        f'echo "{CWD_SENTINEL}"; '
        f"pwd; "
        f"exit $__exit_code"
    )


def _wrap_background(command: str, cwd: str) -> str:
    if PLATFORM["is_windows"]:
        return f"Set-Location -LiteralPath {_quote(cwd)}; {command}"
    return f"cd {_quote(cwd)} && {{ {command} ; }}"


# Substrings that mark an env var as a secret we must NOT leak into shell
# subprocesses. The engine loads the user's AI provider API keys into
# os.environ (app/services/ai/key_manager.py) so matrx-ai can read them; if we
# inherited the full environment into every shell tool, any command the caller
# runs (`env`, `printenv`, `set`) would dump every provider key. Strip them.
_SECRET_ENV_SUBSTRINGS = (
    "API_KEY", "APIKEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
    "PRIVATE_KEY", "ACCESS_KEY", "SESSION_KEY", "CREDENTIAL",
    "HF_TOKEN", "HUGGING_FACE", "_KEY",
)


def _is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_ENV_SUBSTRINGS)


def _shell_env() -> dict[str, str]:
    # Filter out secret-bearing variables so shell tools can't exfiltrate the
    # user's provider API keys / tokens via the process environment.
    env = {k: v for k, v in os.environ.items() if not _is_secret_env_name(k)}
    if PLATFORM["is_windows"]:
        env.setdefault("USERPROFILE", os.path.expanduser("~"))
    else:
        env.setdefault("HOME", os.path.expanduser("~"))
        env.setdefault("USER", os.getenv("USER", "user"))
    return env


async def tool_bash(
    session: ToolSession,
    command: str,
    description: str | None = None,
    timeout: int | None = None,
    run_in_background: bool = False,
) -> ToolResult:
    if not command or not command.strip():
        return ToolResult(type=ToolResultType.ERROR, output="Command must not be empty.")

    timeout_ms = min(timeout or DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS)
    timeout_s = timeout_ms / 1000.0

    if run_in_background:
        return await _bash_background(session, command)

    return await _bash_foreground(session, command, timeout_s)


async def _launch_shell(cmd: str) -> asyncio.subprocess.Process:
    """Launch a shell subprocess in a cross-platform way.

    On Windows, asyncio.create_subprocess_shell does not accept the
    `executable` kwarg reliably; we use create_subprocess_exec with PowerShell
    directly instead.  On POSIX we keep the simpler create_subprocess_shell +
    executable approach which has always worked.
    """
    if PLATFORM["is_windows"]:
        shell_path = _get_shell()
        return await asyncio.create_subprocess_exec(
            shell_path, "-NoProfile", "-NonInteractive", "-Command", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_shell_env(),
        )
    return await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        executable=_get_shell(),
        env=_shell_env(),
    )


async def _bash_foreground(session: ToolSession, command: str, timeout_s: float) -> ToolResult:
    wrapped = _wrap_foreground(command, session.cwd)

    proc = await _launch_shell(wrapped)

    timed_out = False
    try:
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            stdout_bytes = b""

    raw_output = stdout_bytes.decode("utf-8", errors="replace")
    output, new_cwd = _parse_cwd_sentinel(raw_output)
    if new_cwd:
        session.cwd = new_cwd

    if timed_out:
        output = _truncate(output)
        output += f"\n\n[Command timed out after {timeout_s:.0f}s]"
        return ToolResult(output=output)

    output = _truncate(output)
    exit_code = proc.returncode or 0
    if exit_code != 0:
        output += f"\n\n[Exit code: {exit_code}]"

    return ToolResult(output=output, metadata={"cwd": session.cwd})


async def _bash_background(session: ToolSession, command: str) -> ToolResult:
    shell_id = session.next_shell_id()
    wrapped = _wrap_background(command, session.cwd)

    proc = await _launch_shell(wrapped)

    shell = BackgroundShell(shell_id=shell_id, process=proc)
    session.background_shells[shell_id] = shell
    asyncio.create_task(_collect_background_output(shell))

    return ToolResult(
        output=f"Started background command (shell_id: {shell_id}). Use BashOutput to check on it.",
        metadata={"bash_id": shell_id},
    )


async def _collect_background_output(shell: BackgroundShell) -> None:
    assert shell.process.stdout is not None
    try:
        while True:
            line = await shell.process.stdout.readline()
            if not line:
                break
            shell.output_buffer.append(line.decode("utf-8", errors="replace"))
    except Exception as e:
        shell.output_buffer.append(f"\n[Error reading output: {e}]\n")
    finally:
        try:
            shell.return_code = await asyncio.wait_for(shell.process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            shell.return_code = -1
        shell.is_complete = True


async def tool_bash_output(
    session: ToolSession,
    bash_id: str,
    filter: str | None = None,
) -> ToolResult:
    shell = session.background_shells.get(bash_id)
    if shell is None:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"No background shell found with id: {bash_id}",
        )

    new_lines = shell.output_buffer[shell.read_offset:]

    if filter:
        try:
            pattern = re.compile(filter)
        except re.error as e:
            return ToolResult(type=ToolResultType.ERROR, output=f"Invalid filter regex: {e}")
        matched = [line for line in new_lines if pattern.search(line)]
        shell.read_offset = len(shell.output_buffer)
        output_lines = matched
    else:
        shell.read_offset = len(shell.output_buffer)
        output_lines = new_lines

    output = "".join(output_lines)
    status = "completed" if shell.is_complete else "running"
    status_line = f"\n[Shell {bash_id}: {status}"
    if shell.is_complete and shell.return_code is not None:
        status_line += f", exit code: {shell.return_code}"
    status_line += "]"

    return ToolResult(output=output + status_line)


async def tool_task_stop(
    session: ToolSession,
    task_id: str,
) -> ToolResult:
    shell = session.background_shells.get(task_id)
    if shell is None:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"No background task found with id: {task_id}",
        )

    if shell.is_complete:
        return ToolResult(output=f"Task {task_id} already completed (exit code: {shell.return_code}).")

    try:
        shell.process.kill()
        await asyncio.wait_for(shell.process.wait(), timeout=10.0)
    except (ProcessLookupError, asyncio.TimeoutError):
        pass

    shell.is_complete = True
    shell.return_code = -9

    return ToolResult(output=f"Task {task_id} has been stopped.")


def _parse_cwd_sentinel(raw: str) -> tuple[str, str | None]:
    idx = raw.rfind(CWD_SENTINEL)
    if idx == -1:
        return raw, None
    output = raw[:idx].rstrip("\n")
    after = raw[idx + len(CWD_SENTINEL):].strip()
    lines = after.split("\n")
    new_cwd = lines[0].strip() if lines else None
    return output, new_cwd


def _truncate(output: str) -> str:
    if len(output) > MAX_OUTPUT_LENGTH:
        return output[:MAX_OUTPUT_LENGTH] + "\n\n... [output truncated at 30000 characters]"
    return output
