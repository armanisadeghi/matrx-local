---
status: active
updated: 2026-07-18
repos: [matrx-local, matrx-extend]
owner-context: Extension bridge pairing — root cause fixed + shipped in both repos; packaged end-to-end verification on a real machine NEVER RUN. Pick up at "Pickup plan" below.
---

# Handoff — matrx-local ↔ matrx-extend bridge pairing

**One sentence:** the desktop↔extension bridge was dead because the extension
required a pair code the desktop never issued; both halves are now built,
released, and unit/smoke-verified — but the **packaged end-to-end test on a
real machine has never been run**, and that is the next gate.

Read first: [/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md](/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md)
(the full bridge contract; § Auth model documents pairing).

## The vision (Arman's requirements, in his words where possible)

1. **The app and the extension must actually talk — 100%.** Every control in
   the extension's Admin → Debug → Bridges → "Channel B" panel (Re-discover,
   Health, Version, Capabilities, tool test, WS Connect) must work, and the
   desktop must show connected extensions. Before this work: all of them
   failed and the desktop showed none.
2. **Never assume one extension.** "When you have different Chrome profiles,
   you are going to have multiple different instances… it can't assume that
   there's just one, and it needs to look for all of them." One WS session
   per profile; the desktop lists them all.
3. **It must work for real users, not just this machine.** A fix is not done
   until the code is pushed, CI builds it, and the **built packages** are
   tested: matrx-extend rebuild ≈ 60 s (`pnpm zip`, reload unpacked);
   matrx-local ≈ 30 min (`./scripts/release.sh` → version bump + tag → GitHub
   Action → download dmg → install).
4. **Never hot-patch the running installed app** to fake a result. Inspecting
   the live app is fine; the thing you test is the released build.
5. House doctrine that applies here: states-not-errors (a missing pairing is
   a state with a remediation, never a red error), loud recovery, and the
   dev/live isolation rule (Hard Rule 9 — dev engines live on ports
   22240-22259 and `~/.matrx-dev`; the extension only probes the LIVE range
   22140-22159).

## Root cause (found 2026-07-18, fixed same day)

- matrx-extend's security audit **P1-5** removed the "send the user's
  Supabase JWT to a probed localhost port" fallback and required an explicit
  pair token (`src/lib/desktop/http.ts`). Correct decision — but the
  **issuance half was never built**: matrx-local had no pair-code
  generation, storage, display, or acceptance. The "Pair code" box in the
  extension's Settings → Desktop bridge expected a code that existed nowhere.
- Worse, the engine's `/extension/*` auth (`app/api/extension_auth.py`)
  rejected any bearer that didn't parse as a JWT — so even a pasted random
  code would 401.
- Consequence: every HTTP RPC short-circuited on "desktop not paired", the
  WS could never authenticate, `/extension/sessions` was permanently empty.
  Discovery itself was always fine (`/health` is public and schema-valid).

## What shipped

**matrx-local — commit `081138d3b`, released v1.3.128 (installed app 1.3.137
already contains it):**

| Piece | File | Behavior |
|---|---|---|
| Pairing token service | `app/services/pairing.py` | Persistent secret `~/.matrx/pairing.json` (`mxl_pair_` + 32 urlsafe bytes, chmod 600), minted lazily, stable across restarts; `matches_pair_token` is constant-time; `rotate_pair_token()` exists (no UI yet). |
| Bootstrap endpoint | `POST/GET /extension/pair` in `app/api/extension_routes.py` | Returns `{pair_token, engine_version, service}`. Token-free on direct loopback (listed in `_LOCAL_BOOTSTRAP_PATHS`, `app/api/auth.py:106`); **hard-rejects tunnel-originated requests** (cf-* headers → 403). |
| Auth acceptance | `app/api/extension_auth.py` | Pair token checked BEFORE the JWT paths; principal gets `via_pairing=True` and skips the tunnel owner check (a paired caller is an owner device). |
| Desktop UI | `desktop/src/pages/BridgeTest.tsx` (`PairingSection`), `desktop/src/lib/api.ts` (`extensionGetPairInfo`) | Settings → Bridge Test → "Extension pairing": masked code + Reveal/Copy, for manually pairing a browser on ANOTHER machine. Sessions panel already lists N sessions with extension id/version. |
| Tests | `tests/smoke/test_extension_channel.py` | 4 new tests vs a REAL engine: unauthenticated issuance + persistence, tunnel-header rejection, pair-token auth over HTTP **and** WS, wrong-token 401. Boot-check route list updated (`app/api/extension_boot_check.py`). |

**matrx-extend — commits `626de24` + `23c5a40`, v0.1.65 (pushed; zip at
`.output/matrx-extend-0.1.65-chrome.zip`):**

| Piece | File | Behavior |
|---|---|---|
| Auto-pair | `src/lib/desktop/http.ts` — `autoPair()`, `ensurePairToken()` | On first bridge use, POSTs `/extension/pair` and stores the token in `chrome.storage.local` (per Chrome profile — this is what makes multi-profile "just work"). No user credential is ever transmitted, so P1-5 stays satisfied. |
| 401 self-heal | `rpcHttp` | A 401 clears the stored token, re-pairs, retries ONCE. Covers token rotation, engine-home wipes, and poisoned tokens from a port-squatting impostor. |
| WS | `src/lib/desktop/ws-client.ts` | WS connect resolves its `?token=` through `ensurePairToken`. |
| Schema | `src/lib/desktop/types.ts` — `DesktopPairResponseSchema` | Validates the pair response (must self-identify as matrx-local). |
| Debug UI | `src/features/debug/BridgesView.tsx` | "paired" row + **Re-pair** button in Channel B → Discovery. |
| Settings copy | `src/features/settings/SettingsView.tsx` | Explains pairing is automatic locally; the code box is only for remote machines. |

**Verified so far (all green, 2026-07-18):** engine smoke 11/11 (incl. full
pair→RPC→WS round trip on a real booted engine), characterization 23/23,
desktop tsc clean, extension tsc clean + 190/190 vitest, both builds, release
v1.3.128 published with all platform assets. Ten later engine releases
(→1.3.138) did **not** touch any pairing file (verified by diff).

## Gap analysis — vision vs. now

| # | Gap | Severity | Detail |
|---|---|---|---|
| G1 | **Packaged E2E never run** | BLOCKING GATE | The walkthrough below was written for Arman but not executed (engine was down at handoff time). Nothing about pairing has ever been observed working from a real Chrome extension against the installed app. |
| G2 | Multi-profile proof | HIGH (vision #2) | Engine + UI support N sessions, but ≥2 profiles connected simultaneously has never been observed live. |
| G3 | Reverse invoke live proof | HIGH | Engine→extension (`/extension/invoke` → `read_page`) is smoke-tested with a simulated extension only. |
| G4 | Remote (tunnel) pairing untested | MED | Manual copy flow (desktop Bridge Test code → remote extension Settings) is designed + code-complete but never exercised. Note P1-5's "move WS token out of the query string" is still open on the extension side. |
| G5 | Pair-token rotation has no surface | LOW | `rotate_pair_token()` exists; no endpoint/button calls it. If added, extensions recover via the 401 self-heal automatically. |
| G6 | Non-debug desktop surface | LOW | The pair code + sessions live only on the Bridge Test page (admin-ish). Fine for now; a user-grade "Connected browsers" card in Settings is a possible follow-up. |
| G7 | Broadcast + native-messaging substrates | OUT OF SCOPE HERE | Supabase Broadcast fallback and `connectNative` were untouched by this work; they have their own status in the tracker/docs. |

## Pickup plan — run the E2E gate (do this first)

Preconditions: installed app ≥1.3.128 (currently 1.3.137 in /Applications),
extension 0.1.65 loaded unpacked from `matrx-extend/.output/chrome-mv3` (or
rebuild: `cd matrx-extend && pnpm zip`). The engine must be RUNNING (launch
AI Matrx; `curl -s http://127.0.0.1:22140/health` returns JSON).

1. **Engine-side sanity (terminal):**
   ```bash
   curl -s -X POST http://127.0.0.1:22140/extension/pair   # → {"pair_token":"mxl_pair_…"}
   # must equal: python3 -c "import json;print(json.load(open('/Users/armanisadeghi/.matrx/pairing.json'))['pair_token'])"
   TOK=<that token>
   curl -s http://127.0.0.1:22140/extension/sessions -H "Authorization: Bearer $TOK"   # sessions:[] for now
   ```
2. **Extension:** side panel → Debug (admin-gated) → Bridges → Channel B:
   Re-discover → expect `http` transport + engine version; "paired" row →
   `yes` (auto-pairs on first use — already-yes is success, not a skipped
   step); Health / Version / Capabilities → `ok:true` (capabilities ≈80
   tools); Call tool `SystemInfo` → real data; WS Connect → state `open`,
   ping/pong in the log.
3. **Engine side again:** `/extension/sessions` now shows the session with
   `extension_id` + `extension_version: 0.1.65`; `/extension/metrics` has
   rows for `tool`, `ws:connect`, `pair`. The desktop Bridge Test page shows
   the same list.
4. **Multi-profile (G2):** repeat step 2 in a second Chrome profile → 2
   sessions, one per profile.
5. **Reverse invoke (G3):** desktop Bridge Test → Invoke panel → pick a
   session, tool `read_page`, args `{"mode":"text"}` → page text from that
   profile returns. (`action`-tier tools prompt the user in the browser;
   `privileged`/`ask-user` tiers are rejected by design.)
6. **Failure modes you might hit:** "auto-pairing failed (engine offline or
   pre-pairing version)" = engine down or <1.3.128; 401 loops = check
   `~/.matrx/pairing.json` matches what `/extension/pair` returns; extension
   can't find engine at all = it only probes 22140-22159 (a DEV engine on
   22240+ needs the port override in the Bridges panel).
7. **When all pass:** update this doc's status + the tracker row; close the
   G1 gate; then decide with Arman which of G2-G6 to pursue.

## Rules that bite here (don't relearn these)

- **Dev/live isolation (matrx-local Hard Rule 9):** never point a source-run
  engine at `~/.matrx` or ports 22140-22159. Testing dev-engine pairing =
  port override 22240 in the extension's Bridges panel.
- **Release flow:** matrx-local ships ONLY via `./scripts/release.sh`
  (bumps + tags + pushes; GH Action builds; then
  `xattr -cr '/Applications/AI Matrx.app'` after install). Committing code
  without releasing changes nothing for the packaged test.
- **Adding `/extension/*` routes:** update `_EXPECTED` in
  `app/api/extension_boot_check.py` AND, for token-free routes, decide
  loopback posture in `app/api/auth.py` (`_LOCAL_BOOTSTRAP_PATHS`).
  `HANDLERS` commands are pinned by
  `tests/characterization/test_extension_rpc_characterization.py`.
- **Never probe `/extension/rpc` without a bearer** (trips auth-warning rate
  limiter); discovery uses public `/health` only.
- The extension repo may carry unrelated uncommitted changes from other
  sessions — commit only your files.
