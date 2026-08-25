---
name: connect-matrx-extend
description: Use when extending or debugging the connection between the matrx-local Tauri desktop engine and the matrx-extend Chrome extension — adding an RPC command on POST /extension/rpc, exposing a dispatcher tool to the extension, or pushing engine events down the WebSocket reverse channel. Scope guardrail: this skill is for INBOUND extension-to-local work that lives inside this matrx-local repo; do NOT use it for changes that live inside the matrx-extend repo (those go through matrx-extend's own connect-local skill), and do NOT use it for engine-internal work that never crosses the wire (Tauri lifecycle, sidecar build, llama-server signing, transcription pipeline — those have their own docs).
---

# connect-matrx-extend

## 30-second mental model

`matrx-local` is the desktop engine. `matrx-extend` is the Chrome extension
that wants the engine's tools. The wire between them has two halves:

- **REST** — `POST /extension/rpc` on the FastAPI sidecar
  (`127.0.0.1:22140`, auto-scanned). Extension calls in, engine answers
  synchronously. Today only `health` works; everything else returns
  `Unknown command`. Phase 1 introduces a `HANDLERS` registry plus a
  generic `tool` command that forwards to `app/tools/dispatcher.py`.
- **WebSocket** — `/ws` already broadcasts engine→client events
  (downloads, transcription, model lifecycle). Phase 2 adds a dedicated
  `/extension/ws` reverse channel with a request/response envelope so the
  engine can ask the extension to run a browser tool.

Auth is a Bearer token (Supabase JWT preferred, local API_KEY fallback)
via `Authorization` header on REST or `?token=` query param on WebSocket.
Loopback-only today; cross-machine fallback over Supabase Broadcast
reserved for later.

## When to use this skill

- Adding a new RPC command on `/extension/rpc` (a new `HANDLERS` entry).
- Exposing a dispatcher tool to the extension (almost always: just confirm
  the generic `tool` command is wired up — one line in the registry).
- Adding a new engine→client event the extension needs to subscribe to.
- Wiring up the planned `/extension/ws` reverse channel or
  `invoke_extension_tool` plumbing.
- Touching `app/api/extension_routes.py`, `app/api/extension_handlers.py`
  (Phase 1), `app/api/extension_invoke.py` (Phase 2),
  `app/websocket_manager.py` broadcast paths, or anything that calls
  `dispatch()` on behalf of the extension.

## When NOT to use this skill

- Engine-internal work that never crosses the wire — Tauri lifecycle,
  sidecar build, llama-server signing, Whisper inference, OpenWakeWord,
  download manager. Those have their own docs.
- Work that lives inside the matrx-extend repo. Use the extension's
  `connect-local` skill from over there instead.
- Changes to the unrelated `app/api/proxy_routes.py` forward-proxy on
  port 22180. That's a different surface (HTTP forward proxy for the
  cloud backend, not the extension RPC).

## Quick start: check live request metrics

`/extension/*` calls are timed by an in-memory ring in
`app/api/extension_metrics.py`. Snapshot the registry:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:22140/extension/metrics | jq .
```

Or open Settings → Bridge Test in the desktop app — the "Request
metrics" sub-section inside Panel 1 polls this endpoint every 2s and
renders count / errors / p50 / p95 per command. Reset with
`POST /extension/metrics/reset` or the Reset button.

When debugging a hang or timeout, check this surface FIRST: the
`last_called_at` field tells you whether traffic is actually reaching
the engine, and the `last_error` field surfaces the most recent failure
without scrolling logs.

## Quick start: add a new RPC command

1. Open `app/api/extension_handlers.py` (Phase 1; create if missing).
2. Append a handler:
   ```python
   async def fs_list_directory(payload: dict, request: Request) -> dict:
       path = payload.get("path")
       if not path:
           raise ValueError("path is required")
       # Validate against allowlist; never trust raw payload paths.
       return {"entries": list_dir_safe(path)}
   HANDLERS["fs.list_directory"] = fs_list_directory
   ```
3. Confirm `app/api/extension_routes.py` looks the command up in
   `HANDLERS` and falls through to the existing `Unknown command` error.
4. For tool invocations, do NOT add per-tool handlers. The single `tool`
   handler that delegates to `app/tools/dispatcher.py::dispatch` makes
   every dispatcher tool reachable.
5. Test with curl from the loopback (token + port from
   `~/.matrx/local.json`).

## File index

| Path | Purpose | Line range |
|---|---|---|
| `app/api/extension_routes.py` | The `/extension/rpc` route + Pydantic envelopes | 23-47 |
| `app/api/extension_handlers.py` | `HANDLERS` registry (Phase 1, planned) | TBD |
| `app/api/extension_invoke.py` | `invoke_extension_tool` outbound (Phase 2, planned) | TBD |
| `app/tools/dispatcher.py` | `dispatch(tool_name, tool_input, session)` — generic tool entrypoint | top-level |
| `app/websocket_manager.py` | `broadcast()` / `broadcast_notification()` engine push | ~200-220 |
| `/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md` | Full protocol reference | whole file |

## Failure modes

- **Silent — handler returns `{ok: false}`**: extension UI shows
  "desktop unavailable" or surfaces the `error` string. Check the
  matrx-local terminal log for `[extension_routes] RPC error: ...` —
  the existing `try/except` in `handle_rpc` already logs with `exc_info`.
- **Loud — dispatcher exception**: bubbles up through the `try/except`
  in `handle_rpc`, surfaces as `DesktopRpcResponse(ok=False, error=...)`.
  Verify with curl:
  ```bash
  curl -s -X POST http://127.0.0.1:22140/extension/rpc \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"command":"health"}'
  # → {"ok":true,"data":{"status":"ok","version":"...","user_id":null}}
  ```
- **Wrong port**: the extension used to hardcode `22180` (the forward
  proxy port). Real RPC port lives in `~/.matrx/local.json` after engine
  startup. If RPC works in curl but extension reports failure, the
  extension's port-discovery path is the suspect, not this repo.
- **Recurring `[auth] rejected POST /extension/rpc — missing bearer
  token` warnings every ~30 s.** The extension's port-discovery probe
  in `src/lib/desktop/discovery.ts` is hitting `/extension/rpc`
  (auth-walled) instead of `/health` (public). Each cache-miss fans
  out 20 parallel probes; only the one that lands on the engine port
  reaches the auth middleware and logs. Fix lives in the extension
  repo (`connect-local` skill) — do NOT loosen the engine's
  `_PUBLIC_PATHS` to suppress the warning, the probe should never have
  been authenticated.
- **WebSocket reverse-channel timeout**: `invoke_extension_tool` rejects
  with `TimeoutError` after 30 s (default). Either the extension is
  disconnected or the browser tool is genuinely slow. Treat as a soft
  failure and surface to the caller.

## Reference

Full protocol: `/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md`.
Master cross-repo doc:
`/Users/armanisadeghi/code/common-docs/systems/clients/extension/CHANNELS.md`.
