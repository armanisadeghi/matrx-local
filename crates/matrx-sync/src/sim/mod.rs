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
//! | [`rng`] | A hand-rolled deterministic PRNG, so a seed replays forever. |
//! | [`fs`] | An in-memory filesystem with real inode identity and an OS-trash stand-in. |
//! | [`server`] | The mock cloud: feed cursor, 412 preconditions, tombstones, stability lag. |
//! | [`device`] | One simulated device — mock disk, **real** journal on a tempdir file, executor. |
//! | [`scheduler`] | The deterministic scheduler: two devices interleaved, requests reordered, failures and crashes injected, time fast-forwarded. |
//! | [`world`] | One mapping's three trees plus the executor model the FS-C3 property tests use. |

pub mod device;
pub mod fs;
pub mod rng;
pub mod scheduler;
pub mod server;
pub mod world;

pub use device::{Device, StepOutcome};
pub use fs::MemFs;
pub use rng::Rng;
pub use scheduler::{Hazards, Simulation, SimulationReport};
pub use server::{MockServer, ServerError};
pub use world::{RecordedConflict, World};
