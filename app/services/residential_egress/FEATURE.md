# Home Connection — this computer as the internet exit (residential egress)

**The contract is NOT here.** One document rules all five repos:
`/Users/armanisadeghi/code/common-docs/systems/platform/residential-egress/FEATURE.md`.
Read it before changing anything in this folder. This file covers only what the
desktop engine does.

## What it is

The user may lend **their own computer's internet connection** to AI Matrx, used
**only** after a site has blocked our datacenter address, and **only** for their
own work. The device side is one Rust binary, `matrx-egress` (crate
`crates/matrx-egress`) — the only device-side implementation there is. This
service is the engine's supervisor for that binary.

It dials OUT to the aidream gateway and holds the socket: no inbound port, no
router change, no firewall prompt, and nothing on the user's machine is exposed.

**User-facing words are plain English only.** "Home Connection", and the switch
reads *"Use this computer's internet connection when AI Matrx gets blocked."*
Never "proxy", "egress", "residential", or "IP" in anything a user reads.

## What replaced what

`app/services/proxy/` is **deleted**, with `app/api/proxy_routes.py`, the
settings `proxy_enabled` / `proxy_port`, the Settings "Proxy" tab, the `proxy*`
methods in `desktop/src/lib/api.ts`, and `/proxy/status` in `_PUBLIC_PATHS`. It
bound a loopback HTTP proxy on `127.0.0.1` that was reachable from nowhere — it
did nothing for anyone while defaulting to ON. The replacement defaults to
**off**: the user is lending their own connection, so it is an explicit opt-in.

## The pieces here

| Path | Does |
|---|---|
| `supervisor.py` | binary resolution, device registration, child spawn/stop, status, and `reconcile_residential_egress()` — the ONE decision point |
| `app/api/egress_routes.py` | `GET /egress/status`, `POST /egress/enable`, `POST /egress/disable` |
| `app/main.py` Phase 4 | starts it at boot when the setting is on |
| `app/services/daemon_session_reconciler.py` | calls the reconciler on every sign-in and sign-out |
| `app/preflight.py` `SERVICES` | reclaims an orphaned child, identity-proved |
| `desktop/src/pages/Settings.tsx` → "Home Connection" | the switch, the honest status line, "Manage in AI Matrx" |

## Lifecycle ownership (Hard Rule 0)

Identical discipline to cloudflared in `app/services/tunnel/manager.py`:

* the helper is a CHILD of the engine and of nothing else;
* PID + OS creation time + executable path are written to the discovery file
  under the key `residential_egress` **the moment the child spawns**, so a
  crashed engine's orphan can be reclaimed by the next same-world preflight —
  and only that orphan (`orphan_only=True`, `require_discovery_identity=True`);
* the same binary also runs **standalone** on a user's machine, with its own
  tray and its own keychain token. It is not our child. The identity
  requirement is exactly what keeps the sweep off it;
* `stop()` cascades — close stdin (the helper's own graceful stop), then
  SIGTERM, then SIGKILL — before reporting done. Lifespan teardown (Phase S5)
  waits for it.

## The token never rests

`POST {aidream}/egress/devices` mints a bearer that is shown exactly once.
It is written to the child's **stdin** and nowhere else: not to disk, not to
argv (argv is world-readable via `ps`), not to a log line. The test
`tests/unit/test_residential_egress_supervisor.py` asserts the child received it
AND that it is absent from the process's own cmdline.

🚨 **stdin then STAYS OPEN for the child's lifetime.** EOF on stdin is the
helper's shutdown signal, so closing the pipe after writing the token stops the
helper the instant it starts. This was a live bug during the build, caught by
`test_start_spawns_the_child_hands_it_the_token_and_reports_connected`.

## Binary resolution order

1. `MATRX_EGRESS_BINARY` — a developer-only **path VALUE**, never a toggle: it
   names WHICH binary to run and turns nothing on or off. A path that does not
   exist falls through to the order below rather than disabling the feature.
   (Developer-only, so it is not in `docs/official/configuration.md`; that file
   is edit-restricted and documents shipped configuration.)
2. the bundled sidecar next to the frozen engine (macOS `.app` `Resources/` too);
3. `<matrx home>/bin/matrx-egress` — where the standalone installer drops it;
4. the dev workspace build, `desktop/src-tauri/target/{debug,release}/`.

There is no download path. The binary is ours and ships with the app; a missing
one is the honest state `not_installed` with a remedy, never a spinner.

## Every state names itself

`GET /egress/status` returns the helper's own status JSON plus `installed`,
`enabled`, `running`, `device_id`, `uptime_seconds` and `remedy`. `state` is one
of the helper's (`connected`, `connecting`, `paused`, `signed_out`, `error`) or
one of the engine's (`not_installed`, `disabled`, `stopped`). It is never
absent, so no surface has to invent a sentence for a state it did not get.

A child that dies on its own repairs its own state — discovery entry cleared,
status file removed, registry marked `failed` with the sentence and the remedy
— rather than leaving a dead helper reading as connected.

## Packaging

`bundle.externalBin` gains `sidecar/matrx-egress` in **both**
`tauri.conf.json` and `tauri.macos.conf.json` — the overlay's array REPLACES
rather than merges, so an entry missing from the overlay ships no binary on
macOS. `pnpm ensure:egress` (→ `scripts/build-egress.sh --sidecar`) is chained
where `ensure:syncd` runs, and CI has the matching step. The frozen engine needs
no build-time knowledge of the path — it resolves it at runtime, like
cloudflared — so `scripts/build-sidecar.sh` and `specs/*.spec` are untouched.

## Open couplings and gaps

**`scripts/build-egress.sh` does not exist yet** — it belongs to the crate
(`crates/matrx-egress`), written in the same campaign. Until it lands,
`pnpm ensure:egress` fails, and with it `pnpm tauri:dev`, `pnpm tauri:build`
and the CI sidecar step. The chain is deliberately the end state rather than a
tolerant skip: a build that silently ships without the helper is the failure
this repo keeps paying for. `pnpm dev` and the Python engine are unaffected.

**Documentation.** `docs/official/settings-catalog.md` still lists `proxyEnabled` / `proxyPort`
(rows in "Scraping, proxy, remote access, notifications" and the reset-scope
table), and `docs/official/configuration.md` still describes the `base + 40`
proxy port, and `docs/official/settings-audit.md` still audits both keys.
All of it is gone; the replacement is `residentialEgressEnabled` /
`residential_egress_enabled` (default false) and the reset scope `network` now
carries `residential_egress_`. `docs/official/**` is edit-restricted, so this
note stands in until Arman approves the edit.

## Change log

- 2026-09-18 — Created. `app/services/proxy/` deleted; supervisor, routes,
  setting, Settings tab, preflight entry, sidecar packaging added.
