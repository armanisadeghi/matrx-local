//! An in-memory filesystem. **A mock.**
//!
//! Content is modelled by content id — the hex a real file would hash to — never by bytes. Every
//! node carries the identity the rename detector needs: a volume id and an inode that survives a
//! move, exactly as a real one does.

use crate::model::{LocalNode, LocalTree};
use std::collections::BTreeMap;

/// One node on the mock disk.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemNode {
    /// Whether it is a directory.
    pub is_dir: bool,
    /// The content id; `None` for directories.
    pub content: Option<String>,
    /// Size in bytes.
    pub size: Option<i64>,
    /// Modification time, in the simulation's ticks.
    pub mtime_ns: i64,
    /// The inode — stable across a rename, like the real thing.
    pub file_id: String,
}

/// An in-memory filesystem for one simulated device.
#[derive(Debug, Clone)]
pub struct MemFs {
    nodes: BTreeMap<String, MemNode>,
    /// The volume every node lives on.
    pub volume_id: String,
    next_inode: u64,
    /// What the OS trash holds — a local delete is recoverable, so a test can prove it.
    pub trash: Vec<(String, Option<String>)>,
}

impl MemFs {
    /// An empty disk.
    pub fn new(volume_id: impl Into<String>) -> Self {
        MemFs {
            nodes: BTreeMap::new(),
            volume_id: volume_id.into(),
            next_inode: 1,
            trash: Vec::new(),
        }
    }

    fn fresh_inode(&mut self) -> String {
        self.next_inode += 1;
        format!("inode-{}", self.next_inode)
    }

    /// Create or replace a file, keeping its inode if it already existed.
    pub fn write(&mut self, path: &str, content: &str, now: i64) {
        let file_id = match self.nodes.get(path) {
            Some(n) => n.file_id.clone(),
            None => self.fresh_inode(),
        };
        self.nodes.insert(
            path.to_string(),
            MemNode {
                is_dir: false,
                content: Some(content.to_string()),
                size: Some(content.len() as i64),
                mtime_ns: now,
                file_id,
            },
        );
    }

    /// Create a directory.
    pub fn mkdir(&mut self, path: &str, now: i64) {
        if self.nodes.contains_key(path) {
            return;
        }
        let file_id = self.fresh_inode();
        self.nodes.insert(
            path.to_string(),
            MemNode {
                is_dir: true,
                content: None,
                size: None,
                mtime_ns: now,
                file_id,
            },
        );
    }

    /// Remove a node, optionally to the trash (`sync.trash_local_deletes`).
    pub fn remove(&mut self, path: &str, to_trash: bool) {
        if let Some(n) = self.nodes.remove(path) {
            if to_trash {
                self.trash.push((path.to_string(), n.content));
            }
        }
    }

    /// Move a node, preserving its inode — which is what makes a move detectable as one.
    pub fn rename(&mut self, from: &str, to: &str) {
        if let Some(n) = self.nodes.remove(from) {
            self.nodes.insert(to.to_string(), n);
        }
    }

    /// Copy a node to a new path with a **new** inode, as a real copy gets.
    pub fn copy(&mut self, from: &str, to: &str, now: i64) {
        let Some(src) = self.nodes.get(from).cloned() else {
            return;
        };
        let file_id = self.fresh_inode();
        self.nodes.insert(
            to.to_string(),
            MemNode {
                mtime_ns: now,
                file_id,
                ..src
            },
        );
    }

    /// The content id at `path`.
    pub fn content(&self, path: &str) -> Option<&str> {
        self.nodes.get(path).and_then(|n| n.content.as_deref())
    }

    /// Whether a node exists.
    pub fn contains(&self, path: &str) -> bool {
        self.nodes.contains_key(path)
    }

    /// Every path, in order.
    pub fn paths(&self) -> impl Iterator<Item = &String> {
        self.nodes.keys()
    }

    /// Every node, in path order.
    pub fn iter(&self) -> impl Iterator<Item = (&String, &MemNode)> {
        self.nodes.iter()
    }

    /// A scan of the whole disk as the scanner would record it.
    ///
    /// `hash_now = false` models the real scanner's first pass, which records identity and
    /// `(size, mtime)` and leaves the hash for later — the condition invariant I9 exists for.
    pub fn scan(&self, hash_now: bool, now: i64) -> LocalTree {
        self.nodes
            .iter()
            .map(|(path, n)| {
                (
                    path.clone(),
                    LocalNode {
                        path_nfc: path.clone(),
                        is_dir: n.is_dir,
                        size: n.size,
                        mtime_ns: Some(n.mtime_ns),
                        volume_id: Some(self.volume_id.clone()),
                        file_id: Some(n.file_id.clone()),
                        content_hash: if n.is_dir || !hash_now {
                            None
                        } else {
                            n.content.clone()
                        },
                        scanned_at: Some(format!("tick-{now}")),
                    },
                )
            })
            .collect()
    }
}
