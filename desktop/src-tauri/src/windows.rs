//! windows — multi-window manager: registry, leader election, spawn/focus.
//!
//! Window taxonomy (labels are the single source of truth, mirrored in
//! `desktop/src/lib/window-role.ts` and `capabilities/default.json`):
//!   • `main`               — the primary full window (exactly one, recreatable)
//!   • `peer-N`             — additional FULL app windows (VS Code-style)
//!   • `panel-<page>`       — lightweight single-page windows (one per page)
//!   • `transcript-overlay` — the always-on-top overlay (floating_overlay.rs)
//!
//! Leader election: the oldest surviving full window (main or peer) is the
//! leader. Exactly one window runs the singleton frontend services (cloud
//! heartbeat, background tasks, auto-update, wake word). When the leader is
//! destroyed the registry promotes the next-oldest full window and broadcasts
//! `window-leader-changed` with the new leader's label so the frontend can
//! migrate those services. Panels and the overlay never lead.

use serde::Serialize;
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

pub const MAIN_LABEL: &str = "main";
pub const PEER_PREFIX: &str = "peer-";
pub const PANEL_PREFIX: &str = "panel-";
pub const OVERLAY_LABEL: &str = "transcript-overlay";

/// Event broadcast to all windows when leadership moves to a new full window.
/// Payload: the new leader's label (string).
pub const LEADER_CHANGED_EVENT: &str = "window-leader-changed";

/// Pages that may open as lightweight panel windows.
/// (page id, hash route, width, height, title)
///
/// The page id becomes the window label (`panel-<page>`); the frontend's
/// PanelApp derives what to render from the label, so the hash route is a
/// convenience (sensible fallback if the bundle is ever loaded without
/// label branching). Keep in sync with `desktop/src/panels/manifest.tsx`.
const PANEL_PAGES: &[(&str, &str, f64, f64, &str)] = &[
    ("chat", "chat", 960.0, 720.0, "Confidential Chat"),
    ("cloud-chat", "cloud-chat", 960.0, 720.0, "Cloud Chat"),
    ("notes", "notes", 960.0, 720.0, "Notes"),
    ("activity", "activity", 860.0, 640.0, "Activity"),
    ("ports", "ports", 860.0, 640.0, "Ports"),
    ("transcription", "voice", 960.0, 720.0, "Transcription"),
    ("tts", "tts", 960.0, 720.0, "Text to Speech"),
    ("media-generation", "media-generation", 1240.0, 820.0, "Media Generation"),
    ("media-gallery", "media-gallery", 1240.0, 820.0, "Media Gallery"),
];

pub fn is_full_window(label: &str) -> bool {
    label == MAIN_LABEL || label.starts_with(PEER_PREFIX)
}

pub fn is_panel_window(label: &str) -> bool {
    label.starts_with(PANEL_PREFIX)
}

fn window_kind(label: &str) -> &'static str {
    if label == MAIN_LABEL {
        "main"
    } else if label.starts_with(PEER_PREFIX) {
        "peer"
    } else if label.starts_with(PANEL_PREFIX) {
        "panel"
    } else if label == OVERLAY_LABEL {
        "overlay"
    } else {
        "unknown"
    }
}

// ── Registry ─────────────────────────────────────────────────────────────────

#[derive(Default)]
pub struct WindowRegistry(Mutex<RegistryInner>);

#[derive(Default)]
struct RegistryInner {
    /// Monotonic counter for peer labels — starts at 2 (`peer-2`), never reused
    /// within a session so labels stay unambiguous in logs.
    peer_counter: u32,
    /// Full (main/peer) windows in creation order — index 0 is the leader.
    full_windows: Vec<String>,
}

impl WindowRegistry {
    /// Record a full window. Idempotent (re-registration keeps original order).
    pub fn register_full(&self, label: &str) {
        let mut inner = self.0.lock().unwrap();
        if !inner.full_windows.iter().any(|l| l == label) {
            inner.full_windows.push(label.to_string());
        }
    }

    fn next_peer_label(&self) -> String {
        let mut inner = self.0.lock().unwrap();
        inner.peer_counter = inner.peer_counter.max(1) + 1;
        format!("{}{}", PEER_PREFIX, inner.peer_counter)
    }

    pub fn leader(&self) -> Option<String> {
        self.0.lock().unwrap().full_windows.first().cloned()
    }

    /// The most recently created full window still registered.
    pub fn most_recent_full(&self) -> Option<String> {
        self.0.lock().unwrap().full_windows.last().cloned()
    }

    /// Remove a window from the registry. Returns the NEW leader's label if
    /// the removal changed leadership and a full window remains.
    pub fn remove(&self, label: &str) -> Option<String> {
        let mut inner = self.0.lock().unwrap();
        let was_leader = inner.full_windows.first().map(|l| l == label).unwrap_or(false);
        inner.full_windows.retain(|l| l != label);
        if was_leader {
            inner.full_windows.first().cloned()
        } else {
            None
        }
    }
}

// ── Command payloads ─────────────────────────────────────────────────────────

#[derive(Clone, Serialize)]
pub struct WindowInfo {
    pub label: String,
    pub title: String,
    pub kind: String,
    pub focused: bool,
    pub visible: bool,
}

#[derive(Clone, Serialize)]
pub struct WindowRoleInfo {
    pub label: String,
    pub kind: String,
    pub is_leader: bool,
}

// ── Window creation ──────────────────────────────────────────────────────────

/// Recreate the `main` window (after the config-created one was destroyed).
/// Mirrors the window definition in tauri.conf.json.
pub fn create_main_window(app: &AppHandle) -> Result<(), String> {
    if app.get_webview_window(MAIN_LABEL).is_some() {
        return Ok(());
    }
    WebviewWindowBuilder::new(app, MAIN_LABEL, WebviewUrl::App("index.html".into()))
        .title("AI Matrx")
        .inner_size(1400.0, 900.0)
        .min_inner_size(900.0, 600.0)
        .center()
        .build()
        .map_err(|e| format!("Failed to recreate main window: {e}"))?;
    if let Some(registry) = app.try_state::<WindowRegistry>() {
        registry.register_full(MAIN_LABEL);
    }
    crate::refresh_tray_menu(app);
    Ok(())
}

/// Cascade offset (logical px) applied to new full windows relative to the
/// currently focused full window, so stacked windows stay discoverable.
const CASCADE_OFFSET: f64 = 32.0;

fn cascade_position(app: &AppHandle) -> Option<(f64, f64)> {
    let focused = app
        .webview_windows()
        .into_iter()
        .map(|(_, w)| w)
        .find(|w| w.is_focused().unwrap_or(false) && is_full_window(w.label()))
        .or_else(|| app.get_webview_window(MAIN_LABEL))?;
    let pos = focused.outer_position().ok()?;
    let scale = focused.scale_factor().unwrap_or(1.0);
    Some((
        pos.x as f64 / scale + CASCADE_OFFSET,
        pos.y as f64 / scale + CASCADE_OFFSET,
    ))
}

/// Open a new FULL peer window (complete app, VS Code "New Window" style).
/// Returns the new window's label.
#[tauri::command]
pub async fn open_peer_window(app: AppHandle) -> Result<String, String> {
    open_peer_window_impl(&app)
}

/// Non-command form so the native menu (menu.rs) can call it directly.
pub fn open_peer_window_impl(app: &AppHandle) -> Result<String, String> {
    let registry = app
        .try_state::<WindowRegistry>()
        .ok_or_else(|| "WindowRegistry not managed".to_string())?;
    let label = registry.next_peer_label();

    let mut builder =
        WebviewWindowBuilder::new(app, &label, WebviewUrl::App("index.html".into()))
            .title("AI Matrx")
            .inner_size(1400.0, 900.0)
            .min_inner_size(900.0, 600.0);

    if let Some((x, y)) = cascade_position(app) {
        builder = builder.position(x, y);
    } else {
        builder = builder.center();
    }

    builder
        .build()
        .map_err(|e| format!("Failed to open new window: {e}"))?;
    registry.register_full(&label);
    crate::lifecycle_log::log(&format!("[windows] opened peer window {label}"));
    crate::refresh_tray_menu(app);
    Ok(label)
}

/// Open (or focus) the lightweight panel window for a page.
/// Returns the panel window's label.
#[tauri::command]
pub async fn open_panel_window(app: AppHandle, page: String) -> Result<String, String> {
    let Some(&(page_id, route, width, height, title)) =
        PANEL_PAGES.iter().find(|(id, ..)| *id == page)
    else {
        return Err(format!(
            "Unknown panel page '{page}'. Valid pages: {}",
            PANEL_PAGES
                .iter()
                .map(|(id, ..)| *id)
                .collect::<Vec<_>>()
                .join(", ")
        ));
    };

    let label = format!("{PANEL_PREFIX}{page_id}");

    // One panel per page — reopening focuses the existing window.
    if let Some(win) = app.get_webview_window(&label) {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
        return Ok(label);
    }

    let mut builder = WebviewWindowBuilder::new(
        &app,
        &label,
        WebviewUrl::App(format!("index.html#/{route}").into()),
    )
    .title(title)
    .inner_size(width, height)
    .min_inner_size(480.0, 360.0);

    if let Some((x, y)) = cascade_position(&app) {
        builder = builder.position(x, y);
    } else {
        builder = builder.center();
    }

    builder
        .build()
        .map_err(|e| format!("Failed to open {title} window: {e}"))?;
    crate::lifecycle_log::log(&format!("[windows] opened panel window {label}"));
    crate::refresh_tray_menu(&app);
    Ok(label)
}

/// List all app windows (for tray/menu/UI window pickers).
#[tauri::command]
pub async fn list_app_windows(app: AppHandle) -> Result<Vec<WindowInfo>, String> {
    let mut infos: Vec<WindowInfo> = app
        .webview_windows()
        .into_iter()
        .map(|(label, w)| WindowInfo {
            title: w.title().unwrap_or_else(|_| label.clone()),
            kind: window_kind(&label).to_string(),
            focused: w.is_focused().unwrap_or(false),
            visible: w.is_visible().unwrap_or(false),
            label,
        })
        .collect();
    // Stable order: main, peers, panels, overlay.
    infos.sort_by_key(|i| match i.kind.as_str() {
        "main" => 0,
        "peer" => 1,
        "panel" => 2,
        _ => 3,
    });
    Ok(infos)
}

/// Bring a specific window to front (restores the Dock icon on macOS first).
#[tauri::command]
pub async fn focus_app_window(app: AppHandle, label: String) -> Result<(), String> {
    let win = app
        .get_webview_window(&label)
        .ok_or_else(|| format!("Window '{label}' not found"))?;
    #[cfg(target_os = "macos")]
    {
        let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    }
    win.unminimize().map_err(|e| e.to_string())?;
    win.show().map_err(|e| e.to_string())?;
    win.set_focus().map_err(|e| e.to_string())?;
    Ok(())
}

/// Report the invoking window's role: label, kind, and whether it is the
/// leader (the one window that runs singleton frontend services).
#[tauri::command]
pub fn get_window_role(
    window: tauri::Window,
    registry: tauri::State<'_, WindowRegistry>,
) -> WindowRoleInfo {
    let label = window.label().to_string();
    WindowRoleInfo {
        kind: window_kind(&label).to_string(),
        is_leader: registry.leader().as_deref() == Some(label.as_str()),
        label,
    }
}

// ── Lifecycle integration (called from lib.rs) ──────────────────────────────

/// Handle a window being destroyed: registry cleanup + leader promotion.
/// Broadcasts `window-leader-changed` when leadership moves.
pub fn handle_window_destroyed(app: &AppHandle, label: &str) {
    crate::refresh_tray_menu(app);
    if !is_full_window(label) {
        return;
    }
    let Some(registry) = app.try_state::<WindowRegistry>() else {
        return;
    };
    if let Some(new_leader) = registry.remove(label) {
        crate::lifecycle_log::log(&format!(
            "[windows] leader {label} destroyed → promoting {new_leader}"
        ));
        let _ = app.emit(LEADER_CHANGED_EVENT, new_leader);
    } else {
        crate::lifecycle_log::log(&format!("[windows] window {label} destroyed"));
    }
}

/// True when at least one OTHER full window (main/peer) still exists besides
/// `closing_label`. Checked against live windows, not just the registry, so a
/// stale registry entry can never make us skip shutdown on the true last close.
pub fn other_full_windows_exist(app: &AppHandle, closing_label: &str) -> bool {
    app.webview_windows()
        .iter()
        .any(|(label, _)| is_full_window(label) && label != closing_label)
}

/// Hide every panel window and the overlay. Used when the last full window
/// hides to tray — a floating panel with no parent app window is orphaned UX.
pub fn hide_secondary_windows(app: &AppHandle) {
    for (label, win) in app.webview_windows() {
        if is_panel_window(&label) || label == OVERLAY_LABEL {
            let _ = win.hide();
        }
    }
}

/// Focus the most recent full window, or recreate `main` if none exist.
/// This is the generalization of the old single-window `show_main_window`.
pub fn focus_most_recent_full_or_recreate_main(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    {
        // Switch to Regular policy BEFORE show() so the Dock icon appears at
        // the same moment the window becomes visible. Must run on the main
        // thread — all callers (tray, Reopen, single-instance, deep-link) do.
        let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    }

    // Prefer the registry's most-recent full window that still exists.
    let target = app
        .try_state::<WindowRegistry>()
        .and_then(|r| r.most_recent_full())
        .and_then(|label| app.get_webview_window(&label))
        .or_else(|| app.get_webview_window(MAIN_LABEL))
        .or_else(|| {
            // Registry says nothing usable — fall back to ANY live full window.
            app.webview_windows()
                .into_iter()
                .find(|(label, _)| is_full_window(label))
                .map(|(_, w)| w)
        });

    match target {
        Some(window) => {
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }
        None => {
            if let Err(e) = create_main_window(app) {
                eprintln!("[windows] failed to recreate main window: {e}");
            }
        }
    }
}
