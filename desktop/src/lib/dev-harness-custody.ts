/**
 * The browser-mode side of the dev-world harness bridge (MXL-D-091).
 *
 * In the packaged app the webview learns the daemon's endpoint from Tauri
 * (`invoke("syncd_client_config")`). In a plain Chromium page — which is what the Playwright E2E
 * harness drives, and what `pnpm dev` in a browser is — there is no Tauri, so that call cannot
 * answer and the app has no session, no token, and therefore no verifiable screen at all.
 *
 * This module is the ONE fallback, and it is deliberately narrow:
 *
 * - It is behind the compiled-in literal `__MATRX_HARNESS_BRIDGE__`, which `vite.config.ts` sets
 *   from `bridgeEnabled(command, mode)` — the literal `false` in a production build, so the code
 *   below is unreachable in every shipped bundle. The dev server that would answer it is not
 *   installed there either: the plugin's `apply` is false for a production build.
 * - It only ever receives the **read** token, exactly as the packaged webview does. Signing this
 *   computer in or out remains control scope, on disk, out of JavaScript.
 * - The session it reads was installed by the harness script through the daemon's own dev-world
 *   control route. The product has no password field and does not regain one.
 *
 * Nothing fails silently: every refusal the dev server sends carries a reason and a remedy, and
 * both are logged verbatim rather than collapsed into "not signed in".
 */
import { DEV_SYNCD_CONFIG_PATH } from "@/lib/harness-bridge-contract";

/**
 * Compiled in by `vite.config.ts` as a literal `true` or `false` — never an expression that could
 * be true at run time. In a production bundle it is the literal `false`, so nothing below ever
 * runs there. (The endpoint string itself does survive minification, because it is a module-level
 * constant the bundler keeps; that is a string in a bundle, not a reachable path — and no
 * production server serves it, since the plugin is not installed in a production build.)
 */
declare const __MATRX_HARNESS_BRIDGE__: boolean;

/** Exactly what `syncd_client_config` returns. */
export interface HarnessClientConfig {
  base_url: string | null;
  read_token: string | null;
  world: string;
}

/** No endpoint. The app renders the daemon's honest `daemon_not_running` state. */
const UNAVAILABLE: HarnessClientConfig = Object.freeze({
  base_url: null,
  read_token: null,
  world: "unknown",
});

/** Whether this bundle is one the harness bridge exists for. A compiled-in literal. */
const HARNESS_BRIDGE_IN_THIS_BUNDLE = __MATRX_HARNESS_BRIDGE__;

/**
 * The dev-world daemon's endpoint and read token, or [`UNAVAILABLE`].
 *
 * `enabled` is a parameter rather than a direct `import.meta.env` read so the guard — "this is
 * absent from a production bundle" — is provable by a test rather than only by a build step.
 */
export async function devHarnessCustodyConfig(
  enabled: boolean = HARNESS_BRIDGE_IN_THIS_BUNDLE,
): Promise<HarnessClientConfig> {
  if (!enabled) return UNAVAILABLE;
  let response: Response;
  try {
    response = await fetch(DEV_SYNCD_CONFIG_PATH, {
      headers: { Accept: "application/json" },
    });
  } catch (cause) {
    console.error(
      "[dev-harness] The dev server did not answer the harness bridge, so this page has no " +
        "sync daemon and cannot sign in. Start the app with `pnpm dev` from desktop/. Cause:",
      cause,
    );
    return UNAVAILABLE;
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  const decision = body as
    | { ok?: unknown; base_url?: unknown; read_token?: unknown; world?: unknown; reason?: unknown; remedy?: unknown }
    | null;

  if (!response.ok || !decision || decision.ok !== true) {
    console.error(
      "[dev-harness] The harness bridge refused to hand this page a sync session: " +
        `${String(decision?.reason ?? `HTTP ${response.status}`)} ` +
        `Remedy: ${String(decision?.remedy ?? "start the harness daemon in the dev world.")}`,
    );
    return UNAVAILABLE;
  }

  // Belt and braces: the bridge already refuses anything but the dev world, and so does the
  // daemon. A page still never adopts an endpoint that did not say "dev".
  if (decision.world !== "dev") {
    console.error(
      `[dev-harness] The harness bridge answered for world ${JSON.stringify(decision.world)}; ` +
        "only the dev world is ever used from a browser page. Ignoring it.",
    );
    return UNAVAILABLE;
  }
  if (typeof decision.base_url !== "string" || typeof decision.read_token !== "string") {
    console.error(
      "[dev-harness] The harness bridge answered without an endpoint and a read token. " +
        "Restart the harness daemon; it mints both at every start.",
    );
    return UNAVAILABLE;
  }

  return {
    base_url: decision.base_url,
    read_token: decision.read_token,
    world: "dev",
  };
}
