# UI Testing (Playwright E2E)

The desktop UI is Supabase-login-gated, which historically made it unverifiable
by agents — bugs that "worked via curl" but were dead in the UI slipped through.
This harness drives the **real React app** in Chromium with a **dedicated test
account**, so UI changes can be verified end-to-end.

## Quick start

```bash
cd desktop
pnpm test:e2e          # headless
pnpm test:e2e:headed   # watch it run
```

- Playwright auto-starts `pnpm dev` (Vite, http://localhost:1420) if it is not
  already running (`reuseExistingServer: true`).
- On failure you get a screenshot + trace under `desktop/test-results/`
  (`pnpm exec playwright show-trace <trace.zip>`).
- One-time machine setup: `pnpm install` in `desktop/` and
  `npx playwright install chromium`.

## Test account & credentials

- Canonical frontend test login on Supabase instance `txzxabzwovsujtloxrus`:

  ```bash
  AI_ADMIN_USERNAME="admin@admin.com"
  AI_ADMIN_PASSWORD="Password1234#"
  ```

  This is the same account documented by `matrx-frontend`; do not create a
  separate Matrx Local test user when authentication is needed.
- Credentials live in **`desktop/.env.test`**. The harness prefers
  `AI_ADMIN_USERNAME` / `AI_ADMIN_PASSWORD` and accepts the legacy
  `TEST_USER_EMAIL` / `TEST_USER_PASSWORD` aliases. The file is gitignored.
- This identity is a **super-admin test account**. It is appropriate for
  authenticated UI/integration smoke tests, but it must never be used to prove
  ordinary-user RLS, organization membership, or entitlement behavior.
- `pnpm test:e2e:setup` (= `node e2e/setup/create-test-user.mjs`) verifies the
  canonical credentials still sign in and writes the gitignored `.env.test`;
  it is idempotent and safe to run any time.
  If `.env.test` is missing, auth-dependent specs **skip** with a clear reason
  instead of failing.

If the canonical login stops working, report that exact failure instead of
signing up another account or rotating this shared password locally.

## What is covered

| Spec | Coverage |
|---|---|
| `e2e/boot.spec.ts` | **The "does the app even start" guard.** Boots the app (unauthenticated + authenticated) and fails if the ErrorBoundary fallback renders or anything throws uncaught. Runs in CI on every push against the **production bundle**. |
| `e2e/auth.spec.ts` | Login page renders unauthenticated; real email/password sign-in reaches the authenticated shell (sidebar nav). |
| `e2e/cloud-chat-live.spec.ts` | Authenticated Cloud Chat starts a real AIDream conversation, continues it on the same client-minted ID, reloads, and verifies both replies hydrate from durable history. |
| `e2e/media-gen.spec.ts` | `/media-generation` renders with the layout switcher; **all 5 layout variants mount without crashing** (Classic / Studio / Workspace / Gallery / Focus — catches mount-crash drift); Classic → Library tab renders; Private-vault panel opens and shows its create/unlock/unlocked UI (never creates or unlocks a vault). |

Shared plumbing is in `e2e/helpers.ts` (`loadTestCreds`, `loginViaUI`,
`probeEngine`, `dismissEngineMonitorIfOpen`).

## Browser mode vs. Tauri — the one limitation

Playwright drives the app in a plain Chromium page, **not** inside the Tauri
WebView. `window.__TAURI__` is absent, so `isTauri()` (`src/lib/sidecar.ts`)
is `false` and every Tauri-only surface is out of reach:

- No sidecar spawn, tray, auto-update, compact recorder window, llama-server
  control, native permissions, or `invoke()`-backed Rust commands.
- Engine discovery falls back to the JS `fetch()` port scan (22140-22159)
  instead of the Rust-assisted path — this is the same code path `pnpm dev`
  in a browser uses, and it works.
- Everything else — login, routing, all pages, engine REST/WS features —
  renders and functions normally in the browser. This is exactly the supported
  `pnpm dev` development mode, so it is a faithful target for UI verification.

If the engine is not running, the app still reaches the shell with engine
status "error" and auto-opens the Engine Monitor dialog; helpers dismiss it.

## Engine dependency policy (READ-ONLY)

Specs that need the Python engine probe `~/.matrx/local.json` and `GET /health`
(`probeEngine()` in `e2e/helpers.ts`):

- **Live engine found** → use it **read-only**: status/list/health reads only.
  Never trigger model downloads, generation jobs, vault creation, or any
  mutation against the user's engine.
- **No engine** → those specs `test.skip` with an explicit reason
  (start one with `uv sync && uv run python run.py` from the repo root).

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
- Log in via `loginViaUI(page, creds)`; gate on `loadTestCreds()` /
  `probeEngine()` with `test.skip(...)` and a human-actionable reason.

## CI

Out of scope for now — the suite is local-only. The path when we want it:
GitHub Actions job that runs `pnpm dev` + `playwright test` with
`AI_ADMIN_USERNAME`/`AI_ADMIN_PASSWORD` injected as repo secrets (no engine →
engine-dependent specs skip by design), uploading the HTML report as an
artifact.
