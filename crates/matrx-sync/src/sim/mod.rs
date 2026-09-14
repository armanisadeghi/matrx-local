//! FS-C4 — the deterministic simulation harness.
//!
//! **Everything in this module is a MOCK.** It models an in-memory filesystem and an in-memory
//! server so the planner can be driven through hundreds of thousands of situations that would take
//! months to reach with real devices. Green here proves the **planner**; it is never evidence that
//! the product works (SCOPE §6, D2). Product evidence comes from FS-V2 on real machines.
//!
//! Nothing here reads a clock, a random device, or the network: every decision comes from an
//! explicit seed, so a failure replays exactly.
//!
//! | Submodule | What it is |
//! |---|---|
//! | [`world`] | One mapping's three trees plus the executor model that applies a [`crate::Plan`] to them. |

pub mod world;

pub use world::{RecordedConflict, World};
