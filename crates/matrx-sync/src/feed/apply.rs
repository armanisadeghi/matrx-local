//! Folding feed pages into `tree_remote` — the pure half.
//!
//! **The feed is the only truth** (D9). Realtime is a trigger that says "look now"; it never
//! carries deletes, because Postgres replica identity does not put the old row on the wire. So
//! everything that decides what the cloud holds happens here, from feed pages, and Realtime's only
//! job is to make this run sooner.
//!
//! Nothing in this module does IO or reads a clock: it takes a page and a cursor and returns rows
//! plus the new cursor, so the whole of it is testable without a network.

use crate::feed::entry::{FileEntry, FilePage, FolderEntry, FolderPage};
use crate::model::RemoteNode;

/// What one page changed.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct FeedApply {
    /// Rows to write into `tree_remote`, tombstones included — a tombstone is a row with
    /// `deleted_at`, never a missing row.
    pub rows: Vec<RemoteNode>,
    /// The cursor to persist. `None` means the page did not advance it.
    pub next_cursor: Option<String>,
    /// Whether the caller should immediately ask for another page.
    pub has_more: bool,
    /// Entries this device itself wrote, which were skipped.
    ///
    /// Echo suppression (SCOPE §3.1 item 8): our own upload comes back down the feed, and applying
    /// it would re-plan work we just did. They are COUNTED rather than silently dropped, so
    /// "nothing happened" and "it was all our own echo" are distinguishable in a log.
    pub suppressed_echoes: usize,
    /// Entries outside the mapping's cloud prefix, skipped.
    pub outside_mapping: usize,
}

/// What the caller knows that the page does not.
#[derive(Debug, Clone)]
pub struct ApplyContext {
    /// This device's `app_instances.id` (C13). Entries whose `origin_device_id` matches are echoes.
    pub this_device_id: Option<String>,
    /// The mapping's cloud prefix — `""` for an organization root, or `"Folder/Sub"` for a folder
    /// mapping. Entries outside it belong to another mapping or to no mapping.
    pub cloud_prefix: String,
}

impl ApplyContext {
    /// The mapping-relative path of a cloud path, or `None` if it is outside this mapping.
    pub fn relative(&self, cloud_path: &str) -> Option<String> {
        let cloud_path = cloud_path.trim_start_matches('/');
        if self.cloud_prefix.is_empty() {
            return Some(crate::naming::nfc(cloud_path));
        }
        let prefix = self.cloud_prefix.trim_matches('/');
        let rest = cloud_path.strip_prefix(prefix)?;
        // A prefix must end at a path boundary: "Photos" must not swallow "PhotosOld/x.jpg".
        let rest = match rest.strip_prefix('/') {
            Some(rest) => rest,
            None if rest.is_empty() => "",
            None => return None,
        };
        Some(crate::naming::nfc(rest))
    }
}

/// Fold a file page into rows for `tree_remote`.
pub fn apply_file_page(page: &FilePage, ctx: &ApplyContext) -> FeedApply {
    let mut out = FeedApply {
        next_cursor: page.next_cursor.clone(),
        has_more: page.has_more,
        ..FeedApply::default()
    };
    for entry in &page.files {
        // Echo suppression happens BEFORE the prefix test, so an echo outside the mapping is
        // counted as an echo rather than as somebody else's file.
        if is_echo(entry, ctx) {
            out.suppressed_echoes += 1;
            continue;
        }
        let Some(path_nfc) = ctx.relative(&entry.file_path) else {
            out.outside_mapping += 1;
            continue;
        };
        out.rows.push(row_from(entry, path_nfc));
    }
    out
}

/// Fold a folder page into rows for `tree_remote`.
///
/// Folders are directory rows: no content, no checksum. They exist so an empty folder the user
/// made in the browser appears on disk, which a file feed alone can never tell you about.
pub fn apply_folder_page(page: &FolderPage, ctx: &ApplyContext) -> FeedApply {
    let mut out = FeedApply {
        next_cursor: page.next_cursor.clone(),
        has_more: page.has_more,
        ..FeedApply::default()
    };
    for entry in &page.folders {
        let Some(path_nfc) = ctx.relative(&entry.folder_path) else {
            out.outside_mapping += 1;
            continue;
        };
        if path_nfc.is_empty() {
            continue; // the mapping root itself is not a row inside the mapping
        }
        out.rows.push(folder_row_from(entry, path_nfc));
    }
    out
}

/// Whether this entry is this device's own write coming back.
fn is_echo(entry: &FileEntry, ctx: &ApplyContext) -> bool {
    match (&ctx.this_device_id, &entry.origin_device_id) {
        (Some(mine), Some(theirs)) => mine == theirs,
        // No device id on either side means we cannot tell — and an entry we cannot attribute is
        // applied, never suppressed. Suppressing an unattributable entry would drop somebody
        // else's change; applying our own echo costs one wasted comparison.
        _ => false,
    }
}

fn row_from(entry: &FileEntry, path_nfc: String) -> RemoteNode {
    RemoteNode {
        path_nfc: path_nfc.clone(),
        is_dir: false,
        size: entry.size_bytes,
        remote_file_id: Some(entry.file_id.clone()),
        remote_folder_id: entry.folder_id.clone(),
        remote_version: entry.version,
        // A tombstone carries no checksum, and must not inherit the one it had when it was alive:
        // the planner reads a checksum as "this content is in the cloud".
        checksum: if entry.is_tombstone() {
            None
        } else {
            entry.checksum.clone()
        },
        // The user's clock, not the server's — what a surface shows as "modified".
        client_modified_at: entry.client_modified_at.clone(),
        origin_device_id: entry.origin_device_id.clone(),
        deleted_at: entry.deleted_at.clone(),
        seen_at: entry.updated_at.clone(),
    }
}

fn folder_row_from(entry: &FolderEntry, path_nfc: String) -> RemoteNode {
    RemoteNode {
        path_nfc,
        is_dir: true,
        size: None,
        remote_file_id: Some(entry.folder_id.clone()),
        remote_folder_id: entry.parent_id.clone(),
        remote_version: None,
        checksum: None,
        client_modified_at: None,
        origin_device_id: None,
        deleted_at: entry.deleted_at.clone(),
        seen_at: entry.updated_at.clone(),
    }
}
