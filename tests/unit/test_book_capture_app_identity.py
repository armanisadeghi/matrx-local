"""One app, three names: the capture must find the reader the person named.

On 2026-09-21 the first real run on Arman's Mac failed twice on the same app:
``"Kindle"`` could not be activated (AppleScript knows it as "Amazon Kindle",
error -1728) and ``"Amazon Kindle"`` owned no window (the window owner is the
process, "Kindle").  The identity below is the real table System Events
reported for that Mac.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.services.book_capture import drivers as drivers_mod
from app.services.book_capture.app_identity import (
    ReaderApp,
    match_reader_app,
    parse_process_table,
)
from app.services.book_capture.drivers import MacReaderDriver, ReaderUnavailable

KINDLE = ReaderApp("Kindle", "com.amazon.Lassen", 56683, "Kindle", "Amazon Kindle.app")
BOOKS = ReaderApp("Books", "com.apple.iBooksX", 4102, "Books", "Books.app")
PREVIEW = ReaderApp("Preview", "com.apple.Preview", 777, "Preview", "Preview.app")
TABLE = [PREVIEW, KINDLE, BOOKS]


@pytest.mark.parametrize(
    "spoken",
    ["Kindle", "kindle", " Amazon Kindle ", "amazon kindle", "com.amazon.Lassen", "Amazon Kindle.app"],
)
def test_every_name_a_person_uses_for_kindle_finds_the_one_running_app(spoken):
    assert match_reader_app(spoken, TABLE) == KINDLE


def test_an_app_that_is_not_running_is_none_not_a_guess():
    assert match_reader_app("Calibre", TABLE) is None
    assert match_reader_app("   ", TABLE) is None


def test_exact_names_beat_substrings():
    # "Books" must not resolve to a hypothetical "Notebooks" listed earlier.
    notebooks = ReaderApp("Notebooks", "com.example.notebooks", 9, "Notebooks", "Notebooks.app")
    assert match_reader_app("Books", [notebooks, BOOKS]) == BOOKS


LSAPPINFO_SAMPLE = """78) "AutoFill (Cursor)" ASN:0x0-0x1ff1ff: 
    bundleID="com.apple.SafariPlatformSupport.Helper"
    bundle path="/System/Library/Frameworks/SafariServices.framework/Versions/A/XPCServices/AutoFill.xpc"
    executable path="/System/Library/Frameworks/SafariServices.framework/Versions/A/XPCServices/AutoFill.xpc/Contents/MacOS/AutoFill"
    pid = 48197 type="BackgroundOnly" flavor=2 Version="1" fileType="XPC!" creator="????" Arch=ARM64 
79) "Kindle" ASN:0x0-0x201201: 
    bundleID="com.amazon.Lassen"
    bundle path="/Applications/Amazon Kindle.app"
    executable path="/Applications/Amazon Kindle.app/Contents/MacOS/Kindle"
    pid = 56683 type="Foreground" flavor=3 Version="1.472078.10" fileType="APPL" creator="????" Arch=ARM64 sandboxed 
    coalition: 3415  { 56683 57350 }
80) "Books" ASN:0x0-0x2a02a: 
    bundleID="com.apple.iBooksX"
    bundle path="/System/Applications/Books.app"
    executable path="/System/Applications/Books.app/Contents/MacOS/Books"
    pid = 4102 type="Foreground" flavor=3 Version="7.2" fileType="APPL" creator="????" Arch=ARM64 
"""


def test_the_process_table_parses_launch_services_and_keeps_only_foreground_apps():
    apps = parse_process_table(LSAPPINFO_SAMPLE)
    assert apps == [KINDLE, BOOKS]  # the AutoFill helper is not a reader
    assert KINDLE.spoken_name == "Kindle"
    assert "Amazon Kindle" in KINDLE.names


def _fake_quartz(monkeypatch, windows):
    quartz = types.SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0,
        CGWindowListCopyWindowInfo=lambda *_: windows,
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)


def test_the_window_is_found_by_the_resolved_process_never_by_its_name(monkeypatch):
    # The owner name is the PROCESS name ("Kindle"); the person said "Amazon Kindle".
    _fake_quartz(monkeypatch, [
        {"kCGWindowOwnerName": "Kindle", "kCGWindowOwnerPID": 56683, "kCGWindowLayer": 0,
         "kCGWindowName": "Chemistry", "kCGWindowBounds": {"Width": 1200, "Height": 900},
         "kCGWindowNumber": 4242},
        {"kCGWindowOwnerName": "Kindle", "kCGWindowOwnerPID": 56683, "kCGWindowLayer": 0,
         "kCGWindowName": "Palette", "kCGWindowBounds": {"Width": 200, "Height": 100},
         "kCGWindowNumber": 4243},
        {"kCGWindowOwnerName": "Kindle Helper", "kCGWindowOwnerPID": 999, "kCGWindowLayer": 0,
         "kCGWindowName": "", "kCGWindowBounds": {"Width": 5000, "Height": 5000},
         "kCGWindowNumber": 1},
    ])
    driver = MacReaderDriver("Amazon Kindle")
    driver._app = KINDLE
    assert driver._resolve_window_id() == 4242
    assert driver.describes == "Kindle"


def test_a_reader_that_is_not_running_is_refused_in_a_sentence(monkeypatch):
    async def nobody(_wanted):
        return None
    monkeypatch.setattr(drivers_mod, "resolve_reader_app", nobody)
    driver = MacReaderDriver("Kindle")
    with pytest.raises(ReaderUnavailable, match="is not running"):
        asyncio.run(driver.focus())


def test_activation_addresses_the_app_by_bundle_id_not_by_the_spoken_name(monkeypatch):
    async def found(_wanted):
        return KINDLE
    monkeypatch.setattr(drivers_mod, "resolve_reader_app", found)
    driver = MacReaderDriver("Kindle")
    asyncio.run(driver._ensure_app())
    assert driver._applescript_target() == 'application id "com.amazon.Lassen"'
