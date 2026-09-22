"""Resolve the reader the person NAMED into the app macOS actually runs.

One application wears three names on a Mac, and the book capture used to hand
the same string to all three namespaces:

* the AppleScript name (``tell application "…"``) is the bundle's
  ``CFBundleName`` — Kindle's is ``"Amazon Kindle"``;
* the process name and the window owner name are the executable —
  Kindle's is ``"Kindle"``, and its Dock/display name is "Kindle" too while
  the .app on disk is "Amazon Kindle.app";
* the person says whatever is on the Dock tile.

So ``"Kindle"`` activated nothing (-1728, no such application) and
``"Amazon Kindle"`` owned no window.  Both were true and both were useless.
This module resolves the person's word ONCE against the running processes and
returns an identity the driver addresses by bundle id and by pid, which are
unambiguous in every namespace.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import re

# `lsappinfo list` is Launch Services' own table of running apps: display
# name, bundle id, bundle path (the .app file the person double-clicked),
# executable (the process name that owns windows) and pid. It answers in
# ~0.1 s where a System Events walk of every process took over 20 s.
_ENTRY_HEAD = re.compile(r'^\d+\) "(?P<name>[^"]*)" ASN:', re.M)
_BUNDLE_ID = re.compile(r'^\s*bundleID="(?P<v>[^"]*)"', re.M)
_BUNDLE_PATH = re.compile(r'^\s*bundle path="(?P<v>[^"]*)"', re.M)
_EXECUTABLE = re.compile(r'^\s*executable path="(?P<v>[^"]*)"', re.M)
_PID_TYPE = re.compile(r'^\s*pid = (?P<pid>\d+) type="(?P<type>[^"]*)"', re.M)


@dataclass(frozen=True)
class ReaderApp:
    """A running application, addressable in every macOS namespace."""

    process_name: str
    bundle_id: str
    pid: int
    display_name: str
    app_file_name: str

    @property
    def names(self) -> tuple[str, ...]:
        """Every name a person might reasonably call this app."""
        file_stem = self.app_file_name[:-4] if self.app_file_name.endswith(".app") else self.app_file_name
        return tuple(
            n for n in (self.display_name, file_stem, self.process_name) if n
        )

    @property
    def spoken_name(self) -> str:
        """The name to put in a sentence for the person."""
        return self.names[0] if self.names else self.bundle_id or "the reader"


def parse_process_table(text: str) -> list[ReaderApp]:
    """Parse ``lsappinfo list`` into the apps a person could be naming.

    Only foreground apps (the ones with a Dock tile and windows) are kept:
    helpers, XPC services and background agents can never be "the reader".
    """
    apps: list[ReaderApp] = []
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
            ReaderApp(
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


def match_reader_app(wanted: str, apps: list[ReaderApp]) -> ReaderApp | None:
    """Pick the running app the person meant, or ``None``.

    Exact matches on any of the app's names win over substring matches, and a
    bundle id typed verbatim matches too.  Case and surrounding spaces never
    matter.  When several apps match the same way the first listed wins, which
    is the front-most in System Events order.
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


async def list_running_apps(timeout: float = 10.0) -> list[ReaderApp]:
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


async def resolve_reader_app(wanted: str) -> ReaderApp | None:
    return match_reader_app(wanted, await list_running_apps())
