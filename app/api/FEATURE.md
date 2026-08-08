# FEATURE — Extension Bridge (`extension_*` files in this directory)

Scope: `extension_handlers.py`, `extension_routes.py`, `extension_auth.py`,
`extension_broadcast.py`, `extension_invoke.py`, `extension_ws_manager.py`,
`extension_bridge_routes.py`, `extension_metrics.py`, `extension_boot_check.py`,
plus `cross_component_envelope.py` / `cross_component_router.py`.
Cross-repo map: `docs/MATRX_EXTEND_CONNECTION.md`.

## One command surface, several transports

**`extension_handlers.py::HANDLERS` is the SINGLE command surface.** Every
transport funnels into the same registry (`@register("name")`, async
`(args, request|None) → dict`):

- **HTTP** — `POST /extension/rpc` (`extension_routes.py`; loopback or
  Cloudflare tunnel).
- **WebSocket** — `/extension/ws` reverse channel (`extension_ws_manager.py`).
- **Supabase Broadcast** — cross-machine fallback: v2 envelopes arrive in
  `extension_broadcast.py::_on_broadcast`, are parsed by
  `cross_component_envelope.parse_envelope`, and `kind:"rpc"` envelopes are
  dispatched into HANDLERS by `cross_component_router.py`, answered with a
  reply envelope (`action: "<action>.result"`, same `requestId`).

Never add a command to one transport only — add a handler, all transports get it.
Handlers must NOT swallow errors (the route converts exceptions to `ok:false`);
the `tool` handler is the one sanctioned exception (tool-layer failures encode
as payloads so the extension can distinguish RPC vs tool failures). Handlers
must tolerate `request=None` (non-HTTP transports).

## Envelope v2

`cross_component_envelope.py` mirrors
`matrx-frontend/lib/types/bridge-envelope.ts` **byte-for-byte** (matrx-extend
mirrors it too). v2 adds `kind: "rpc"|"wake"|"presence"`, `fromInstance` /
`toInstance`, and an optional `v` field (defaults to 2; v1 publishers parse
unchanged). `kind:"task"` is intentionally absent — durable tasks live in
`sch_task`, wake envelopes only point at the row. If you change the shape,
change ALL mirrors in the same effort (frontend `v` mirror is currently
pending — FOUND_DEFECTS MXL-D-026).

## Auth posture (`extension_auth.py`)

Desktop app on the user's machine ⇒ **no server-side secrets, ever** — never
reference `SUPABASE_JWT_SECRET`. Incoming tokens: JWKS signature + expiry
validation for asymmetric algs (RS256/ES256) when `SUPABASE_URL` is set;
HS256 falls back to bearer-presence over loopback (loud one-time degraded-mode
log). Rejection logging is rate-limited (first miss WARNs, quiet-gap re-WARNs,
periodic INFO rate summary; state dict bounded at 256 keys). Full rationale in
the module docstring + `docs/MATRX_EXTEND_CONNECTION.md`.

The engine-issued `mxl_pair_…` token is accepted over both direct loopback and
the Cloudflare tunnel, but only for `/extension/*`. `AuthMiddleware` performs
that narrow outer-gate exception so the request can reach
`validate_extension_principal`, which repeats the constant-time pair-token
check. The same token on `/tools/*`, `/settings`, or any unrelated route still
goes through remote Supabase verification and fails closed. `/extension/pair`
always reaches its route-owned origin check so tunnel callers receive the
contractual hard 403; the token itself is never remotely issuable.

## Diagnostics

- `/extension/boot-check` (+ boot self-check at engine startup)
- `/extension/metrics` — per-command count/errors/latencies
- `/extension/tunnel/status`, `/extension/broadcast/status`
- `extension_bridge_routes.py` backs the desktop Bridge Test panel — test-only
  surface; product features do not belong there.

Status: `health`/`version`/`capabilities`/`tool` handlers live. On 2026-08-08,
the installed v1.4.14 app completed live browser→engine RPCs, two simultaneous
Chrome-profile WS sessions, and independent engine→browser `read_page`
reverse invokes. Remote pair-token HTTP auth is covered by the real-engine
smoke suite and must be re-proved against the released build whenever this
middleware contract changes.
