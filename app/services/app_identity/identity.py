"""Resolve the app a person NAMED into the app macOS actually runs.

One application wears three names on a Mac, and every desktop tool used to
hand the same string to all three namespaces:

* the AppleScript name (``tell application "…"``) is the bundle's
  ``CFBundleName`` — Kindle's is ``"Amazon Kindle"``;
* the process name and the CoreGraphics window owner name are the
  executable — Kindle's is ``"Kindle"``, while the .app on disk is
  "Amazon Kindle.app";
* the person says whatever is on the Dock tile.

So ``tell application "Kindle" to activate`` raised AppleScript ``-1728``
(no such application) and ``tell application "Amazon Kindle"`` owned no
window.  Both were true and both were useless.

This module resolves the person's word ONCE against the running apps and
returns an identity every tool addresses by **bundle id** (AppleScript) and
**pid** (System Events and CoreGraphics), which are unambiguous in every
namespace.  It is the single identity authority for this repo: no tool may
interpolate a person's app name into ``tell application "<name>"`` or match
it against ``kCGWindowOwnerName`` on its own.  Guard:
``tests/unit/test_app_identity_is_the_only_authority.py``.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

# `lsappinfo list` is Launch Services' own table of running apps: display
# name, bundle id, bundle path (the .app file the person double-clicked),
# executable (the process name that owns windows) and pid. It answers in
# ~0.1 s where a System Events walk of every process took over 20 s.
_ENTRY_HEAD = re.compile(r'^\d+\) "(?P<name>[^"]*)" ASN:', re.M)
_BUNDLE_ID = re.compile(r'^\s*bundleID="(?P<v>[^"]*)"', re.M)
_BUNDLE_PATH = re.compile(r'^\s*bundle path="(?P<v>[^"]*)"', re.M)
_EXECUTABLE = re.compile(r'^\s*executable path="(?P<v>[^"]*)"', re.M)
_PID_TYPE = re.compile(r'^\s*pid = (?P<pid>\d+) type="(?P<type>[^"]*)"', re.M)


class AppNotRunning(RuntimeError):
    """The named app is not running. Carries a sentence naming what is."""


@dataclass(frozen=True)
class RunningApp:
    """A running application, addressable in every macOS namespace."""

    process_name: str
    bundle_id: str
    pid: int
    display_name: str
    app_file_name: str

    @property
    def names(self) -> tuple[str, ...]:
        """Every name a person might reasonably call this app."""
        file_stem = (
            self.app_file_name[:-4]
            if self.app_file_name.endswith(".app")
            else self.app_file_name
        )
        return tuple(n for n in (self.display_name, file_stem, self.process_name) if n)

    @property
    def spoken_name(self) -> str:
        """The name to put in a sentence for the person."""
        return self.names[0] if self.names else self.bundle_id or "that app"

    @property
    def applescript_target(self) -> str:
        """The ``tell`` target that cannot raise -1728.

        A bundle id is the only application specifier macOS guarantees to
        resolve; the display name is a last resort for an app Launch Services
        reported without one.
        """
        if self.bundle_id:
            return f'application id "{self.bundle_id}"'
        return f'application "{self.spoken_name}"'

    @property
    def process_target(self) -> str:
        """The System Events process specifier, addressed by pid.

        ``application process "Kindle"`` is a NAME lookup in a third
        namespace; ``whose unix id is <pid>`` is the same process we resolved.
        """
        return f"(first application process whose unix id is {self.pid})"


# Backwards-compatible alias for the book-capture driver's vocabulary.
ReaderApp = RunningApp


def parse_process_table(text: str) -> list[RunningApp]:
    """Parse ``lsappinfo list`` into the apps a person could be naming.

    Only foreground apps (the ones with a Dock tile and windows) are kept:
    helpers, XPC services and background agents can never be what the person
    meant by an app name.
    """
    apps: list[RunningApp] = []
    heads = list(_ENTRY_HEAD.finditer(text))
    for i, head in enumerate(heads):
        block = text[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        pid_type = _PID_TYPE.search(block)
        if not pid_type or pid_type.group("type") != "Foreground":
            continue
        bundle_id = _BUNDLE_ID.search(block).group("v") if _BUNDLE_ID.search(block) else ""
        bundle_path = _BUNDLE_PATH.search(block).group("v") if _BUNDLE_PATH.search(block) else ""
        executable = _EXECUTABLE.search(block).group("v") if _EXECUTABLE.search(block) else ""
        apps.append(
            RunningApp(
                process_name=executable.rsplit("/", 1)[-1] if executable else head.group("name"),
                bundle_id=bundle_id,
                pid=int(pid_type.group("pid")),
                display_name=head.group("name"),
                app_file_name=bundle_path.rsplit("/", 1)[-1] if bundle_path else "",
            )
        )
    return apps


def _norm(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").split())


def match_running_app(wanted: str, apps: list[RunningApp]) -> RunningApp | None:
    """Pick the running app the person meant, or ``None``.

    Exact matches on any of the app's names win over substring matches, and a
    bundle id typed verbatim matches too.  Case and surrounding spaces never
    matter.  When several apps match the same way the first listed wins, which
    is the front-most in Launch Services order.
    """
    w = _norm(wanted)
    if not w:
        return None
    for app in apps:
        if w == _norm(app.bundle_id) or any(w == _norm(n) for n in app.names):
            return app
    for app in apps:
        if any(w in _norm(n) or _norm(n) in w for n in app.names if n):
            return app
    return None


# Kept for the book-capture driver's original vocabulary.
match_reader_app = match_running_app


def _shared_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def close_running_names(wanted: str, apps: list[RunningApp], limit: int = 5) -> list[str]:
    """Running apps whose name is plausibly what the person typed."""
    w = _norm(wanted)
    scored: list[tuple[int, str]] = []
    for app in apps:
        best = 0
        for name in app.names:
            n = _norm(name)
            if not n:
                continue
            best = max(best, _shared_prefix(w, n))
            if set(w.split()) & set(n.split()):
                best = max(best, 3)
        if best >= 3:
            scored.append((best, app.spoken_name))
    scored.sort(key=lambda s: (-s[0], s[1]))
    seen: list[str] = []
    for _, name in scored:
        if name not in seen:
            seen.append(name)
    return seen[:limit]


def not_running_message(wanted: str, apps: list[RunningApp]) -> str:
    """One sentence that refuses, and names what IS running that is close."""
    close = close_running_names(wanted, apps)
    running = []
    for app in apps:
        if app.spoken_name not in running:
            running.append(app.spoken_name)
    if close:
        return (
            f"“{wanted}” is not running on this Mac — did you mean "
            + ", ".join(close)
            + "? Open it first, or name one of those."
        )
    if not running:
        return f"“{wanted}” is not running on this Mac, and no app with a window is."
    shown = ", ".join(running[:8])
    more = f" (+{len(running) - 8} more)" if len(running) > 8 else ""
    return (
        f"“{wanted}” is not running on this Mac — these are: {shown}{more}. "
        "Open it first, or name one of those."
    )


async def list_running_apps(timeout: float = 10.0) -> list[RunningApp]:
    proc = await asyncio.create_subprocess_exec(
        "lsappinfo", "list",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise RuntimeError(
            f"Listing running apps took longer than {timeout:.0f} seconds."
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(
            "Could not list running apps: "
            + (stderr.decode(errors="replace").strip() or "no error output")
        )
    return parse_process_table(stdout.decode(errors="replace"))


async def resolve_running_app(wanted: str) -> RunningApp | None:
    return match_running_app(wanted, await list_running_apps())


# Kept for the book-capture driver's original vocabulary.
resolve_reader_app = resolve_running_app


async def require_running_app(wanted: str) -> RunningApp:
    """Resolve, or raise :class:`AppNotRunning` with the honest sentence.

    This is what every desktop tool calls: an app that is not running is
    refused in ONE sentence that names what is, never with AppleScript's
    ``-1728`` and never by silently doing nothing.
    """
    apps = await list_running_apps()
    app = match_running_app(wanted, apps)
    if app is None:
        raise AppNotRunning(not_running_message(wanted, apps))
    return app
