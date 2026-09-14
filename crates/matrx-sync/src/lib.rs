//! `matrx-sync` — the AI Matrx folder-sync engine.
//!
//! One crate, two packagings (D1): the `matrx-syncd` daemon and the Tauri app compile this same
//! source. The contracts it implements are frozen at G1 in
//! `common-docs/projects/folder-sync/specs/` — `SPEC-ENGINE.md` (journal §4, knob registry §2,
//! honest states §3.6, invariants §4.3) and `CONTRACT-RULINGS.md` C1–C14.
//!
//! # Module map
//!
//! | Module | What it owns |
//! |---|---|
//! | [`naming`] | Conflict-copy naming (D7) and client-side name safety — pure, values injected. |
//! | [`sim`] | FS-C4. The deterministic simulation harness. **A mock — never product evidence.** |
//! | [`planner`] | FS-C3. `plan()` — a pure function of three trees, a direction and knobs. No IO, no clock, no randomness. |
//! | [`journal`] | FS-C2. The SQLite journal: the three trees, the per-mapping op queues, conflicts, migrations, and the synced-tree write guard (invariant I1). |
//! | [`model`] | The typed rows and trees every other module speaks in. |
//! | [`states`] | The ONE honest-state enum (C3) and the `contracts/honest_states.json` artifact (E16). |
//! | [`knobs`] | The knob registry as a struct the caller passes in — no limit is ever a constant inside a decision. |
//!
//! # What this crate never does
//!
//! It opens no network connection, reads no host clock, and composes no `~/.matrx` path: every one
//! of those is the daemon's, injected as a value. That is what makes the planner testable to the
//! four CanopyCheck invariants (SPEC-ENGINE §4.3) and the harness reproducible from a seed.

#![forbid(unsafe_code)]
#![warn(missing_docs)]
#![warn(clippy::all)]

pub mod error;
pub mod journal;
pub mod knobs;
pub mod model;
pub mod naming;
pub mod planner;
pub mod sim;
pub mod states;

pub use error::{Result, SyncError};
pub use knobs::Knobs;
pub use model::{Direction, LocalTree, RemoteTree, SyncedTree};
pub use planner::{plan, Plan, PlanContext, PlanOp};
pub use states::{HonestState, Scope, HONEST_STATES};

/// This crate's version, as compiled.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
