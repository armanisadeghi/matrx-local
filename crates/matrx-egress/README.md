# `matrx-egress` — AI Matrx Home Connection

The device leg of residential egress: a person lends **their own computer's internet connection**
to **their own** AI Matrx work, and only when a site has blocked our servers.

Contract (the one source of truth for every repo in this system):
`common-docs/systems/platform/residential-egress/FEATURE.md`.

**What it is not:** it is not a proxy anybody else can use, and it is not a way into this
computer. It dials OUT and holds one WebSocket — nothing listens here, no port is opened, no
router setting changes — and every connection the server asks it to make is judged against the
public-address policy before a packet leaves, so the socket can never reach this computer's own
network.

## The commands

| Command | What it does |
|---|---|
| `matrx-egress` | Standalone. Reads the token from the OS keychain (pairs first if there is none) and shows the menu-bar item. |
| `matrx-egress pair [--server URL] [--name NAME]` | Connects this computer to an account: prints a code, opens the page, waits for approval, saves the token. |
| `matrx-egress run --token-stdin --server URL --status-file PATH [--no-tray] [--name NAME]` | Engine mode. The desktop app runs this as a child: the token is the first line of stdin, there is no keychain and no pairing, the status document is written to `PATH` on every change and every 5 s, one JSON line per state change goes to stdout, and it exits 0 on stdin EOF or SIGTERM. |
| `matrx-egress status` / `pause` / `resume` / `sign-out` | Talk to the running standalone instance over `<home>/egress/control.json`. |
| `matrx-egress install` / `uninstall` | Start (or stop starting) when this person signs in. |
| `--version`, `--help` | |

Options everywhere: `--server`, `--web`, `--name`, `--world live|dev`, `--status-file`,
`--token-stdin`, `--no-tray`, `--tray`.

## Where things live

| | macOS / Linux | Windows |
|---|---|---|
| Home | `$MATRX_HOME_DIR`, default `~/.matrx` (dev `~/.matrx-dev`) | `%LOCALAPPDATA%\Matrx\<world>\` |
| Status document | `<home>/egress/status.json` | same |
| Control file | `<home>/egress/control.json`, mode 0600, deleted on a clean stop | same |
| Credential | Keychain service `ai.matrx.home-connection` (dev `…​.dev`), account = the device identity | Credential Manager / Secret Service, same names |
| Device identity | `inst_<stable_machine_id>-helper` | same |

The home paths, the two worlds and the keychain-per-world rule are `matrx-syncd`'s, for the same
reason (matrx-local Hard Rule 9): a source build lives in the dev world and can never touch the
packaged app's home or its keychain items.

The device identity reuses the desktop engine's hash
(`app/services/cloud_sync/instance_manager.py::_stable_machine_id`: `hostname | machine | system
[| hardware uuid] [| salt]`, SHA-256, first 32 hex characters) with a `-helper` suffix, so the
standalone helper and the desktop app can both be set up on one machine without one overwriting
the other's row. **Verified on this Mac:** the account name the helper wrote into the keychain is
the same string that algorithm produces, recomputed independently in Python.

## The protocol, as implemented

`wss://<server>/egress/device`, headers `Authorization: Bearer mxe_<device_id>_<secret>` and
`X-Matrx-Egress-Protocol: 1`. Frames are binary, `[u8 type][u32 BE stream_id][payload]`, stream 0
is control. HELLO first; PING answered with PONG at once; OPEN resolved, judged, dialled with a
15-second budget, answered with OPENED; DATA both ways in ≤ 64 KiB frames; CLOSE ends a stream.

| Close code | What happens |
|---|---|
| `4401` | Stops for good: "This computer was removed from your account — run Connect again to add it back." The token is deliberately **not** deleted — a refusal that turns out to be the server's mistake must not cost the person their pairing. |
| `4403` | "Paused from the web", retried every 60 s. |
| `4409` | Stops: a newer connection for this computer won. |
| anything else | Reconnects with a jittered 1 → 60 s backoff. The backoff resets only on a session that reached HELLO_ACK. |

**Backpressure.** Device→server uses the WebSocket's own: when it cannot keep up the helper stops
reading that TCP socket, which pushes back on the far end rather than growing a buffer.
Server→device cannot push back, so it is capped: more than 4 MiB waiting for one stream and that
stream is closed with `{"reason":"overloaded"}`. `max_streams` from HELLO_ACK wins over the
helper's own offer, and an OPEN past it is refused `busy`.

**The public-address policy** (`src/policy.rs`) resolves the name, then refuses loopback, RFC1918,
CGNAT `100.64/10`, link-local (including `169.254.169.254`), multicast, broadcast, every reserved
and documentation range, and the IPv6 equivalents — unique-local, link-local, Teredo, 6to4,
IPv4-mapped and NAT64 forms are judged as the IPv4 address inside them. **One** non-public answer
refuses the whole OPEN, and the connect then uses exactly the addresses the policy approved, so
nothing can change between the check and the connection.

## What is proven, and how

Run on this Mac (Apple Silicon, macOS 26.0), against the debug build.

```
cargo test -p matrx-egress                       100 passed
cargo test -p matrx-egress -- --ignored            2 passed   (they reach the public internet)
cargo clippy -p matrx-egress --all-targets         clean
```

**Bytes really move.** `src/fake_gateway.rs` stands up a real WebSocket gateway speaking the
contract's protocol, runs the real relay against it, and drives a **real TLS client** through the
relayed stream to `api.ipify.org` — a TLS handshake is the strictest available check that every
byte crossed unchanged in both directions:

```
HELLO from the helper : Hello { protocol: 1, helper_version: "0.1.0", platform: "darwin",
                                hostname: "…", client_kind: "helper", max_streams: 64 }
OPENED                : Opened { ok: true }
bytes gateway→device  : 443
bytes device→gateway  : 3498
public address through the relay : 68.4.250.160
public address fetched directly  : 68.4.250.160
status : {"state":"connected","device_name":"The test Mac","streams_total":1,"bytes_relayed":3941,…}
```

**The LAN stays out of reach**, end to end rather than at the unit:

```
       127.0.0.1 → policy: "that address points back at this computer…"
     192.168.1.1 → policy: "that address is on a private network…"
 169.254.169.254 → policy: "that address is a local-network-only address…"
       localhost → policy: "that address points back at this computer…"
```

**The whole standalone life**, against the local fake gateway
(`cargo run -p matrx-egress --example fake-gateway -- --port 8099`), in the **dev** world:

* `pair` → code printed, page opened, approval polled, the token written to the keychain **by this
  binary** (`security find-generic-password -s ai.matrx.home-connection.dev` shows service and
  account, and no email address anywhere in the item).
* Standalone start → `state: connected`, `device_name` from HELLO_ACK, `status.json` (0644) and
  `control.json` (0600) written under `~/.matrx-dev/egress/`.
* `matrx-egress status` / `pause` / `resume` → the status flips to `paused` and back to
  `connected`, the socket closes and reconnects, and `PATCH /egress/devices/{id}` is sent both
  times.
* `matrx-egress sign-out` → `DELETE /egress/devices/{id}` sent, the keychain item gone, the
  process exited, `control.json` removed, and `matrx-egress status` then answers "the AI Matrx
  Home Connection is not running on this computer."

**The menu-bar item**, read live out of the accessibility tree while the helper ran on this Mac:

```
AI Matrx Home Connection — Connected as This computer (local test)   (disabled title)
──────────
Pause          Open in AI Matrx          Turn off and remove this computer
──────────
Quit
```

…and after `matrx-egress pause`, the same menu read back with the title
`AI Matrx Home Connection — Paused` and the item reading **Resume** — the tray redraws from the
status document's revision, so it can never disagree with `status`.

**The menu when something is wrong**, read the same way on 2026-09-18 with the helper pointed at a
gateway that closes the socket with `4401`:

```
AI Matrx Home Connection — Removed from your account          (disabled title)
This computer was removed from your account —                 (disabled)
run Connect again to add it back. Open AI                      (disabled)
Matrx on the web and connect this computer                     (disabled)
again.                                                         (disabled)
──────────
Connect this computer again…
Open in AI Matrx
──────────
Quit
```

Two things to see in it. The title says WHICH kind of "not connected" this is — `signed_out` reads
"Removed from your account", `error` reads "Not connected: <the reason>" — and the sentence and its
remedy are on the lines under it, wrapped, where before there was nothing at all. And **Pause and
Resume are gone**: after a `4401` there is no connection to pause and resuming would dial with a
token the server has already refused, so the menu offers the one thing that is true instead. Same
read against a refused server, where the helper is still retrying, keeps `Pause` and carries the
reason: `AI Matrx Home Connection — Connecting` / "this computer could not reach AI Matrx:
Connection refused (os error 61) Nothing to do — it will try again in about 3 seconds."

What the menu says and what it offers is `menu::model` — plain data, no tray in it, asserted on
every platform — so the drawing in `tray.rs` decides nothing.

**The supervisor does not abandon the tray.** A `4401` or `4409` used to end `run()` while `main`
kept the menu bar item alive, so `Resume` wrote into a watch channel with no receiver and the menu
read "Connecting" for ever. The loop now parks in the terminal state instead: it stops dialling,
records which ending it was, and every surface — menu, CLI, control socket — answers with the
sentence rather than with silence. Proven failing-then-passing:
`supervisor::tests::a_removed_computer_parks_and_answers_resume_with_the_truth` fails with "the run
loop abandoned the tray" the moment that `return` comes back.

## Windows and Linux: written, reasoned, not run

This machine is a Mac, so everything below was written against its documentation and is
**unproven by execution** — named here rather than implied to work, exactly as `matrx-syncd`'s
README does for the same reason.

- **Windows start-up entry.** `reg add HKCU\…\CurrentVersion\Run`, per-user, value
  `AI Matrx Home Connection`. Never a machine-wide service: the helper holds one person's
  credential and lends one person's connection.
- **Windows home and file modes.** `%LOCALAPPDATA%\Matrx\<world>\`, which is already ACL'd to this
  user and SYSTEM; there is no `chmod`, and the code says so rather than pretending to set one.
- **Windows installer.** NSIS (`packaging/windows-installer.nsi`), because this repo already
  speaks NSIS through the Tauri bundle. It has never been compiled here; the CI job is the first
  thing that will say whether it does.
- **Windows hardware id.** `Get-CimInstance Win32_ComputerSystemProduct` rather than the engine's
  deprecated `wmic`; a machine where neither answers simply has no hardware id, never a made-up
  one.
- **Linux.** No tray at all — the module is not compiled — and the same status is printed on
  stdout. `keyring` 4.x resolves the Secret Service; a locked or absent one is a named state with
  a remedy, never a fallback to disk. The `.deb` ships a systemd **user** unit and its postinst
  prints the one command that enables it, because root cannot enable another person's user unit.

## Packaging

`scripts/build-egress.sh` builds the binary; `--sidecar` also copies it to
`desktop/src-tauri/sidecar/matrx-egress-<triple>[.exe]`, the naming rule Tauri's
`bundle.externalBin` requires. **This crate does not wire the sidecar into `tauri.conf.json` and
does not touch `desktop/`** — that is a separate change with a separate owner.

`.github/workflows/egress-helper.yml` builds and publishes the three installers named by the
contract (`AI-Matrx-Home-Connection-macos.pkg`,
`AI-Matrx-Home-Connection-windows-setup.exe`, `ai-matrx-home-connection_amd64.deb`) to **both** a
versioned `egress-v<version>` tag and the moving `egress-latest` release that the
`residential_egress.helper_download_base_url` knob points at.

## Deviations from the contract, and why

Each of these is a decision, not an oversight.

1. **`matrx-sync`'s `KeyringStore` is not reused.** It names syncd's service through
   `World::keychain_service()` and stores a Supabase refresh token plus a user id; this helper
   stores a device bearer token, a device id and a server. Sharing it would have meant editing
   that crate, which this work may not do, so `keyring` 4.2 is used directly — the same crate, the
   same three stores, the same "a locked store is a named state, never a disk fallback" rule.
2. **`run` mode defaults to NO tray.** The contract lists `--no-tray` on `run`, which implies the
   tray is the default there. A child process of the desktop app must not put a second icon in the
   person's menu bar, so `run` defaults it off; `--no-tray` is still accepted, and `--tray` turns
   it on for testing. Standalone still defaults to showing it.
3. **`pause` and `sign-out` present the DEVICE token to `PATCH`/`DELETE /egress/devices/{id}`.**
   The contract marks those routes "user JWT", and a headless helper has no user session — the
   device token for its own row is the only credential it can have. If the gateway refuses it,
   both commands still take effect on THIS computer immediately and the status says the account
   has not been told, with the remedy. **The gateway owner needs to decide** whether to accept a
   device token on its own row.
4. **The tray's web links.** Both menu items open the computers list,
   `<web>/user-settings/files/devices` (the removal item adds `?computer=<device_id>` so the page can
   bring that computer into view; Remove lives on that list and names its consequence first).
   Settled by the owner 2026-09-18; the base is the `--web` flag and both paths live in one
   place (`supervisor::WebUrls`).
5. **A `4401` does not delete the keychain item.** The status says the computer was removed and
   the helper stops; the token stays so a server-side mistake cannot cost the person their
   pairing. `sign-out` is the one command that forgets it.
6. **The macOS `.pkg` may ship unsigned.** Signing an installer needs a Developer ID *Installer*
   identity and this repository has no secret for one. The workflow signs the binary with the app
   identity it does have, builds the pkg unsigned, skips notarization, and says so loudly in the
   job summary — it never ships something that quietly fails Gatekeeper.
7. **`tray-icon` 0.21 / `tao` 0.34** are the versions this workspace's lock file already resolves
   through Tauri, so the helper adds no second copy of the objc2 stack. `muda` is used through
   `tray_icon::menu`, its own re-export, which is the only way the two can never disagree.

## Still to prove

- **The real pairing flow against the live server.** `POST /egress/pairings` and its polling route
  are being built in `aidream` as this lands. The flow here is implemented against the contract
  and proven against a mocked server and the local fake gateway; the **owner proves the live
  pairing** once the route exists.
- **Windows and Linux**, as above.
- **The installers themselves**, which have never been produced: the workflow has not run.
- **A real relay through the real gateway** (rather than the fake one), which needs the aidream
  half.
