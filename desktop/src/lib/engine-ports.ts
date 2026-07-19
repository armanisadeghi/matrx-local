/**
 * Engine port constants — single source of truth for the frontend.
 *
 * Live, dev, and smoke-test builds are separate worlds. Smoke builds receive
 * a run-specific port base from scripts/smoke.sh; production ignores that
 * override unless the explicit isolation marker is also compiled in.
 */

const LIVE_ENGINE_PORT_BASE = 22140;
const DEV_ENGINE_PORT_BASE = 22240;
const MIN_TEST_ENGINE_PORT_BASE = 23000;
const MAX_TEST_ENGINE_PORT_BASE = 65000;

export interface EnginePortEnvironment {
  dev: boolean;
  isolatedSmoke: boolean;
  smokePortBase?: string | undefined;
}

export function resolveEnginePortBase(env: EnginePortEnvironment): number {
  if (env.isolatedSmoke) {
    const parsed = Number(env.smokePortBase);
    if (
      !Number.isInteger(parsed) ||
      parsed < MIN_TEST_ENGINE_PORT_BASE ||
      parsed > MAX_TEST_ENGINE_PORT_BASE
    ) {
      throw new Error(
        `Isolated smoke build requires VITE_MATRX_TEST_ENGINE_PORT_BASE in ${MIN_TEST_ENGINE_PORT_BASE}–${MAX_TEST_ENGINE_PORT_BASE}`,
      );
    }
    return parsed;
  }
  return env.dev ? DEV_ENGINE_PORT_BASE : LIVE_ENGINE_PORT_BASE;
}

export const ENGINE_PORT_BASE = resolveEnginePortBase({
  dev: import.meta.env.DEV,
  isolatedSmoke: import.meta.env.VITE_MATRX_ISOLATED_SMOKE === "1",
  smokePortBase: import.meta.env.VITE_MATRX_TEST_ENGINE_PORT_BASE,
});
export const ENGINE_PORT_SCAN = 20;

export const ENGINE_PORT_RANGE_LABEL = `${ENGINE_PORT_BASE}–${
  ENGINE_PORT_BASE + ENGINE_PORT_SCAN - 1
}`;

export const ENGINE_DEFAULT_URL = `http://127.0.0.1:${ENGINE_PORT_BASE}`;

/** All ports in this build's scan range, in probe order. */
export function enginePortList(): number[] {
  return Array.from({ length: ENGINE_PORT_SCAN }, (_, i) => ENGINE_PORT_BASE + i);
}
