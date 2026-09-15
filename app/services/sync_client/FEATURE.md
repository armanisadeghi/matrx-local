# `sync_client` — the engine asks the daemon for a token

**Register item:** FS-C5. **Contract:** `common-docs/projects/folder-sync/specs/SPEC-CUSTODY.md`
@ `c5ae35ad` §6, under `CONTRACT-RULINGS.md` C5/C6/C7. **Ruling:** DECISIONS D17.

## The one sentence

The Python engine is a **token consumer, never a token holder**. It asks `matrx-syncd` for a
short-lived access token and keeps it in memory only. There is no refresh token anywhere in
Python, which is what makes the MXL-D-046 class — a UI-pushed token nothing headless can renew —
structurally impossible rather than merely fixed.

## What it is

| | |
|---|---|
| Entry point | `get_sync_client()` → `SyncDaemonClient` |
| Token | `await client.access_token()` → a JWT, or `None` |
| State | `await client.session()` → a `SessionSnapshot`, always |
| Transport | the per-user Unix socket on macOS/Linux; the loopback TCP listener on Windows (C7) |
| Credential | line 1 of `<home>/syncd.token` — the **control** scope (S17) |
| Discovery | `<home>/syncd.json` (C5). **Never** the engine's `local.json`, which has a single-publisher clobber guard and six readers |
| World | `MATRX_HOME_DIR`, the same variable `run.py` already sets — so the engine and the daemon are always in the same world (Hard Rule 9) |

## The rules this module exists to keep

1. **It never raises because there is no session.** Signed out, sign-in needed, offline, the
   keychain unavailable, the daemon not running — each is a `SessionSnapshot` with a state and a
   remedy sentence. A local model, the local tools and the file browser are not gated on a token
   (S13), so a missing session must never propagate as an exception.
2. **It never refreshes and never stores** (S12). A 401 is answered by asking again with
   `force=True`, at most once a minute; the daemon decides whether that becomes a rotation.
3. **It caches until 30 s before expiry and no longer** (S11), in memory, never on disk.
4. **An expiry it cannot read means "do not cache"** — never "cache forever". MXL-D-046 was
   exactly the opposite mistake.
5. **The five session states are the ONE honest-state enum's session slice** (C3). Nothing here
   invents a sixth. `daemon_not_running` is SPEC-ENGINE's device state and is the only value this
   module adds, because "the daemon is not there" is not a session condition.

## Tests

`tests/unit/test_sync_client.py` runs against a **real Unix socket** speaking a real HTTP/1.1
conversation, so the transport selection, the httpx UDS transport, the header contract and the
parsing are exercised for real; only the daemon's decision is scripted. The daemon's own evidence
is a live run recorded in `crates/matrx-syncd/README.md` § "FS-C5 proof".

## Not yet wired

`TokenRepo` still reads the local `auth_tokens` row. Repointing it here is SPEC-CUSTODY §10 step 4,
and §10 is explicit that it happens **after** the webview stops holding a session — one release, no
moment with two rotating holders. Until that lands, this module is the seam and nothing consumes it.
