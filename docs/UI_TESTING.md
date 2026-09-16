# UI Testing (Playwright E2E)

The desktop UI is login-gated, which historically made it unverifiable by agents — bugs that
"worked via curl" but were dead in the UI slipped through. This harness drives the **real React
app** in Chromium, signed in as the canonical admin test account, so UI changes can be verified
end to end.

## How signing in works now (MXL-D-091)

**There is no password form in this app, and there must not be one.** The `matrx-syncd` daemon is
the device's only session holder (SPEC-CUSTODY, D17). The custody cutover removed the
email/password screen — and with it the only door this harness had, so for a while no screen of
Matrx Local could be verified at all.

The door was restored one layer lower: the harness signs the **dev-world daemon** in, before the
browser starts, and the app then adopts an ordinary session.

```bash
cd desktop
# 1. the daemon binary (once, or after touching crates/)
cargo build -p matrx-syncd            # from the repo root

# 2. sign a private dev-world daemon in as admin@admin.com
eval "$(node e2e/setup/harness-session.mjs --export)"   # exports MATRX_HOME_DIR

# 3. serve the app with that home, and run the suite
pnpm dev                              # the harness bridge reads MATRX_HOME_DIR
pnpm test:e2e
```

`e2e/setup/harness-session.mjs` performs the Supabase password grant itself with
`AI_ADMIN_USERNAME` / `AI_ADMIN_PASSWORD`, hands the daemon the resulting session through its
dev-world-only control route `POST /v1/harness/session`, and verifies `GET /v1/session` really
says signed in as that account. The password reaches neither the app, the daemon, nor disk, and
every failure exits with a reason and a remedy.

`desktop/vite-plugins/dev-syncd-bridge.ts` is what makes a browser page able to reach the daemon
at all: in Chromium there is no Tauri, so `invoke("syncd_client_config")` cannot answer. The dev
server serves the daemon's loopback endpoint and its **read** token — exactly what the packaged
webview holds — at `GET /__matrx-dev/syncd-config`. Every rule it enforces lives in
`desktop/src/lib/harness-bridge-contract.ts`:

- it is off in a production build (`bridgeEnabled`), so no shipped bundle or server has it;
- it refuses `~/.matrx`, the installed app's home, even if `MATRX_HOME_DIR` points there;
- it refuses any daemon whose `syncd.json` does not say `world: "dev"`.

The daemon carries two more guards: the route does not exist in the live world, and
`Custodian::install_harness_session` refuses there. Guards:
`src/lib/dev-harness-custody.test.ts`, `crates/matrx-sync/tests/custody.rs`
(`the_harness_door_is_refused_in_the_live_world`), `crates/matrx-syncd/src/api/routes.rs`.

### Against a built bundle

`pnpm dev` is not the only surface. `pnpm build:harness && pnpm preview:harness` builds and
serves a bundle the harness can sign into, for when a dev server is not available. `pnpm build`
— what Tauri's `beforeBuildCommand` and `scripts/release.sh` run — has no bridge at all.

## Quick start

```bash
cd desktop
pnpm test:e2e          # headless
pnpm test:e2e:headed   # watch it run
```

- Playwright auto-starts `pnpm dev` (Vite, http://localhost:1420) if it is not
  already running (`reuseExistingServer: true`). Start it yourself with the harness
  `MATRX_HOME_DIR` when you want the signed-in specs to run rather than skip.
- On failure you get a screenshot + trace under `desktop/test-results/`
  (`pnpm exec playwright show-trace <trace.zip>`).
- One-time machine setup: `pnpm install` in `desktop/` and
  `npx playwright install chromium`.

## Test account & credentials

- The canonical shared admin test account, on the ONE database
  (`https://db.matrxserver.com`, addressed only by URL):

  ```bash
  AI_ADMIN_USERNAME="admin@admin.com"
  AI_ADMIN_PASSWORD="<see AI_ADMIN_PASSWORD in .env>"
  ```

  This is the same account documented by `matrx-frontend`; do not create a
  separate Matrx Local test user when authentication is needed, and never use
  Arman's personal account.
- `harness-session.mjs` reads them from the first of `desktop/.env.test`,
  `desktop/.env`, `.env`, `../aidream/.env`, `../matrx-frontend/.env` that has
  them, or from the environment. All of those files are gitignored, and the
  script never prints, logs or writes either value.
- This identity is a **super-admin test account**. It is appropriate for
  authenticated UI/integration smoke tests, but it must never be used to prove
  ordinary-user RLS, organization membership, or entitlement behavior.
- Authenticated specs gate on `harnessIdentity()` — the daemon's own answer to
  "who is signed in on this device" — and **skip** with the remedy sentence when
  nobody signed it in, instead of failing.

If the canonical login stops working, report that exact failure instead of
signing up another account or rotating this shared password locally.

## What is covered

| Spec | Coverage |
|---|---|
| `e2e/boot.spec.ts` | **The "does the app even start" guard.** Boots the app (unauthenticated + authenticated) and fails if the ErrorBoundary fallback renders or anything throws uncaught. Runs in CI on every push against the **production bundle**. |
| `e2e/auth.spec.ts` | The sign-in screen renders unauthenticated and has **no** email or password field (asserted, so the product cannot quietly regain a second credential holder); the harness session reaches the authenticated shell; and a redirect-only route does not trap later navigation. |
| `e2e/cloud-chat-live.spec.ts` | Authenticated Cloud Chat starts a real AIDream conversation, continues it on the same client-minted ID, reloads, and verifies both replies hydrate from durable history. |
| `e2e/media-gen.spec.ts` | `/media-generation` renders with the layout switcher; **all 5 layout variants mount without crashing** (Classic / Studio / Workspace / Gallery / Focus — catches mount-crash drift); Classic → Library tab renders; Private-vault panel opens and shows its create/unlock/unlocked UI (never creates or unlocks a vault). |

Shared plumbing is in `e2e/helpers.ts` (`harnessHome`, `harnessIdentity`,
`signInViaHarness`, `probeEngine`, `dismissEngineMonitorIfOpen`).

## Browser mode vs. Tauri — the one limitation

Playwright drives the app in a plain Chromium page, **not** inside the Tauri
WebView. `window.__TAURI__` is absent, so `isTauri()` (`src/lib/sidecar.ts`)
is `false` and every Tauri-only surface is out of reach:

- No sidecar spawn, tray, auto-update, compact recorder window, llama-server
  control, native permissions, or `invoke()`-backed Rust commands.
- Engine discovery falls back to the JS `fetch()` port scan instead of the
  Rust-assisted path — the same code path `pnpm dev` in a browser uses, and it
  works. **The scan is pinned to the DEV band, 22240–22259.** 22140–22159 is the
  installed app's, it belongs to the person using this Mac, and no test may read
  or probe it (CLAUDE.md, Hard Rule 9). A `harness`-mode build is a build, so
  `import.meta.env.DEV` is false in it — until MXL-D-091 that silently put the
  browser harness on 22140. Guard: `src/lib/engine-ports.test.ts`.
- Everything else — login, routing, all pages, engine REST/WS features —
  renders and functions normally in the browser. This is exactly the supported
  `pnpm dev` development mode, so it is a faithful target for UI verification.

If the engine is not running, the app still reaches the shell with engine
status "error" and auto-opens the Engine Monitor dialog; helpers dismiss it.

## Engine dependency policy (READ-ONLY)

Specs that need the Python engine probe the harness home's `local.json` and
`GET /health` (`probeEngine()` in `e2e/helpers.ts`) — never `~/.matrx`, which is
the installed app's discovery file:

- **Live engine found** → use it **read-only**: status/list/health reads only.
  Never trigger model downloads, generation jobs, vault creation, or any
  mutation against the user's engine.
- **No engine** → those specs `test.skip` with an explicit reason. Start a dev
  engine in the harness home with
  `MATRX_HOME_DIR=$MATRX_HOME_DIR uv run python run.py` from the repo root
  (`uv sync --all-extras` first — plain `uv sync` strips extras).

Engine-independent specs (auth, page mount smoke) always run.

## Writing new specs

- Keep selectors resilient: roles + accessible names / visible text
  (`getByRole`, `getByLabel`, `getByText`) — never CSS classes.
- Prefer `exact: true` when a short name ("Refresh", "Private") could collide
  with other accessible names on the page (strict-mode violations).
- Routes use `HashRouter`: deep-link as `/#/media-generation`, or better,
  navigate by clicking the real sidebar links.
- The React ErrorBoundary fallback text is `"Something went wrong"` —
  asserting its absence is the cheap mount-crash check.
- Open the app via `signInViaHarness(page)`; gate on `harnessIdentity()` /
  `probeEngine()` with `test.skip(...)` and a human-actionable reason.

## CI

Out of scope for now — the suite is local-only. The path when we want it:
GitHub Actions job that builds `matrx-syncd`, runs
`node e2e/setup/harness-session.mjs` with `AI_ADMIN_USERNAME`/`AI_ADMIN_PASSWORD`
injected as repo secrets, then `pnpm dev` + `playwright test` with the
`MATRX_HOME_DIR` it printed (no engine → engine-dependent specs skip by design),
uploading the HTML report as an artifact.
