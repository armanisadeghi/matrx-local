//! The ONE honest-state enum (SPEC-ENGINE §3.6, rulings C2/C3/C14).
//!
//! One vocabulary, owned by engine core. It is emitted as the machine-readable artifact
//! [`contracts/honest_states.json`](../contracts/honest_states.json) (ruling E16), which is the single
//! input to three consumers: SPEC-ENGINE's own three tables, SPEC-SERVER's generated
//! `files.sync_mappings.state` CHECK, and SPEC-SERVER's divergence test.
//!
//! The artifact is checked in and [`artifact_json`] renders the exact bytes it must hold; the test
//! `tests/honest_states_artifact.rs` fails if the file on disk is stale.
//!
//! Nothing in the codebase may invent a state value. `desired_state ∈ {active, paused, removed}`
//! is a different, user-written field owned by SPEC-SERVER (C4) and never appears here.

use serde::{Deserialize, Serialize};

/// Which surfaces a state value may be written to (SPEC-ENGINE §3.6 tables a/b/c).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    /// `session_state.state` — table (a).
    Session,
    /// `/v1/status.device.state` and `daemon.state` events — table (b).
    Device,
    /// `files.sync_mappings.state` and the journal's `mappings.state` — table (c).
    /// SPEC-SERVER's generated CHECK copies exactly this subset.
    Mapping,
}

impl Scope {
    /// The wire spelling, identical to the serde representation.
    pub const fn as_str(self) -> &'static str {
        match self {
            Scope::Session => "session",
            Scope::Device => "device",
            Scope::Mapping => "mapping",
        }
    }
}

/// One honest state: a value, the scopes it is legal on, the sentence a surface shows, and the
/// identifier of the one-click remedy behind it.
///
/// `remedy_action` is an action identifier, not a label; the label is rendered per surface from
/// `title`. `None` means "nothing to do" — only healthy/transient states carry it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct HonestState {
    /// The value written to a database column or an API payload.
    pub value: &'static str,
    /// Every scope this value is legal on.
    pub scopes: &'static [Scope],
    /// The sentence a surface shows. Never a spinner (law 4).
    pub title: &'static str,
    /// The one-click remedy's action identifier, or `None` when there is nothing to do.
    pub remedy_action: Option<&'static str>,
}

use Scope::{Device, Mapping, Session};

/// Every honest state, in the spec's table order (a, then b, then c).
///
/// This is the source of truth. The three rendered tables in SPEC-ENGINE §3.6 and SPEC-SERVER's
/// CHECK are all projections of this list.
pub const HONEST_STATES: &[HonestState] = &[
    // ---- (a) session states — SPEC-CUSTODY §8a names exactly these five.
    HonestState {
        value: "signed_in",
        scopes: &[Session, Device],
        title: "Signed in",
        remedy_action: None,
    },
    HonestState {
        value: "sign_in_needed",
        scopes: &[Session, Device, Mapping],
        title: "Sign in to keep syncing",
        remedy_action: Some("sign_in"),
    },
    HonestState {
        value: "signed_out",
        scopes: &[Session, Device, Mapping],
        title: "Signed out",
        remedy_action: Some("sign_in"),
    },
    HonestState {
        value: "credential_store_unavailable",
        scopes: &[Session, Device, Mapping],
        title: "The system keychain is unavailable",
        remedy_action: Some("retry_credential_store"),
    },
    HonestState {
        value: "offline",
        scopes: &[Session, Device, Mapping],
        title: "Offline — retrying",
        remedy_action: Some("retry_now"),
    },
    // ---- (b) device states.
    HonestState {
        value: "healthy",
        scopes: &[Device],
        title: "Syncing normally",
        remedy_action: None,
    },
    HonestState {
        value: "daemon_not_running",
        scopes: &[Device],
        title: "Sync is not running",
        remedy_action: Some("start_sync"),
    },
    HonestState {
        value: "daemon_not_registered",
        scopes: &[Device],
        title: "Finish sync setup",
        remedy_action: Some("finish_sync_setup"),
    },
    HonestState {
        value: "daemon_starting",
        scopes: &[Device],
        title: "Sync is starting",
        remedy_action: None,
    },
    HonestState {
        value: "crash_looping",
        scopes: &[Device],
        title: "Sync keeps stopping unexpectedly",
        remedy_action: Some("view_sync_diagnostics"),
    },
    HonestState {
        value: "safe_mode",
        scopes: &[Device],
        title: "Sync is paused in safe mode after repeated crashes",
        remedy_action: Some("leave_safe_mode"),
    },
    HonestState {
        value: "shutdown_stalled",
        scopes: &[Device],
        title: "Sync did not shut down in time",
        remedy_action: Some("view_sync_diagnostics"),
    },
    HonestState {
        value: "daemon_newer_than_app",
        scopes: &[Device],
        title: "Sync is newer than this app",
        remedy_action: Some("restart_app"),
    },
    HonestState {
        value: "daemon_older_than_journal",
        scopes: &[Device],
        title: "Update AI Matrx to read this sync database",
        remedy_action: Some("update_app"),
    },
    HonestState {
        value: "daemon_older_than_app",
        scopes: &[Device, Mapping],
        title: "Sync is older than this app and is restarting",
        remedy_action: Some("restart_sync"),
    },
    HonestState {
        value: "long_paths_unavailable",
        scopes: &[Device],
        title: "Windows long paths are disabled on this machine",
        remedy_action: Some("enable_long_paths"),
    },
    // ---- (c) mapping-row states — SPEC-SERVER's CHECK is generated from exactly this subset.
    HonestState {
        value: "pending",
        scopes: &[Mapping],
        title: "Waiting to start",
        remedy_action: None,
    },
    HonestState {
        value: "idle",
        scopes: &[Mapping],
        title: "Up to date",
        remedy_action: None,
    },
    HonestState {
        value: "scanning",
        scopes: &[Mapping],
        title: "Checking for changes",
        remedy_action: None,
    },
    HonestState {
        value: "syncing",
        scopes: &[Mapping],
        title: "Syncing",
        remedy_action: None,
    },
    HonestState {
        value: "paused",
        scopes: &[Mapping],
        title: "Paused",
        remedy_action: Some("resume_mapping"),
    },
    HonestState {
        value: "removing",
        scopes: &[Mapping],
        title: "Removing this folder from sync",
        remedy_action: None,
    },
    HonestState {
        value: "removed",
        scopes: &[Mapping],
        title: "Removed from sync",
        remedy_action: None,
    },
    HonestState {
        value: "needs_conflict_resolution",
        scopes: &[Mapping],
        title: "Some files need you to choose a version",
        remedy_action: Some("resolve_conflicts"),
    },
    HonestState {
        value: "root_missing",
        scopes: &[Mapping],
        title: "The synced folder is missing",
        remedy_action: Some("relocate_folder"),
    },
    HonestState {
        value: "suspended_marker_missing",
        scopes: &[Mapping],
        title: "Sync stopped: the folder marker is gone",
        remedy_action: Some("confirm_folder_identity"),
    },
    HonestState {
        value: "suspended_mass_delete",
        scopes: &[Mapping],
        title: "Sync stopped: an unusually large deletion was detected",
        remedy_action: Some("review_mass_delete"),
    },
    HonestState {
        value: "suspended_disk_full",
        scopes: &[Mapping],
        title: "Sync stopped: not enough free disk space",
        remedy_action: Some("free_disk_space"),
    },
    HonestState {
        value: "admission_revoked",
        scopes: &[Mapping],
        title: "This folder can no longer be synced",
        remedy_action: Some("review_admission"),
    },
    HonestState {
        value: "permission_denied",
        scopes: &[Mapping],
        title: "AI Matrx is not allowed to read this folder",
        remedy_action: Some("grant_folder_access"),
    },
    HonestState {
        value: "watcher_exhausted",
        scopes: &[Mapping],
        title: "Live folder watching ran out of system watches; checking on a timer",
        remedy_action: Some("raise_inotify_watches"),
    },
    HonestState {
        value: "over_quota",
        scopes: &[Mapping],
        title: "Your storage is full",
        remedy_action: Some("manage_storage"),
    },
    HonestState {
        value: "disk_low",
        scopes: &[Mapping],
        title: "This disk is nearly full",
        remedy_action: Some("free_disk_space"),
    },
    HonestState {
        value: "polling_fallback",
        scopes: &[Mapping],
        title: "Live updates unavailable, checking every minute",
        remedy_action: None,
    },
];

impl HonestState {
    /// Look a state up by its wire value.
    pub fn get(value: &str) -> Option<&'static HonestState> {
        HONEST_STATES.iter().find(|s| s.value == value)
    }

    /// Whether this value may be written to `scope`.
    pub fn allows(&self, scope: Scope) -> bool {
        self.scopes.contains(&scope)
    }
}

/// Every value legal on `scope`, in table order.
///
/// `values_for(Scope::Mapping)` is exactly what SPEC-SERVER's `files.sync_mappings.state`
/// CHECK is generated from.
pub fn values_for(scope: Scope) -> Vec<&'static str> {
    HONEST_STATES
        .iter()
        .filter(|s| s.allows(scope))
        .map(|s| s.value)
        .collect()
}

/// The exact bytes of `crates/matrx-sync/contracts/honest_states.json` (E16).
///
/// Rendered here rather than read from disk so the artifact can never drift from the enum
/// without a test failing.
pub fn artifact_json() -> String {
    let mut out = String::from("[\n");
    for (i, s) in HONEST_STATES.iter().enumerate() {
        let scopes: Vec<String> = s
            .scopes
            .iter()
            .map(|sc| format!("\"{}\"", sc.as_str()))
            .collect();
        let remedy = match s.remedy_action {
            Some(a) => format!("\"{a}\""),
            None => "null".to_string(),
        };
        out.push_str(&format!(
            "  {{\"value\": \"{}\", \"scopes\": [{}], \"title\": {}, \"remedy_action\": {}}}{}\n",
            s.value,
            scopes.join(", "),
            serde_json::to_string(s.title).expect("a &str always serialises"),
            remedy,
            if i + 1 == HONEST_STATES.len() { "" } else { "," }
        ));
    }
    out.push_str("]\n");
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    #[test]
    fn values_are_unique() {
        let mut seen = BTreeSet::new();
        for s in HONEST_STATES {
            assert!(seen.insert(s.value), "duplicate honest state {}", s.value);
        }
    }

    #[test]
    fn every_state_has_a_scope_and_a_title() {
        for s in HONEST_STATES {
            assert!(!s.scopes.is_empty(), "{} has no scope", s.value);
            assert!(!s.title.is_empty(), "{} has no title", s.value);
        }
    }

    #[test]
    fn mapping_subset_matches_spec_table_c() {
        // SPEC-ENGINE §3.6 table (c), verbatim. SPEC-SERVER's CHECK is generated from this set,
        // so a drift here silently breaks the server's divergence test.
        let expected: BTreeSet<&str> = [
            "pending",
            "idle",
            "scanning",
            "syncing",
            "paused",
            "removing",
            "removed",
            "needs_conflict_resolution",
            "root_missing",
            "suspended_marker_missing",
            "suspended_mass_delete",
            "suspended_disk_full",
            "admission_revoked",
            "permission_denied",
            "watcher_exhausted",
            "over_quota",
            "disk_low",
            "polling_fallback",
            "sign_in_needed",
            "signed_out",
            "credential_store_unavailable",
            "offline",
            "daemon_older_than_app",
        ]
        .into_iter()
        .collect();
        let actual: BTreeSet<&str> = values_for(Scope::Mapping).into_iter().collect();
        assert_eq!(actual, expected);
    }

    #[test]
    fn session_subset_matches_spec_table_a() {
        let expected: BTreeSet<&str> = [
            "signed_in",
            "sign_in_needed",
            "signed_out",
            "credential_store_unavailable",
            "offline",
        ]
        .into_iter()
        .collect();
        let actual: BTreeSet<&str> = values_for(Scope::Session).into_iter().collect();
        assert_eq!(actual, expected);
    }

    #[test]
    fn artifact_round_trips_as_json() {
        let parsed: serde_json::Value =
            serde_json::from_str(&artifact_json()).expect("artifact is valid JSON");
        assert_eq!(
            parsed.as_array().map(|a| a.len()),
            Some(HONEST_STATES.len())
        );
    }
}
