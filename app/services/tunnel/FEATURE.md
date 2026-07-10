# FEATURE — Cloudflare Tunnel (`cloudflared`)

Exposes the local engine (`127.0.0.1:{port}`) to the internet so remote
devices (frontend, extension, aidream) can reach this machine.

## Ownership — non-negotiable (root `CLAUDE.md` Hard Rule 0)

**cloudflared is a child of the PYTHON ENGINE.** Rust never spawns it, never
pkills it, never "cleans it up" — Rust signals the engine
(`POST /admin/shutdown` / SIGTERM) and the engine cascades to its children
during lifespan teardown (`app/main.py` shutdown → `TunnelManager.stop()` with
a timeout cap → `instance_manager.update_tunnel_url(None, active=False)`).
Orphan cloudflared processes from a dead engine are reclaimed by
`app/preflight.py` (cmdline pattern `cloudflared tunnel`, discovery key
`tunnel`) — that is the ONLY out-of-band kill path.

Every state change goes through `app/launcher.py` registry (`"tunnel"`:
starting/ready/degraded/failed/stopping/stopped; DEGRADED = process running
but no URL captured). Do not add ad-hoc state prints.

## Lifecycle & the `tunnel_enabled` gate

- Startup: `app/main.py` Phase 5 reads `settings_sync.get("tunnel_enabled",
  TUNNEL_ENABLED)` — settings value wins over env default (False). Only then
  does `get_tunnel_manager().start(port)` run.
- Runtime: `POST /tunnel/start` / `/tunnel/stop` (`app/api/tunnel_routes.py`)
  flip the tunnel AND persist `tunnel_enabled` so the choice survives
  restarts. Introspection: `GET /extension/tunnel/status`
  (+ `app/api/tunnel_state.py`, `MATRX_PREFER_TUNNEL` preferred-mode logic).

## Two modes (`manager.py`)

- **Quick tunnel** (default, zero-config): random `*.trycloudflare.com` URL
  per start; URL changes every restart. Transient quicktunnel flakes get one
  retry (`_is_transient_quicktunnel_flake`).
- **Named tunnel** (`CLOUDFLARE_TUNNEL_TOKEN` set): stable subdomain, survives
  restarts.

Binary resolution: bundled → preinstalled discovery
(`_find_preinstalled_cloudflared`) → pinned download
(`_CLOUDFLARED_VERSION = 2025.1.0`) cached at `~/.matrx/bin/cloudflared`
(atomic `.tmp` → rename; Windows spawn uses
`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`).

## Who writes `app_instances` (cloud)

Two writers, on purpose — keep them consistent:

1. `instance_manager.update_tunnel_url(url, active=...)` — on explicit
   start/stop (sets `tunnel_url`/`tunnel_ws_url`/`tunnel_active`/
   `tunnel_updated_at`).
2. `settings_sync.heartbeat` — re-asserts the tunnel fields every 5 min so a
   recovered cloud row converges.

Known defects to keep in mind before touching these paths
(`KNOWN_DEFECTS.md`): heartbeat omits `tunnel_updated_at` (MXL-D-010) and both
writers PATCH blindly — PostgREST 2xx on zero matched rows hides orphaned
instances (MXL-D-003).

Remote consumers resolve this machine via `last_seen` + `tunnel_active`
(frontend resolve, aidream `_is_online`) — never gate on `tunnel_updated_at`.
