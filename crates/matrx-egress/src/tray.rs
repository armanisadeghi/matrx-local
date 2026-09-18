//! The menu-bar / system-tray item — macOS and Windows only.
//!
//! Five lines, plain English, exactly the contract's menu:
//!
//! ```text
//! AI Matrx Home Connection — Connected as Arman's MacBook Pro   (the title, not clickable)
//! ─────────────────────────────────────────────────────────────
//! Pause                      ↔  Resume
//! Open in AI Matrx
//! Turn off and remove this computer
//! ─────────────────────────────────────────────────────────────
//! Quit
//! ```
//!
//! **The event loop owns the main thread and tokio runs on a worker thread.** On macOS a tray item
//! belongs to the `NSApplication`, which must be on the main thread, and the icon must be created
//! *after* the loop has started (`StartCause::Init`) or it does not appear at all. On Linux there
//! is no tray: the helper prints the same status on stdout, and this module is not compiled.

#![cfg(any(target_os = "macos", target_os = "windows"))]

use crate::status::{State, StatusHandle};
use crate::supervisor::{Command, Commands};
use std::sync::Arc;
use std::time::Duration;
use tao::event::{Event, StartCause};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{TrayIconBuilder, TrayIconEvent};

/// How often the menu re-reads the status. Fast enough that a state change looks instant, slow
/// enough to cost nothing.
const REFRESH: Duration = Duration::from_millis(400);

/// Run the tray. **Never returns** until the person chooses Quit, and must be called on the main
/// thread.
pub fn run(status: Arc<StatusHandle>, commands: Commands, is_paused: impl Fn() -> bool + 'static) {
    let event_loop = EventLoopBuilder::new().build();

    // Built inside the loop's first iteration on macOS; declared here so it outlives the closure.
    let mut tray = None;
    let mut items: Option<Items> = None;
    let mut last_revision = u64::MAX;

    event_loop.run(move |event, _target, control_flow| {
        *control_flow = ControlFlow::WaitUntil(std::time::Instant::now() + REFRESH);

        if let Event::NewEvents(StartCause::Init) = event {
            match build(&status) {
                Ok((icon, built)) => {
                    tray = Some(icon);
                    items = Some(built);
                }
                Err(e) => {
                    // Law 4: no silent absence. The helper keeps running — the relay does not need
                    // a menu — and says on stdout what the person has lost and what to use instead.
                    eprintln!(
                        "[egress] the menu-bar item could not be shown ({e}). The home connection \
                         is still running; use `matrx-egress status` and `matrx-egress pause` from \
                         a terminal, or manage this computer in AI Matrx on the web."
                    );
                }
            }
        }

        // Redraw only when something actually changed.
        let revision = status.revision();
        if revision != last_revision {
            last_revision = revision;
            if let (Some(tray), Some(items)) = (tray.as_ref(), items.as_ref()) {
                let snapshot = status.snapshot();
                items.title.set_text(snapshot.title_line());
                items
                    .pause
                    .set_text(if is_paused() || snapshot.state == State::Paused {
                        "Resume"
                    } else {
                        "Pause"
                    });
                let _ = tray.set_tooltip(Some(snapshot.title_line()));
            }
        }

        while let Ok(menu_event) = MenuEvent::receiver().try_recv() {
            let Some(items) = items.as_ref() else { continue };
            let command = if menu_event.id == items.pause.id() {
                if is_paused() {
                    Command::Resume
                } else {
                    Command::Pause
                }
            } else if menu_event.id == items.open.id() {
                Command::OpenComputersPage
            } else if menu_event.id == items.remove.id() {
                Command::ConfirmRemovalInBrowser
            } else if menu_event.id == items.quit.id() {
                Command::Quit
            } else {
                continue;
            };
            let quitting = command == Command::Quit;
            if commands.send(command).is_err() {
                *control_flow = ControlFlow::Exit;
                return;
            }
            if quitting {
                // Give the supervisor a moment to close the socket politely, then go.
                std::thread::sleep(Duration::from_millis(400));
                *control_flow = ControlFlow::Exit;
                return;
            }
        }

        // Drained so the channel cannot grow; a click on the icon itself opens the menu and needs
        // nothing from us.
        while TrayIconEvent::receiver().try_recv().is_ok() {}
    });
}

struct Items {
    title: MenuItem,
    pause: MenuItem,
    open: MenuItem,
    remove: MenuItem,
    quit: MenuItem,
}

fn build(status: &Arc<StatusHandle>) -> Result<(tray_icon::TrayIcon, Items), String> {
    let snapshot = status.snapshot();
    let menu = Menu::new();
    // The title line is an item that cannot be chosen — the same shape every menu-bar app uses to
    // say what it is doing.
    let title = MenuItem::new(snapshot.title_line(), false, None);
    let pause = MenuItem::new(
        if snapshot.state == State::Paused {
            "Resume"
        } else {
            "Pause"
        },
        true,
        None,
    );
    let open = MenuItem::new("Open in AI Matrx", true, None);
    let remove = MenuItem::new("Turn off and remove this computer", true, None);
    let quit = MenuItem::new("Quit", true, None);

    menu.append(&title).map_err(|e| e.to_string())?;
    menu.append(&PredefinedMenuItem::separator())
        .map_err(|e| e.to_string())?;
    menu.append(&pause).map_err(|e| e.to_string())?;
    menu.append(&open).map_err(|e| e.to_string())?;
    menu.append(&remove).map_err(|e| e.to_string())?;
    menu.append(&PredefinedMenuItem::separator())
        .map_err(|e| e.to_string())?;
    menu.append(&quit).map_err(|e| e.to_string())?;

    let image = crate::icon::tray_icon()?;
    let icon = tray_icon::Icon::from_rgba(image.bytes, image.width, image.height)
        .map_err(|e| format!("the tray icon could not be used: {e}"))?;

    let tray = TrayIconBuilder::new()
        .with_menu(Box::new(menu))
        .with_tooltip(snapshot.title_line())
        .with_icon(icon)
        .build()
        .map_err(|e| e.to_string())?;

    Ok((
        tray,
        Items {
            title,
            pause,
            open,
            remove,
            quit,
        },
    ))
}
