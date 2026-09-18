//! The menu-bar / system-tray item — macOS and Windows only.
//!
//! This module DRAWS; it decides nothing. What the menu says and what it offers is
//! [`crate::menu::model`], which is plain data and is asserted in tests on every platform. So the
//! ordinary menu looks like this:
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
//! …and one that has something to say carries it under the title, also not clickable:
//!
//! ```text
//! AI Matrx Home Connection — Removed from your account
//! This computer was removed from your account — run
//! Connect again to add it back. Open AI Matrx on the web
//! and connect this computer again.
//! ─────────────────────────────────────────────────────────────
//! Connect this computer again…
//! Open in AI Matrx
//! ─────────────────────────────────────────────────────────────
//! Quit
//! ```
//!
//! The whole menu is rebuilt whenever the status changes, rather than the items being edited in
//! place: the SET of true actions changes with the state (a removed computer is offered neither
//! Pause nor Resume), and a menu that could only edit its labels would have to keep an item that
//! does nothing — which is the failure this exists to remove.
//!
//! **The event loop owns the main thread and tokio runs on a worker thread.** On macOS a tray item
//! belongs to the `NSApplication`, which must be on the main thread, and the icon must be created
//! *after* the loop has started (`StartCause::Init`) or it does not appear at all. On Linux there
//! is no tray: the helper prints the same status on stdout, and this module is not compiled.

#![cfg(any(target_os = "macos", target_os = "windows"))]

use crate::menu::MenuModel;
use crate::status::StatusHandle;
use crate::supervisor::{Command, Commands, Terminal};
use std::sync::Arc;
use std::time::Duration;
use tao::event::{Event, StartCause};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tray_icon::menu::{Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem};
use tray_icon::{TrayIconBuilder, TrayIconEvent};

/// How often the menu re-reads the status. Fast enough that a state change looks instant, slow
/// enough to cost nothing.
const REFRESH: Duration = Duration::from_millis(400);

/// Run the tray. **Never returns** until the person chooses Quit, and must be called on the main
/// thread.
///
/// `is_paused` and `terminal` are closures rather than values because the supervisor owns both and
/// they move while the menu is up.
pub fn run(
    status: Arc<StatusHandle>,
    commands: Commands,
    is_paused: impl Fn() -> bool + 'static,
    terminal: impl Fn() -> Option<Terminal> + 'static,
) {
    let event_loop = EventLoopBuilder::new().build();

    // Built inside the loop's first iteration on macOS; declared here so it outlives the closure.
    let mut tray = None;
    // Which menu item id runs which command — rebuilt with the menu.
    let mut bindings: Vec<(MenuId, Command)> = Vec::new();
    let mut shown: Option<MenuModel> = None;
    let mut last_revision = u64::MAX;

    event_loop.run(move |event, _target, control_flow| {
        *control_flow = ControlFlow::WaitUntil(std::time::Instant::now() + REFRESH);

        let revision = status.revision();
        let model = crate::menu::model(&status.snapshot(), terminal(), is_paused());

        if let Event::NewEvents(StartCause::Init) = event {
            match build(&model) {
                Ok((icon, built)) => {
                    tray = Some(icon);
                    bindings = built;
                    shown = Some(model.clone());
                    last_revision = revision;
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

        // Redraw only when something actually changed. The revision catches status changes; the
        // model comparison catches Pause↔Resume and the terminal switch, which move without one.
        if let Some(tray) = tray.as_ref() {
            if revision != last_revision || shown.as_ref() != Some(&model) {
                last_revision = revision;
                match render(&model) {
                    Ok((menu, built)) => {
                        tray.set_menu(Some(Box::new(menu)));
                        bindings = built;
                        let _ = tray.set_tooltip(Some(tooltip(&model)));
                        shown = Some(model.clone());
                    }
                    Err(e) => eprintln!(
                        "[egress] the menu-bar item could not be updated ({e}), so it is showing \
                         what it showed before. `matrx-egress status` has the current answer."
                    ),
                }
            }
        }

        while let Ok(menu_event) = MenuEvent::receiver().try_recv() {
            let Some(command) = bindings
                .iter()
                .find(|(id, _)| *id == menu_event.id)
                .map(|(_, command)| *command)
            else {
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

/// The hover text: the title, plus the sentence when there is one, because a menu bar item's
/// tooltip is the only part visible without clicking.
fn tooltip(model: &MenuModel) -> String {
    if model.details.is_empty() {
        model.title.clone()
    } else {
        format!("{}\n{}", model.title, model.details.join(" "))
    }
}

/// Build the menu for a model. Returns which item id carries which command.
fn render(model: &MenuModel) -> Result<(Menu, Vec<(MenuId, Command)>), String> {
    let menu = Menu::new();

    let title = MenuItem::new(&model.title, false, None);
    menu.append(&title).map_err(|e| e.to_string())?;
    for line in &model.details {
        // Not clickable: these are the helper talking, not something to choose.
        let detail = MenuItem::new(line, false, None);
        menu.append(&detail).map_err(|e| e.to_string())?;
    }
    menu.append(&PredefinedMenuItem::separator())
        .map_err(|e| e.to_string())?;

    let mut bindings = Vec::with_capacity(model.actions.len());
    let last = model.actions.len().saturating_sub(1);
    for (i, action) in model.actions.iter().enumerate() {
        // Quit is always last and always sits under its own separator.
        if i == last && action.command == Command::Quit && last > 0 {
            menu.append(&PredefinedMenuItem::separator())
                .map_err(|e| e.to_string())?;
        }
        let item = MenuItem::new(&action.label, true, None);
        bindings.push((item.id().clone(), action.command));
        menu.append(&item).map_err(|e| e.to_string())?;
    }

    Ok((menu, bindings))
}

fn build(model: &MenuModel) -> Result<(tray_icon::TrayIcon, Vec<(MenuId, Command)>), String> {
    let (menu, bindings) = render(model)?;

    let image = crate::icon::tray_icon()?;
    let icon = tray_icon::Icon::from_rgba(image.bytes, image.width, image.height)
        .map_err(|e| format!("the tray icon could not be used: {e}"))?;

    let tray = TrayIconBuilder::new()
        .with_menu(Box::new(menu))
        .with_tooltip(tooltip(model))
        .with_icon(icon)
        .build()
        .map_err(|e| e.to_string())?;

    Ok((tray, bindings))
}
