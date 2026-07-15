# Multi-Window System (peers + panels)

Matrx Local supports VS Code-style multi-window: any number of **full peer
windows** (complete app) plus one **lightweight panel window per page**. One
process, one Python engine, one tray icon.

## Window taxonomy (labels are the single source of truth)

| Kind | Label | Boots | Created by |
|---|---|---|---|
| Main | `main` | full `<App/>` | tauri.conf.json (recreatable by Rust) |
| Peer | `peer-N` | full `<App/>` | File > New Window (Cmd/Ctrl+Shift+N), tray "New Window", `open_peer_window` |
| Panel | `panel-<page>` | `<PanelApp/>` (manifest providers only) | toolbar pop-out button, sidebar right-click, View > Move Page to New Window, `open_panel_window` |
| Overlay | `transcript-overlay` | `<TranscriptOverlay/>` only | floating_overlay.rs |

The three places a label taxonomy lives — keep them in sync:
- Rust: `desktop/src-tauri/src/windows.rs` (`PANEL_PAGES`, prefixes, registry)
- TS: `desktop/src/lib/window-role.ts` (`PanelPage`, role detection)
- Capabilities: `desktop/src-tauri/capabilities/default.json` `windows` globs
  (`peer-*`, `panel-*`) — a label outside these globs gets NO IPC permissions
  and fails silently.

## Leader election (singleton services)

Exactly one full window — the **leader**, Rust-elected as the oldest surviving
full window — runs: the background-task orchestrator, the 5-min cloud
heartbeat, and auto-update polling. Per-window (deliberately duplicated):
engine WebSockets, 10s health poll, all context providers.

- Rust: `WindowRegistry` in windows.rs; promotion broadcasts
  `window-leader-changed` (payload = new leader label).
- TS: `lib/window-role.ts` mirrors it into a store; gate effects with
  `useWindowLeader()` (see use-engine.ts / use-auto-update.ts for the
  pattern). Never invent a second leadership mechanism.

## Close policy (lib.rs on_window_event)

- Panels/overlay close freely.
- A full window with other full windows alive closes freely; `Destroyed`
  prunes the registry and promotes a new leader.
- The LAST full window applies the app policy: close-to-tray hides it (and
  all panels + overlay with it, macOS Dock icon → Accessory), otherwise
  graceful shutdown closes everything.
- Dock click / tray "Show" / second launch → focus the most recent full
  window, recreating `main` if none exist
  (`focus_most_recent_full_or_recreate_main`).

## Adding a new panel page (checklist)

1. `windows.rs` `PANEL_PAGES`: add `(page-id, route, w, h, title)`.
2. `lib/window-role.ts`: add the id to `PanelPage`.
3. `panels/manifest.tsx`: add entry — title, **exact provider list** (run a
   transitive context-hook scan of the page's component tree; a missing
   provider crashes the panel, an extra one wastes pollers), and render fn.
   Add the route to `ROUTE_TO_PANEL` if the page exists in the main app.
4. Provider ORDER must respect App.tsx nesting (PromptMatrix inside MediaGen,
   MediaActions after Gen/Library/Vault, Transcription innermost).

## Known limits (accepted v1)

- Mic recording is single-owner (Rust CPAL): the second window gets the
  "Already recording" error — surface it, don't fight it.
- `peer-*` geometry is not persisted (labels are session-scoped);
  `main`/`panel-*` geometry persists via tauri-plugin-window-state
  (overlay denylisted — it positions itself).
- An update restart does not restore the window set.
- Opening a new window while an OAuth sign-in is pending can briefly show the
  pending screen in both; the PKCE code is single-use so only the initiating
  window completes.
- Native menu: the Edit submenu must keep ALL predefined items or macOS loses
  Cmd+C/V/X — see menu.rs module docs.
