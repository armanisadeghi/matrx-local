// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@/lib/dev-harness-custody", () => ({
  devHarnessCustodyConfig: vi.fn(async () => ({ base_url: null, read_token: null, world: "unknown" })),
}));

beforeEach(() => {
  vi.resetModules();
  invoke.mockReset();
  vi.unstubAllGlobals();
});

it("waits for native startup config and retries after an unavailable result", async () => {
  let resolveConfig!: (config: { base_url: string; read_token: string; world: string }) => void;
  invoke.mockImplementationOnce(
    () => new Promise((resolve) => { resolveConfig = resolve; }),
  );
  const fetchMock = vi.fn(async () => Response.json({
    signed_in: false, user_id: null, email: null, state: "signed_out", state_reason: null,
    since: null, next_attempt_at: null, cloud_state_write_pending: false,
  }));
  vi.stubGlobal("fetch", fetchMock);
  const custodian = await import("./custodian");
  const pending = custodian.getSession();
  await Promise.resolve();
  expect(fetchMock).not.toHaveBeenCalled();
  resolveConfig({ base_url: "http://daemon.test", read_token: "read", world: "dev" });
  expect((await pending).state).toBe("signed_out");

  custodian.resetCustodianConfig();
  invoke.mockRejectedValueOnce(new Error("not ready"));
  expect((await custodian.getSession()).state).toBe("daemon_not_running");
  invoke.mockResolvedValueOnce({ base_url: "http://daemon.test", read_token: "read", world: "dev" });
  expect((await custodian.getSession()).state).toBe("signed_out");
});
