# Extension Bridge (matrx-extend ↔ engine)

Single handler registry: `app/api/extension_handlers.py::HANDLERS`.

| Transport | Entry |
|-----------|--------|
| HTTP | `POST /extension/rpc` |
| WebSocket | `/extension/ws` (engine → extension reverse invoke) |
| Supabase Broadcast v2 | `cross_component_router.py` → same HANDLERS |

Diagnostics: `/extension/boot-check`, `/extension/metrics`, `/extension/tunnel/status`, desktop Bridge Test panel.

**Full protocol, tunnel chain, verification steps:** [MATRX_EXTEND_CONNECTION.md](../MATRX_EXTEND_CONNECTION.md)  
**Route rules:** `app/api/FEATURE.md`  
**Skill:** `.cursor/skills/connect-matrx-extend/SKILL.md`

---

## Status (verify wording)

Engine-side smoke: `tests/smoke/test_extension_channel.py`, `tests/characterization/test_broadcast_rpc_dispatch.py` (health, version, capabilities, tool, WS round-trip).

> **NOTE:** [CLAUDE.md](../../CLAUDE.md) marks Channel B "FULLY ACTIVE, engine-side verified (2026-07-10)". In-browser E2E remains manual — see MATRX_EXTEND_CONNECTION.md § Verification status. Reconcile docs when manual pass is recorded.
