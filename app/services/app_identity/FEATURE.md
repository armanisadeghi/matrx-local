# FEATURE — App identity (one macOS app, three names)

**The one rule:** no code in this repo turns a person's or an agent's app name
into an AppleScript `tell application "<name>"`, a System Events
`application process "<name>"`, or a `kCGWindowOwnerName` match. It resolves
the name HERE, once, and then addresses the app by **bundle id** (AppleScript)
and **pid** (System Events, CoreGraphics).

## Why

One application wears three names on a Mac:

| namespace | Kindle's answer |
|---|---|
| AppleScript (`CFBundleName`) | `Amazon Kindle` |
| process / `kCGWindowOwnerName` (the executable) | `Kindle` |
| the .app on disk | `Amazon Kindle.app` |
| the Dock tile the person reads | `Kindle` |

So `tell application "Kindle" to activate` raises AppleScript **-1728, no such
application**, and `tell application "Amazon Kindle"` owns no window. Both are
true and both are useless. On 2026-09-21 this failed the owner's first real
desktop-agent run twice in a row (`local_window focus app_name="Kindle"`,
`local_process focus_app "Kindle"`); a fix landed for the book capture alone
(5f681d6b11) and every other tool kept the bug until 2026-09-22.

## The surface

`app/services/app_identity/identity.py`

- `require_running_app(name) -> RunningApp` — the one every tool calls. Raises
  `AppNotRunning` whose message is ONE sentence that names what IS running and
  close (never a bare "-1728", never a silent no-op).
- `RunningApp.applescript_target` → `application id "com.amazon.Lassen"`
- `RunningApp.process_target` → `(first application process whose unix id is 56683)`
- `resolve_running_app` / `match_running_app` / `list_running_apps` /
  `not_running_message` for callers that want the `None` instead of the raise.

Source of truth is `lsappinfo list` — Launch Services' own table of running
foreground apps (display name, bundle id, bundle path, executable, pid). It
answers in ~0.1 s where a System Events walk of every process took over 20 s.

## Who consumes it

`app/tools/tools/window_manager.py` (list / focus / move / minimize),
`app/tools/tools/process_manager.py` (`focus_app`),
`app/tools/tools/input_automation.py` (`type_text`, `hotkey`, `mouse_click`
when aimed at an app), `app/services/book_capture/drivers.py`.

Window ENUMERATION on macOS is CoreGraphics, not System Events: it needs no
Accessibility grant, carries the window id a window-scoped screenshot
requires, and answers in ~0.1 s instead of blowing a 15-second AppleScript
budget with a blank error. Window titles come back empty without Screen
Recording — the tool says so and raises the grant rather than showing a
nameless list.

`local_launch_app` and `local_get_installed_apps` deliberately do NOT resolve
here: one exists to start an app that is not running, the other reads the disk.

## Guard

`tests/unit/test_app_identity_is_the_only_authority.py` — a static scan that
fails on any `tell application "…{`/`application process "…{` anywhere under
`app/`, a scan against owner-name matching, and per-tool tests that drive the
real handlers against the real `lsappinfo` table captured from the owner's Mac
and assert on the argv handed to `osascript`. Proven failing (11 offenders) on
the code as it was, 2026-09-22.
