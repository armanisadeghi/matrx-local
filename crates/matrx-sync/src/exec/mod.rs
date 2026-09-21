//! FS-L1 unit 4 — the executor: the one module that turns a [`crate::Plan`] into changes on a
//! disk and in the cloud.
//!
//! Everything before this point described. This performs — and it is therefore the first place
//! where a mistake destroys a user's file rather than producing a wrong `Vec`. The whole design
//! follows from that:
//!
//! | Submodule | What it owns |
//! |---|---|
//! | [`error`] | Every failure as a named honest state with a remedy — never a panic, never a silent skip. |
//! | [`io`] | The two seams, [`io::LocalIo`] and [`io::RemoteIo`], so the decisions are testable without a disk that can fill up or a server that must be asked to return 412. |
//! | [`order`] | Dependency order: dirs before files, moves before the creates that reuse their paths, deletes last and deepest-first. |
//! | [`local`] | The production disk: atomic writes through `.matrx-sync/tmp`, NFC/NFD and case resolution (I8), preserved mtimes, the OS trash. |
//! | [`engine`] | The executor itself: enqueue, act, confirm — and re-plan rather than retry when the world moved. |
//! | [`fakes`] | A **test seam**: fault-injectable stand-ins for both. Never product evidence. |
//!
//! The synced tree is written ONLY through the three guarded doors in
//! [`crate::journal::confirm`]. This module holds no fourth door and could not compile one: the
//! token that authorises a `tree_synced` write is private to that file.

pub mod engine;
pub mod error;
pub mod fakes;
pub mod io;
pub mod local;
pub mod order;

pub use local::RealLocalIo;
pub use engine::{ExecContext, ExecEvent, ExecReport, Executor, OpOutcome};
pub use error::{ExecError, ExecResult, Precondition};
pub use io::{
    DeleteRequest, LocalIo, LocalStat, NoProgress, ProgressSink, RemoteIo, RemoteObject,
    RenameRequest, Staged, UploadRequest,
};
