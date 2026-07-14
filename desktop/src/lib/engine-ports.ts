/**
 * Engine port constants — single source of truth for the frontend.
 *
 * Dev/live isolation (MXL-D-043): dev builds (`pnpm dev`, `pnpm tauri:dev` —
 * `import.meta.env.DEV`) look for the engine in the DEV port range
 * 22240-22259, matching run.py's dev/live isolation guard for source-run
 * engines and Rust's `debug_assertions` gate. The shipped bundle uses the
 * live range 22140-22159. A dev frontend must never adopt the installed
 * app's engine, and vice versa.
 */

export const ENGINE_PORT_BASE = import.meta.env.DEV ? 22240 : 22140;
export const ENGINE_PORT_SCAN = 20;

export const ENGINE_PORT_RANGE_LABEL = `${ENGINE_PORT_BASE}–${
  ENGINE_PORT_BASE + ENGINE_PORT_SCAN - 1
}`;

export const ENGINE_DEFAULT_URL = `http://127.0.0.1:${ENGINE_PORT_BASE}`;

/** All ports in this build's scan range, in probe order. */
export function enginePortList(): number[] {
  return Array.from({ length: ENGINE_PORT_SCAN }, (_, i) => ENGINE_PORT_BASE + i);
}
