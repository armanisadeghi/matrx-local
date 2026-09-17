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
`403 forbidden_scope`.

**The token file's lifetime is the daemon's.** Both tokens are minted fresh at every start and the
file is **deleted on a graceful shutdown**, alongside `syncd.json` and the socket. A file that
outlives the process that minted it authorises nothing — the next daemon mints different tokens —
so leaving it behind only puts a live-looking credential on disk for no one. The rule this places
on every consumer: **re-read `syncd.json` AND `syncd.token` together on each reconnect**, never
cache either across a daemon restart. The port, the socket path and both tokens all change.
`desktop/src/lib/custodian.ts` does this through `resetCustodianConfig()`, and
`app/services/sync_client` reads both files per request.

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
touched (Hard Rule 9). The isolated packaged smoke harness also selects the dev world explicitly,
even though its app binary is a release build, so its private home never selects the live Keychain
service.

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

### The real sign-in, as `admin@admin.com`

S3's two loopback redirect URIs were the "remaining task for FS-C5 (small)" the spec names. They
are now registered on OAuth client `af37ec97-…` (additively; nothing was removed):

```
http://localhost:22161/oauth/callback      (live)
http://localhost:22261/oauth/callback      (dev)
```

The flow, run end to end against the **live** authorization server:

```
POST /v1/sign-in            → authorize_url with the S256 challenge, redirect_kind "loopback"
GET  <authorize_url>        → 302 to the consent UI with an authorization_id
(consent granted as admin@admin.com)
GET  http://localhost:22261/oauth/callback?code=…&state=…      ← the daemon's OWN listener
[syncd/custody] loopback sign-in completed

GET /v1/session
{"signed_in":true,"user_id":"87a6e699-3622-4869-8843-d0867456c0dd","email":"admin@admin.com",
 "state":"signed_in","state_reason":"Signed in and syncing.","since":"2026-09-15T06:52:42Z",
 "cloud_state_write_pending":false}
```

**The daemon exchanged the code.** The verifier never left it, and nothing but the daemon ever saw
a refresh token.

### The token, handed to both consumers

```
# as the Python engine, control scope, over the Unix socket
GET /v1/token → {"token_type":"Bearer","user_id":"87a6e699-…","expires_at":"2026-09-22T06:52:42Z"}
  decoded JWT: sub=87a6e699-…  email=admin@admin.com  role=authenticated  (1442 chars)

# as the webview, READ scope, over the loopback listener with Origin: tauri://localhost
GET /v1/token → the same user_id, the same expiry
```

One holder, two consumers, and the webview's token authorises nothing else (`POST /v1/sign-out`
with it is `403 forbidden_scope`, proven above).

### Custody

```
$ security find-generic-password -s com.aimatrx.syncd.dev
  "svce"<blob>="com.aimatrx.syncd.dev"
  "acct"<blob>="87a6e699-3622-4869-8843-d0867456c0dd"
```

Service is the **dev** world's; the account is the Supabase user id, so **no item name contains an
email address** (§4). The journal's `session_state` row holds the state, the email and the expiry —
and no token:

```
$ sqlite3 ~/.matrx-dev/syncd.db 'select state, email, last_refresh_at from session_state'
signed_in|admin@admin.com|2026-09-15T06:52:42Z
```

### What the access token's lifetime actually is — a spec fact that did not hold

SPEC-CUSTODY §3.1 records "default access-token lifetime 3600 s" as VERIFIED from Supabase's docs,
and S9's worked example is "with 3600 s, T+36 m". **On this project the access token lives
604 800 s (7 days)** — `exp − iat` on the real token, matching the `expires_in` the token endpoint
returned:

```
daemon says expires_at : 2026-09-22T06:52:42Z
JWT exp claim          : 2026-09-22T06:52:42Z
JWT iat claim          : 2026-09-15T06:52:42Z   → 604800 s
```

Two consequences, both recorded rather than worked around:

1. Nothing in the implementation assumed 3600 — the schedule is computed from the token, so S9
   still holds; it simply lands at ~4.2 days here instead of 36 minutes.
2. **The timer-driven rotation cannot be observed in an afternoon**, so this file does not claim
   it. What *is* proven below is the rotation that matters most and that MXL-D-046 never had: the
   daemon renewing the session **from the keychain, with nothing else running**.

This is an amendment-worthy observation for SPEC-CUSTODY §3.1/S9 and is reported, not edited in.

### Five defects the live run found, each fixed and each with a test proven failing first

Unit tests did not find these. Running the thing did.

1. **A cancelled sign-in leaked its loopback listener.** S5 says a second `POST /v1/sign-in`
   cancels the first, and S3 *fixes* the redirect port, so every later sign-in failed
   `loopback_port_unavailable` until the daemon restarted. Releasing it needs `abort()` **and the
   await** — abort only requests cancellation, and the task still owns the socket until it stops.
2. **The loopback task aborted itself mid-exchange.** Completing a sign-in released the listener,
   and the completion ran *inside* that very task, so the token exchange was cancelled and the
   sign-in vanished with no log line at all.
3. **The loopback page claimed success before the exchange happened.** It said "You are signed in
   to AI Matrx" while the daemon had not yet asked for a token — and on the run above it said that
   while the sign-in was in fact being lost. It now says only that the sign-in was received.
4. **A keychain approval dialog hung the daemon forever.** macOS binds an item's ACL to the
   creating binary's designated requirement, so a rebuilt binary prompts — and a login-time daemon
   has no window to answer in. S15 names this shape exactly. Every credential-store call is now
   bounded, and the bound must stay well under the hand-out budget or the caller is told `offline`,
   a true refusal with a false cause.
5. **A refusal wore the wrong sentence.** `GET /v1/token` answered
   `{"state":"signed_out","state_reason":"Signed in and syncing."}` — the state came from the live
   session and the sentence from a journal row that had not caught up. A refusal's sentence now
   always belongs to the state it reports.

A sixth, found while stopping the daemon: **`POST /v1/shutdown` answered `202 accepted` and then
did nothing** when it arrived before the run loop reached its `select!` — which is precisely the
window in which the daemon is adopting its session and most likely to be stuck. The notification
now leaves a permit for the next waiter, and adoption runs as a task so the daemon is controllable
from its first moment.

### The keychain dialog, as a state rather than a hang

Starting a **rebuilt** binary against an item an older build created is the S15 situation. It now
answers in three seconds instead of never:

```
[syncd] session state on start: credential_store_unavailable (macOS is asking permission for AI
  Matrx Sync to use your keychain, and a background service has no window to ask in. Open AI Matrx
  and sign in again — the prompt appears while the app is in front, and allowing it once is enough.)

GET /v1/session → {"state":"credential_store_unavailable","signed_in":false,
                   "user_id":"87a6e699-…","email":"admin@admin.com", …}
GET /v1/token   → 409, the same state and the same sentence
```

The remedy names **this** machine's situation. Telling a Mac user to install gnome-keyring was the
first version of this sentence, and it was a remedy that helped nobody.

### Sign-out (§9, S20)

```
GET  /v1/session   → signed_in, admin@admin.com
security find-generic-password -s com.aimatrx.syncd.dev
  "svce"="com.aimatrx.syncd.dev"  "acct"="87a6e699-3622-4869-8843-d0867456c0dd"

POST /v1/sign-out  → {"ok":true}

security find-generic-password -s com.aimatrx.syncd.dev
  security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.

GET  /v1/session   → {"state":"signed_out",
                      "state_reason":"Signed out on this device — sign in again to resume syncing."}
GET  /v1/token     → 409, the same state and the same sentence
sqlite3 syncd.db   → signed_out | Signed out on this device — … | cloud_state_write_pending = 1
```

**The keychain item is gone**, and no revocation was attempted — S20 is explicit that the only
revocation on this path is per *grant*, which would sign the account out of every Matrx device the
user owns. `cloud_state_write_pending = 1` is honest rather than cosmetic: this device has no
`app_instances` row yet (that is FS-L2a's), so it owns no `files.sync_mappings` rows to write
`signed_out` into. The journal says the write is outstanding instead of pretending it happened.

### Headless rotation — the proof MXL-D-046 never had

No app, no browser, no human. The daemon is the only thing of ours alive in the dev world:

```
$ pgrep -fl matrx-syncd
72850 desktop/src-tauri/target/debug/matrx-syncd

served token iat                    = 1789486920
POST /v1/shutdown                   → 202, "stopped cleanly"
(start the binary again — nothing else)
[syncd] session state on start: signed_in (Signed in and syncing.)
served token iat after restart      = 1789486928     ← a NEW access token
sqlite3 syncd.db → signed_in | last_refresh_at 2026-09-15T15:42:08Z
```

The daemon signed itself in **from the OS keychain** and minted a fresh access token by presenting
the refresh token to the live authorization server, with no UI anywhere on the machine. That is the
capability the React-pushed token path never had, and the reason D17 exists.

What this does *not* yet prove is the **timer-driven** rotation at `0.6 × lifetime`, because this
project's access tokens live 604 800 s: the schedule lands ~4.2 days out. The rotation *mechanism*
— present the stored refresh token, take the new one, write it ahead of use — is the same code path
in both cases and is exercised here against the real server.

### Still to prove

* **The timer-driven rotation** at `0.6 × lifetime` — ~4.2 days out on this project, for the reason
  recorded above. The mechanism is proven; the timer is not.
* **Windows and Linux** — written and reasoned, never run; see the section above.
* **The webview and the engine actually consuming the token.** `desktop/src/lib/supabase.ts` still
  constructs its client with its own session, and `TokenRepo` still reads the local `auth_tokens`
  row. SPEC-CUSTODY §10 makes that switch **one atomic release** across 43 `supabase.auth.*` call
  sites in 21 files — setting the `accessToken` option makes `supabase.auth` throw on every access,
  so a half-switch cannot ship. It is **not** landed here. Until it is, this daemon is an
  *additional* session holder rather than the only one, and nothing in this file claims otherwise.

## FS-C5b — the app side of the cutover

`origin/main` `e6a3a1491`. The webview, the Tauri host and the Python engine all became token
*consumers* in one commit; there is no build in that series where the app holds a refresh token.

**Proven, mechanically:**

* `pnpm typecheck` clean across the 21 repointed files; `cargo build -p aimatrx-desktop` clean;
  152 Rust tests; 373 Python tests.
* The §13 grep guard finds no `setSession`, `persistSession`, `autoRefreshToken` or
  `refresh_token` anywhere in `desktop/src` outside generated API types.
* `supabase.auth` cannot be reached at all: the client is built with `accessToken`, which makes
  every property access on that namespace throw. A half-switch cannot compile, let alone ship.
* The local `auth_tokens` table is dropped by local-DB migration 34, so a residual encrypted
  refresh token cannot survive the upgrade.

### The cutover's own migration (CS-19, landed after the above)

FS-C5b shipped in 1.4.124 **without carrying the session the previous release had**, and that is
how a Mac signed in on 2026-09-14 reported `{"state":"signed_out","world":"live"}` on 2026-09-15:
the daemon's journal is created by the release that introduced the daemon, so `resume()` found no
row and could only say `signed_out`; local-DB migration 34 DROPPED `auth_tokens` without reading
it; and the webview's own pre-cutover Supabase entry — the one surviving refresh token on the
machine — was left unread because the new client cannot see it.

`POST /v1/adopt` (control scope) + `Custodian::adopt_legacy_session` close that: the app offers
that entry once, the daemon presents it on the ordinary refresh grant, takes custody and logs
`carried over the session this computer already had for …`. A definitive refusal becomes
`sign_in_needed` carrying `CUTOVER_SIGN_IN_REMEDY`, which the sign-in screen and every engine
lane's blocker now show verbatim; a transport failure is `retry` and spends nothing. Both halves
are proven failing-then-passing (`crates/matrx-sync/tests/custody.rs`,
`desktop/src/lib/legacy-session-handover.test.ts`, `desktop/src/pages/Login.test.tsx`,
`tests/unit/test_session_freshness.py`), and the hand-run §13 grep is now the mechanical
`pnpm check:session-custody` with its own self-test.

**NOT proven here:** the handover running inside the packaged app on a real pre-cutover home.
The one place that could be proven is Arman's installed 1.4.124, which agents do not touch.

**NOT proven here, and not claimed:** the end-to-end run *through the app UI* — sign in from the
window, a data query and a `realtime.setAuth()` observed in the live webview, quit-and-relaunch
still signed in, sign out from the UI. Two concrete reasons, both about this machine rather than
the code:

1. `pnpm tauri:dev` needs port **1420**, and the daemon's dev CORS allow-list permits exactly that
   origin (SPEC-ENGINE §3). Another session on this shared checkout holds 1420 with
   `vite preview --strictPort`, and taking it from them is not mine to do. Moving the app to
   another port would mean widening the allow-list to pass a test, which is falsifying the
   contract rather than satisfying it.
2. Driving the packaged window instead needs a user-approved automation grant, and no human is
   present in this session to approve one.

What the packaged **smoke** run does cover is the startup surface this change touches, and it is
CLEAN on a real packaged artifact (`./scripts/smoke.sh packaged`, isolated home, isolated engine
ports):

```
app.log line 1:  [syncd] started matrx-syncd (pid 3848)
✅ packaged: app exited cleanly on a graceful quit signal in 16s
✅ packaged: no orphaned children after shutdown
✅ packaged app log: no fatal lines in the log
✅ packaged: pre-existing live engine remained healthy and PID-stable
Result: CLEAN
```

The app's very first log line is SPEC-ENGINE §1.2 step 3 — the app ensuring the daemon is running
— and the isolated home afterwards holds `syncd.db` but **no** `syncd.json` and **no**
`syncd.token`, which is the teardown rule above, observed on the packaged build.

**A note for FS-L2a while it is fresh.** An isolated smoke run gives the daemon an isolated
`MATRX_HOME_DIR`, so its journal, discovery file and tokens are isolated — but its *world* is
still decided by `debug_assertions`, so a release smoke build allocates from the **live** daemon
band (22160–22179) and would name a keychain item in the live service namespace if a sign-in ever
happened during one. The engine solves the same problem with `VITE_MATRX_TEST_ENGINE_PORT_BASE`;
the daemon has no equivalent yet. Harmless today (the allocator simply takes a free port, and
smoke never signs in), and it belongs with the rest of the lifecycle work.
