//! FS-L1 unit 3 — the remote side.
//!
//! **The feed is the only truth** (D9). Realtime is a trigger that says "look now" and nothing
//! more: Postgres replica identity does not put the old row on the wire, so Realtime can never
//! carry a delete, and an engine that believed it would resurrect every deleted file. Everything
//! that decides what the cloud holds is folded from feed pages.
//!
//! | Submodule | What it owns |
//! |---|---|
//! | [`entry`] | The wire shape, typed from the LIVE service rather than from prose. |
//! | [`apply`] | Folding pages into `tree_remote`: tombstones, echo suppression, the mapping prefix. Pure. |
//! | [`realtime`] | The Phoenix-channel trigger — it says "look now" and never carries data, plus the `polling_fallback` state that announces itself. |
//! | [`client`] | The one place that speaks HTTP, behind a trait so everything else can be driven without a network. |

pub mod apply;
pub mod client;
pub mod entry;
pub mod realtime;

pub use apply::{apply_file_page, apply_folder_page, ApplyContext, FeedApply};
pub use client::{
    classify_status, FeedAuth, FeedConfig, FeedError, FeedTransport, HttpFeed,
};
pub use realtime::{
    access_token_frame, heartbeat_frame, interpret, join_frame, LiveState, LiveTracker,
    PhoenixFrame, Signal, WatchedTable,
};
pub use entry::{FileEntry, FilePage, FolderEntry, FolderPage};
