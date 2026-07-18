"""Window management tools — list, focus, move, resize, minimize/maximize windows."""

from __future__ import annotations

import asyncio
import json
import logging

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.services.action_needed import os_permission_needed
from app.tools.session import ToolSession
from app.tools.tools import NO_GUI_MSG, has_display
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_ACCESSIBILITY_HINT = (
    "macOS Accessibility permission required.\n"
    "Go to: System Settings → Privacy & Security → Accessibility\n"
    "Add and enable the calling application (Terminal, or the Matrx Local app)."
)


def _check_applescript_error(stderr: bytes) -> str | None:
    """Return a friendly permission hint or None if error is not permission-related."""
    text = stderr.decode(errors="replace")
    if (
        "-1743" in text
        or "-25211" in text
        or "not authorized" in text.lower()
        or "assistive" in text.lower()
    ):
        return _ACCESSIBILITY_HINT
    return None


async def tool_list_windows(
    session: ToolSession,
    app_filter: str | None = None,
) -> ToolResult:
    """List all visible windows with their titles, positions, and sizes."""
    try:
        if PLATFORM["is_mac"]:
            return await _list_windows_macos(app_filter)
        elif PLATFORM["is_windows"]:
            return await _list_windows_windows(app_filter)
        elif not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        else:
            return await _list_windows_linux(app_filter)
    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Failed to list windows: {e}"
        )


async def _list_windows_macos(app_filter: str | None) -> ToolResult:
    script = """
tell application "System Events"
    set windowList to {}
    repeat with theApp in (every application process whose visible is true)
        set appName to name of theApp
        try
            repeat with theWindow in (every window of theApp)
                set winName to name of theWindow
                set winPos to position of theWindow
                set winSize to size of theWindow
                set end of windowList to appName & "|||" & winName & "|||" & (item 1 of winPos as text) & "," & (item 2 of winPos as text) & "|||" & (item 1 of winSize as text) & "," & (item 2 of winSize as text)
            end repeat
        end try
    end repeat
    set AppleScript's text item delimiters to "\\n"
    return windowList as text
end tell
"""
    proc = await asyncio.create_subprocess_exec(
        "osascript",
        "-e",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

    if proc.returncode != 0:
        friendly = _check_applescript_error(stderr)
        msg = (
            friendly or f"AppleScript error: {stderr.decode(errors='replace').strip()}"
        )
        return ToolResult(
            type=ToolResultType.ERROR,
            output=msg,
            action_needed=(
                os_permission_needed(
                    feature="Window management",
                    permission_key="accessibility",
                    source="tool.window-manager",
                )
                if friendly
                else None
            ),
        )

    windows = []
    for line in stdout.decode().strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|||")
        if len(parts) >= 4:
            app_name = parts[0].strip()
            if app_filter and app_filter.lower() not in app_name.lower():
                continue
            pos = parts[2].strip().split(",")
            size = parts[3].strip().split(",")
            windows.append(
                {
                    "app": app_name,
                    "title": parts[1].strip(),
                    "x": int(pos[0]) if pos[0].strip().lstrip("-").isdigit() else 0,
                    "y": int(pos[1]) if pos[1].strip().lstrip("-").isdigit() else 0,
                    "width": int(size[0]) if size[0].strip().isdigit() else 0,
                    "height": int(size[1]) if size[1].strip().isdigit() else 0,
                }
            )

    lines = [f"{'APP':<25} {'TITLE':<35} {'POS':>12} {'SIZE':>12}"]
    lines.append("-" * 90)
    for w in windows:
        lines.append(
            f"{w['app']:<25} {w['title'][:34]:<35} {w['x']:>5},{w['y']:<6} {w['width']:>5}x{w['height']:<5}"
        )

    return ToolResult(
        output=f"Windows ({len(windows)}):\n" + "\n".join(lines),
        metadata={"windows": windows, "count": len(windows)},
    )


async def _list_windows_windows(app_filter: str | None) -> ToolResult:
    ps_script = """
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class WindowLister {
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public static string GetWindows() {
        var results = new List<string>();
        EnumWindows((hWnd, lParam) => {
            if (!IsWindowVisible(hWnd)) return true;
            var sb = new StringBuilder(256);
            GetWindowText(hWnd, sb, 256);
            if (sb.Length == 0) return true;
            RECT rect; GetWindowRect(hWnd, out rect);
            uint pid; GetWindowThreadProcessId(hWnd, out pid);
            var proc = System.Diagnostics.Process.GetProcessById((int)pid);
            results.Add($"{proc.ProcessName}|||{sb}|||{rect.Left},{rect.Top}|||{rect.Right-rect.Left},{rect.Bottom-rect.Top}");
            return true;
        }, IntPtr.Zero);
        return string.Join("\\n", results);
    }
}
"@
[WindowLister]::GetWindows()
"""
    proc = await asyncio.create_subprocess_exec(
        CAPABILITIES["powershell_path"],
        "-Command",
        ps_script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)

    windows = []
    for line in stdout.decode().strip().split("\n"):
        parts = line.split("|||")
        if len(parts) >= 4:
            app_name = parts[0].strip()
            if app_filter and app_filter.lower() not in app_name.lower():
                continue
            pos = parts[2].split(",")
            size = parts[3].split(",")
            windows.append(
                {
                    "app": app_name,
                    "title": parts[1].strip(),
                    "x": int(pos[0]) if pos[0].strip().lstrip("-").isdigit() else 0,
                    "y": int(pos[1]) if pos[1].strip().lstrip("-").isdigit() else 0,
                    "width": int(size[0]) if size[0].strip().isdigit() else 0,
                    "height": int(size[1]) if size[1].strip().isdigit() else 0,
                }
            )

    lines = [f"{'APP':<25} {'TITLE':<35} {'POS':>12} {'SIZE':>12}"]
    lines.append("-" * 90)
    for w in windows:
        lines.append(
            f"{w['app']:<25} {w['title'][:34]:<35} {w['x']:>5},{w['y']:<6} {w['width']:>5}x{w['height']:<5}"
        )

    return ToolResult(
        output=f"Windows ({len(windows)}):\n" + "\n".join(lines),
        metadata={"windows": windows, "count": len(windows)},
    )


async def _list_windows_linux(app_filter: str | None) -> ToolResult:
    proc = await asyncio.create_subprocess_exec(
        "wmctrl",
        "-l",
        "-G",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

    if proc.returncode != 0:
        return ToolResult(
            type=ToolResultType.ERROR,
            output=f"wmctrl required on Linux: {stderr.decode()}",
        )

    windows = []
    for line in stdout.decode().strip().split("\n"):
        parts = line.split(None, 7)
        if len(parts) >= 8:
            title = parts[7]
            if app_filter and app_filter.lower() not in title.lower():
                continue
            windows.append(
                {
                    "app": title,
                    "title": title,
                    "x": int(parts[2]),
                    "y": int(parts[3]),
                    "width": int(parts[4]),
                    "height": int(parts[5]),
                }
            )

    lines = [f"{'TITLE':<50} {'POS':>12} {'SIZE':>12}"]
    lines.append("-" * 80)
    for w in windows:
        lines.append(
            f"{w['title'][:49]:<50} {w['x']:>5},{w['y']:<6} {w['width']:>5}x{w['height']:<5}"
        )

    return ToolResult(
        output=f"Windows ({len(windows)}):\n" + "\n".join(lines),
        metadata={"windows": windows, "count": len(windows)},
    )


async def tool_focus_window(
    session: ToolSession,
    app_name: str,
    window_title: str | None = None,
) -> ToolResult:
    """Focus/activate a specific window by app name and optional title."""
    try:
        if not PLATFORM["is_mac"] and not PLATFORM["is_windows"] and not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        if PLATFORM["is_mac"]:
            if window_title:
                script = f"""
tell application "System Events"
    tell application process "{app_name}"
        set frontmost to true
        repeat with w in windows
            if name of w contains "{window_title}" then
                perform action "AXRaise" of w
                return "Focused: " & name of w
            end if
        end repeat
        return "Window not found: {window_title}"
    end tell
end tell
"""
            else:
                script = f'tell application "{app_name}" to activate'

            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                friendly = _check_applescript_error(stderr)
                msg = friendly or stderr.decode(errors="replace").strip()
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=msg,
                    action_needed=(
                        os_permission_needed(
                            feature="Window focus",
                            permission_key="accessibility",
                            source="tool.window-manager",
                        )
                        if friendly
                        else None
                    ),
                )
            return ToolResult(output=stdout.decode().strip() or f"Focused: {app_name}")

        elif PLATFORM["is_windows"]:
            target = window_title or app_name
            ps_script = f"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class WinFocus {{
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}}
"@
$proc = Get-Process | Where-Object {{ $_.MainWindowTitle -like '*{target}*' }} | Select-Object -First 1
if ($proc) {{
    [WinFocus]::ShowWindow($proc.MainWindowHandle, 9)
    [WinFocus]::SetForegroundWindow($proc.MainWindowHandle)
    "Focused: $($proc.MainWindowTitle)"
}} else {{ "Window not found: {target}" }}
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=stdout.decode().strip())

        else:
            target = window_title or app_name
            proc = await asyncio.create_subprocess_exec(
                "wmctrl",
                "-a",
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=f"Focused: {target}")

    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Failed to focus window: {e}"
        )


async def tool_move_window(
    session: ToolSession,
    app_name: str,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> ToolResult:
    """Move and/or resize a window by app name."""
    try:
        if not PLATFORM["is_mac"] and not PLATFORM["is_windows"] and not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        if PLATFORM["is_mac"]:
            parts = []
            if x is not None and y is not None:
                parts.append(f"set position of window 1 to {{{x}, {y}}}")
            if width is not None and height is not None:
                parts.append(f"set size of window 1 to {{{width}, {height}}}")
            if not parts:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output="Provide x,y for position and/or width,height for size.",
                )

            script = f"""
tell application "System Events"
    tell application process "{app_name}"
        {chr(10).join(parts)}
    end tell
end tell
"""
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                friendly = _check_applescript_error(stderr)
                msg = friendly or stderr.decode(errors="replace").strip()
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=msg,
                    action_needed=(
                        os_permission_needed(
                            feature="Move and resize windows",
                            permission_key="accessibility",
                            source="tool.window-manager",
                        )
                        if friendly
                        else None
                    ),
                )
            return ToolResult(output=f"Moved/resized {app_name} window")

        elif PLATFORM["is_windows"]:
            ps_parts = []
            if x is not None:
                ps_parts.append(f"$x = {x}")
            if y is not None:
                ps_parts.append(f"$y = {y}")
            if width is not None:
                ps_parts.append(f"$w = {width}")
            if height is not None:
                ps_parts.append(f"$h = {height}")

            ps_script = f"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class WinMove {{
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [StructLayout(LayoutKind.Sequential)] public struct RECT {{ public int Left, Top, Right, Bottom; }}
}}
"@
$proc = Get-Process -Name '{app_name}' -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne [IntPtr]::Zero }} | Select-Object -First 1
if ($proc) {{
    $rect = New-Object WinMove+RECT
    [WinMove]::GetWindowRect($proc.MainWindowHandle, [ref]$rect)
    $x = {x if x is not None else "$rect.Left"}
    $y = {y if y is not None else "$rect.Top"}
    $w = {width if width is not None else "($rect.Right - $rect.Left)"}
    $h = {height if height is not None else "($rect.Bottom - $rect.Top)"}
    [WinMove]::MoveWindow($proc.MainWindowHandle, $x, $y, $w, $h, $true)
    "Moved: $($proc.ProcessName)"
}} else {{ "Process not found: {app_name}" }}
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=stdout.decode().strip())

        else:
            # Linux with wmctrl
            mvarg = f"0,{x or -1},{y or -1},{width or -1},{height or -1}"
            proc = await asyncio.create_subprocess_exec(
                "wmctrl",
                "-r",
                app_name,
                "-e",
                mvarg,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                friendly = _check_applescript_error(stderr)
                msg = friendly or stderr.decode(errors="replace").strip()
                return ToolResult(type=ToolResultType.ERROR, output=msg)
            return ToolResult(output=f"Moved/resized: {app_name}")

    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Failed to move window: {e}"
        )


async def tool_minimize_window(
    session: ToolSession,
    app_name: str,
    action: str = "minimize",
) -> ToolResult:
    """Minimize, maximize, or restore a window. Action: minimize, maximize, restore."""
    if action not in ("minimize", "maximize", "restore"):
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Action must be 'minimize', 'maximize', or 'restore'.",
        )

    try:
        if not PLATFORM["is_mac"] and not PLATFORM["is_windows"] and not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        if PLATFORM["is_mac"]:
            if action == "minimize":
                script = f"""
tell application "System Events"
    tell application process "{app_name}"
        try
            click (first button of window 1 whose subrole is "AXMinimizeButton")
        end try
    end tell
end tell
"""
            elif action == "maximize":
                script = f"""
tell application "System Events"
    tell application process "{app_name}"
        try
            click (first button of window 1 whose subrole is "AXFullScreenButton")
        end try
    end tell
end tell
"""
            else:
                script = f'tell application "{app_name}" to activate'

            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                friendly = _check_applescript_error(stderr)
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=friendly or stderr.decode(errors="replace").strip(),
                    action_needed=(
                        os_permission_needed(
                            feature="Window controls",
                            permission_key="accessibility",
                            source="tool.window-manager",
                        )
                        if friendly
                        else None
                    ),
                )
            return ToolResult(output=f"{action.capitalize()}d: {app_name}")

        elif PLATFORM["is_windows"]:
            show_cmd = {"minimize": 6, "maximize": 3, "restore": 9}[action]
            ps_script = f"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class WinState {{
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}}
"@
$proc = Get-Process -Name '{app_name}' -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne [IntPtr]::Zero }} | Select-Object -First 1
if ($proc) {{
    [WinState]::ShowWindow($proc.MainWindowHandle, {show_cmd})
    "{action.capitalize()}d: $($proc.ProcessName)"
}} else {{ "Process not found: {app_name}" }}
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=stdout.decode().strip())

        else:
            if action == "minimize":
                flag = "-b add,hidden"
            elif action == "maximize":
                flag = "-b add,maximized_vert,maximized_horz"
            else:
                flag = "-b remove,maximized_vert,maximized_horz,hidden"

            parts = flag.split()
            proc = await asyncio.create_subprocess_exec(
                "wmctrl",
                "-r",
                app_name,
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=f"{action.capitalize()}d: {app_name}")

    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Failed to {action} window: {e}"
        )
