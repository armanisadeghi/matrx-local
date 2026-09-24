//! The knob registry as a struct the caller passes in (SPEC-ENGINE §2, D22).
//!
//! **No knob is ever a hardcoded constant inside a decision** (law: limits are knobs, and agents
//! set them). The planner reads every limit from a [`Knobs`] value handed to it; resolving the
//! `mapping > device > org` precedence is the daemon's job, not this crate's, so what arrives here
//! is already resolved.
//!
//! The defaults below are the registry's frozen G1 defaults. **Every provisional value in the
//! registry carries a review date of 2026-12-13**; a value still standing after it is a defect
//! visible in SPEC-ENGINE §2.

use serde::{Deserialize, Serialize};

/// What `download_only` does with a local edit (`sync.download_only_local_edit`, D6).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DownloadOnlyLocalEdit {
    /// Keep the local bytes, flag the row, never overwrite. The default and the only D6 value.
    PreserveAndFlag,
}

/// Every knob the planner reads, already resolved through `mapping > device > org`.
///
/// Knobs that only the daemon's IO layers read (transfer concurrency, watcher debounce, supervisor
/// timeouts, retention) are deliberately absent: this struct is the *planner's* surface, so a knob
/// appearing here is a knob some pure decision depends on.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Knobs {
    /// `sync.mass_delete_percent` — percent 1–100, default 50 (D8).
    pub mass_delete_percent: u8,
    /// `sync.mass_delete_count` — items ≥10, default 1000 (D8). The **absolute** arm.
    pub mass_delete_count: u32,
    /// `sync.mass_delete_window_hours` — hours ≥1, default 24. The breaker's **rolling window**.
    ///
    /// `deleted_count` and `deleted_percent` are measured over the deletions this mapping executed
    /// inside this window **plus** the plan being built — never over one plan alone. Counting one
    /// plan let a wipe through in instalments (H1): the daemon plans on a debounce timer and the
    /// scanner walks a large tree incrementally, so an `rm -rf`, an unmounted drive, or ransomware
    /// working alphabetically all arrive as a stream of small deletions.
    pub mass_delete_window_hours: u32,
    /// `sync.mass_delete_min_count` — items ≥1, default 20. The **floor under the percentage
    /// arm**, added by SPEC-ENGINE amendment 2 (2026-09-13).
    ///
    /// It is what lets the percentage arm protect a small folder without suspending a two-file
    /// folder for one deletion — the false positive that made amendment 1 choose AND-only.
    pub mass_delete_min_count: u32,
    /// `sync.upload_only_propagates_deletes` — default true (D6).
    pub upload_only_propagates_deletes: bool,
    /// `sync.download_only_local_edit` — default `preserve_and_flag` (D6).
    pub download_only_local_edit: DownloadOnlyLocalEdit,
    /// `sync.conflict_copy_template` — default the Dropbox convention (D7).
    pub conflict_copy_template: String,
    /// `sync.max_segment_chars` — chars per name segment, default 255.
    pub max_segment_chars: u32,
    /// `files.max_path_chars` — decoded chars, default 400 (server-enforced too, C12).
    pub max_path_chars: u32,
    /// `files.max_file_bytes` — default 5 GB (C12, D10).
    pub max_file_bytes: u64,
    /// `files.max_items_per_mapping` — default 1,000,000 (C12).
    pub max_items_per_mapping: u64,
    /// `sync.trash_local_deletes` — default true: a local delete goes to the OS trash.
    pub trash_local_deletes: bool,
    /// `sync.follow_symlinks` — default false: skipped and reported.
    pub follow_symlinks: bool,
    /// `sync.sync_secrets_class` — default false; opt-in, never indexed regardless (E6).
    pub sync_secrets_class: bool,
    /// `sync.secrets_class_patterns` — the glob list.
    pub secrets_class_patterns: Vec<String>,
    /// `sync.ignore_junk_patterns` — the glob list.
    pub ignore_junk_patterns: Vec<String>,
    /// `knowledge.index_for_knowledge` — the ONE home for this choice (C11, D21).
    pub index_for_knowledge: bool,
}

impl Default for Knobs {
    /// The frozen G1 defaults of SPEC-ENGINE §2.
    fn default() -> Self {
        Knobs {
            mass_delete_percent: 50,
            mass_delete_count: 1000,
            mass_delete_min_count: 20,
            mass_delete_window_hours: 24,
            upload_only_propagates_deletes: true,
            download_only_local_edit: DownloadOnlyLocalEdit::PreserveAndFlag,
            conflict_copy_template: "{stem} (conflicted copy from {device} {YYYY-MM-DD}){ext}"
                .to_string(),
            max_segment_chars: 255,
            max_path_chars: 400,
            max_file_bytes: 5_000_000_000,
            max_items_per_mapping: 1_000_000,
            trash_local_deletes: true,
            follow_symlinks: false,
            sync_secrets_class: false,
            secrets_class_patterns: [
                ".env*",
                "*.pem",
                "id_rsa*",
                "id_ed25519*",
                "*.key",
                "*.p12",
                "*.keychain*",
                "wallet.dat",
                "*.kdbx",
            ]
            .iter()
            .map(|s| (*s).to_string())
            .collect(),
            ignore_junk_patterns: [
                ".DS_Store",
                "Thumbs.db",
                "desktop.ini",
                "~$*",
                "*.tmp",
                "*.part",
                "*.crdownload",
                ".matrx-sync/",
                ".Spotlight-V100",
                ".Trashes",
                ".fseventsd",
                "$RECYCLE.BIN/",
                "System Volume Information/",
            ]
            .iter()
            .map(|s| (*s).to_string())
            .collect(),
            index_for_knowledge: true,
        }
    }
}

/// The knobs only the executor's IO layers read (SPEC-ENGINE §2), resolved by the daemon.
///
/// Kept apart from [`Knobs`] on purpose: that struct is the PLANNER's surface, and a knob that
/// appears there is one a pure decision depends on. Nothing here changes what the plan says — only
/// how fast, how patiently and how many at once the executor carries it out.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TransferKnobs {
    /// `sync.transfer_concurrency` — device, 1–16, default 4. One pool per device.
    pub transfer_concurrency: u32,
    /// `sync.locked_file_retry_base_s` — device, default 5. The first backoff step.
    pub retry_base_s: u32,
    /// `sync.locked_file_retry_max_s` — device, default 900. The backoff ceiling.
    pub retry_max_s: u32,
    /// `sync.lease_ttl_s` — device, default 900.
    pub lease_ttl_s: u32,
    /// `sync.request_timeout_s` — device, default 300, 10–3600. **A registry row this unit adds**
    /// (SPEC-ENGINE §2 had no timeout knob, and a request with no timeout hangs a mapping
    /// forever on a captive portal). Sized for a buffered upload of a large file on a slow link.
    pub request_timeout_s: u64,
}

impl Default for TransferKnobs {
    fn default() -> Self {
        TransferKnobs {
            transfer_concurrency: 4,
            retry_base_s: 5,
            retry_max_s: 900,
            lease_ttl_s: 900,
            request_timeout_s: 300,
        }
    }
}

impl TransferKnobs {
    /// Check every value against its documented range; refuse, never clamp.
    pub fn validate(&self) -> crate::Result<()> {
        use crate::SyncError::KnobOutOfRange;
        if !(1..=16).contains(&self.transfer_concurrency) {
            return Err(KnobOutOfRange {
                knob: "sync.transfer_concurrency",
                value: self.transfer_concurrency.to_string(),
            });
        }
        if self.retry_base_s == 0 || self.retry_max_s < self.retry_base_s {
            return Err(KnobOutOfRange {
                knob: "sync.locked_file_retry_base_s",
                value: format!("{} (max {})", self.retry_base_s, self.retry_max_s),
            });
        }
        if !(10..=3600).contains(&self.request_timeout_s) {
            return Err(KnobOutOfRange {
                knob: "sync.request_timeout_s",
                value: self.request_timeout_s.to_string(),
            });
        }
        Ok(())
    }
}

impl Knobs {
    /// Check every value against its documented range (SPEC-ENGINE §2).
    ///
    /// The daemon calls this when it resolves knobs; the planner assumes a validated value and
    /// never silently clamps one — a clamp would be a limit chosen by an agent.
    pub fn validate(&self) -> crate::Result<()> {
        use crate::SyncError::KnobOutOfRange;
        if !(1..=100).contains(&self.mass_delete_percent) {
            return Err(KnobOutOfRange {
                knob: "sync.mass_delete_percent",
                value: self.mass_delete_percent.to_string(),
            });
        }
        if self.mass_delete_count < 10 {
            return Err(KnobOutOfRange {
                knob: "sync.mass_delete_count",
                value: self.mass_delete_count.to_string(),
            });
        }
        if self.mass_delete_window_hours < 1 {
            return Err(KnobOutOfRange {
                knob: "sync.mass_delete_window_hours",
                value: self.mass_delete_window_hours.to_string(),
            });
        }
        if self.mass_delete_min_count < 1 {
            return Err(KnobOutOfRange {
                knob: "sync.mass_delete_min_count",
                value: self.mass_delete_min_count.to_string(),
            });
        }
        if self.max_segment_chars == 0 {
            return Err(KnobOutOfRange {
                knob: "sync.max_segment_chars",
                value: self.max_segment_chars.to_string(),
            });
        }
        if self.max_path_chars == 0 {
            return Err(KnobOutOfRange {
                knob: "files.max_path_chars",
                value: self.max_path_chars.to_string(),
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_the_frozen_registry_values() {
        let k = Knobs::default();
        assert_eq!(k.mass_delete_percent, 50);
        assert_eq!(k.mass_delete_count, 1000);
        assert_eq!(k.mass_delete_min_count, 20);
        assert_eq!(k.mass_delete_window_hours, 24);
        assert_eq!(k.max_path_chars, 400);
        assert_eq!(k.max_segment_chars, 255);
        assert_eq!(k.max_file_bytes, 5_000_000_000);
        assert_eq!(k.max_items_per_mapping, 1_000_000);
        assert!(k.upload_only_propagates_deletes);
        assert!(!k.sync_secrets_class);
        k.validate().expect("the frozen defaults are in range");
    }

    #[test]
    fn out_of_range_knobs_are_refused_not_clamped() {
        let percent = Knobs {
            mass_delete_percent: 0,
            ..Knobs::default()
        };
        assert!(percent.validate().is_err());
        let count = Knobs {
            mass_delete_count: 9,
            ..Knobs::default()
        };
        assert!(count.validate().is_err());
        let floor = Knobs {
            mass_delete_min_count: 0,
            ..Knobs::default()
        };
        assert!(floor.validate().is_err());
        let window = Knobs {
            mass_delete_window_hours: 0,
            ..Knobs::default()
        };
        assert!(window.validate().is_err());
    }
}
