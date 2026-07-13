/**
 * Deterministic RNG (mulberry32) — a seeded plan replays identically.
 *
 * Math.random() would make "random sample of 50" unreproducible: the user
 * could never re-run the same 50 combinations, and a resumed/duplicated batch
 * would silently draw a different sample. Every random choice in the planner
 * goes through here.
 */
export class Rng {
  private state: number;

  constructor(seed: number) {
    // Guard against 0 / NaN / negatives collapsing the generator.
    this.state = (Math.trunc(seed) || 0x9e3779b9) >>> 0;
  }

  /** Uniform float in [0, 1). */
  next(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0;
    let t = this.state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** Uniform integer in [0, maxExclusive). */
  int(maxExclusive: number): number {
    if (maxExclusive <= 0) return 0;
    return Math.floor(this.next() * maxExclusive);
  }

  /** A seed in the engine's accepted range (uint32). */
  seed(): number {
    return this.int(MAX_SEED + 1);
  }
}

/** Largest seed the generation engine accepts (2^32 − 1). */
export const MAX_SEED = 4294967295;

/** A random seed for "surprise me", outside any plan's deterministic stream. */
export function randomSeed(): number {
  return Math.floor(Math.random() * (MAX_SEED + 1));
}

/**
 * `count` distinct integers from [0, total), ascending.
 *
 * Uses rejection sampling when the sample is sparse relative to the population
 * (the common case: 50 of 10,000) and a partial Fisher–Yates otherwise, so
 * neither a huge population nor a near-total sample degenerates.
 */
export function sampleIndices(
  total: number,
  count: number,
  rng: Rng,
): number[] {
  if (count >= total) return Array.from({ length: total }, (_, i) => i);
  if (count <= 0) return [];

  // Sparse: reject duplicates. Expected draws stay near `count` while
  // count < total/2, and the set lookup keeps it cheap.
  if (count < total / 2) {
    const picked = new Set<number>();
    while (picked.size < count) picked.add(rng.int(total));
    return [...picked].sort((a, b) => a - b);
  }

  // Dense: partial shuffle of an index array — bounded work, no rejection.
  const pool = Array.from({ length: total }, (_, i) => i);
  for (let i = 0; i < count; i += 1) {
    const j = i + rng.int(total - i);
    const a = pool[i] as number;
    const b = pool[j] as number;
    pool[i] = b;
    pool[j] = a;
  }
  return pool.slice(0, count).sort((a, b) => a - b);
}
