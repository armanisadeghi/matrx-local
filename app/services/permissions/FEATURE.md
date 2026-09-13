# Device permissions — who asks, who answers

The Dashboard, the Permissions modal, the Setup wizard and the Devices page all
read ONE model (`desktop/src/hooks/use-permissions.ts`, mounted once via
`PermissionsProvider`). Every permission key names the one process that can
truthfully read its status AND make the OS show its prompt. Nothing else may
report a permission status.

| Authority | Keys (macOS) | Status read | Prompt |
|---|---|---|---|
| `plugin` — tauri-plugin-macos-permissions, app process | microphone, camera, accessibility, full_disk_access, input_monitoring | plugin check (AVFoundation / AXIsProcessTrusted / FS probe / IOHID) | mic+camera: in-app OS dialog; rest: the exact System Settings pane |
| `app` — `desktop/src-tauri/src/tcc.rs`, app process | contacts, calendar, reminders, photos, location, speech_recognition, bluetooth | framework class-level `authorizationStatus` (never prompts) | framework request API on the main thread; returns the real post-prompt status |
| `engine` — `app/services/permissions/checker.py` | screen_recording (macOS); every platform permission on Windows/Linux | `GET /devices/permissions[/{name}]` | `POST /devices/permissions/request/screen-recording` (macOS: the ONLY engine-requestable key) |
| `first_use` — not queryable by any public API | automation (Apple Events, incl. Mail), local_network | shown as a STATE ("asked on first use"), never counted | System Settings pane only |

## Why the split is exactly this

- macOS attributes a privacy prompt to the **process that calls the request
  API**, shows it only if that process's bundle carries the matching
  `NS*UsageDescription`, and CoreLocation delivers nothing without a run
  loop. The engine helper (`Matrx Engine.app`) has no run loop and shipped
  without the Location/Speech keys, so its Contacts/Location/… requests were
  silently ignored: "Request access" did nothing and System Settings never
  listed the app (2026-09-13). The app process has the run loop, the keys
  (`desktop/src-tauri/Info.plist`) and the identity; the engine is its child
  with the same code-signing identifier, so the grant covers the engine too.
- Screen recording stays with the engine: the capture runs there, and its
  `CGRequestScreenCaptureAccess` is what lists the app under Screen Recording.
  The app's own `CGPreflightScreenCaptureAccess` reads false until relaunch
  after an in-session grant, so the app never reports it.
- Both helper specs still declare every usage key
  (`tests/unit/test_folder_usage_descriptions.py` pins them): a missing key
  silently disables Location and crashes the process on Speech.

## The count

`summarizePermissions()` is the one number every surface shows: granted +
limited over keys with a countable status, reported only when every key has
answered (`complete`). `first_use`, `unavailable` and `unknown` never enter
the denominator. Before this, the Dashboard mixed an engine list (which
included Wi‑Fi scans and Mail) with a plugin map and the number changed
under the user's eyes ("6/18", then "10/19").

## What is NOT a permission

Wi‑Fi scans, network interfaces and connected hardware are device
inventories: the Devices page shows them without a permission badge.
Messages access is Full Disk Access; Mail access is Automation of Mail.app.
Neither gets a row of its own. `check_bluetooth` in the engine reports the
radio state for the Devices card; the Bluetooth *permission* is
`CBManager.authorization` via the app.

## Rules

1. Never add a permission status source outside the authority table. A page
   that renders engine rows must override their status with the hook's value
   for every key in `hookAuthoritativeKeys()` (Devices does).
2. Never send a person to a System Settings pane for a `not_determined`
   permission the app can prompt for — the app is not listed there yet.
3. Never call a not-yet-asked permission "denied" or "not granted".
4. Status reads must never prompt (`authorizationStatus` class methods only;
   no `SCShareableContent`, no `AVCaptureDevice.authorizationStatus` from the
   engine).
