"""System tools — platform info, screenshot capture, directory listing."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from app.common.platform_ctx import CAPABILITIES, PLATFORM
from app.services.artifacts import get_artifact_service
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from PIL import Image


def _detect_chrome() -> tuple[bool, str | None, str | None]:
    """Return (playwright_available, chrome_path, chrome_version)."""
    import subprocess as _sp

    if not CAPABILITIES["has_playwright"]:
        return False, None, None

    import playwright  # noqa: F401

    pw_available = True

    chrome_path: str | None = None
    chrome_version: str | None = None
    if PLATFORM["is_mac"]:
        # macOS: check known .app bundle locations
        for loc in [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        ]:
            if Path(loc).exists():
                chrome_path = loc
                break

        # Playwright headless shell (macOS cache)
        if chrome_path is None:
            headless_base = Path.home() / "Library" / "Caches" / "ms-playwright"
            if headless_base.exists():
                shells = sorted(
                    headless_base.glob(
                        "chromium_headless_shell-*/chrome-mac/headless_shell"
                    )
                )
                if shells:
                    chrome_path = str(shells[-1])

        # Version from Info.plist
        if chrome_path:
            ver_file = Path(chrome_path).parent.parent / "Info.plist"
            if ver_file.exists():
                try:
                    import plistlib

                    with open(ver_file, "rb") as f:
                        plist = plistlib.load(f)
                    chrome_version = plist.get("CFBundleShortVersionString")
                except Exception:
                    pass

    elif PLATFORM["is_windows"]:
        # Windows: check common install paths
        for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.getenv(env_var)
            if base:
                loc = os.path.join(
                    base, "Google", "Chrome", "Application", "chrome.exe"
                )
                if Path(loc).exists():
                    chrome_path = loc
                    break

    else:
        # Linux / WSL: use centralized capability check
        if CAPABILITIES["has_chrome"]:
            chrome_path = CAPABILITIES["chrome_path"]

        # Playwright headless shell (Linux cache)
        if chrome_path is None:
            headless_base = Path.home() / ".cache" / "ms-playwright"
            if headless_base.exists():
                shells = sorted(
                    headless_base.glob(
                        "chromium_headless_shell-*/chrome-linux/headless_shell"
                    )
                )
                if shells:
                    chrome_path = str(shells[-1])

    # Version via --version flag (works on Windows and Linux)
    if chrome_path and chrome_version is None:
        try:
            result = _sp.run(
                [chrome_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                for part in reversed(parts):
                    if part and part[0].isdigit():
                        chrome_version = part
                        break
        except Exception:
            pass

    return pw_available, chrome_path, chrome_version


async def tool_system_info(
    session: ToolSession,
) -> ToolResult:
    info = {
        "platform": PLATFORM["system"],
        "platform_release": PLATFORM["release"],
        "platform_version": PLATFORM["version"],
        "architecture": PLATFORM["machine"],
        "hostname": PLATFORM["hostname"],
        "python_version": PLATFORM["python_version"],
        "cwd": session.cwd,
        "home": str(Path.home()),
        "user": os.getenv("USER", os.getenv("USERNAME", "unknown")),
    }
    pw_available, chrome_path, chrome_version = _detect_chrome()
    info["playwright_available"] = pw_available
    info["chrome_path"] = chrome_path
    info["chrome_version"] = chrome_version
    lines = [f"{k}: {v}" for k, v in info.items()]
    return ToolResult(output="\n".join(lines), metadata=info)


def _get_screen_geometry() -> list[dict]:
    """Return a list of monitor dicts with keys: index, x, y, width, height, is_primary."""
    monitors: list[dict] = []
    if CAPABILITIES["has_screeninfo"]:
        import screeninfo

        for i, m in enumerate(screeninfo.get_monitors()):
            monitors.append(
                {
                    "index": i + 1,
                    "x": m.x,
                    "y": m.y,
                    "width": m.width,
                    "height": m.height,
                    "is_primary": getattr(m, "is_primary", i == 0),
                    "name": getattr(m, "name", f"Monitor {i + 1}"),
                }
            )
        return monitors

    # Fallback: try tkinter for basic primary-screen dimensions
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.destroy()
        monitors.append(
            {"index": 1, "x": 0, "y": 0, "width": w, "height": h, "is_primary": True, "name": "Primary"}
        )
    except Exception:
        pass

    return monitors


def _grab_screenshot_screencapture(
    bbox: tuple[int, int, int, int] | None = None,
) -> "Image.Image":
    """Capture a screenshot using macOS screencapture CLI (macOS only).

    screencapture is the preferred method on macOS 15+ because:
    - CGWindowListCreateImage and CGDisplayCreateImage are deprecated in macOS 15.1
    - The CLI respects Screen Recording TCC permission
    - -x suppresses the camera shutter sound
    - -R specifies a region (x,y,width,height) for bbox captures
    """
    import io
    import subprocess
    import tempfile
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = ["screencapture", "-x", "-t", "png"]
        if bbox is not None:
            x, y, w, h = bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]
            cmd += ["-R", f"{x},{y},{w},{h}"]
        cmd.append(tmp_path)

        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            raise OSError(
                f"screencapture failed (exit {result.returncode}): {err or 'no error output'}. "
                "Grant Screen Recording in System Settings → Privacy & Security → Screen Recording."
            )

        with open(tmp_path, "rb") as f:
            data = f.read()
        if not data:
            raise OSError(
                "screencapture produced an empty file — Screen Recording permission may be denied. "
                "Grant it in System Settings → Privacy & Security → Screen Recording."
            )
        return Image.open(io.BytesIO(data))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _grab_screenshot_quartz_legacy(
    bbox: tuple[int, int, int, int] | None = None,
    all_screens: bool = True,
) -> "Image.Image":
    """Capture a screenshot using Quartz APIs (macOS < 15 fallback only).

    CGWindowListCreateImage and CGDisplayCreateImage are deprecated as of macOS 15.1.
    Use _grab_screenshot_screencapture() on macOS 15+.
    """
    import io
    import Quartz
    from PIL import Image

    if bbox is not None:
        x, y, w, h = bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]
        rect = Quartz.CGRectMake(x, y, w, h)
        cg_image = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
    elif all_screens:
        rect = Quartz.CGRectInfinite
        cg_image = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
        if cg_image is None:
            cg_image = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())
    else:
        cg_image = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())

    if cg_image is None:
        raise OSError("Screen capture failed — screen recording permission may be required")

    data = Quartz.CFDataCreateMutable(None, 0)
    dest = Quartz.CGImageDestinationCreateWithData(data, "public.png", 1, None)
    if dest is None:
        raise OSError("Failed to create image destination for screenshot")
    Quartz.CGImageDestinationAddImage(dest, cg_image, None)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise OSError("Failed to finalize screenshot image")

    return Image.open(io.BytesIO(bytes(data)))


def _macos_version_tuple() -> tuple[int, int]:
    """Return (major, minor) macOS version, e.g. (15, 1) for Sequoia 15.1."""
    try:
        ver = PLATFORM["mac_version"]  # e.g. "15.1.0"
        parts = ver.split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        return (0, 0)


def _grab_screenshot(
    bbox: tuple[int, int, int, int] | None = None,
    all_screens: bool = True,
) -> "Image.Image":
    """Cross-platform screenshot capture.

    macOS strategy (in order of preference):
    1. screencapture -x CLI — works on all macOS versions, not deprecated,
       respects Screen Recording TCC. This is the primary path.
    2. Quartz CGWindowListCreateImage — fallback for macOS < 15 if screencapture
       is somehow unavailable (highly unlikely in practice).
    3. PIL ImageGrab — last resort or non-macOS.
    """
    if PLATFORM["is_mac"]:
        try:
            return _grab_screenshot_screencapture(bbox=bbox)
        except OSError:
            raise  # propagate permission errors; don't silently fall through
        except Exception:
            pass  # unexpected error — try Quartz legacy

        mac_major, mac_minor = _macos_version_tuple()
        if mac_major < 15 and CAPABILITIES["has_quartz"]:
            try:
                return _grab_screenshot_quartz_legacy(bbox=bbox, all_screens=all_screens)
            except OSError:
                raise

    from PIL import ImageGrab

    try:
        return ImageGrab.grab(bbox=bbox, all_screens=all_screens)
    except TypeError:
        return ImageGrab.grab(bbox=bbox)


async def tool_list_screens(
    session: ToolSession,
) -> ToolResult:
    """List all connected monitors with their geometry."""
    monitors = _get_screen_geometry()
    if not monitors:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Could not detect screen geometry. Install 'screeninfo' for multi-monitor support.",
        )

    lines = []
    for m in monitors:
        primary = " (primary)" if m.get("is_primary") else ""
        lines.append(
            f"Monitor {m['index']}{primary}: {m['width']}x{m['height']} at ({m['x']}, {m['y']})  name={m['name']}"
        )

    return ToolResult(
        output="\n".join(lines),
        metadata={"monitors": monitors, "count": len(monitors)},
    )


async def tool_screenshot(
    session: ToolSession,
    monitor: int | str = "all",
    region: list[int] | None = None,
) -> ToolResult:
    """Capture a screenshot.

    Args:
        monitor: Which display to capture.
                 - "all"     : full virtual desktop (all monitors combined, default)
                 - "primary" : the primary monitor only
                 - 1, 2, …   : specific monitor by 1-based index (use ListScreens to discover)
        region: Optional [x, y, width, height] crop within the selected monitor's coordinate space.
                Coordinates are absolute screen pixels when monitor="all", or relative to the
                chosen monitor's top-left corner otherwise.
    """
    if not CAPABILITIES["has_pil"]:
        return ToolResult(
            type=ToolResultType.ERROR,
            output="Screenshot requires Pillow.",
        )

    bbox: tuple[int, int, int, int] | None = None
    monitor_meta: dict = {}

    if monitor == "all":
        # all_screens=True ensures all displays are included on every platform
        all_screens = True
    else:
        all_screens = False
        monitors = _get_screen_geometry()

        if monitor == "primary":
            target = next((m for m in monitors if m.get("is_primary")), monitors[0] if monitors else None)
        else:
            try:
                idx = int(monitor)
            except (ValueError, TypeError):
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"Invalid monitor value '{monitor}'. Use 'all', 'primary', or a 1-based integer index.",
                )
            target = next((m for m in monitors if m["index"] == idx), None)
            if target is None:
                available = [m["index"] for m in monitors]
                return ToolResult(
                    type=ToolResultType.ERROR,
                    output=f"Monitor {idx} not found. Available monitors: {available}. Use ListScreens to see all displays.",
                )

        if target:
            monitor_meta = target
            if region:
                if len(region) != 4:
                    return ToolResult(
                        type=ToolResultType.ERROR,
                        output="region must be [x, y, width, height] with exactly 4 values.",
                    )
                rx, ry, rw, rh = region
                # Translate relative coords to absolute screen space
                bbox = (
                    target["x"] + rx,
                    target["y"] + ry,
                    target["x"] + rx + rw,
                    target["y"] + ry + rh,
                )
            else:
                bbox = (
                    target["x"],
                    target["y"],
                    target["x"] + target["width"],
                    target["y"] + target["height"],
                )

    if region and monitor == "all":
        if len(region) != 4:
            return ToolResult(
                type=ToolResultType.ERROR,
                output="region must be [x, y, width, height] with exactly 4 values.",
            )
        rx, ry, rw, rh = region
        bbox = (rx, ry, rx + rw, ry + rh)

    try:
        screenshot = _grab_screenshot(bbox=bbox, all_screens=all_screens)
    except OSError as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Screenshot failed: {e}")

    w, h = screenshot.size
    buffer = io.BytesIO()
    screenshot.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    meta: dict = {
        "width": w,
        "height": h,
        "monitor": monitor,
    }
    if monitor_meta:
        meta["monitor_info"] = monitor_meta
    if region:
        meta["region"] = region

    artifact, provider_path = await get_artifact_service().create_screenshot(
        content=image_bytes,
        width=w,
        height=h,
        capture_source="desktop",
        capture=meta,
        session=session,
    )

    return ToolResult(
        output=(
            f"Screenshot captured ({w}x{h}, {len(image_bytes)} bytes); "
            f"artifact_id={artifact.artifact_id}"
        ),
        artifact=artifact,
        provider_image_path=provider_path,
        metadata={**meta, "artifact_id": artifact.artifact_id},
    )


async def tool_list_directory(
    session: ToolSession,
    path: str | None = None,
    show_hidden: bool = False,
    cursor: str | None = None,
    limit: int = 100,
) -> ToolResult:
    target = session.resolve_path(path or ".")
    from app.services.filesystem import get_filesystem_service
    try:
        page = await get_filesystem_service().list_directory(
            target, cursor=cursor, limit=limit, show_hidden=show_hidden
        )
    except (OSError, ValueError, asyncio.TimeoutError) as e:
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Cannot list directory: {e}"
        )
    data = page.to_dict()
    items = [
        f"  {entry['name']}{os.sep if entry['kind'] == 'dir' else ''}"
        for entry in data["entries"]
    ]
    header = f"{target}{os.sep} ({page.total} items; showing {len(items)})"
    if page.next_cursor:
        items.append(f"  ... more (cursor={page.next_cursor})")
    return ToolResult(
        output=header + "\n" + "\n".join(items),
        metadata=data,
    )


async def tool_open_url(
    session: ToolSession,
    url: str,
) -> ToolResult:
    """Open a URL in the user's default browser."""
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception as e:
        return ToolResult(type=ToolResultType.ERROR, output=f"Failed to open URL: {e}")

    return ToolResult(output=f"Opened {url} in default browser")


async def tool_open_path(
    session: ToolSession,
    path: str,
) -> ToolResult:
    """Open a file or directory using the OS default handler (Finder, Explorer, etc.)."""
    from app.tools.tools import open_path_cross_platform

    resolved = session.resolve_path(path)

    if not os.path.exists(resolved):
        return ToolResult(
            type=ToolResultType.ERROR, output=f"Path not found: {resolved}"
        )

    success, message = open_path_cross_platform(resolved)
    if not success:
        return ToolResult(type=ToolResultType.ERROR, output=message)
    return ToolResult(output=message)
