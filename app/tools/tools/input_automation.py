"""Input automation tools — keyboard and mouse control for desktop automation."""

from __future__ import annotations

import asyncio
import logging

from app.common.platform_ctx import CAPABILITIES, PLATFORM
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
    """Return a friendly error string if stderr contains a known macOS permission error.

    Returns None if the error is not permission-related (caller should
    include the raw stderr text instead).
    """
    text = stderr.decode(errors="replace")
    # -1743 = not authorised to send Apple Events
    # -25211 = AXUIElement access denied
    # -600 = application not running (rare but can appear)
    if (
        "-1743" in text
        or "-25211" in text
        or "not authorized" in text.lower()
        or "assistive" in text.lower()
    ):
        return _ACCESSIBILITY_HINT
    return None


async def tool_type_text(
    session: ToolSession,
    text: str,
    delay_ms: int = 50,
    app_name: str | None = None,
) -> ToolResult:
    """Type text string via simulated keystrokes. Optionally target a specific app."""
    if not text:
        return ToolResult(type=ToolResultType.ERROR, output="Text must not be empty.")

    try:
        if not PLATFORM["is_mac"] and not PLATFORM["is_windows"] and not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        if PLATFORM["is_mac"]:
            # Escape for AppleScript
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            if app_name:
                script = f"""
tell application "{app_name}" to activate
delay 0.3
tell application "System Events"
    keystroke "{escaped}"
end tell
"""
            else:
                script = f"""
tell application "System Events"
    keystroke "{escaped}"
end tell
"""
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                friendly = _check_applescript_error(stderr)
                msg = (
                    friendly or f"AppleScript error: {stderr.decode(errors='replace')}"
                )
                return ToolResult(type=ToolResultType.ERROR, output=msg)
            return ToolResult(
                output=f"Typed {len(text)} characters"
                + (f" into {app_name}" if app_name else "")
            )

        elif PLATFORM["is_windows"]:
            escaped = text.replace("'", "''")
            ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{escaped}')
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            return ToolResult(output=f"Typed {len(text)} characters")

        else:
            # Linux: xdotool
            proc = await asyncio.create_subprocess_exec(
                "xdotool",
                "type",
                "--delay",
                str(delay_ms),
                text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"xdotool error (install: sudo apt install xdotool): {stderr.decode()}",
                )
            return ToolResult(output=f"Typed {len(text)} characters")

    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Failed to type text: {e}")


async def tool_hotkey(
    session: ToolSession,
    keys: str,
    app_name: str | None = None,
) -> ToolResult:
    """Send a keyboard shortcut. Format: modifier+key (e.g., 'cmd+c', 'ctrl+shift+s', 'alt+tab').

    Modifiers: cmd/command, ctrl/control, alt/option, shift
    """
    if not keys:
        return ToolResult(type=ToolResultType.ERROR, output="Keys must not be empty.")

    parts = [k.strip().lower() for k in keys.split("+")]
    if len(parts) < 2:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Format: modifier+key (e.g., 'cmd+c', 'ctrl+shift+s')",
        )

    try:
        if not PLATFORM["is_mac"] and not PLATFORM["is_windows"] and not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        if PLATFORM["is_mac"]:
            modifiers = []
            key = parts[-1]
            for mod in parts[:-1]:
                if mod in ("cmd", "command"):
                    modifiers.append("command down")
                elif mod in ("ctrl", "control"):
                    modifiers.append("control down")
                elif mod in ("alt", "option"):
                    modifiers.append("option down")
                elif mod == "shift":
                    modifiers.append("shift down")

            mod_str = ", ".join(modifiers)

            # Map special key names
            special_keys = {
                "tab": "tab",
                "return": "return",
                "enter": "return",
                "escape": "escape",
                "esc": "escape",
                "space": "space",
                "delete": "delete",
                "backspace": "delete",
                "up": "up arrow",
                "down": "down arrow",
                "left": "left arrow",
                "right": "right arrow",
                "f1": "F1",
                "f2": "F2",
                "f3": "F3",
                "f4": "F4",
                "f5": "F5",
                "f6": "F6",
                "f7": "F7",
                "f8": "F8",
                "f9": "F9",
                "f10": "F10",
                "f11": "F11",
                "f12": "F12",
            }

            if key in special_keys:
                keystroke = (
                    f'key code (key code of "{special_keys[key]}") using {{{mod_str}}}'
                )
                # Use simpler approach for special keys
                script_body = f'keystroke "" using {{{mod_str}}}'
                # Actually, for special keys in AppleScript:
                script_body = f"key code {_macos_keycode(key)} using {{{mod_str}}}"
            else:
                script_body = f'keystroke "{key}" using {{{mod_str}}}'

            if app_name:
                script = f"""
tell application "{app_name}" to activate
delay 0.2
tell application "System Events"
    {script_body}
end tell
"""
            else:
                script = f"""
tell application "System Events"
    {script_body}
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
                msg = (
                    friendly or f"AppleScript error: {stderr.decode(errors='replace')}"
                )
                return ToolResult(type=ToolResultType.ERROR, output=msg)
            return ToolResult(
                output=f"Sent hotkey: {keys}" + (f" to {app_name}" if app_name else "")
            )

        elif PLATFORM["is_windows"]:
            # Map to SendKeys format
            key_map = {
                "ctrl": "^",
                "control": "^",
                "alt": "%",
                "option": "%",
                "shift": "+",
                "cmd": "^",
                "command": "^",  # Map cmd to ctrl on Windows
            }
            send_keys = ""
            for mod in parts[:-1]:
                send_keys += key_map.get(mod, "")

            special = {
                "enter": "{ENTER}",
                "return": "{ENTER}",
                "tab": "{TAB}",
                "escape": "{ESC}",
                "esc": "{ESC}",
                "space": " ",
                "delete": "{DELETE}",
                "backspace": "{BACKSPACE}",
                "up": "{UP}",
                "down": "{DOWN}",
                "left": "{LEFT}",
                "right": "{RIGHT}",
                "f1": "{F1}",
                "f2": "{F2}",
                "f3": "{F3}",
                "f4": "{F4}",
            }
            key = parts[-1]
            send_keys += special.get(key, key)

            escaped = send_keys.replace("'", "''")
            ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{escaped}')
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=f"Sent hotkey: {keys}")

        else:
            # Linux: xdotool
            xdo_parts = []
            for part in parts[:-1]:
                mod_map = {
                    "ctrl": "ctrl",
                    "control": "ctrl",
                    "alt": "alt",
                    "option": "alt",
                    "shift": "shift",
                    "cmd": "super",
                    "command": "super",
                }
                xdo_parts.append(mod_map.get(part, part))
            xdo_parts.append(parts[-1])

            proc = await asyncio.create_subprocess_exec(
                "xdotool",
                "key",
                "+".join(xdo_parts),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"xdotool error: {stderr.decode()}",
                )
            return ToolResult(output=f"Sent hotkey: {keys}")

    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Failed to send hotkey: {e}"
        )


def _macos_keycode(key: str) -> int:
    """Map key names to macOS virtual key codes."""
    codes = {
        "return": 36,
        "enter": 36,
        "tab": 48,
        "space": 49,
        "delete": 51,
        "backspace": 51,
        "escape": 53,
        "esc": 53,
        "up": 126,
        "down": 125,
        "left": 123,
        "right": 124,
        "f1": 122,
        "f2": 120,
        "f3": 99,
        "f4": 118,
        "f5": 96,
        "f6": 97,
        "f7": 98,
        "f8": 100,
        "f9": 101,
        "f10": 109,
        "f11": 103,
        "f12": 111,
    }
    return codes.get(key, 0)


async def tool_mouse_click(
    session: ToolSession,
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    app_name: str | None = None,
) -> ToolResult:
    """Click the mouse at screen coordinates. Button: left, right, middle."""
    if button not in ("left", "right", "middle"):
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Button must be 'left', 'right', or 'middle'.",
        )

    try:
        if not PLATFORM["is_mac"] and not PLATFORM["is_windows"] and not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        if PLATFORM["is_mac"]:
            if app_name:
                activate = f'tell application "{app_name}" to activate\ndelay 0.3\n'
            else:
                activate = ""

            # cliclick verbs: c=left click, dc=double, tc=triple, rc=right.
            # The old code computed a click_type and never used it — every
            # click was a single LEFT click regardless of button/clicks.
            if button == "right":
                verb = "rc"
            elif button == "middle":
                # cliclick has no middle-click verb; surface that honestly.
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output="Middle-click is not supported on macOS (cliclick has no middle-button verb).",
                )
            elif clicks >= 3:
                verb = "tc"
            elif clicks == 2:
                verb = "dc"
            else:
                verb = "c"
            # Use cliclick if available, otherwise AppleScript approach
            script = f"""
{activate}
do shell script "if command -v cliclick >/dev/null; then cliclick {verb}:{x},{y}; else echo 'no_cliclick'; fi"
"""
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = stdout.decode().strip()

            if "no_cliclick" in output:
                # Fallback: use Python via subprocess
                py_script = f"""
import Quartz
point = Quartz.CGPointMake({x}, {y})
event_down = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, point, Quartz.kCGMouseButtonLeft)
event_up = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, point, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_down)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, event_up)
"""
                proc2 = await asyncio.create_subprocess_exec(
                    "python3",
                    "-c",
                    py_script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=10)
                if proc2.returncode != 0:
                    return ToolResult(
                        output=f"Clicked at ({x}, {y}) — note: install 'cliclick' for reliable mouse control on macOS.",
                    )

            return ToolResult(output=f"Clicked {button} at ({x}, {y})")

        elif PLATFORM["is_windows"]:
            # mouse_event flags per button: (down, up)
            flag_map = {
                "left": ("0x0002", "0x0004"),
                "right": ("0x0008", "0x0010"),
                "middle": ("0x0020", "0x0040"),
            }
            down_flag, up_flag = flag_map[button]
            click_lines = "\n".join(
                f"[MouseOps]::mouse_event({down_flag}, 0, 0, 0, [IntPtr]::Zero)\n"
                f"[MouseOps]::mouse_event({up_flag}, 0, 0, 0, [IntPtr]::Zero)"
                for _ in range(max(1, clicks))
            )
            ps_script = f"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class MouseOps {{
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, IntPtr dwExtraInfo);
}}
"@
[MouseOps]::SetCursorPos({x}, {y})
{click_lines}
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=f"Clicked {button} at ({x}, {y})")

        else:
            btn_map = {"left": "1", "right": "3", "middle": "2"}
            args = ["xdotool", "mousemove", str(x), str(y), "click"]
            if clicks > 1:
                args.extend(["--repeat", str(clicks)])
            args.append(btn_map[button])

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"xdotool error: {stderr.decode()}",
                )
            return ToolResult(output=f"Clicked {button} at ({x}, {y}) x{clicks}")

    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Failed to click: {e}")


async def tool_mouse_move(
    session: ToolSession,
    x: int,
    y: int,
) -> ToolResult:
    """Move the mouse cursor to screen coordinates."""
    try:
        if not PLATFORM["is_mac"] and not PLATFORM["is_windows"] and not has_display():
            return ToolResult(type=ToolResultType.ERROR, output=NO_GUI_MSG)
        if PLATFORM["is_mac"]:
            script = f"""
do shell script "if command -v cliclick >/dev/null; then cliclick m:{x},{y}; else python3 -c \\"
import Quartz
point = Quartz.CGPointMake({x}, {y})
event = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft)
Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
\\"; fi"
"""
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=f"Mouse moved to ({x}, {y})")

        elif PLATFORM["is_windows"]:
            ps_script = f"""
Add-Type @"
using System; using System.Runtime.InteropServices;
public class MouseMove {{
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
}}
"@
[MouseMove]::SetCursorPos({x}, {y})
"""
            proc = await asyncio.create_subprocess_exec(
                CAPABILITIES["powershell_path"],
                "-Command",
                ps_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return ToolResult(output=f"Mouse moved to ({x}, {y})")

        else:
            proc = await asyncio.create_subprocess_exec(
                "xdotool",
                "mousemove",
                str(x),
                str(y),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"xdotool error: {stderr.decode()}",
                )
            return ToolResult(output=f"Mouse moved to ({x}, {y})")

    except Exception as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Failed to move mouse: {e}"
        )
