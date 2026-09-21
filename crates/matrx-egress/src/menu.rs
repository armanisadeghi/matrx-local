//! What the menu says and what it offers — the whole model, with no tray in it.
//!
//! The tray is macOS/Windows only and needs a running event loop, so anything decided inside
//! `tray.rs` cannot be asserted. Everything that is a *judgement* therefore lives here: the title
//! line, the detail lines under it, and which actions are TRUE right now. `tray.rs` is left with
//! nothing but drawing.
//!
//! Two laws shape it:
//!
//! * **Law 4 — nothing fails silently.** A menu never offers a control that would do nothing. When
//!   this computer has been removed from the account, "Pause" is a lie and "Resume" is a no-op, so
//!   neither appears: the one true action does. When another copy took over, there is nothing to
//!   pause at all.
//! * **An error is said out loud, with its remedy.** The title says the short version; the lines
//!   under it carry the whole sentence and what to do about it, wrapped to a width a menu can show.

use crate::status::{State, Status};
use crate::supervisor::{Command, Terminal};

/// How wide a detail line may be before it wraps. Menus grow to their widest item, so this is the
/// width of the menu itself — wide enough for a sentence, narrow enough not to span the screen.
///
/// Only the tray (macOS/Windows) draws a menu, but the decision of what it says stays compiled
/// and tested on every platform (see the module doc) — so on a platform with no tray this is
/// legitimately unused outside its own tests, not a mistake.
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
const WRAP: usize = 46;

/// How long the reason in the title may be before it is cut at a word.
const TITLE_REASON: usize = 52;

/// At most this many detail lines. A menu is not a log.
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
const MAX_DETAIL_LINES: usize = 6;

/// One thing the person can choose, and what it does.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
pub struct Action {
    /// What the item says.
    pub label: String,
    /// What choosing it asks of the supervisor.
    pub command: Command,
}

/// The whole menu, decided.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
pub struct MenuModel {
    /// The first line: not clickable, says what the helper is doing.
    pub title: String,
    /// The lines under it: what went wrong and what to do. Empty when nothing is wrong.
    pub details: Vec<String>,
    /// The actions, in order. `Quit` is always the last one.
    pub actions: Vec<Action>,
}

#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
fn action(label: &str, command: Command) -> Action {
    Action {
        label: label.to_string(),
        command,
    }
}

/// Decide the menu.
///
/// `terminal` is `Some` once a session ended in a way that cannot be retried — the account removed
/// this computer (`4401`) or another copy took over (`4409`). In either case the connection will
/// not come back by itself, so Pause/Resume is replaced by the one action that is true.
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
pub fn model(status: &Status, terminal: Option<Terminal>, paused: bool) -> MenuModel {
    let title = status.title_line();
    let details = detail_lines(status);

    let actions = match terminal {
        Some(Terminal::Removed) => vec![
            // The account no longer has this computer, so there is nothing to pause and nothing
            // to remove. Connecting it again is the only thing left that does something.
            action("Connect this computer again…", Command::OpenComputersPage),
            action("Open in AI Matrx", Command::OpenComputersPage),
            action("Quit", Command::Quit),
        ],
        Some(Terminal::Replaced) => vec![
            // Another copy of the helper holds this computer's connection. This one is not going
            // to get it back while that one runs, so the honest choice is to quit this one. The
            // account still lists the computer, so both web items stay true.
            action("Open in AI Matrx", Command::OpenComputersPage),
            action(
                "Turn off and remove this computer",
                Command::ConfirmRemovalInBrowser,
            ),
            action("Quit", Command::Quit),
        ],
        None => vec![
            if paused || status.state == State::Paused {
                action("Resume", Command::Resume)
            } else {
                action("Pause", Command::Pause)
            },
            action("Open in AI Matrx", Command::OpenComputersPage),
            action(
                "Turn off and remove this computer",
                Command::ConfirmRemovalInBrowser,
            ),
            action("Quit", Command::Quit),
        ],
    };

    MenuModel {
        title,
        details,
        actions,
    }
}

/// The sentence and its remedy, wrapped for a menu. Empty when there is nothing to say.
///
/// The remedy is shown whenever there is one, even without a sentence before it: "what to do" is
/// never withheld because "what happened" happens to be absent.
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
pub fn detail_lines(status: &Status) -> Vec<String> {
    let mut text = String::new();
    if let Some(error) = status.last_error.as_deref() {
        text.push_str(error.trim());
    }
    if let Some(remedy) = status.remedy.as_deref() {
        if !text.is_empty() {
            text.push(' ');
        }
        text.push_str(remedy.trim());
    }
    if text.trim().is_empty() {
        return Vec::new();
    }

    let mut lines = wrap(&text, WRAP);
    if lines.len() > MAX_DETAIL_LINES {
        lines.truncate(MAX_DETAIL_LINES);
        if let Some(last) = lines.last_mut() {
            last.push('…');
        }
    }
    lines
}

/// The short version of the sentence, for the title line: its first sentence, cut at a word.
pub fn short_reason(last_error: &str) -> Option<String> {
    let trimmed = last_error.trim();
    if trimmed.is_empty() {
        return None;
    }
    // The first sentence — a full stop followed by a space, so "1.5 MB" stays whole.
    let first = match trimmed.find(". ") {
        Some(i) => &trimmed[..i],
        None => trimmed.trim_end_matches('.'),
    };
    let first = first.trim();
    if first.is_empty() {
        return None;
    }
    if first.chars().count() <= TITLE_REASON {
        return Some(first.to_string());
    }
    let mut out = String::new();
    for word in first.split_whitespace() {
        if out.chars().count() + word.chars().count() + 1 > TITLE_REASON {
            break;
        }
        if !out.is_empty() {
            out.push(' ');
        }
        out.push_str(word);
    }
    if out.is_empty() {
        // One word longer than the whole budget: cut it rather than show nothing.
        out = first.chars().take(TITLE_REASON).collect();
    }
    out.push('…');
    Some(out)
}

/// Greedy word wrap. A word longer than the width gets its own line rather than being cut in half.
#[cfg_attr(not(any(target_os = "macos", target_os = "windows")), allow(dead_code))]
fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        let candidate = line.chars().count() + if line.is_empty() { 0 } else { 1 } + word.chars().count();
        if !line.is_empty() && candidate > width {
            lines.push(std::mem::take(&mut line));
        }
        if !line.is_empty() {
            line.push(' ');
        }
        line.push_str(word);
    }
    if !line.is_empty() {
        lines.push(line);
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::status::{State, StatusHandle};

    fn handle() -> StatusHandle {
        StatusHandle::new("https://example.test".into(), None, false)
    }

    #[test]
    fn a_healthy_menu_offers_pause_and_says_nothing_extra() {
        let handle = handle();
        handle.set_device_name("The kitchen Mac");
        handle.set_state(State::Connected);
        let menu = model(&handle.snapshot(), None, false);
        assert_eq!(
            menu.title,
            "AI Matrx Home Connection — Connected as The kitchen Mac"
        );
        assert!(menu.details.is_empty(), "{:?}", menu.details);
        assert_eq!(menu.actions[0].command, Command::Pause);
        assert_eq!(menu.actions.last().expect("quit").command, Command::Quit);
    }

    #[test]
    fn a_paused_menu_offers_resume() {
        let handle = handle();
        handle.set_state(State::Paused);
        let menu = model(&handle.snapshot(), None, true);
        assert_eq!(menu.actions[0].command, Command::Resume);
        assert_eq!(menu.actions[0].label, "Resume");
    }

    #[test]
    fn an_error_reaches_the_menu_as_a_sentence_and_a_remedy() {
        let handle = handle();
        handle.set_error(
            State::Error,
            "AI Matrx refused this computer's connection (503 Service Unavailable)",
            "Nothing to do — it will try again in about 30 seconds.",
        );
        let menu = model(&handle.snapshot(), None, false);
        let detail = menu.details.join(" ");
        assert!(
            detail.contains("refused this computer's connection"),
            "the menu lost the sentence: {detail}"
        );
        assert!(
            detail.contains("try again in about 30 seconds"),
            "the menu lost the remedy: {detail}"
        );
        for line in &menu.details {
            assert!(line.chars().count() <= WRAP + 8, "unwrapped line: {line}");
        }
    }

    #[test]
    fn a_remedy_without_a_sentence_is_still_shown() {
        // The review's finding on the desktop half, asserted on this half too: "what to do" is
        // never hidden because "what happened" is absent.
        let status = Status {
            state: State::Error,
            since: crate::status::now_rfc3339(),
            device_name: None,
            server: "https://example.test".into(),
            streams_active: 0,
            streams_total: 0,
            bytes_relayed: 0,
            last_error: None,
            remedy: Some("Open AI Matrx and connect this computer again.".into()),
            helper_version: crate::VERSION.into(),
        };
        assert_eq!(
            detail_lines(&status).join(" "),
            "Open AI Matrx and connect this computer again."
        );
    }

    #[test]
    fn signed_out_and_error_do_not_wear_the_same_title() {
        let handle = handle();
        handle.set_error(
            State::SignedOut,
            "This computer was removed from your account — run Connect again to add it back.",
            "Open AI Matrx on the web and connect this computer again.",
        );
        let removed = handle.snapshot().title_line();
        handle.set_error(
            State::Error,
            "This computer could not reach AI Matrx: network is unreachable",
            "Check this computer's internet connection.",
        );
        let errored = handle.snapshot().title_line();
        assert_eq!(
            removed,
            "AI Matrx Home Connection — Removed from your account"
        );
        assert!(
            errored.starts_with("AI Matrx Home Connection — Not connected: "),
            "{errored}"
        );
        assert_ne!(removed, errored, "two different states read identically");
    }

    #[test]
    fn a_removed_computer_is_never_offered_pause_or_resume() {
        let handle = handle();
        handle.set_error(
            State::SignedOut,
            "This computer was removed from your account — run Connect again to add it back.",
            "Open AI Matrx on the web and connect this computer again.",
        );
        let menu = model(&handle.snapshot(), Some(Terminal::Removed), false);
        for a in &menu.actions {
            assert!(
                a.command != Command::Pause && a.command != Command::Resume,
                "a removed computer was offered {:?}",
                a.command
            );
        }
        assert_eq!(menu.actions[0].label, "Connect this computer again…");
        assert!(!menu.details.is_empty(), "the removal said nothing");
    }

    #[test]
    fn a_replaced_connection_offers_no_pause_and_keeps_quit() {
        let handle = handle();
        handle.set_error(
            State::Error,
            "Another copy of the Home Connection took over for this computer.",
            "Only one copy runs at a time. Quit this one, or quit the other and start this one \
             again.",
        );
        let menu = model(&handle.snapshot(), Some(Terminal::Replaced), false);
        for a in &menu.actions {
            assert!(
                a.command != Command::Pause && a.command != Command::Resume,
                "a replaced connection was offered {:?}",
                a.command
            );
        }
        assert_eq!(menu.actions.last().expect("quit").command, Command::Quit);
        assert!(menu
            .details
            .join(" ")
            .contains("took over for this computer"));
    }

    #[test]
    fn no_line_in_any_state_speaks_jargon() {
        let handle = handle();
        handle.set_error(
            State::Error,
            "This computer could not reach AI Matrx: network is unreachable",
            "Check this computer's internet connection.",
        );
        for terminal in [None, Some(Terminal::Removed), Some(Terminal::Replaced)] {
            let menu = model(&handle.snapshot(), terminal, false);
            let all = std::iter::once(menu.title.clone())
                .chain(menu.details.clone())
                .chain(menu.actions.iter().map(|a| a.label.clone()))
                .collect::<Vec<_>>()
                .join(" | ");
            for jargon in ["egress", "proxy", "residential", "socket", "relay", "4401"] {
                assert!(!all.contains(jargon), "{terminal:?} says {jargon:?}: {all}");
            }
        }
    }

    #[test]
    fn a_long_sentence_is_cut_at_a_word_in_the_title() {
        let long = "AI Matrx refused this computer's connection because the account it belongs \
                    to is no longer allowed to lend connections";
        let short = short_reason(long).expect("a reason");
        assert!(short.chars().count() <= TITLE_REASON + 1, "{short}");
        assert!(short.ends_with('…'), "{short}");
        assert!(!short.contains("  "), "{short}");
        // Cut at a word: the last thing before the ellipsis is a whole word.
        assert!(long.contains(short.trim_end_matches('…')), "{short}");
    }

    #[test]
    fn the_title_reason_is_the_first_sentence_only() {
        assert_eq!(
            short_reason("Paused from the web. Turn it back on in AI Matrx.").as_deref(),
            Some("Paused from the web")
        );
        assert_eq!(short_reason("   ").as_deref(), None);
    }
}
