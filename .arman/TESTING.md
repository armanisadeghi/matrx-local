## Your testing flow explained

**Quick dev testing (use this daily — no build needed):**
```bash
# Terminal 1
uv run python run.py

# Terminal 2  
cd desktop && pnpm dev  # opens http://localhost:1420 in browser
```
This covers 95% of everything — all tools, all pages, auth, scraping, API. Takes 10 seconds to start.

**What it CANNOT test:** tray behavior, autostart on login, native OS file pickers, Tauri system dialogs, the sidecar spawning lifecycle. Those only work in the full Tauri build.

**Full Tauri desktop test (weekly or before releases):**
```bash
bash scripts/launch.sh
```
This opens 2–3 terminals: one for the engine log, one for `pnpm tauri:dev`. The reason for multiple terminals is intentional — each long-running process gets its own window so you can see their output independently. On WSL it opens Windows Terminal tabs, on macOS it opens Terminal.app tabs. That's expected behavior.

**When do you need to build the sidecar?** Only when preparing a production release. For daily development, the dev stub is used (405-byte placeholder). Run `bash scripts/build-sidecar.sh` only when you're about to cut a release tag.

---

## Cleanup script

```bash
bash scripts/stop.sh          # graceful (SIGTERM then SIGKILL if needed)
bash scripts/stop.sh --force  # immediate SIGKILL
```

This kills: Python engine, Vite server, Tauri window, Matrx Engine sidecar (and legacy aimatrx-engine processes from before the rename), any process on ports 22140–22159 and 1420. Then prints port status to confirm they're free. You can run it any time — if nothing is running it just confirms everything is clean.

---

## Agent priority queue (in `.matrx/AGENT_TASKS.md`)

_Stale snapshot — verify against `.matrx/AGENT_TASKS.md` before acting. Historical list:_

The next 7 highest-priority items for the agent team, in order:

1. **Dashboard profile "Not Found"** — core UX broken
2. **Dashboard browser engine "standby"** — wrong status
3. **Documents New Folder/Note broken** — core feature broken
4. **Documents sync is cosmetic** — no real sync happening
5. **Web search tool argument errors** — tool completely non-functional
6. **Notify tool does nothing** — tool non-functional
7. **Record Audio broken** — tool non-functional

After those P0 fixes, the P1 work is: Scraping UX overhaul, scrollable tool outputs, file pickers for path-required tools.