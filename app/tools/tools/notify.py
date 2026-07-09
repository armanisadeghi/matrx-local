"""Notification tool — send native OS notifications. Cross-platform."""

from __future__ import annotations

import asyncio
import logging
import time

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)


async def _broadcast_notification(title: str, message: str, level: str = "info") -> None:
    """Broadcast a notification event to all connected WebSocket clients."""
    try:
        from app.main import websocket_manager
        await websocket_manager.broadcast_notification(title, message, level)
    except Exception as e:
        logger.warning("Failed to broadcast notification to WS clients: %s", e)


async def send_notification(
    title: str,
    message: str,
    timeout: int = 10,
    level: str = "info",
    broadcast: bool = True,
) -> ToolResult:
    """
    Core notification logic — fires the OS popup and broadcasts to all WS clients.
    Called by both tool_notify (via WebSocket tool call) and the /notify REST endpoint
    (used by the cloud / AI server to push notifications to the user).
    """
    os_fired = False

    # Try plyer first (cross-platform, works on macOS/Windows with desktop)
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="Matrx Local",
            timeout=timeout,
        )
        os_fired = True
    except ImportError:
        pass
    except Exception as e:
        logger.warning("plyer notification failed: %s", e)

    # Platform-specific fallbacks
    if not os_fired:
        try:
            if PLATFORM["is_mac"]:
                script = (
                    f'display notification {_osa_str(message)} '
                    f'with title {_osa_str(title)}'
                )
                proc = await asyncio.create_subprocess_exec(
                    "osascript", "-e", script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
                os_fired = proc.returncode == 0

            elif PLATFORM["is_windows"]:
                ps_script = (
                    f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
                    f"$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                    f"$template.SelectSingleNode('//text[@id=1]').InnerText = '{_ps_escape(title)}'; "
                    f"$template.SelectSingleNode('//text[@id=2]').InnerText = '{_ps_escape(message)}'; "
                    f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                    f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Matrx Local').Show($toast)"
                )
                proc = await asyncio.create_subprocess_exec(
                    CAPABILITIES["powershell_path"], "-NonInteractive", "-Command", ps_script,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
                os_fired = proc.returncode == 0

            elif PLATFORM["is_linux"]:
                proc = await asyncio.create_subprocess_exec(
                    "notify-send", "-t", str(timeout * 1000), title, message,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=10)
                os_fired = proc.returncode == 0

        except FileNotFoundError as e:
            logger.debug("Notification binary not found: %s", e)
        except Exception as e:
            logger.debug("Notification fallback failed: %s", e)

    if not os_fired:
        logger.info("NOTIFY [%s]: %s", title, message)

    # Always broadcast to connected UI clients regardless of OS popup outcome
    if broadcast:
        await _broadcast_notification(title, message, level)

    return ToolResult(
        output=f"Notification sent: {title}",
        metadata={"os_fired": os_fired, "title": title, "message": message, "level": level},
    )


async def tool_notify(
    session: ToolSession,
    title: str,
    message: str,
    timeout: int = 10,
    level: str = "info",
) -> ToolResult:
    """Send a native OS desktop notification and broadcast it to the UI."""
    return await send_notification(title, message, timeout, level, broadcast=True)


def _osa_str(s: str) -> str:
    """Escape a string for AppleScript."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ps_escape(s: str) -> str:
    """Escape a string for PowerShell single-quote context."""
    return s.replace("'", "''")
