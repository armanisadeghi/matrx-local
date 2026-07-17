/**
 * Deterministic RNG (mulberry32) for analysis, imports, and stable tests.
 *
 * Math.random() would make "random sample of 50" unreproducible: the user
 * could never re-run the same 50 combinations. Actual submitted batches use
 * the Web-Crypto source below through createBatchSnapshot(), so a new attempt
 * never inherits this deterministic analysis stream.
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

export interface RandomSource {
  /** Uniform integer in [0, maxExclusive). */
  int: (maxExclusive: number) => number;
  /** A seed in the generation engine's accepted uint32 range. */
  seed: () => number;
}

function secureUint32(): number {
  const values = new Uint32Array(1);
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.getRandomValues !== undefined) {
    cryptoApi.getRandomValues(values);
    return values[0] as number;
  }
  // Tauri's renderer provides Web Crypto. This fallback keeps non-browser
  // tooling usable; it is never the production entropy source.
  return Math.floor(Math.random() * (MAX_SEED + 1));
}

/** OS-backed entropy used for every newly created batch snapshot. */
export const secureRandom: RandomSource = {
  int(maxExclusive: number): number {
    if (maxExclusive <= 0) return 0;
    if (maxExclusive >= MAX_SEED + 1) return secureUint32();
    // Rejection sampling avoids modulo bias for non-power-of-two ranges.
    const ceiling = MAX_SEED + 1 - ((MAX_SEED + 1) % maxExclusive);
    let value = secureUint32();
    while (value >= ceiling) value = secureUint32();
    return value % maxExclusive;
  },
  seed: secureUint32,
};

/** A random seed for "surprise me", outside any plan's deterministic stream. */
export function randomSeed(): number {
  return secureRandom.seed();
}

/** Fisher–Yates shuffle. It returns a new array and never mutates the source. */
export function shuffled<T>(
  values: readonly T[],
  random: RandomSource = secureRandom,
): T[] {
  const out = [...values];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = random.int(i + 1);
    const current = out[i] as T;
    out[i] = out[j] as T;
    out[j] = current;
  }
  return out;
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
