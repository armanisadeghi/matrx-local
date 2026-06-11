# Agent Tasks

Active worklist managed by agents. **See `AGENT_INSTRUCTIONS.md` for rules** — especially around task format, condensation, and when to ask the user.

> Quick scan order for arriving agents:
> 1. **Needs Clarification** below (questions waiting on the user)
> 2. **Blocked** (waiting on external)
> 3. **Active** (`ready` and `in-progress`)
> 4. **Completed** (recent context, condensed)

---

## Needs Clarification

_(none)_

## Blocked

_(none)_

## Active

### Bug-hunt wave 1 — deferred items (2026-06-11 full-system audit)
A comprehensive audit fixed ~150 verified bugs (see commits 9ca5652..ab1f3c8).
These were found, verified, and deliberately deferred:

- [ ] **Rust: LlmServerState mutex held across 120s health wait** — status
  commands block during model load. Shutdown is now covered via the PID
  handle; releasing the lock during the wait needs a careful refactor with
  local cargo-check. (`llm/commands.rs:90`, `llm/server.rs:39-189`)
- [ ] **Rust: audio capture is f32-only** — `build_input_stream` fails on
  I16/U16-default devices (common on Linux/ALSA); match `sample_format()`
  and convert. (`transcription/audio_capture.rs:69-102`)
- [ ] **Rust: vulkaninfo VRAM parse order inverted** — `size =` precedes
  `DEVICE_LOCAL` in real output, so AMD/Intel VRAM is never detected and
  tier selection lowballs (24GB GPU → tiny model + gpu_layers=1).
  (`transcription/hardware.rs:227-260`)
- [ ] **Rust: floating overlay mixes physical/logical px** on scaled
  secondary monitors; `sidecar_status` hardcodes port 22140.
- [ ] **Unmounted tool panels carry latent bugs** (BrowserPanel runner,
  SchedulerPanel/RecordAudio arg mismatches, TerminalPanel stale closures,
  AutomationPanel render-loop, KeyValueField index keys) — fix before
  re-wiring them into Tools.tsx. Only Monitoring/Process panels are live
  (both fixed).
- [ ] **Engine: dead stub files** (audio/player.py, tts/player.py,
  transcription/transcribe.py, files/explorer.py, files/uploader.py,
  audio/recorder.py) — no importers; delete or implement.
- [ ] **use-chat-tts**: chunks are serialized but `speakText` resolves at
  synthesis end, not playback drain — a small gap between sentences can
  remain; gapless append-mode scheduling is the follow-up.
- [ ] **extension_auth reject-log window** (`_REJECT_LOG_WINDOW_SECONDS`
  dead constant, unbounded `_reject_log_state`) — logging-only.
- [ ] **capabilities/setup pip installs break in frozen builds**
  (`sys.executable -m pip` is the sidecar binary) — needs a packaged-build
  strategy, not a code tweak.


_(none)_

---

## Completed

- [BUG] /extension/* auth log noise + misleading "JWT validation DISABLED" warning. The deployed binary fired (a) "JWT validation DISABLED — neither SUPABASE_JWT_SECRET nor SUPABASE_URL is configured" *while* SUPABASE_URL was actually configured (incoming tokens were HS256 but engine had no secret, so the bypass-to-degraded path was triggered with the wrong message), and (b) per-request "JWKS validation failed: kid …" + "missing Bearer token" WARNINGs every 2s during /extension/rpc polling, completely drowning the engine log. Split `_log_degraded_mode_once` into reason-specific variants (`no_paths` vs `hs256_no_secret`), added `_debug_log_jwks_failure` that suppresses repeats by `(kid, error_type)`, and added `_log_rejection` that logs the first miss as WARNING then demotes to DEBUG with a periodic 60s rate-summary at INFO. `app/api/extension_auth.py`. 2026-05-08
- [BUG] /devices/permissions endpoint took 21s on every page hit (cold ``check_all_permissions`` runs 15 OS probes including macOS ``system_profiler SP{Audio,Camera,Bluetooth,AirPort}DataType``, which serialise on the private cfprefsd IPC). Frontend abort timeout was 15s so the call never completed → `console.error("Failed to load permissions:", err)` → `Failed to load permissions: {}` in the captured log (Error props are non-enumerable, JSON.stringify drops them). Added a 30s TTL in-process cache with single-flight refresh in `app/api/permissions_routes.py`, exposed `force_refresh=true` query param, invalidated the cache from the Windows grant route, bumped the frontend timeout to 35s, surfaced `forceRefresh` on `engine.getDevicePermissions()`, and routed permissions/ports error logs through `logWarn` so they capture `.message` and `.stack` instead of `{}`. 2026-05-08
- [BUG] Frontend race on page mount: `engineStatus` flips to `"connected"` the moment REST is reachable, but `connectWebSocket()` runs slightly later in `use-engine.ts` (it waits for the Supabase session token). Pages that called `engine.invokeToolWs(...)` inside an `engineStatus === "connected"` `useEffect` would throw `WebSocket not connected` on first render. Added `engine.isWsConnected()` + `engine.waitForWs(timeoutMs)` and made `invokeToolWs` wait up to 3s for the upgrade before failing; `Ports.tsx` skips the tick if WS still isn't ready. `desktop/src/lib/api.ts`, `desktop/src/pages/Ports.tsx`. 2026-05-08
- [BUG] llama-server crash on launch (macOS bundled app): `dyld: Library not loaded: @rpath/libllama-common.0.dylib`. `download-llama-server.sh` was downloading the dylibs into `desktop/src-tauri/binaries/` and `install_name_tool` was rewriting the rpath to `@executable_path/../Resources/binaries`, but `tauri.macos.conf.json` had no `bundle.resources` entry to copy them into the bundle — so `Contents/Resources/binaries/` did not exist on installed apps and every dylib lookup failed. Mirrored the existing Windows pattern by adding `"resources": { "binaries/*.dylib": "binaries/" }` to `tauri.macos.conf.json`. Next signed/notarized build will ship the dylibs alongside `llama-server` and local LLM inference will work again. 2026-05-08
- [ENH] Gemma 4 unblocked: bumped llama.cpp b8519→b9076, refreshed all 4 catalog entries with HF-verified byte sizes (E2B/E4B/26B-A4B/31B). 2026-05-08
- [ENH] Boot self-check: every engine startup verifies /extension/* routes registered + JWT posture + tunnel/metrics/discovery, logs summary, GET/POST /extension/boot-check endpoints + Bridge Test panel. 2026-05-07 (3f911668)
- [ENH] /extension/tunnel/status: runtime introspection of Cloudflare tunnel state with preferred-mode logic (MATRX_PREFER_TUNNEL); Bridge Test sub-panel surfaces it with re-pair hint when engine prefers tunnel. 2026-05-07 (82fdce6a)
- [ENH] /extension/* observability: in-memory metrics (count, errors, last-N latencies) per command via app/api/extension_metrics.py + GET /extension/metrics + Metrics panel in Bridge Test. 2026-05-07 (648a17e1)
- [ENH] /extension/* JWT validation: real Supabase JWKS (preferred) + HS256 (fallback) signature + expiry check via new app/api/extension_auth.py. Loopback fallback preserved when SUPABASE_JWT_SECRET and SUPABASE_URL both unset (loud one-time WARNING). 2026-05-07 (61a26f7b)
