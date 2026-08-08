# Matrx Extend ↔ Matrx Local Connection

> Where the Chrome extension meets the desktop engine. Living doc; update on
> every protocol change. Everything below is SHIPPED unless explicitly marked
> pending — verified against live code and installed packages 2026-08-08.

## Role

`matrx-local` is the Tauri v2 desktop engine that exposes a local OS surface
(filesystem, shell, Whisper transcription, llama-server, OpenWakeWord, the
unified scrape proxy, ~80 dispatcher tools) over a loopback FastAPI sidecar.
`matrx-extend` is the Chrome extension that drives a frontier browser-agent
harness. The extension *invokes* the local OS surface (extension → local)
**and** services engine-pushed tool invocations when local code needs to
drive the browser (local → extension). This document is the contract for both
directions, the auth model, and how to add new commands without breaking the
channel.

## Surface area (all shipped)

| File | What it does | Direction |
|---|---|---|
| `app/api/extension_routes.py` | `POST /extension/rpc` (dispatches into `HANDLERS`) + the `/extension/ws` reverse-push WebSocket route. | both |
| `app/services/pairing.py` + `POST/GET /extension/pair` (in `extension_routes.py`) | **Pairing bootstrap.** Engine-issued persistent secret (`~/.matrx/pairing.json`, `mxl_pair_…`). The endpoint is token-free on direct loopback (local-bootstrap path) and HARD-REJECTED over the tunnel; the extension auto-pairs through it with zero user action, and the desktop Bridge Test page shows the code for manual remote pairing. The token authenticates all `/extension/*` (HTTP + WS), including over the tunnel (a paired caller is an owner device — skips the owner check). | extension → local |
| `app/api/extension_handlers.py` | The `HANDLERS` registry — `health`, `version`, `capabilities`, `tool` (generic dispatcher passthrough). Plus `invoke_command(command, args, request=None)` — the transport-agnostic dispatch every non-HTTP transport uses. **One command surface for all transports.** | extension → local |
| `app/tools/dispatcher.py` | `dispatch(tool_name, tool_input, session) -> ToolResult` — the public function the `tool` command uses to reach all ~80 engine tools. | extension → local |
| `app/api/extension_invoke.py` | `invoke_extension_tool(tool_name, args, session_id, timeout_seconds=30)` — engine-side outbound RPC primitive. Sends `extension.invoke` over `/extension/ws`, awaits the correlated `extension.result` by `callId`. | local → extension |
| `app/api/extension_ws_manager.py` | Per-connection session registry + callId-keyed Future table for `/extension/ws`. | local → extension |
| `app/api/extension_bridge_routes.py` | Introspection/driver endpoints: `GET /extension/sessions`, `POST /extension/invoke` (HTTP wrapper around `invoke_extension_tool`), `POST /extension/sessions/disconnect`, broadcast status/test, metrics, boot-check, `WS /extension/bridge-events`. | both |
| `app/api/extension_broadcast.py` | Supabase Broadcast substrate — per-user channel `matrx-local-bridge:<userId>`. `connect_broadcast` / `disconnect_broadcast` / `publish_to_extension` / `publish_envelope`. Inbound envelopes route into the dispatcher (below). | both |
| `app/api/cross_component_envelope.py` | v2 CrossComponentEnvelope Python mirror (`v`, `kind`, `direction`, `action`, `requestId`, `payload`, `timestamp`, `fromInstance`, `toInstance`). Mirrors `matrx-frontend/lib/types/bridge-envelope.ts` and `matrx-extend/src/lib/messaging/cross-component-envelope.ts`. | both |
| `app/api/cross_component_router.py` | The inbound Broadcast dispatcher. `kind:"rpc"` envelopes are invoked against the SAME `HANDLERS` registry as `/extension/rpc` and answered with a reply envelope (`action: "<action>.result"`, same `requestId`, `direction: "local->extension"`). `wake` (Phase 3c) and `presence` remain log-only. | extension → local |

Extension-side counterparts (in matrx-extend): `src/lib/desktop/discovery.ts`
(local port probe 22140–22159 plus owner-RLS `app_instances.tunnel_url`
discovery), `src/lib/desktop/http.ts` (`rpcHttp` →
`POST /extension/rpc`, pairing token), `src/lib/desktop/ws-client.ts` +
`ws-offscreen.ts` (MV3 offscreen-document WebSocket), and the reverse-invoke
consumer in `src/lib/background/bootstrap.ts`
(`registerWsReverseInvocationHandler` → `handleWebmcpCall` → tool registry →
`extension.result`). The `desktop_run_command` privileged tool
(`src/lib/tools/handlers/privileged.ts`) is the browser→engine caller: agents
invoke any `HANDLERS` command through it, including the generic `tool`
passthrough to the whole dispatcher catalog.

## Message envelopes (versioned)

1. **`/extension/rpc` (HTTP):** request `{command, args}` → response
   `{ok, data?, error?}` (`DesktopRpcResponse`). Tool-layer failures ride
   inside `data` as `{ok: false, error, error_type}` so the extension can
   distinguish "never ran" from "ran and failed".
2. **`/extension/ws` (reverse push):**
   - engine → extension: `{"type":"extension.invoke","callId","toolName","args"}`
   - extension → engine: `{"type":"extension.result","callId","ok",("result"|"error","errorType"?)}`
   - liveness: `{"type":"ping"}` ⇄ `{"type":"pong", engine_version, tool_catalog_hash}`;
     a hash mismatch makes the extension refetch `capabilities`.
   - on connect the engine sends `{"type":"hello", session_id, engine_version, tool_catalog_hash}`.
3. **Supabase Broadcast (cross-machine fallback):** v2 CrossComponentEnvelope,
   **`v: 2`** (version field; v1 publishers omit it and all v2 fields — they
   parse with defaults). Inbound `kind:"rpc"` dispatches `action` as a
   `HANDLERS` command with `payload` as its args; the reply envelope carries
   the `invoke_command` result (`{ok, data?}` / `{ok:false, error, error_type}`)
   in `payload`, `action: "<action>.result"`, and the caller's `requestId`.
   Bump `v` ONLY on a breaking wire change; keep parse back-compat one version.

## Observability

Every call into `/extension/*` (HTTP RPC, the introspection endpoints, WS
lifecycle on `/extension/ws`) and every Broadcast rpc dispatch (metric name
`broadcast:<command>`) is timed and counted by an in-memory ring in
`app/api/extension_metrics.py`. Resets on engine restart by design.

  * `GET  /extension/metrics` — JSON snapshot per command name
    (`tool`, `bridge:invoke`, `ws:connect`, `ws:message`, `broadcast:tool`, …)
    with `count`, `error_count`, `last_n_latencies_ms` (≤100),
    `last_called_at`, `last_error`.
  * `POST /extension/metrics/reset` — drops every row. Idempotent.

Bounds: latency ring caps at 100 samples/command; 200 distinct commands (a
synthetic `_overflow` row appears past the cap). The desktop **Bridge Test**
page (Settings → Bridge Test) polls these and renders count / errors / p50 /
p95 per command.

### Boot self-check

Every engine startup verifies: all `/extension/*` routes registered, JWT
posture (incl. bad-token smoke test), tunnel-state singleton, metrics reset,
discovery file validity. Logged as a `[boot] …` block; cached at
`GET /extension/boot-check`, re-runnable via `POST /extension/boot-check/run`.
See `app/api/extension_boot_check.py`. A failed check never blocks startup.

## Substrates

The engine is reachable via **three** paths:

  1. **Local loopback (always)** — `http://127.0.0.1:<port>` (`22140` default;
     auto-scans `22140–22159`). Default for the extension.
  2. **Cloudflare tunnel (when active)** — `https://<random>.trycloudflare.com`
     (quick mode) or a stable URL (named mode, `CLOUDFLARE_TUNNEL_TOKEN`).
     Same FastAPI app, same routes, same auth. See the runbook below for the
     full remote-control chain.
  3. **Supabase Broadcast (cross-machine fallback)** — per-user channel
     `matrx-local-bridge:<userId>`; rpc envelopes dispatch into the same
     `HANDLERS` registry. Gated by the `extension_broadcast_enabled` setting
     (default ON). The engine subscribes on authenticated boot/token
     configuration and disconnects during sign-out/shutdown.

### Discovery primitives

- **`~/.matrx/local.json`** — bootstrap discovery file: `port`, `host`,
  `url`, `ws`, `pid`, `version`, plus `tunnel_url` / `tunnel_ws` when the
  tunnel is active (written on boot auto-start AND on `POST /tunnel/start`).
- **`GET /extension/tunnel/status`** (authenticated) — live runtime state:
  `active`, `local_url`, `mode` (`quick`/`named`), `uptime_seconds`, and a
  `preferred` hint (`"tunnel"` only when up AND `MATRX_PREFER_TUNNEL=true`).
  Backed by `app/api/tunnel_state.py`.

## Auth model

- Bearer token in `Authorization` header (REST) or `?token=` query param (WS
  upgrades — browsers cannot set WS headers).
- The extension uses the ENGINE-ISSUED pairing token — it never sends the raw
  Supabase access token to a probed localhost port (audit P1-5). Pairing is
  zero-touch on the same machine: on first use the extension calls
  `POST /extension/pair` (loopback-only) and stores the returned
  `mxl_pair_…` secret; a 401 later (rotated/stale token) auto-clears and
  re-pairs once. Remote browsers pair manually by copying the code from the
  desktop app (Settings → Bridge Test → Extension pairing) into the
  extension's Settings → Desktop bridge → Pair code.
- `extension_auth` accepts the pair token as a bearer BEFORE the JWT paths
  (constant-time compare, `app/services/pairing.py`); pair-token principals
  carry `via_pairing=True` and skip the tunnel owner check.
- The app-wide `AuthMiddleware` recognizes a valid pair token only for
  `/extension/*`, allowing the request to reach the extension-specific
  validator. The same token on every unrelated remote route still fails
  normal Supabase verification. `/extension/pair` owns its tunnel-origin
  rejection and always returns the contractual 403 remotely.
- **`/extension/*` validation** (`app/api/extension_auth.py::validate_extension_principal`):
  a malformed bearer (not a JWT) fails closed with 401. Well-formed tokens:
  1. **JWKS / asymmetric (RS256/ES256)** — verified locally against
     `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`; fails closed.
  2. **HS256 / unverifiable** — introspected against the Supabase auth
     server; if unconfirmed: rejected over the tunnel, accepted
     presence-only over loopback (the socket is the boundary).
  Over the tunnel there is additionally an **owner check** — a valid token
  from a different AI Matrx user gets 403 on `/extension/*` and `/sandbox/*`.
- Missing token → HTTP 401 / WS close 1008, always. The principal lands on
  `request.state.principal` (`verified=True` only via crypto/introspection).
- Port discovery: the extension probes `GET /health` (public) across
  22140–22159 and caches the winner 30 min. Never probe `/extension/rpc`
  without a bearer — it trips AuthMiddleware warnings.

## Adding a command inbound (extension → matrx-local)

One registration serves BOTH transports (HTTP rpc + Broadcast rpc):

1. **Pick a name.** Lowercase, dotted if domain-scoped (`fs.list_directory`).
   Renaming is a breaking change for the extension.
2. **Register in `app/api/extension_handlers.py`:**
   ```python
   @register("fs.list_directory")
   async def fs_list_directory(args: Dict[str, Any], req: Optional[Request]) -> Dict[str, Any]:
       ...
   ```
   `req` is the FastAPI Request over HTTP and `None` over Broadcast —
   handlers must tolerate None or reject loudly.
3. **For tool invocations use the existing generic `tool` command** — never
   one handler per tool.
4. **Auth/validation** — never trust `args`; re-validate paths/commands.
5. **Errors** — raise; the HTTP route converts to `ok=false`, the Broadcast
   router converts to a `{ok:false, error, error_type}` reply payload.
6. **Update** `tests/characterization/test_extension_rpc_characterization.py`
   (`EXPECTED_COMMANDS` is pinned) and the extension client.
7. **Test with curl** (port/token from `~/.matrx/local.json`):
   ```bash
   curl -s -X POST http://127.0.0.1:22140/extension/rpc \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"command":"tool","args":{"tool_name":"SystemInfo","tool_input":{}}}'
   ```

## Calling the extension outbound (matrx-local → extension)

```python
from app.api.extension_invoke import invoke_extension_tool
result = await invoke_extension_tool("read_page", {"mode": "text"}, session_id, timeout_seconds=30)
# result = {"type": "extension.result", "callId": ..., "ok": bool, "result"|"error"...}
```

- Get a `session_id` from `extension_ws_manager.list_active_sessions()` (or
  `GET /extension/sessions`). No session → fail cleanly, don't hang.
- Failure modes to handle: no extension attached (RuntimeError), timeout
  (RuntimeError), mid-call disconnect (ConnectionError), extension-side error
  (`ok: false`). All soft failures.
- The extension services these via `handleWebmcpCall` with the SAME
  permission gating as page/frontend callers: `privileged` and `ask-user`
  tier tools are rejected, `action` tier always confirms with the user.
- No retries on the channel; callers own retry + dedup.
- HTTP driver for testing: `POST /extension/invoke {session_id, tool_name, args, timeout_seconds}`.

## Verification status (2026-08-08)

- **Engine-side round trip: VERIFIED in CI-grade tests.**
  `tests/smoke/test_extension_channel.py` runs the real engine (port 22199)
  and exercises: `POST /extension/rpc` `tool` → real dispatcher
  (`SystemInfo`), 401 on missing bearer, unknown-command envelope, WS
  `hello`/`ping`/`pong`, and the FULL reverse-invoke callId round trip
  (`POST /extension/invoke` → `extension.invoke` → simulated extension →
  `extension.result` → HTTP response) including the error path.
  `tests/characterization/test_broadcast_rpc_dispatch.py` pins the Broadcast
  rpc dispatcher (reply shape, filtering, unknown command, tool routing).
- **Extension-side units:** matrx-extend `tests/unit/ws-invoke.test.ts`
  (reverse-invoke consumer) + existing dispatch tests; `pnpm test`.
- **Installed/package E2E: VERIFIED.** Installed matrx-local v1.4.14 and the
  real Chrome profile (matrx-extend v0.1.68) completed discovery, pairing,
  health/version/capabilities, real `SystemInfo`, and WS connection. A clean
  temporary Chrome-for-Testing profile loaded from the v0.1.69 packaged build
  auto-paired independently. The engine listed both sessions simultaneously,
  and targeted `read_page` reverse invokes returned independently from both.
- **Production frontend bridge: VERIFIED.** `demos.aimatrx.com` completed
  direct and Supabase Broadcast ping/capabilities/`get_active_tab` calls
  against the installed extension; the append-message route returned its
  expected authenticated 404 health probe.
- **Remote HTTP regression:** the real-engine suite pins valid pair token →
  `/extension/rpc` 200 over tunnel headers, wrong pair → 401, pair bootstrap →
  403, and pair token on non-extension route → 401. Repeat against the released
  tunnel after any auth middleware change.

---

## Remote-control runbook — the 6-link chain (web → this machine)

How a web/mobile client ends up executing on this PC, and how to verify each
link. Auth for `/extension/*` and `/tunnel/*` curl calls: any well-formed JWT
works on loopback; use the API key from `~/.matrx/local.json` where noted.

**The chain:** engine → tunnel → `app_instances` row → aidream local-proxy →
frontend resolve → SandboxPanel.

**Tunnel lifecycle truth (code-verified):**
- Auto-start on boot is gated by the `tunnel_enabled` setting
  (`~/.matrx/settings.json`, default **false**; env `TUNNEL_ENABLED` default
  false) — `app/main.py` Phase 5.
- `POST /tunnel/start` starts cloudflared, persists `tunnel_enabled=true`
  (auto-start from then on), pushes `tunnel_url`/`tunnel_ws_url`/
  `tunnel_active=true`/`tunnel_updated_at` to `app_instances`
  (`instance_manager.update_tunnel_url`), updates `~/.matrx/local.json` and
  the runtime singleton. `POST /tunnel/stop` reverses all of it
  (`tunnel_active=false`, URL nulled). Engine shutdown also clears the row.
- The 5-minute heartbeat (`settings_sync.heartbeat`) refreshes `last_seen`
  AND re-asserts live tunnel state every beat, so a stale URL self-corrects
  within one interval. (`tunnel_updated_at` is only bumped by start/stop —
  see AGENT_TASKS.)

**Step 1 — engine up:**
```bash
curl -s http://127.0.0.1:22140/health
```
**Step 2 — tunnel up:**
```bash
curl -s http://127.0.0.1:22140/tunnel/status             # public (in _PUBLIC_PATHS)
curl -s -X POST http://127.0.0.1:22140/tunnel/start \
  -H "Authorization: Bearer $API_KEY"                     # flip it ON (persists);
                                                          # AuthMiddleware is presence-only
                                                          # on loopback — API_KEY from root .env
# expect {"running":true,"url":"https://<x>.trycloudflare.com",...}
curl -s https://<x>.trycloudflare.com/health             # tunnel reaches engine
```
**Step 3 — cloud row fresh** (Supabase `app_instances`, project
`txzxabzwovsujtloxrus`):
```sql
select instance_id, last_seen, tunnel_active, tunnel_url, tunnel_updated_at
from app_instances where user_id = '<uid>' order by last_seen desc;
```
Expect `tunnel_active=true`, `tunnel_url` set, `last_seen` < 10 min (the
frontend's staleness window).
**Step 4 — aidream local-proxy** (forwards to `{tunnel_url}/sandbox/{path}`;
this engine serves the orchestrator-shape `/sandbox/*` surface —
`app/api/sandbox_routes.py`, mounted in `app/main.py`):
```bash
curl -s https://server.app.matrxserver.com/api/local-proxy/<app_instances.id>/fs/list?path=/ \
  -H "X-Sandbox-Access-Token: <user Supabase JWT>"
```
404 = row not found / not owner; 410 = `_is_online` false (tunnel_active
false or last_seen stale).
**Step 5 — frontend resolve:**
```bash
curl -s -X POST https://<frontend>/api/compute-targets/resolve \
  -H "Cookie: <session>" -d '{"kind":"local-pc","id":"<app_instances.id>"}'
```
410 `device_offline` = same freshness gate as step 4; 200 returns the
`base_url` pointing at step 4's proxy.
**Step 6 — SandboxPanel:** pick the device in the frontend SandboxPanel; fs
listings/exec should flow. Errors here with steps 1–5 green = client-side.

The tunnel's enabled/active state is user configuration, not a documentation
constant. Inspect `/tunnel/status` and the matching `app_instances` row; never
toggle it merely to make a test pass.

---

## Pointer to the master cross-repo doc

`/Users/armanisadeghi/code/matrx-extend/docs/CROSS_REPO_INTEGRATION.md` —
source of truth for which repo owns which side of the contract; this file is
the local view from inside `matrx-local`.

## Pointer to the local skill

`./.cursor/skills/connect-matrx-extend/SKILL.md` — the agent skill that
loads when work touches this connection.
