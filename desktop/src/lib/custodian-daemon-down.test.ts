// @vitest-environment jsdom
/** cb53a722 — the daemon-down snapshot must carry the REAL reason and a remedy, and Start sync
 *  must be a control that reports what happened. Proven failing before the fix: `DAEMON_DOWN` was
 *  a frozen constant whose reason said "Choose Start sync to start it" and no `syncd_start`
 *  command existed for anything to call. */
import { beforeEach, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@/lib/dev-harness-custody", () => ({
  devHarnessCustodyConfig: vi.fn(async () => ({ base_url: null, read_token: null, world: "unknown" })),
}));

const CANNOT_EXECUTE = {
  running: false,
  code: "helper_cannot_execute",
  state_reason:
    "This build's sync helper cannot start on this computer (bundled matrx-syncd --version was " +
    "killed by signal 9), so this computer cannot sign in or sync.",
  remedy: "Update AI Matrx to the latest version. If this keeps happening after updating, report it from Settings → Support.",
  detail: "bundled matrx-syncd --version was killed by signal 9",
};

beforeEach(() => {
  vi.resetModules();
  invoke.mockReset();
  vi.unstubAllGlobals();
});

it("replaces the generic daemon-down reason with the host's real one", async () => {
  invoke.mockImplementation(async (command: string) => {
    if (command === "syncd_client_config") throw new Error(CANNOT_EXECUTE.state_reason);
    if (command === "syncd_daemon_state") return CANNOT_EXECUTE;
    throw new Error(`unexpected command ${command}`);
  });
  const custodian = await import("./custodian");

  // The first answer may still be the generic one — the host is being asked as it renders.
  const first = await custodian.getSession();
  expect(first.state).toBe("daemon_not_running");
  expect(await custodian.refreshDaemonDown()).toMatchObject({
    state: "daemon_not_running",
    state_reason: CANNOT_EXECUTE.state_reason,
    remedy: CANNOT_EXECUTE.remedy,
  });
  const second = await custodian.getSession();
  expect(second.state_reason).toContain("killed by signal 9");
  expect(second.remedy).toContain("Update AI Matrx");
});

it("keeps an honest reason when the host itself cannot be asked", async () => {
  invoke.mockRejectedValue(new Error("no tauri here"));
  const custodian = await import("./custodian");
  const snapshot = await custodian.getSession();
  await custodian.refreshDaemonDown();
  expect(snapshot.state).toBe("daemon_not_running");
  expect(custodian.DAEMON_DOWN.state_reason).toContain("is not running on this computer");
  expect(custodian.DAEMON_DOWN.remedy).toBe("Choose Start sync to start it.");
});

it("Start sync that fails again reports the NEW reason, never silence", async () => {
  const NEVER_READY = {
    running: false,
    code: "never_became_ready",
    state_reason: "AI Matrx Sync started but never became ready (the new daemon did not report the expected version in time), so this computer cannot sign in or sync.",
    remedy: "Choose Start sync to try again. If it keeps failing, restart your computer and report it from Settings → Support.",
    detail: "the new daemon did not report the expected version in time",
  };
  invoke.mockImplementation(async (command: string) => {
    if (command === "syncd_start") return NEVER_READY;
    if (command === "syncd_daemon_state") return CANNOT_EXECUTE;
    throw new Error("no daemon");
  });
  const custodian = await import("./custodian");
  const after = await custodian.startSync();
  expect(after.state).toBe("daemon_not_running");
  expect(after.state_reason).toContain("never became ready");
  expect(after.remedy).toContain("restart your computer");
  expect(custodian.currentSession().state_reason).toContain("never became ready");
});

it("a Start sync the host cannot even hear still answers in sentences", async () => {
  invoke.mockRejectedValue(new TypeError("Cannot read properties of undefined (reading 'invoke')"));
  const custodian = await import("./custodian");
  const after = await custodian.startSync();
  expect(after.state_reason).toContain("could not ask this computer to start sync");
  // The detail is kept, but never alone: a bare TypeError on screen is the same defect again.
  expect(after.state_reason).toContain("TypeError");
  expect(after.remedy).toContain("Quit AI Matrx and open it again");
});

it("Start sync that works reads the daemon's own session", async () => {
  const SIGNED_IN = {
    signed_in: true, user_id: "u1", email: "admin@admin.com", state: "signed_in",
    state_reason: null, since: null, next_attempt_at: null, cloud_state_write_pending: false,
  };
  invoke.mockImplementation(async (command: string) => {
    if (command === "syncd_start") return { running: true, code: "running", state_reason: "", remedy: null, detail: "" };
    if (command === "syncd_client_config") return { base_url: "http://daemon.test", read_token: "read", world: "dev" };
    throw new Error(`unexpected command ${command}`);
  });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(SIGNED_IN)));
  const custodian = await import("./custodian");
  expect(await custodian.startSync()).toMatchObject({ signed_in: true, state: "signed_in" });
});
