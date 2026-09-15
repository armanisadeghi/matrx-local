# `matrx-syncd` — the AI Matrx folder-sync daemon

**What it is today: FS-C5's slice, and nothing more.** It is the device's only session holder
(SPEC-CUSTODY, DECISIONS D17): it runs the PKCE transaction, owns the OS keychain item, rotates
headlessly, and serves the five custody-owned auth routes plus `GET /v1/health`,
`GET /v1/version`, `GET /v1/events` and `POST /v1/shutdown`.

**It does not sync files.** The mapping routes, the watchers, the planner execution, the login-item
registration and the supervisor handshake are FS-L2a's. A route SPEC-ENGINE's table lists but this
build does not serve answers **404 with the §3.1 error envelope** — never a stub with a plausible
shape.

Contracts, frozen at G1: `common-docs/projects/folder-sync/specs/SPEC-CUSTODY.md` @ `c5ae35ad`,
`SPEC-ENGINE.md` @ `6c226529`, `CONTRACT-RULINGS.md` @ `894d04ef`.

## The two transports, one API (C6, C7)

| | macOS / Linux | Windows |
|---|---|---|
| Control endpoint | `<home>/run/syncd.sock`, socket 0600, parent dir 0700 | `\\.\pipe\matrx-syncd-<world>-<sha1(user SID)[:12]>`, `first_pipe_instance` |
| Loopback API | one TCP listener in the daemon band, `base+2 … base+19` then `base+0`, skipping the fixed OAuth port | same |
| Home | `$MATRX_HOME_DIR`, default `~/.matrx` (dev `~/.matrx-dev`) | `%LOCALAPPDATA%\Matrx\<world>\` |
| Discovery · tokens · journal | `syncd.json` · `syncd.token` · `syncd.db` in that home | same |

Both transports serve the **identical** `/v1` routes with the **identical** bearer auth. The Tauri
host and the Python engine prefer the socket/pipe; the webview is a browser context and uses the
listener, under §3's `Origin` allow-list, its CORS headers and the `Host` allow-list that is the
actual DNS-rebinding guard.

**Ports (C7, S21).** Daemon band 22160–22179 live / 22260–22279 dev — never inside the engine's
22140–22159 / 22240–22259. The ONE fixed port is the OAuth loopback callback, 22161 / 22261, fixed
only because Supabase matches redirect URIs exactly.

**Scopes (S17).** `syncd.token` is one file, two lines: line 1 `control`, line 2 `read`. `control`
authorises everything and never enters JavaScript; `read` authorises `GET /v1/token`,
`/v1/session`, `/v1/status`, `/v1/version` and `/v1/events` and is rejected elsewhere with
`403 forbidden_scope`. Both are minted fresh at every start.

## Windows and Linux: written, reasoned, not run

This machine is a Mac, so the Windows and Linux paths below were written against their
documentation and are **unproven by execution**. They are named here rather than implied to work.

- **Windows named pipe.** `ServerOptions::first_pipe_instance(true)` on the first instance refuses
  to attach to a pipe another process already created under our name; each accepted client is
  handed off and a fresh instance is created before the next `connect()`. The per-user suffix is
  `sha1(user SID)[:12]` — the SID, not the username, because two accounts can share a display name
  across a domain trust while SIDs never collide. The SID is read with the documented
  `OpenProcessToken` → `GetTokenInformation(TokenUser)` → `ConvertSidToStringSidW` sequence.
- **Windows file modes.** There is no `chmod`. Isolation comes from `%LOCALAPPDATA%`, which is
  already ACL'd to this user and SYSTEM, and files created there inherit it. `set_dir_mode_0700`
  is a documented no-op on Windows rather than a pretence.
- **Windows liveness.** `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` then `CloseHandle`. Query
  only: the daemon is never signalled or terminated (rule 9, D19).
- **Linux keychain.** `keyring` 4.2.0's default `v1` features resolve the Secret Service store.
  A locked or absent Secret Service is normal on a headless session, and there is **no second
  store to try** — so the daemon runs in memory for that session and publishes
  `credential_store_unavailable` with its remedy. It never falls back to disk (S7).
- **Linux scheme handler.** A source run has no `x-scheme-handler/aimatrx` MimeType entry, which
  is exactly why S3 makes the loopback redirect the dev world's only leg.

## FS-C5 proof

Every command below was run on this machine, in the **dev world** (`~/.matrx-dev`, daemon band
22260–22279), against the debug build at
`desktop/src-tauri/target/debug/matrx-syncd`. The packaged app and its live world were never
touched (Hard Rule 9).

### Start, discovery and file modes

```
$ matrx-syncd --version
matrx-syncd 0.1.0 (matrx-sync 0.1.0)

$ matrx-syncd &
[syncd] matrx-syncd 0.1.0 serving the dev world on /Users/…/.matrx-dev/run/syncd.sock (loopback 22262)
[syncd] session state on start: signed_out (Sign in on this computer to start syncing your folders.)

$ cat ~/.matrx-dev/syncd.json
{"version":1,"world":"dev","pid":27684,"socket_path":"/Users/…/.matrx-dev/run/syncd.sock",
 "tcp_port":22262,"daemon_version":"0.1.0","started_at":"2026-09-15T06:36:05Z"}

$ ls -l ~/.matrx-dev/syncd.json ~/.matrx-dev/syncd.token ~/.matrx-dev/run/syncd.sock
srw-------  …  /Users/…/.matrx-dev/run/syncd.sock
-rw-------  …  /Users/…/.matrx-dev/syncd.json
-rw-------  …  /Users/…/.matrx-dev/syncd.token
```

The discovery file carries **no token and no token path** (C5); the port taken is 22262, i.e. the
allocator skipped the reserved OAuth port 22261.

### Auth, scopes and the browser guards (loopback listener)

| Request | Result |
|---|---|
| `GET /v1/health`, no credential | `200 {"ok":true,"protocol_version":1,"state":"signed_out","world":"dev"}` |
| `GET /v1/session`, no token | `401 unauthorized` with its remedy |
| `GET /v1/session`, read token, no `X-Matrx-Client` | `400 bad_request`, `details.missing_header` |
| `GET /v1/session`, read token + header | `200`, the full snapshot |
| `POST /v1/sign-out`, **read** token | `403 forbidden_scope` — a page cannot sign this device out (§12) |
| `GET /v1/token` while signed out | `409 {"state":"signed_out","state_reason":…,"since":…}` — never a 500, never an empty 200 |
| `Host: evil.example:22262` | `403 forbidden_origin`, `details.host` |
| `Origin: https://evil.example` | `403 forbidden_origin`, `details.origin` |
| `OPTIONS /v1/token`, `Origin: http://localhost:1420` | `204` + `access-control-allow-origin: http://localhost:1420` (echoed, never `*`), `vary: Origin`, `allow-headers` including `X-Matrx-Client`, `allow-methods: GET, OPTIONS`, `max-age: 600` |

### Both transports serve the same API

```
$ curl --unix-socket ~/.matrx-dev/run/syncd.sock -H "Authorization: Bearer $CONTROL" \
       -H "X-Matrx-Client: engine" http://localhost/v1/version
{"daemon_version":"0.1.0","protocol_version":1,"min_protocol_version":1,
 "executable_path":"…/target/debug/matrx-syncd","world":"dev"}
```

### Sign-in transaction, and S3's loopback redirect bound on both families

```
$ curl --unix-socket … -X POST … -d '{}' http://localhost/v1/sign-in
{"transaction_id":"xQn6u8VpbDdAmxUbxGlJKw",
 "authorize_url":"https://db.matrxserver.com/auth/v1/oauth/authorize?response_type=code
   &client_id=af37ec97-…&redirect_uri=http%3A%2F%2Flocalhost%3A22261%2Foauth%2Fcallback
   &state=…&code_challenge=…&code_challenge_method=S256&scope=email%20profile",
 "redirect_uri":"http://localhost:22261/oauth/callback","redirect_kind":"loopback"}

$ lsof -nP -iTCP:22261 -sTCP:LISTEN
matrx-syn 27684 …  TCP 127.0.0.1:22261 (LISTEN)
matrx-syn 27684 …  TCP [::1]:22261 (LISTEN)
```

Both loopback families are bound, so either resolution of `localhost` lands (S3). The URL carries
the S256 challenge and **not** the verifier, and omits `openid` deliberately.

### A callback this daemon never issued (S14)

```
$ curl … -d '{"code":"x","state":"not-ours"}' http://localhost/v1/sign-in/callback
409 {"error":{"code":"unknown_transaction",
  "message":"no sign-in is in progress for that link on this computer",
  "remedy":"That sign-in link belongs to a different copy of AI Matrx (or has expired) — start
            sign-in again from the app you want to sign in to."}}
```

### A route this build does not serve

```
$ curl … http://localhost/v1/mappings
404 {"error":{"code":"not_found",
  "message":"This version of AI Matrx Sync does not serve that request yet.",
  "remedy":"Update AI Matrx.","details":{"method":"GET","path":"/v1/mappings"}}}
```

### The event stream (C8) and graceful shutdown (§1.3)

```
$ curl -N -H "Authorization: Bearer $READ" -H "X-Matrx-Client: app" \
       http://127.0.0.1:22262/v1/events    # while a sign-out happened
event: session.changed
data: {"session":{"signed_in":false,"state":"signed_out",
       "state_reason":"Signed out on this device — sign in again to resume syncing.",…},
       "rotated":false}

$ curl … -X POST … -d '{"reason":"test"}' http://localhost/v1/shutdown
202 {"accepted":true,"budget_s":20}
[syncd] shutdown requested
[syncd] stopped cleanly
$ ls ~/.matrx-dev/syncd.json ~/.matrx-dev/run/syncd.sock
ls: … No such file or directory      # both removed; the journal WAL was checkpointed
```

### Automated

```
$ cargo test -p matrx-sync --test custody     # 25 passed  (the §13 seam battery)
$ cargo test -p matrx-syncd                   # 15 passed
$ cargo clippy -p matrx-syncd --all-targets -- -D warnings   # clean
```

### Still to prove

The end-to-end sign-in as `admin@admin.com`, the hand-out to both consumers, rotation with the app
dead, and the sign-out keychain wipe are the remaining real-machine proofs, and they need S3's two
loopback redirect URIs registered on OAuth client `af37ec97-…` first — SPEC-CUSTODY S3 names that
registration as FS-C5's own remaining task. Until they are recorded here, **nothing in this file
claims them.**
