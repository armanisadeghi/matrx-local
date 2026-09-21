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
//! | [`custody`] | FS-C5. Credential custody: PKCE in the daemon, the keychain item, rotation, the session state. **The one module here that speaks HTTP and reads a clock** — every such seam is a trait. |
//! | [`sim`] | FS-C4. The deterministic simulation harness. **A mock — never product evidence.** |
//! | [`scan`] | FS-L1. The real-filesystem scanner: identity, the size/mtime fast path, the walk. The first module here that touches a disk. |
//! | [`exec`] | FS-L1. The executor: a plan applied to the disk and the cloud, op by op, under the journal's double-confirmation discipline. The first module that can destroy a user's file. |
//! | [`feed`] | FS-L1. The remote side: the change feed is the only truth; Realtime is only a trigger. |
//! | [`watch`] | FS-L1. Local change detection: the watcher is an optimisation, the periodic rescan is the mechanism. |
//! | [`planner`] | FS-C3. `plan()` — a pure function of three trees, a direction and knobs. No IO, no clock, no randomness. |
//! | [`journal`] | FS-C2. The SQLite journal: the three trees, the per-mapping op queues, conflicts, migrations, and the synced-tree write guard (invariant I1). |
//! | [`model`] | The typed rows and trees every other module speaks in. |
//! | [`states`] | The ONE honest-state enum (C3) and the `contracts/honest_states.json` artifact (E16). |
//! | [`knobs`] | The knob registry as a struct the caller passes in — no limit is ever a constant inside a decision. |
//!
//! # What the sync engine never does
//!
//! [`planner`], [`journal`], [`sim`], [`naming`], [`model`], [`states`] and [`knobs`] open no
//! network connection, read no host clock, and compose no `~/.matrx` path: every one of those is
//! the daemon's, injected as a value. That is what makes the planner testable to the four
//! CanopyCheck invariants (SPEC-ENGINE §4.3) and the harness reproducible from a seed.
//!
//! [`custody`] is the one exception, and it is an exception by contract: SPEC-CUSTODY's state
//! machine *is* an HTTP conversation with a clock in it. It still composes no `~/.matrx` path —
//! the daemon passes it a [`custody::World`] and a journal — and every one of its outside edges
//! (the authorization server, the OS keychain, the cloud write, the notifier, the clock) is a
//! trait with a named fake, so §13's battery drives every branch with none of them.

#![forbid(unsafe_code)]
#![warn(missing_docs)]
#![warn(clippy::all)]

pub mod custody;
pub mod error;
pub mod exec;
pub mod feed;
pub mod journal;
pub mod knobs;
pub mod model;
pub mod naming;
pub mod planner;
pub mod scan;
pub mod watch;
pub mod sim;
pub mod states;

pub use custody::{Custodian, CustodyConfig, CustodyError, SessionState, World};
pub use error::{Result, SyncError};
pub use exec::{ExecContext, ExecError, ExecReport, Executor, LocalIo, RemoteIo};
pub use knobs::Knobs;
pub use model::{Direction, LocalTree, RemoteTree, SyncedTree};
pub use planner::{plan, Plan, PlanContext, PlanOp};
pub use states::{HonestState, Scope, HONEST_STATES};

/// This crate's version, as compiled.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
