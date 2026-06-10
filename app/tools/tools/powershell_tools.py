"""PowerShell tools — environment variables, registry, services, event log, Windows features.

These tools use PowerShell (powershell.exe on Windows, pwsh on macOS/Linux if installed).
All tools degrade gracefully when PowerShell is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)


def _powershell_exe() -> str | None:
    """Return the best available PowerShell executable, or None if not found."""
    return CAPABILITIES["powershell_path"]


def _ps_unavailable_error() -> ToolResult:
    msg = (
        "PowerShell is not available on this system.\n"
        "• Windows: PowerShell is built-in. If missing, reinstall via winget install Microsoft.PowerShell\n"
        "• macOS:   brew install --cask powershell\n"
        "• Linux:   https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-linux"
    )
    return ToolResult(type=ToolResultType.ERROR, output=msg)


def _ps_quote(val: str) -> str:
    """Escape a value for safe embedding inside a PowerShell single-quoted
    literal ('...').

    Within a single-quoted PowerShell string the only metacharacter is the
    single quote itself, escaped by doubling it. Doing this on every piece of
    user input prevents the quote-breakout command injection that raw
    f-string interpolation allowed (e.g. name="x'; rm-stuff; #").
    Callers MUST keep the surrounding quotes: f"...'{_ps_quote(v)}'...".
    """
    return str(val).replace("'", "''")


async def _run_ps(script: str, timeout: int = 30) -> tuple[str, str, int]:
    """Run a PowerShell script. Returns (stdout, stderr, returncode)."""
    exe = _powershell_exe()
    if exe is None:
        raise FileNotFoundError("No PowerShell executable found")

    proc = await asyncio.create_subprocess_exec(
        exe, "-NoProfile", "-NonInteractive", "-Command", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
        proc.returncode or 0,
    )


# ── Environment Variables ──────────────────────────────────────────────────────

async def tool_ps_get_env(
    session: ToolSession,
    name: str | None = None,
) -> ToolResult:
    """Read environment variables via PowerShell.

    If name is provided, returns the value of that specific variable.
    Otherwise returns all environment variables sorted alphabetically.

    Examples:
    - name="PATH" → returns the PATH variable value
    - name=None   → returns all variables
    """
    try:
        if name:
            script = f"$v = [System.Environment]::GetEnvironmentVariable('{_ps_quote(name)}'); if ($null -eq $v) {{ 'NOT SET' }} else {{ $v }}"
            stdout, stderr, rc = await _run_ps(script)
            if rc != 0:
                return ToolResult(type=ToolResultType.ERROR, output=f"PowerShell error: {stderr or stdout}")
            return ToolResult(
                output=f"{name} = {stdout}",
                metadata={"name": name, "value": stdout if stdout != "NOT SET" else None},
            )
        else:
            script = "Get-ChildItem Env: | Sort-Object Name | ForEach-Object { \"$($_.Name)=$($_.Value)\" }"
            stdout, stderr, rc = await _run_ps(script)
            if rc != 0:
                return ToolResult(type=ToolResultType.ERROR, output=f"PowerShell error: {stderr or stdout}")

            env_vars: dict[str, str] = {}
            lines = []
            for line in stdout.split("\n"):
                if "=" in line:
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip()
                    lines.append(f"  {k.strip()} = {v.strip()}")

            return ToolResult(
                output=f"Environment variables ({len(env_vars)}):\n" + "\n".join(lines),
                metadata={"variables": env_vars, "count": len(env_vars)},
            )
    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output="PowerShell timed out")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"GetEnv failed: {e}")


async def tool_ps_set_env(
    session: ToolSession,
    name: str,
    value: str,
    scope: str = "Process",
) -> ToolResult:
    """Set an environment variable via PowerShell.

    Scope options:
    - "Process"  — current process only (default, safe, not persistent)
    - "User"     — persists for the current user (Windows only)
    - "Machine"  — system-wide persistent (requires admin, Windows only)

    Note: Process scope only affects the engine process, not your terminal.
    """
    if not name:
        return ToolResult(type=ToolResultType.ERROR, output="Variable name must not be empty.")
    if scope not in ("Process", "User", "Machine"):
        return ToolResult(type=ToolResultType.ERROR, output="Scope must be Process, User, or Machine.")

    try:
        # Use SetEnvironmentVariable for every scope (incl. Process) so the
        # variable NAME is passed as a quoted string argument rather than
        # interpolated into a `$env:NAME` reference — the latter allowed
        # injection via a crafted name. All inputs are quote-escaped.
        if scope == "Process":
            script = (
                f"[System.Environment]::SetEnvironmentVariable("
                f"'{_ps_quote(name)}', '{_ps_quote(value)}', 'Process'); "
                f"\"Set {_ps_quote(name)} (Process scope)\""
            )
        else:
            if not PLATFORM["is_windows"]:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"Scope '{scope}' is only available on Windows. Use 'Process' on macOS/Linux.",
                )
            script = (
                f"[System.Environment]::SetEnvironmentVariable("
                f"'{_ps_quote(name)}', '{_ps_quote(value)}', '{_ps_quote(scope)}'); "
                f"\"Set {_ps_quote(name)} ({_ps_quote(scope)} scope)\""
            )

        stdout, stderr, rc = await _run_ps(script)
        if rc != 0:
            return ToolResult(type=ToolResultType.ERROR, output=f"PowerShell error: {stderr or stdout}")
        return ToolResult(
            output=stdout or f"Set {name} = {value} ({scope} scope)",
            metadata={"name": name, "value": value, "scope": scope},
        )
    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output="PowerShell timed out")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"SetEnv failed: {e}")


# ── Registry ───────────────────────────────────────────────────────────────────

async def tool_registry_read(
    session: ToolSession,
    key_path: str,
    value_name: str | None = None,
) -> ToolResult:
    """Read a Windows registry key or value (Windows only).

    key_path examples:
    - "HKLM:\\\\SOFTWARE\\\\Microsoft\\\\Windows NT\\\\CurrentVersion"
    - "HKCU:\\\\SOFTWARE\\\\MyApp"
    - "HKLM:\\\\SYSTEM\\\\CurrentControlSet\\\\Services\\\\Tcpip\\\\Parameters"

    If value_name is omitted, all values under the key are returned.
    """
    if not PLATFORM["is_windows"]:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Registry access is only available on Windows.",
        )
    try:
        if value_name:
            script = f"(Get-ItemProperty -Path '{_ps_quote(key_path)}' -Name '{_ps_quote(value_name)}' -ErrorAction Stop).'{_ps_quote(value_name)}'"
        else:
            script = f"Get-ItemProperty -Path '{_ps_quote(key_path)}' -ErrorAction Stop | ConvertTo-Json -Depth 2"

        stdout, stderr, rc = await _run_ps(script, timeout=15)
        if rc != 0:
            return ToolResult(type=ToolResultType.ERROR, output=f"Registry error: {stderr or stdout}")

        # Try to parse as JSON for structured output
        try:
            data = json.loads(stdout)
            lines = [f"Registry key: {key_path}"]
            if isinstance(data, dict):
                for k, v in data.items():
                    if not k.startswith("PS"):  # Skip PowerShell metadata
                        lines.append(f"  {k} = {v}")
            return ToolResult(
                output="\n".join(lines),
                metadata={"key_path": key_path, "values": data},
            )
        except json.JSONDecodeError:
            return ToolResult(
                output=f"Registry key: {key_path}\n  {value_name} = {stdout}",
                metadata={"key_path": key_path, "value_name": value_name, "value": stdout},
            )
    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output="PowerShell timed out")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"RegistryRead failed: {e}")


async def tool_registry_write(
    session: ToolSession,
    key_path: str,
    value_name: str,
    value: str,
    value_type: str = "String",
) -> ToolResult:
    """Write a value to the Windows registry (Windows only, use with caution).

    value_type options: String, DWord, QWord, Binary, MultiString, ExpandString

    key_path examples:
    - "HKCU:\\\\SOFTWARE\\\\MyApp"  (current user — no admin required)
    - "HKLM:\\\\SOFTWARE\\\\MyApp"  (machine-wide — requires admin)

    WARNING: Modifying system registry keys can break Windows. Prefer HKCU paths.
    """
    if not PLATFORM["is_windows"]:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Registry access is only available on Windows.",
        )

    allowed_types = {"String", "DWord", "QWord", "Binary", "MultiString", "ExpandString"}
    if value_type not in allowed_types:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"value_type must be one of: {', '.join(sorted(allowed_types))}",
        )

    try:
        kp = _ps_quote(key_path)
        vn = _ps_quote(value_name)
        vv = _ps_quote(value)
        # value_type is allow-listed above, safe to interpolate bare.
        script = f"""
if (-not (Test-Path '{kp}')) {{
    New-Item -Path '{kp}' -Force | Out-Null
}}
Set-ItemProperty -Path '{kp}' -Name '{vn}' -Value '{vv}' -Type {value_type} -ErrorAction Stop
"Written: {vn} ({value_type})"
"""
        stdout, stderr, rc = await _run_ps(script, timeout=15)
        if rc != 0:
            return ToolResult(type=ToolResultType.ERROR, output=f"Registry write error: {stderr or stdout}")
        return ToolResult(
            output=stdout or f"Written: {key_path}\\{value_name} = {value}",
            metadata={"key_path": key_path, "value_name": value_name, "value": value, "value_type": value_type},
        )
    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output="PowerShell timed out")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"RegistryWrite failed: {e}")


# ── Windows Services ──────────────────────────────────────────────────────────

async def tool_service_list(
    session: ToolSession,
    filter: str | None = None,
    status: str | None = None,
) -> ToolResult:
    """List Windows/system services with their status (Windows/macOS/Linux).

    filter: filter by service name (case-insensitive substring)
    status: filter by status — "running", "stopped", "paused" (Windows only)

    On Windows: uses Get-Service (PowerShell)
    On macOS:   uses launchctl list
    On Linux:   uses systemctl list-units
    """
    try:
        if PLATFORM["is_windows"]:
            status_filter = ""
            if status:
                status_map = {"running": "Running", "stopped": "Stopped", "paused": "Paused"}
                ps_status = status_map.get(status.lower(), status)
                status_filter = f" | Where-Object {{ $_.Status -eq '{_ps_quote(ps_status)}' }}"

            name_filter = ""
            if filter:
                f = _ps_quote(filter)
                name_filter = f" | Where-Object {{ $_.Name -like '*{f}*' -or $_.DisplayName -like '*{f}*' }}"

            script = f"""
Get-Service{status_filter}{name_filter} | Sort-Object Status, DisplayName |
Select-Object Name, DisplayName, Status, StartType |
ForEach-Object {{ "$($_.Status)|||$($_.Name)|||$($_.DisplayName)|||$($_.StartType)" }}
"""
            stdout, stderr, rc = await _run_ps(script)
            if rc != 0:
                return ToolResult(type=ToolResultType.ERROR, output=f"Get-Service error: {stderr or stdout}")

            services = []
            for line in stdout.split("\n"):
                parts = line.split("|||")
                if len(parts) >= 3:
                    services.append({
                        "status": parts[0].strip(),
                        "name": parts[1].strip(),
                        "display_name": parts[2].strip(),
                        "start_type": parts[3].strip() if len(parts) > 3 else "",
                    })

            lines = [f"Services ({len(services)} found):"]
            lines.append(f"{'STATUS':<10} {'START':<10} NAME")
            lines.append("-" * 70)
            for s in services:
                status_icon = "●" if s["status"] == "Running" else "○"
                lines.append(f"{status_icon} {s['status']:<9} {s['start_type']:<10} {s['name']}  ({s['display_name']})")

            return ToolResult(
                output="\n".join(lines),
                metadata={"services": services, "count": len(services)},
            )

        elif PLATFORM["is_mac"]:
            proc = await asyncio.create_subprocess_exec(
                "launchctl", "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = stdout_b.decode()
            if filter:
                lines_out = [l for l in output.split("\n") if not filter or filter.lower() in l.lower()]
                output = "\n".join(lines_out)
            return ToolResult(output=f"macOS services (launchctl):\n{output[:5000]}")

        else:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-units", "--type=service", "--no-pager",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = stdout_b.decode()
            if filter:
                filtered = [l for l in output.split("\n") if not filter or filter.lower() in l.lower()]
                output = "\n".join(filtered)
            return ToolResult(output=f"System services (systemctl):\n{output[:5000]}")

    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output="Service list timed out")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"ServiceList failed: {e}")


async def tool_service_control(
    session: ToolSession,
    name: str,
    action: str,
) -> ToolResult:
    """Start, stop, or restart a system service.

    action: "start", "stop", "restart", "pause", "resume"

    On Windows: uses PowerShell Start-Service / Stop-Service / Restart-Service
    On macOS:   uses launchctl start/stop (service name must be the label)
    On Linux:   uses systemctl start/stop/restart

    Requires appropriate permissions. Windows may require elevated privileges.
    """
    valid_actions = {"start", "stop", "restart", "pause", "resume"}
    if action not in valid_actions:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"action must be one of: {', '.join(sorted(valid_actions))}",
        )

    try:
        if PLATFORM["is_windows"]:
            ps_cmd = {
                "start": "Start-Service",
                "stop": "Stop-Service",
                "restart": "Restart-Service",
                "pause": "Suspend-Service",
                "resume": "Resume-Service",
            }[action]
            script = f"{ps_cmd} -Name '{_ps_quote(name)}' -ErrorAction Stop -PassThru | Select-Object -ExpandProperty Status"
            stdout, stderr, rc = await _run_ps(script, timeout=30)
            if rc != 0:
                return ToolResult(type=ToolResultType.ERROR, output=f"Service {action} failed: {stderr or stdout}")
            return ToolResult(
                output=f"Service '{name}' {action}ed. New status: {stdout}",
                metadata={"service": name, "action": action, "status": stdout},
            )

        elif PLATFORM["is_mac"]:
            lc_action = "start" if action in ("start", "resume") else "stop"
            proc = await asyncio.create_subprocess_exec(
                "launchctl", lc_action, name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return ToolResult(type=ToolResultType.ERROR, output=f"launchctl {lc_action} failed: {stderr_b.decode()}")
            return ToolResult(output=f"Service '{name}' {action}ed.")

        else:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", action, name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return ToolResult(type=ToolResultType.ERROR, output=f"systemctl {action} failed: {stderr_b.decode()}")
            return ToolResult(output=f"Service '{name}' {action}ed.")

    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output=f"Service {action} timed out")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"ServiceControl failed: {e}")


# ── Event Log ─────────────────────────────────────────────────────────────────

async def tool_event_log(
    session: ToolSession,
    log_name: str = "System",
    level: str = "Error",
    count: int = 20,
    source: str | None = None,
) -> ToolResult:
    """Read Windows Event Log entries (Windows only).

    log_name: "System", "Application", "Security", or a custom log name
    level:    "Error", "Warning", "Information", "Critical" (or "All" for no filter)
    count:    number of most recent entries to return (max 200)
    source:   filter by event source/provider name

    Returns timestamp, level, event ID, source, and message for each entry.
    """
    if not PLATFORM["is_windows"]:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "Windows Event Log is only available on Windows.\n"
                "On Linux, try: journalctl -n 50 --no-pager\n"
                "On macOS, try: log show --last 1h --style compact"
            ),
        )

    count = min(max(1, count), 200)
    valid_levels = {"Error", "Warning", "Information", "Critical", "All"}
    if level not in valid_levels:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"level must be one of: {', '.join(sorted(valid_levels))}",
        )

    try:
        level_filter = "" if level == "All" else f" -Level @{{Label='{level}';Value=([System.Diagnostics.Eventing.Reader.StandardEventLevel]::'{level}'.value__)}}"
        # Map level names to numeric IDs for Get-WinEvent
        level_id_map = {"Critical": 1, "Error": 2, "Warning": 3, "Information": 4}
        level_filter_part = ""
        if level != "All" and level in level_id_map:
            level_filter_part = f" | Where-Object {{ $_.Level -eq {level_id_map[level]} }}"

        source_filter = ""
        if source:
            source_filter = f" | Where-Object {{ $_.ProviderName -like '*{_ps_quote(source)}*' }}"

        script = f"""
try {{
    Get-WinEvent -LogName '{_ps_quote(log_name)}' -MaxEvents {count * 3} -ErrorAction Stop{level_filter_part}{source_filter} |
    Select-Object -First {count} |
    ForEach-Object {{
        $ts = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
        $msg = ($_.Message -replace '\\r|\\n', ' ') -replace '\\s+', ' '
        if ($msg.Length -gt 200) {{ $msg = $msg.Substring(0, 200) + '...' }}
        "$ts|||$($_.LevelDisplayName)|||$($_.Id)|||$($_.ProviderName)|||$msg"
    }}
}} catch [System.Exception] {{
    "ERROR: $_"
}}
"""
        stdout, stderr, rc = await _run_ps(script, timeout=30)

        if stdout.startswith("ERROR:"):
            return ToolResult(type=ToolResultType.ERROR, output=f"Event log error: {stdout[7:]}")

        entries = []
        for line in stdout.split("\n"):
            parts = line.split("|||")
            if len(parts) >= 5:
                entries.append({
                    "timestamp": parts[0].strip(),
                    "level": parts[1].strip(),
                    "event_id": parts[2].strip(),
                    "source": parts[3].strip(),
                    "message": parts[4].strip(),
                })

        lines = [f"Event Log: {log_name} — {len(entries)} {level} entries"]
        lines.append("-" * 90)
        for e in entries:
            lines.append(f"[{e['timestamp']}] {e['level']:11} ID:{e['event_id']:>5}  {e['source']}")
            lines.append(f"  {e['message']}")
            lines.append("")

        return ToolResult(
            output="\n".join(lines),
            metadata={"entries": entries, "log_name": log_name, "level": level, "count": len(entries)},
        )
    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output="Event log query timed out")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"EventLog failed: {e}")


# ── Windows Features ──────────────────────────────────────────────────────────

async def tool_windows_features(
    session: ToolSession,
    filter: str | None = None,
    installed_only: bool = True,
) -> ToolResult:
    """List Windows optional features and capabilities (Windows only).

    filter:         filter by feature name (case-insensitive substring)
    installed_only: if True (default), only show installed/enabled features

    Returns feature name and state (Enabled/Disabled/NotPresent).
    """
    if not PLATFORM["is_windows"]:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=(
                "Windows Features is only available on Windows.\n"
                "On Linux, try: dpkg --list  or  apt list --installed\n"
                "On macOS, try: system_profiler SPSoftwareDataType"
            ),
        )

    try:
        state_filter = " | Where-Object { $_.State -eq 'Enabled' }" if installed_only else ""
        name_filter = f" | Where-Object {{ $_.FeatureName -like '*{_ps_quote(filter)}*' }}" if filter else ""

        script = f"""
Get-WindowsOptionalFeature -Online -ErrorAction Stop{name_filter}{state_filter} |
Sort-Object FeatureName |
ForEach-Object {{ "$($_.FeatureName)|||$($_.State)" }}
"""
        stdout, stderr, rc = await _run_ps(script, timeout=60)
        if rc != 0:
            # Fallback to Get-WindowsCapability
            script2 = f"""
Get-WindowsCapability -Online -ErrorAction Stop{name_filter} |
Where-Object {{ $_.State -eq 'Installed' -or -not ${installed_only} }} |
Sort-Object Name |
ForEach-Object {{ "$($_.Name)|||$($_.State)" }}
"""
            stdout, stderr, rc = await _run_ps(script2, timeout=60)
            if rc != 0:
                return ToolResult(type=ToolResultType.ERROR, output=f"Feature query failed: {stderr or stdout}")

        features = []
        for line in stdout.split("\n"):
            parts = line.split("|||")
            if len(parts) >= 2:
                features.append({
                    "name": parts[0].strip(),
                    "state": parts[1].strip(),
                })

        lines = [f"Windows Features ({len(features)} {'installed' if installed_only else 'total'}):"]
        lines.append("")
        for f in features:
            state_icon = "✓" if f["state"] in ("Enabled", "Installed") else "○"
            lines.append(f"  {state_icon} {f['name']}  [{f['state']}]")

        return ToolResult(
            output="\n".join(lines),
            metadata={"features": features, "count": len(features)},
        )
    except FileNotFoundError:
        return _ps_unavailable_error()
    except asyncio.TimeoutError:
        return ToolResult(type=ToolResultType.ERROR, output="Feature query timed out (this can take 30-60s on some systems)")
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"WindowsFeatures failed: {e}")
