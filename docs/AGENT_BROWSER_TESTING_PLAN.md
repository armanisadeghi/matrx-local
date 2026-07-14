# Agent Browser Testing Surface — Plan

> **Status:** Long-term agent task — filed, not yet started. Approved for the
> backlog by Arman 2026-07-14. See `.matrx/AGENT_TASKS.md` → TASK: Agent
> Browser Testing Surface.
>
> **One-line goal:** Give agents running *inside* Matrx Local a real,
> CDP-controllable Chromium so they can drive websites and UIs — especially
> **localhost dev servers** — click, inspect, capture console/network/errors,
> screenshot, and diagnose problems end-to-end.

---

## The reframing that produced this plan

The trigger was "most sites block our Browser tab — what does it take to ship
our own Chromium?" Investigating the code changed the question entirely.

### What the "Browser" tab is today (and why it's useless)

The current Browser tab (`desktop/src/pages/BrowserLab.tsx`) is **not a browser
engine problem** — it's an `<iframe>` pointed the wrong way. Two sub-tabs:

- **`basic`** (`BrowserPage.tsx`) — raw `<iframe src={url}>`. This *is* a real
  engine (the Tauri webview), but it embeds the target as a **child frame**, so
  any site sending `X-Frame-Options: DENY` or CSP `frame-ancestors` (Google,
  YouTube, most major apps) refuses to render. That's a server-side directive
  the embedder cannot override. Unfixable from the iframe side.
- **`tauri`** (`TauriFetchBrowser.tsx`) — Rust `reqwest` fetches the raw HTML
  (`proxy_fetch`, `lib.rs:~1229`, spoofed Chrome UA), strips CSP meta tags,
  points an iframe at a `blob:` URL. Dodges framing, but it's **one static HTML
  document** — no cookies, no session, no JS execution against the real origin,
  no subresource/XHR from the right origin. Every SPA renders blank. Its own
  header comment admits this.

### Key insight #1 — we are not missing a browser engine

Tauri v2 already ships a Chromium-class engine (WebView2 on Windows, WKWebView
on macOS, WebKitGTK on Linux). "Ship our own Chromium" describes a *symptom*.

### Key insight #2 — a native webview panel is the WRONG fix for THIS purpose

A native top-level `WebviewWindow` would fix human browsing (top-level
navigation is not subject to X-Frame-Options). But this feature is **for
agents, not humans.** A window an agent can't introspect gives the agent
nothing: no click/keystroke injection, no DOM read, no console capture, no
network interception, no screenshots it can see. Rendering a page ≠ controlling
and observing a page.

### Key insight #3 — what agents need is CDP, and we already bundle it

The primitive for agent UI-testing is the **Chrome DevTools Protocol**:
`navigate`, `click`, `type`, `evaluate(js)`, console-log capture, network
interception, `Page.captureScreenshot`. **Playwright is a CDP driver over a real
Chromium — and it already lives in the package** at
`scraper-service/app/core/fetcher/browser_pool.py`. It's currently wired only
for one-shot scraping (fetch HTML → extract text), not exposed as an
interactive, multi-step agent surface.

**So the real project is not "embed a browser." It is "expose the Chromium we
already bundle as a first-class agent tool surface."**

### Key insight #4 — being LOCAL is the whole advantage

The purpose is agents testing **localhost dev servers** and diagnosing them.
Because the browser and the dev server live on the same machine, the agent can:
`pnpm dev` via the existing shell tool → drive local Chromium straight at
`http://localhost:3000`. No tunnel, no CORS, no deploy, no signed URLs. This is
the strongest reason to build it in matrx-local rather than cloud/sandbox.

### Key insight #5 — the real packaging cost is the Chromium binary, not Playwright

`scripts/build-sidecar.sh:225`: **"Playwright browsers will be auto-installed at
runtime (not bundled)."** Playwright the *library* is a declared dep with
hidden-imports in all 4 `specs/*.spec`, but the ~150 MB Chromium is a **runtime
download**, not shipped in the sidecar. Today that's fine for occasional
scraping; for a core agent-testing surface, a first-run 150 MB download (or a
missing browser on an offline machine) is a real product concern to decide on:
bundle it (installer bloat, Hard Rules 6–7) vs. managed first-run download with
a proper "downloading browser" STATE (per the states-not-errors doctrine).

---

## What to build

A long-lived, **headed**, agent-driven browser session exposed through the
existing ~80-tool dispatcher, backed by the Playwright/Chromium already in the
package.

### 1. Managed browser session service
- A persistent Playwright **headed** browser (persistent context for cookies)
  as a managed service in `app/launcher.py` + registry (Hard Rule 0:
  start/ready/stopping/stopped, diagnostic snapshot on failure). **Not** spawned
  ad-hoc per tool call.
- Reuse / extend `scraper-service`'s `browser_pool.py` rather than forking a
  second Chromium lifecycle — one browser-management path (Build-the-platform).
- Dev/live isolation (Hard Rule 9): dev sessions must not collide with a
  packaged app's browser/profile.

### 2. Agent tool set (`app/tools/tools/browser_*`)
The bulk of the work — Python over the existing Playwright:
- `browser.navigate(url)`, `browser.back/forward/reload`
- `browser.click`, `browser.type`, `browser.fill`, `browser.press`,
  `browser.select`, `browser.hover`, `browser.scroll`
- `browser.eval(js)` / `browser.snapshot_dom` (accessibility tree preferred over
  raw HTML for agent legibility)
- `browser.screenshot` (returned as a tool result the agent can see)
- `browser.get_console`, `browser.get_page_errors`, `browser.get_network`
  (requests/responses, failed statuses) — **the diagnostic payload that makes
  this a diagnosis tool, not just a clicker.** `page.on("console")`,
  `page.on("pageerror")`, `page.on("request"/"response")`.
- `browser.wait_for` (selector / load state / network idle)

### 3. Human-visible view (so the user watches the agent work)
- MVP: a visible headed Chromium window is enough.
- Better: CDP `Page.startScreencast` streamed into an in-app tab. More work;
  defer past MVP.
- The current iframe Browser tab is **replaced/repurposed**, not left beside
  this as a dead variant (annihilate-what-you-replace).

### 4. localhost workflow glue
- Agent spins up a dev server (existing shell tool), then drives the browser at
  `localhost`. Optionally a convenience that ties "start server → wait for port
  → open browser" together.

---

## The real obstacles (decide before building)

1. **Chromium bundling vs. runtime download.** ~150 MB. Bundle (installer bloat,
   4× specs, code-signing the Chromium helpers on macOS à la Hard Rule 7) vs.
   managed first-run download surfaced as a STATE. **Arman decision needed.**
2. **PyInstaller + Playwright browser path.** Getting a bundled Chromium found at
   runtime in the frozen sidecar is the classic packaging headache (browser
   path / `PLAYWRIGHT_BROWSERS_PATH`). Only bites if we choose to bundle.
3. **Headed browser on an end-user machine.** Window management; must not feel
   like malware popping windows; visible-in-app vs. floating OS window.
4. **Session lifecycle.** Long-lived headed browser = managed service in the
   launcher/registry, not per-call spawn (Hard Rule 0).
5. **Reusing vs. forking the scraper's browser pool.** Prefer extending
   `browser_pool.py`; avoid a second independent Chromium lifecycle.
6. **Security surface.** An agent-driven browser with `eval` and localhost reach
   is powerful. Scope which origins/ports agents may drive; keep it a local,
   user-visible action.

---

## What this is explicitly NOT
- **Not** a native Tauri webview panel (wrong tool — no agent control/observability).
- **Not** bundling CEF / building Chromium from scratch (Playwright already
  bundles a Chromium; that's the 150 MB we already reference).
- **Not** the scraper's one-shot `proxy_fetch` / fetch-and-extract path.
- **Not** a fix to the human `<iframe>` Browser tab — that tab is superseded.

---

## Open questions for Arman (when this is picked up)
- Bundle Chromium in the installer, or managed first-run download-as-a-STATE?
- Headed visible window MVP acceptable, or is in-app screencast required for v1?
- Any origin/port allowlist policy for what agents may drive?
- Does this share the sandbox/cloud agent-browser story, or is local-only the
  scope (localhost testing is the stated driver)?
