/**
 * THE DEV-WORLD HARNESS BRIDGE (MXL-D-091).
 *
 * The **file reads and the two servers**. Every rule the bridge enforces lives in
 * `src/lib/harness-bridge-contract.ts`, which this file and the browser page share so the guard
 * exists exactly once; read that module for why the bridge exists and why it cannot reach the
 * live world.
 *
 * `apply` is false for a production build, so this plugin is not installed at all in the pipeline
 * every shipped artifact comes out of (`pnpm build`, via Tauri's `beforeBuildCommand` and
 * `scripts/release.sh`). A `harness`-mode preview (`pnpm build:harness && pnpm preview:harness`)
 * is the one non-dev-server surface it serves, so a browser harness can also run against a built
 * bundle.
 */
import { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Plugin } from "vite";

import {
  DEV_HOME_DIRNAME,
  DEV_SYNCD_CONFIG_PATH,
  LIVE_HOME_DIRNAME,
  bridgeEnabled,
  decideDevSyncdConfig,
  type DevSyncdDecision,
} from "../src/lib/harness-bridge-contract";

/**
 * Where the harness daemon's files are, for this process's environment.
 *
 * `MATRX_HOME_DIR` first, then the home `e2e/setup/harness-session.mjs` recorded in
 * `desktop/.harness-home` (so `pnpm dev` needs no environment plumbing), then this world's
 * default. Never `~/.matrx`.
 */
export function resolveDevHome(
  env: Record<string, string | undefined> = process.env,
  recordedHomeFile = path.resolve(process.cwd(), ".harness-home"),
): string {
  const override = env.MATRX_HOME_DIR;
  if (override && override.length > 0) return override;
  const recorded = readOrNull(recordedHomeFile)?.trim();
  if (recorded) return recorded;
  return path.join(os.homedir(), DEV_HOME_DIRNAME);
}

function readOrNull(file: string): string | null {
  try {
    return readFileSync(file, "utf8");
  } catch {
    return null;
  }
}

/** Read the daemon's files and decide, for one request. */
function answer(): DevSyncdDecision {
  const home = resolveDevHome();
  const discoveryText = readOrNull(path.join(home, "syncd.json"));
  let discovery: unknown = null;
  if (discoveryText) {
    try {
      discovery = JSON.parse(discoveryText);
    } catch {
      discovery = null;
    }
  }
  return decideDevSyncdConfig({
    home,
    liveHome: path.join(os.homedir(), LIVE_HOME_DIRNAME),
    discovery,
    tokenFile: readOrNull(path.join(home, "syncd.token")),
    enabled: true,
  });
}

type Middleware = (
  req: unknown,
  res: {
    setHeader: (name: string, value: string) => void;
    statusCode: number;
    end: (body: string) => void;
  },
) => void;

const handler: Middleware = (_req, res) => {
  const decision = answer();
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Cache-Control", "no-store");
  res.statusCode = decision.ok ? 200 : decision.status;
  res.end(JSON.stringify(decision));
};

/**
 * The Vite plugin. It exists on a dev server and on a `harness`-mode preview, and **nowhere
 * else** — `apply` returns false for the production build every shipped artifact is made from.
 */
export function devSyncdBridge(): Plugin {
  return {
    name: "matrx-dev-syncd-bridge",
    apply: (config, env) => bridgeEnabled(env.command, config.mode ?? env.mode),
    configureServer(server) {
      server.middlewares.use(DEV_SYNCD_CONFIG_PATH, handler as never);
    },
    configurePreviewServer(server) {
      server.middlewares.use(DEV_SYNCD_CONFIG_PATH, handler as never);
    },
  };
}
