//! `matrx-sync` — the AI Matrx folder-sync engine.
//!
//! **Scaffold only (spike FS-C1).** This crate holds no sync logic yet. It
//! exists so the daemon binary (`matrx-syncd`) and the Tauri app compile the
//! same source, with zero duplication, before any behaviour is written
//! (folder-sync `DECISIONS.md` D1, `SCOPE.md` §4).
//!
//! Spec: `common-docs/projects/folder-sync/SCOPE.md`.

/// This crate's version, as compiled.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub mod journal;
