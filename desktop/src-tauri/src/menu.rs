//! menu — native application menu (multi-window aware).
//!
//! Replaces Tauri's default menu with one that adds window management:
//!   • File > New Window (CmdOrCtrl+Shift+N) → opens a full peer window
//!   • View > Move Page to New Window (CmdOrCtrl+Alt+N) → asks the focused
//!     window's frontend to reopen its current page as a panel window
//!   • Window — on macOS registered as the native NSApp windows menu, which
//!     provides the automatic window list, Cmd+` cycling, and
//!     "Bring All to Front" for free.
//!
//! IMPORTANT: the Edit submenu must keep ALL predefined clipboard items —
//! replacing the default menu on macOS silently kills Cmd+C/V/X/Z otherwise.
//!
//! Custom items emit `menu://…` events to the FOCUSED window only, so with
//! multiple windows exactly one frontend reacts.

use tauri::menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::{Emitter, Manager};

/// Event sent to the focused window when the user picks "Move Page to New
/// Window". The frontend maps its current route to a panel page and invokes
/// `open_panel_window`.
pub const MOVE_TO_WINDOW_EVENT: &str = "menu://move-to-window";

/// Event sent to the focused window when the user picks Settings from the app
/// menu. The frontend navigates to /settings.
pub const OPEN_SETTINGS_EVENT: &str = "menu://open-settings";

pub fn setup_app_menu(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let new_window = MenuItemBuilder::with_id("file-new-window", "New Window")
        .accelerator("CmdOrCtrl+Shift+N")
        .build(app)?;
    let move_to_window =
        MenuItemBuilder::with_id("view-move-to-window", "Move Page to New Window")
            .accelerator("CmdOrCtrl+Alt+N")
            .build(app)?;

    let mut menu_builder = MenuBuilder::new(app);

    // ── App menu (macOS only — first submenu becomes the application menu) ──
    #[cfg(target_os = "macos")]
    {
        let settings = MenuItemBuilder::with_id("app-settings", "Settings…")
            .accelerator("Cmd+,")
            .build(app)?;
        let app_menu = SubmenuBuilder::new(app, "AI Matrx")
            .about(None)
            .separator()
            .item(&settings)
            .separator()
            .services()
            .separator()
            .hide()
            .hide_others()
            .show_all()
            .separator()
            .quit()
            .build()?;
        menu_builder = menu_builder.item(&app_menu);
    }

    // ── File ────────────────────────────────────────────────────────────────
    let file_menu = {
        let builder = SubmenuBuilder::new(app, "File")
            .item(&new_window)
            .separator()
            .close_window();
        // Quit lives in the app menu on macOS; put it under File elsewhere.
        #[cfg(not(target_os = "macos"))]
        let builder = builder.separator().quit();
        builder.build()?
    };
    menu_builder = menu_builder.item(&file_menu);

    // ── Edit (ALL predefined items — see module docs) ───────────────────────
    let edit_menu = SubmenuBuilder::new(app, "Edit")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;
    menu_builder = menu_builder.item(&edit_menu);

    // ── View ────────────────────────────────────────────────────────────────
    let view_menu = SubmenuBuilder::new(app, "View")
        .item(&move_to_window)
        .separator()
        .fullscreen()
        .build()?;
    menu_builder = menu_builder.item(&view_menu);

    // ── Window ──────────────────────────────────────────────────────────────
    let window_menu = SubmenuBuilder::new(app, "Window")
        .minimize()
        .maximize()
        .build()?;
    #[cfg(target_os = "macos")]
    window_menu.set_as_windows_menu_for_nsapp()?;
    menu_builder = menu_builder.item(&window_menu);

    app.set_menu(menu_builder.build()?)?;

    app.on_menu_event(|app, event| match event.id().as_ref() {
        "file-new-window" => {
            if let Err(e) = crate::windows::open_peer_window_impl(app) {
                eprintln!("[menu] New Window failed: {e}");
            }
        }
        "view-move-to-window" => emit_to_focused(app, MOVE_TO_WINDOW_EVENT),
        "app-settings" => emit_to_focused(app, OPEN_SETTINGS_EVENT),
        _ => {}
    });

    Ok(())
}

/// Emit an event to the currently focused webview window only (falls back to
/// the most recent full window so menu picks never vanish into the void).
fn emit_to_focused(app: &tauri::AppHandle, event: &str) {
    let target = app
        .webview_windows()
        .into_iter()
        .find(|(_, w)| w.is_focused().unwrap_or(false))
        .map(|(label, _)| label)
        .or_else(|| {
            app.try_state::<crate::windows::WindowRegistry>()
                .and_then(|r| r.most_recent_full())
        });
    if let Some(label) = target {
        let _ = app.emit_to(&label, event, ());
    }
}
