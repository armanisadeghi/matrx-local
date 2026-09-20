"""The reader seam: focus a window, capture it, turn the page.

Everything that touches the user's screen lives behind :class:`ReaderDriver`.
That is not test decoration — it is the whole reason the capture loop can be
proven correct without an agent ever seizing the owner's display
(Arman, 2026-09-17: agent browsers stealing his focus).  The automated suite
drives :class:`SyntheticReaderDriver`; only a real, human-initiated run
constructs :class:`MacReaderDriver`.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from app.common.platform_ctx import PLATFORM


class ReaderUnavailable(RuntimeError):
    """The named reader window could not be found or driven."""


class CaptureFailed(RuntimeError):
    """A frame could not be captured. Carries the operating system's reason."""


class ReaderDriver(Protocol):
    """One reader window, driven page by page."""

    async def focus(self) -> None:
        """Bring the reader window forward so keystrokes reach it."""

    async def capture(self) -> bytes:
        """Return PNG bytes of the reader window only — never the whole screen."""

    async def send_next_page(self, key: str) -> None:
        """Send the page-turn key to the focused reader."""

    @property
    def describes(self) -> str:
        """Human-readable name of what is being captured, for provenance."""


# ── macOS ────────────────────────────────────────────────────────────────


#: AppleScript key codes for the keys a reader plausibly binds to "next page".
_KEY_CODES = {
    "right": 124,
    "left": 123,
    "down": 125,
    "up": 126,
    "space": 49,
    "page_down": 121,
    "page_up": 116,
    "return": 36,
}


def supported_keys() -> list[str]:
    return sorted(_KEY_CODES)


class MacReaderDriver:
    """Drives a real reader window on macOS.

    Capture is window-scoped (``screencapture -l <window id>``), never a full
    screen grab: the owner's other windows, notifications and wallpaper are
    not ours to collect, and a window grab stays correct when something
    overlaps the reader.
    """

    def __init__(self, app_name: str, *, window_title: str | None = None) -> None:
        self.app_name = app_name
        self.window_title = window_title
        self._window_id: int | None = None

    @property
    def describes(self) -> str:
        return self.app_name

    # -- window resolution ------------------------------------------------

    def _resolve_window_id(self) -> int:
        """Find the CoreGraphics window number of the reader's main window.

        ``window_manager``'s AppleScript listing cannot help here: it reports
        titles and bounds but no window id, and ``screencapture -l`` needs the
        id.  Quartz is the only source of it.
        """
        try:
            import Quartz  # pyobjc-framework-Quartz
        except ImportError as exc:  # pragma: no cover - depends on host wheels
            raise ReaderUnavailable(
                "Window-scoped capture needs the Quartz framework, which is not "
                "installed in this engine. Reinstall the app's Python runtime."
            ) from exc

        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        wanted = self.app_name.strip().lower()
        candidates = []
        for info in infos or []:
            owner = str(info.get("kCGWindowOwnerName") or "")
            if wanted not in owner.lower():
                continue
            if int(info.get("kCGWindowLayer") or 0) != 0:
                continue  # menu bars, shadows, overlays
            title = str(info.get("kCGWindowName") or "")
            if self.window_title and self.window_title.lower() not in title.lower():
                continue
            bounds = info.get("kCGWindowBounds") or {}
            area = float(bounds.get("Width") or 0) * float(bounds.get("Height") or 0)
            candidates.append((area, int(info.get("kCGWindowNumber") or 0), owner))

        if not candidates:
            raise ReaderUnavailable(
                f"No visible window belonging to “{self.app_name}” was found. "
                "Open the book in that app and leave the window on screen."
            )
        # The book is in the largest window; palettes and inspectors are small.
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _window_id_cached(self) -> int:
        if self._window_id is None:
            self._window_id = self._resolve_window_id()
        return self._window_id

    # -- driver surface ---------------------------------------------------

    async def focus(self) -> None:
        script = f'tell application "{self.app_name}" to activate'
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            raise ReaderUnavailable(
                f"Could not bring “{self.app_name}” forward: "
                f"{stderr.decode(errors='replace').strip() or 'unknown error'}"
            )
        await asyncio.get_running_loop().run_in_executor(None, self._window_id_cached)

    async def capture(self) -> bytes:
        window_id = await asyncio.get_running_loop().run_in_executor(
            None, self._window_id_cached
        )
        return await asyncio.get_running_loop().run_in_executor(
            None, _screencapture_window, window_id
        )

    async def send_next_page(self, key: str) -> None:
        code = _KEY_CODES.get(key)
        if code is None:
            raise ReaderUnavailable(
                f"“{key}” is not a page-turn key this driver knows. "
                f"Use one of: {', '.join(supported_keys())}."
            )
        script = (
            f'tell application "{self.app_name}" to activate\n'
            f'tell application "System Events" to key code {code}'
        )
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            text = stderr.decode(errors="replace")
            raise ReaderUnavailable(
                "Could not send the page-turn key. "
                + (
                    "Allow Accessibility for AI Matrx in System Settings."
                    if ("-1743" in text or "-25211" in text or "assistive" in text.lower())
                    else text.strip() or "unknown error"
                )
            )


def _screencapture_window(window_id: int) -> bytes:
    """Grab one window as PNG bytes via the macOS ``screencapture`` CLI.

    ``-l`` scopes the grab to a single window, ``-x`` silences the shutter and
    ``-o`` drops the drop shadow so the frame is the window's own pixels.
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["screencapture", "-x", "-o", "-t", "png", "-l", str(window_id), tmp_path],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            raise CaptureFailed(
                "screencapture failed (exit "
                f"{result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip() or 'no error output'}"
            )
        data = Path(tmp_path).read_bytes()
        if not data:
            raise CaptureFailed(
                "screencapture produced an empty file — Screen Recording "
                "permission is most likely denied."
            )
        return data
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── Synthetic ────────────────────────────────────────────────────────────


class SyntheticReaderDriver:
    """A reader made of pre-rendered frames. Touches nothing on screen.

    It reproduces the one behaviour that ends a real run: past the last page,
    the key does nothing and the same frame comes back again.
    """

    def __init__(self, frames: list[bytes], *, name: str = "Synthetic Reader") -> None:
        if not frames:
            raise ValueError("A synthetic reader needs at least one frame.")
        self._frames = frames
        self._index = 0
        self._name = name
        self.focus_calls = 0
        self.key_presses: list[str] = []

    @property
    def describes(self) -> str:
        return self._name

    async def focus(self) -> None:
        self.focus_calls += 1

    async def capture(self) -> bytes:
        return self._frames[self._index]

    async def send_next_page(self, key: str) -> None:
        self.key_presses.append(key)
        # Clamp: the last page is the last page, exactly like a real reader.
        self._index = min(self._index + 1, len(self._frames) - 1)


def build_driver(app_name: str, *, window_title: str | None = None) -> ReaderDriver:
    """Return the real driver for this platform, or refuse by name."""
    if not PLATFORM["is_mac"]:
        raise ReaderUnavailable(
            "Book capture drives a reader window with macOS screen capture and "
            "is only available on macOS."
        )
    return MacReaderDriver(app_name, window_title=window_title)
