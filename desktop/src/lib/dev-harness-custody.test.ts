/**
 * MXL-D-091's browser-side guards.
 *
 * Two properties are load-bearing and neither is provable by reading the code:
 *
 * 1. **The harness bridge is refused in the live world**, and it is refused before anything that
 *    could succeed — a live-world daemon holds a real person's session and is never handed to a
 *    test. Proven against `decideDevSyncdConfig`, the one decision the dev server makes.
 * 2. **It is absent outside a dev server**, so no shipped bundle can ask for it.
 *
 * The first was proven failing-then-passing against a version whose world check was inverted.
 */
import { rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it, vi, afterEach } from "vitest";
import {
  DEV_SYNCD_CONFIG_PATH,
  HARNESS_MODE,
  bridgeEnabled,
  decideDevSyncdConfig,
  type DevSyncdInputs,
} from "@/lib/harness-bridge-contract";
import { resolveDevHome } from "../../vite-plugins/dev-syncd-bridge";
import { devHarnessCustodyConfig } from "./dev-harness-custody";

const DEV_HOME = "/private/tmp/matrx-harness-home";
const LIVE_HOME = path.join(os.homedir(), ".matrx");

function inputs(over: Partial<DevSyncdInputs> = {}): DevSyncdInputs {
  return {
    home: DEV_HOME,
    liveHome: LIVE_HOME,
    discovery: { version: 1, world: "dev", tcp_port: 22263 },
    tokenFile: "control-token-line\nread-token-line\n",
    enabled: true,
    ...over,
  };
}

describe("the dev-world harness bridge decision", () => {
  it("hands a dev-world daemon's endpoint and READ token to the page", () => {
    const decision = decideDevSyncdConfig(inputs());
    expect(decision).toEqual({
      ok: true,
      base_url: "http://127.0.0.1:22263",
      read_token: "read-token-line",
      world: "dev",
    });
    // Line 1 is the CONTROL token and authorises sign-out and shutdown. It never leaves disk.
    expect(JSON.stringify(decision)).not.toContain("control-token-line");
  });

  it("REFUSES a live-world daemon, naming the world it found", () => {
    const decision = decideDevSyncdConfig(
      inputs({ discovery: { version: 1, world: "live", tcp_port: 22161 } }),
    );
    expect(decision.ok).toBe(false);
    if (decision.ok) throw new Error("unreachable");
    expect(decision.status).toBe(403);
    expect(decision.code).toBe("not_the_dev_world");
    expect(decision.reason).toContain('"live"');
    expect(decision.remedy).toContain("--world dev");
    // The refusal must not leak the token it declined to hand over.
    expect(JSON.stringify(decision)).not.toContain("read-token-line");
  });

  it("REFUSES the installed app's own home even when MATRX_HOME_DIR points at it", () => {
    const decision = decideDevSyncdConfig(inputs({ home: LIVE_HOME }));
    expect(decision.ok).toBe(false);
    if (decision.ok) throw new Error("unreachable");
    expect(decision.status).toBe(403);
    expect(decision.code).toBe("live_home_refused");
    expect(decision.remedy).toContain("MATRX_HOME_DIR");
  });

  it("is ABSENT outside a dev server or a harness-mode preview", () => {
    const decision = decideDevSyncdConfig(inputs({ enabled: false }));
    expect(decision.ok).toBe(false);
    if (decision.ok) throw new Error("unreachable");
    expect(decision.status).toBe(404);
    expect(decision.code).toBe("not_a_harness_surface");
  });

  it("exists for a dev server and a harness preview, and for NOTHING a user installs", () => {
    expect(bridgeEnabled("serve", "development")).toBe(true);
    expect(bridgeEnabled("build", HARNESS_MODE)).toBe(true);
    expect(bridgeEnabled("serve", HARNESS_MODE)).toBe(true);
    // `pnpm build` — the ONE command Tauri's beforeBuildCommand and scripts/release.sh run.
    expect(bridgeEnabled("build", "production")).toBe(false);
    expect(bridgeEnabled("build", "development")).toBe(false);
  });

  it("says why, with a remedy, when no daemon is publishing", () => {
    for (const broken of [
      inputs({ discovery: null }),
      inputs({ discovery: { version: 1, world: "dev" } }),
      inputs({ tokenFile: "only-one-line\n" }),
      inputs({ tokenFile: null }),
    ]) {
      const decision = decideDevSyncdConfig(broken);
      expect(decision.ok).toBe(false);
      if (decision.ok) throw new Error("unreachable");
      expect(decision.remedy.length).toBeGreaterThan(20);
    }
  });

  it("resolves the DEV home, never the live one, in all three tiers", () => {
    const noRecord = "/private/tmp/matrx-harness-home-that-does-not-exist";
    // 1. MATRX_HOME_DIR wins.
    expect(resolveDevHome({ MATRX_HOME_DIR: DEV_HOME }, noRecord)).toBe(DEV_HOME);
    // 2. then the home `harness-session.mjs` recorded.
    const recorded = path.join(tmpdir(), `matrx-harness-record-${process.pid}`);
    writeFileSync(recorded, `${DEV_HOME}\n`);
    try {
      expect(resolveDevHome({}, recorded)).toBe(DEV_HOME);
    } finally {
      rmSync(recorded, { force: true });
    }
    // 3. then this world's default — which is never the installed app's home.
    expect(resolveDevHome({}, noRecord)).toBe(path.join(os.homedir(), ".matrx-dev"));
    expect(resolveDevHome({}, noRecord)).not.toBe(LIVE_HOME);
  });
});

describe("the page's own use of the bridge", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not even ask for it from a production bundle", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    await expect(devHarnessCustodyConfig(false)).resolves.toEqual({
      base_url: null,
      read_token: null,
      world: "unknown",
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("adopts a dev-world answer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        expect(url).toBe(DEV_SYNCD_CONFIG_PATH);
        return new Response(
          JSON.stringify({
            ok: true,
            base_url: "http://127.0.0.1:22263",
            read_token: "read-token-line",
            world: "dev",
          }),
          { status: 200 },
        );
      }),
    );
    await expect(devHarnessCustodyConfig(true)).resolves.toEqual({
      base_url: "http://127.0.0.1:22263",
      read_token: "read-token-line",
      world: "dev",
    });
  });

  it("never adopts an endpoint that did not say dev, and says so out loud", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              ok: true,
              base_url: "http://127.0.0.1:22161",
              read_token: "read-token-line",
              world: "live",
            }),
            { status: 200 },
          ),
      ),
    );
    await expect(devHarnessCustodyConfig(true)).resolves.toEqual({
      base_url: null,
      read_token: null,
      world: "unknown",
    });
    expect(error).toHaveBeenCalled();
    error.mockRestore();
  });

  it("logs the bridge's own reason and remedy on a refusal", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              ok: false,
              status: 503,
              code: "no_daemon",
              reason: "No sync daemon is publishing in /private/tmp/x.",
              remedy: "Start one with the harness script.",
            }),
            { status: 503 },
          ),
      ),
    );
    await devHarnessCustodyConfig(true);
    expect(error.mock.calls[0]?.[0]).toContain("No sync daemon is publishing");
    expect(error.mock.calls[0]?.[0]).toContain("Start one with the harness script.");
    error.mockRestore();
  });
});
