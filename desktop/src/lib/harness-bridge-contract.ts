/**
 * THE DEV-WORLD HARNESS BRIDGE CONTRACT (MXL-D-091).
 *
 * The endpoint, the mode gate, and the ONE decision the bridge makes — with no Node imports, so
 * the browser page and the Vite plugin share exactly one copy of the guard rather than two that
 * could drift. The plugin (`desktop/vite-plugins/dev-syncd-bridge.ts`) does the file reads and
 * calls in here; `desktop/src/lib/dev-harness-custody.ts` is the page's consumer.
 *
 * ## Why the bridge exists
 *
 * The custody cutover made `matrx-syncd` the device's only session holder and removed the
 * email/password form from the product. That was right. What it also removed was the only door the
 * browser-mode E2E harness had: in a plain Chromium page there is no Tauri, so
 * `invoke("syncd_client_config")` — the ONE way the webview learns the daemon's loopback endpoint
 * and its read token — cannot answer. With no endpoint the app renders the sign-in screen forever,
 * so NO screen of Matrx Local could be verified by an agent any more. Every UI claim about this
 * app became unverifiable, which is a larger defect than the one the cutover fixed.
 *
 * ## What it hands over, and what it never does
 *
 * Only the daemon's loopback base URL and its **read** token — exactly what the packaged webview
 * already holds. The control token stays on disk, so a page still cannot sign this computer in or
 * out; the session itself is installed by `desktop/e2e/setup/harness-session.mjs` through the
 * daemon's own dev-world-only control route. The product has no password field and does not
 * regain one.
 *
 * ## Why it cannot reach the live world
 *
 * 1. **It is off in every shipped build.** [`bridgeEnabled`] is false for a production build,
 *    which is what Tauri's `beforeBuildCommand` and `scripts/release.sh` run (`pnpm build`). The
 *    plugin's `apply` reads it, so no production server serves the endpoint at all; and
 *    `vite.config.ts` compiles it into the bundle as the literal `__MATRX_HARNESS_BRIDGE__`, so
 *    the page's consumer is unreachable code there. It also selects the engine port band, so a
 *    harness build never scans the installed app's 22140–22159.
 * 2. **It refuses the live world's home.** `~/.matrx` is the packaged app's — Arman's — and is
 *    named and rejected even if `MATRX_HOME_DIR` points at it.
 * 3. **It refuses a live-world daemon.** `syncd.json` carries the daemon's own `world`; anything
 *    but `dev` is a 403 that names what it found.
 *
 * The daemon carries its own two guards on top: `POST /v1/harness/session` does not exist in the
 * live world, and `Custodian::install_harness_session` refuses there.
 */
/** The endpoint. One literal, read by the plugin and by the browser-side consumer. */
export const DEV_SYNCD_CONFIG_PATH = "/__matrx-dev/syncd-config";

/**
 * The ONE Vite mode, besides a dev server, in which the bridge exists.
 *
 * `pnpm build:harness` + `pnpm preview:harness` builds and serves a bundle the harness can sign
 * into. Every shipped build goes through `pnpm build` (mode `production`), which Tauri's
 * `beforeBuildCommand` and `scripts/release.sh` are the only callers of — so the bridge is absent
 * from the app a user installs, in the bundle as well as on the server.
 */
export const HARNESS_MODE = "harness";

/** Whether the bridge exists at all for this Vite invocation. The outermost of its three layers. */
export function bridgeEnabled(command: string, mode: string): boolean {
  return command === "serve" || mode === HARNESS_MODE;
}

/** What the dev world's home is called, when `MATRX_HOME_DIR` does not say (Hard Rule 9). */
export const DEV_HOME_DIRNAME = ".matrx-dev";

/** The packaged app's home. Never this bridge's, in any circumstance. */
export const LIVE_HOME_DIRNAME = ".matrx";

/** Everything the decision below is allowed to look at. No I/O, so it is testable. */
export interface DevSyncdInputs {
  /** The resolved home directory the daemon's files were read from. */
  readonly home: string;
  /** The live world's home on this machine, for the "never Arman's app" comparison. */
  readonly liveHome: string;
  /** `syncd.json`'s parsed contents, or `null` when it is absent or unparseable. */
  readonly discovery: unknown;
  /** `syncd.token`'s raw contents, or `null` when it is absent. */
  readonly tokenFile: string | null;
  /**
   * Whether the bridge exists for this Vite invocation — a dev server, or a `harness`-mode
   * preview. A production build must never reach here.
   */
  readonly enabled: boolean;
}

/** Granted: exactly the shape `syncd_client_config` returns. */
export interface DevSyncdGranted {
  readonly ok: true;
  readonly base_url: string;
  readonly read_token: string;
  readonly world: "dev";
}

/** Refused, with the HTTP status, the reason, and what to do about it (law 4). */
export interface DevSyncdRefused {
  readonly ok: false;
  readonly status: number;
  readonly code: string;
  readonly reason: string;
  readonly remedy: string;
}

export type DevSyncdDecision = DevSyncdGranted | DevSyncdRefused;

/** Compare two directory paths without pulling node:path into the browser bundle. */
function normalizeDir(dir: string): string {
  return dir.replace(/\/+$/, "");
}

function refuse(
  status: number,
  code: string,
  reason: string,
  remedy: string,
): DevSyncdRefused {
  return { ok: false, status, code, reason, remedy };
}

/**
 * THE GUARD. Decide whether the dev-world harness bridge may hand out this daemon's read token.
 *
 * Order matters: the two "this must never be reachable" checks come before anything that could
 * succeed, so a broken file or an odd token can never be the reason a live-world request was
 * answered.
 */
export function decideDevSyncdConfig(inputs: DevSyncdInputs): DevSyncdDecision {
  if (!inputs.enabled) {
    return refuse(
      404,
      "not_a_harness_surface",
      "The harness bridge only exists on the Vite dev server and on a `harness`-mode preview.",
      "Run the harness against `pnpm dev`, or `pnpm build:harness && pnpm preview:harness`; a production build has no harness sign-in by design.",
    );
  }
  if (normalizeDir(inputs.home) === normalizeDir(inputs.liveHome)) {
    return refuse(
      403,
      "live_home_refused",
      `The harness bridge was pointed at ${inputs.liveHome}, which is the installed app's home.`,
      "Unset MATRX_HOME_DIR or point it at a dev-world home; the installed app is never a test target.",
    );
  }

  const discovery = inputs.discovery as
    | { world?: unknown; tcp_port?: unknown }
    | null
    | undefined;
  if (!discovery || typeof discovery !== "object") {
    return refuse(
      503,
      "no_daemon",
      `No sync daemon is publishing in ${inputs.home}.`,
      "Start one with `node desktop/e2e/setup/harness-session.mjs`, which starts the dev daemon and signs it in.",
    );
  }
  if (discovery.world !== "dev") {
    return refuse(
      403,
      "not_the_dev_world",
      `The daemon publishing in ${inputs.home} says its world is ${JSON.stringify(discovery.world)}, not "dev".`,
      "Start the harness daemon with `--world dev`; a live-world daemon holds a real person's session and is never handed to a test.",
    );
  }
  if (typeof discovery.tcp_port !== "number") {
    return refuse(
      503,
      "no_listener",
      `The dev daemon in ${inputs.home} has no loopback listener up yet.`,
      "Wait a moment and reload; the daemon publishes its port once the listener binds.",
    );
  }

  const readToken = (inputs.tokenFile ?? "").split("\n")[1]?.trim();
  if (!readToken) {
    return refuse(
      503,
      "no_read_token",
      `${inputs.home}/syncd.token does not hold two tokens.`,
      "Restart the harness daemon; it mints both scoped tokens at every start.",
    );
  }

  return {
    ok: true,
    base_url: `http://127.0.0.1:${discovery.tcp_port}`,
    read_token: readToken,
    world: "dev",
  };
}

