//! The deterministic scheduler. **A mock.**
//!
//! Two devices and one cloud, driven from a seed. Every step the scheduler picks a device and an
//! action — scan, poll the feed, execute one queued operation — which is what reorders requests:
//! device A's upload can land between device B's plan and device B's write, and that is exactly
//! the interleaving that produces a 412.
//!
//! It also injects transient failures, crashes a device (dropping its journal handle and
//! reopening the file, so the device genuinely resumes from what it had committed), and
//! fast-forwards the clock past the server's stability lag.
//!
//! Everything is seeded. [`SimulationReport`] carries the seed, and every assertion helper prints
//! it, so a failure is replayed by re-running with the same number.

use super::device::{Device, StepOutcome};
use super::rng::Rng;
use super::server::MockServer;
use crate::knobs::Knobs;
use crate::model::Direction;
use crate::Result;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// How often, out of 1000 steps, each injected hazard fires.
#[derive(Debug, Clone, Copy)]
pub struct Hazards {
    /// Transient server/network failures.
    pub transient_per_mille: u32,
    /// Device crashes (journal handle dropped and reopened).
    pub crash_per_mille: u32,
    /// Clock jumps, which release events held back by the stability lag.
    pub time_jump_per_mille: u32,
}

impl Default for Hazards {
    fn default() -> Self {
        Hazards {
            transient_per_mille: 80,
            crash_per_mille: 20,
            time_jump_per_mille: 150,
        }
    }
}

/// What a run did — enough to assert on and to reproduce.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SimulationReport {
    /// The seed. **Print this on every failure.**
    pub seed: u64,
    /// How many scheduler steps ran.
    pub steps: usize,
    /// How many transient failures were injected.
    pub transients: usize,
    /// How many crashes were injected.
    pub crashes: usize,
    /// How many server calls were refused on a precondition.
    pub races: usize,
    /// How many rounds the settle phase needed.
    pub settle_rounds: usize,
    /// Whether the settle phase reached quiet before its bound.
    pub settled: bool,
}

/// Two devices, one cloud, one seed.
pub struct Simulation {
    /// The mock cloud.
    pub server: MockServer,
    /// The simulated devices.
    pub devices: Vec<Device>,
    /// The seeded choice source.
    pub rng: Rng,
    /// The simulation clock, in ticks.
    pub clock: i64,
    /// Which hazards fire and how often.
    pub hazards: Hazards,
    transients: usize,
    crashes: usize,
    races: usize,
}

impl Simulation {
    /// Build a run: two devices sharing one mapping id, each with its own journal file under
    /// `journal_dir` (a tempdir the caller owns).
    pub fn new(
        seed: u64,
        journal_dir: &Path,
        direction: Direction,
        knobs: Knobs,
        stability_lag: i64,
    ) -> Result<Self> {
        let mapping_id = "mapping-1";
        let mut devices = Vec::new();
        for name in ["device-a", "device-b"] {
            devices.push(Device::open(
                name,
                journal_dir.join(format!("{name}.db")),
                mapping_id,
                direction,
                knobs.clone(),
            )?);
        }
        Ok(Simulation {
            server: MockServer::new(stability_lag),
            devices,
            rng: Rng::new(seed),
            clock: 0,
            hazards: Hazards::default(),
            transients: 0,
            crashes: 0,
            races: 0,
        })
    }

    /// Run `steps` interleaved scheduler steps with hazards.
    pub fn run(&mut self, steps: usize) -> Result<()> {
        for _ in 0..steps {
            self.clock += 1;
            if self.rng.chance(self.hazards.time_jump_per_mille, 1000) {
                // Fast-forward past the stability lag, releasing whatever the feed held back.
                self.clock += self.server.stability_lag + 1;
            }
            let i = self.rng.below(self.devices.len());
            if self.rng.chance(self.hazards.crash_per_mille, 1000) {
                self.crashes += 1;
                self.devices[i].crash_and_restart()?;
                continue;
            }
            let inject = if self.rng.chance(self.hazards.transient_per_mille, 1000) {
                self.transients += 1;
                Some("injected transient failure")
            } else {
                None
            };
            let now = self.clock;
            match self.rng.below(4) {
                0 => self.devices[i].scan(now)?,
                1 => {
                    self.devices[i].poll_feed(&self.server, now)?;
                }
                _ => {
                    let outcome = self.devices[i].execute_one(&mut self.server, now, inject)?;
                    if outcome == StepOutcome::Raced {
                        self.races += 1;
                    }
                }
            }
        }
        Ok(())
    }

    /// Run without hazards until every device is quiet, or the bound is reached.
    ///
    /// A real fleet reaches quiet because the network eventually works; this models that, and
    /// nothing weaker. The bound is generous but finite, so a non-converging planner fails rather
    /// than hangs.
    pub fn settle(&mut self, bound: usize) -> Result<(usize, bool)> {
        for round in 0..bound {
            self.clock += self.server.stability_lag + 1;
            let mut quiet = true;
            let now = self.clock;
            for i in 0..self.devices.len() {
                self.devices[i].scan(now)?;
                self.devices[i].poll_feed(&self.server, now)?;
                // Drain this device's plan, one op at a time, so its own writes are visible to it.
                for _ in 0..256 {
                    let outcome = self.devices[i].execute_one(&mut self.server, now, None)?;
                    match outcome {
                        StepOutcome::Idle => break,
                        StepOutcome::Raced => {
                            self.races += 1;
                            quiet = false;
                        }
                        _ => quiet = false,
                    }
                    self.devices[i].scan(now)?;
                }
            }
            if quiet && round > 0 {
                return Ok((round + 1, true));
            }
        }
        Ok((bound, false))
    }

    /// Run, settle, and report.
    pub fn run_and_settle(&mut self, steps: usize, bound: usize) -> Result<SimulationReport> {
        self.run(steps)?;
        let (settle_rounds, settled) = self.settle(bound)?;
        Ok(SimulationReport {
            seed: self.rng.seed,
            steps,
            transients: self.transients,
            crashes: self.crashes,
            races: self.races,
            settle_rounds,
            settled,
        })
    }

    /// Every device's disk contents, path → content id.
    pub fn device_contents(&self) -> Vec<BTreeMap<String, Option<String>>> {
        self.devices
            .iter()
            .map(|d| {
                d.fs.iter()
                    .map(|(p, n)| (p.clone(), n.content.clone()))
                    .collect()
            })
            .collect()
    }

    /// Every content id that exists anywhere — on any disk, in any trash, or live in the cloud.
    pub fn reachable_contents(&self) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for d in &self.devices {
            for (_, n) in d.fs.iter() {
                if let Some(c) = &n.content {
                    out.insert(c.clone());
                }
            }
            for (_, c) in &d.fs.trash {
                if let Some(c) = c {
                    out.insert(c.clone());
                }
            }
        }
        for (_, c) in self.server.live_contents() {
            if let Some(c) = c {
                out.insert(c);
            }
        }
        out
    }

    /// Assert that every device and the cloud agree, ignoring paths that carry an open conflict on
    /// either device (those are `needs_conflict_resolution` by design).
    ///
    /// Returns the disagreements rather than panicking, so the caller can print the seed with them.
    pub fn disagreements(&self) -> Result<Vec<String>> {
        let mut exempt: BTreeSet<String> = BTreeSet::new();
        for d in &self.devices {
            for c in d.journal().open_conflicts(&d.mapping_id)? {
                exempt.insert(c.path_nfc);
            }
        }
        let cloud = self.server.live_contents();
        let mut out = Vec::new();
        let mut paths: BTreeSet<String> = cloud.keys().cloned().collect();
        for d in &self.devices {
            paths.extend(d.fs.paths().cloned());
        }
        for path in paths {
            if exempt.contains(&path) {
                continue;
            }
            let in_cloud = cloud.get(&path).cloned();
            for d in &self.devices {
                if d.suspended.is_some() {
                    continue; // a suspended mapping is deliberately not syncing
                }
                let on_disk = d.fs.iter().find(|(p, _)| **p == path).map(|(_, n)| n.content.clone());
                match (&in_cloud, &on_disk) {
                    (Some(a), Some(b)) if a == b => {}
                    (None, None) => {}
                    _ => out.push(format!(
                        "{path}: cloud={in_cloud:?} {}={on_disk:?}",
                        d.name
                    )),
                }
            }
        }
        Ok(out)
    }
}
