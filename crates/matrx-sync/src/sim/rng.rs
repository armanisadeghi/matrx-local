//! A deterministic pseudo-random generator.
//!
//! Hand-rolled rather than pulled from a crate for one reason: the harness must produce the
//! **same** sequence forever, on every platform and every toolchain, so a seed printed by a
//! failure five months from now still replays the failure. A dependency's algorithm can change
//! between versions; this one cannot.
//!
//! It is `xorshift64*` — not cryptographic, and not trying to be.

/// A seeded, reproducible source of choices.
#[derive(Debug, Clone)]
pub struct Rng {
    state: u64,
    /// The seed this generator started from. Printed on every failure.
    pub seed: u64,
}

impl Rng {
    /// Start from `seed`. A zero seed is mapped to a non-zero one, since xorshift is stuck at 0.
    pub fn new(seed: u64) -> Self {
        Rng {
            state: if seed == 0 { 0x9E37_79B9_7F4A_7C15 } else { seed },
            seed,
        }
    }

    /// The next 64 bits.
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// A number in `0..n`. `n == 0` yields 0.
    pub fn below(&mut self, n: usize) -> usize {
        if n == 0 {
            return 0;
        }
        (self.next_u64() % n as u64) as usize
    }

    /// True with probability `numerator / denominator`.
    pub fn chance(&mut self, numerator: u32, denominator: u32) -> bool {
        if denominator == 0 {
            return false;
        }
        (self.next_u64() % denominator as u64) < numerator as u64
    }

    /// Pick one element, or `None` from an empty slice.
    pub fn pick<'a, T>(&mut self, items: &'a [T]) -> Option<&'a T> {
        if items.is_empty() {
            None
        } else {
            let i = self.below(items.len());
            items.get(i)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_same_seed_gives_the_same_sequence() {
        let a: Vec<u64> = (0..16).map(|_| Rng::new(42).next_u64()).collect();
        let mut r = Rng::new(42);
        let b: Vec<u64> = (0..16).map(|_| r.next_u64()).collect();
        assert_eq!(a[0], b[0]);
        let mut c = Rng::new(42);
        let d: Vec<u64> = (0..16).map(|_| c.next_u64()).collect();
        assert_eq!(b, d);
    }

    #[test]
    fn different_seeds_diverge() {
        assert_ne!(Rng::new(1).next_u64(), Rng::new(2).next_u64());
    }
}
