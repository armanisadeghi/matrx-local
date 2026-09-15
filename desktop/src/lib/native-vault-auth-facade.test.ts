import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listener: undefined as undefined | ((snapshot: any, rotated: boolean) => void),
  getSession: vi.fn(), reconcileResult: "unsupported_platform",
  reconcile: vi.fn(async () => mocks.reconcileResult),
}));
const signedOut = { signed_in: false, user_id: null, email: null, state: "signed_out", state_reason: null, since: null, next_attempt_at: null, cloud_state_write_pending: false };
vi.mock("@/lib/custodian", () => ({
  currentSession: () => signedOut, getSession: mocks.getSession,
  subscribeSession: vi.fn((listener) => { mocks.listener = listener; return () => undefined; }),
  sessionToMatrx: (snapshot: any) => snapshot.signed_in ? { user: { id: snapshot.user_id, email: snapshot.email } } : null,
  getAuthedSession: vi.fn(), getToken: vi.fn(),
}));
vi.mock("@/lib/sidecar", () => ({ invalidateNativeVaultHostActor: async () => "unsupported_platform", reconcileNativeVaultHostActor: mocks.reconcile }));
vi.mock("@/lib/api", () => ({ engine: { prepareSessionTransition: async () => ({ status: "unavailable" }) } }));
afterEach(() => { vi.useRealTimers(); vi.resetModules(); mocks.listener = undefined; mocks.getSession.mockReset(); mocks.reconcile.mockClear(); mocks.reconcileResult = "unsupported_platform"; });

it("accepts anonymous daemon lifecycle state and isolates subscribers", async () => {
  vi.useFakeTimers(); mocks.getSession.mockResolvedValue(signedOut);
  const auth = await import("./native-vault-auth"); const received = vi.fn();
  auth.subscribeNativeVaultHostEvents(() => { throw new Error("isolated"); }); auth.subscribeNativeVaultHostEvents(received);
  await Promise.resolve();
  expect(received).toHaveBeenCalledOnce(); await vi.advanceTimersByTimeAsync(0);
  await expect(received.mock.calls[0]?.[0].completion).resolves.toEqual({ accepted: true });
});

it("retries failed current-account reconciliation by publishing a newer envelope", async () => {
  vi.useFakeTimers(); const snapshot = { ...signedOut, signed_in: true, user_id: "actor-a", email: "a@test" };
  mocks.getSession.mockResolvedValue(snapshot); mocks.reconcileResult = "state_corrupt";
  const auth = await import("./native-vault-auth"); const received = vi.fn(); auth.subscribeNativeVaultHostEvents(received);
  await Promise.resolve(); await vi.advanceTimersByTimeAsync(0);
  await expect(received.mock.calls[0]?.[0].completion).rejects.toThrow("could not reconcile");
  mocks.reconcileResult = "applied"; const retry = auth.retryNativeVaultAccountCleanup("actor-a"); await vi.advanceTimersByTimeAsync(0);
  await expect(retry).resolves.toBe(true); expect(received).toHaveBeenCalledTimes(2);
});

it("refuses retry when the daemon current account changed", async () => {
  mocks.getSession.mockResolvedValue({ ...signedOut, signed_in: true, user_id: "actor-b" });
  const auth = await import("./native-vault-auth");
  await expect(auth.retryNativeVaultAccountCleanup("actor-a")).resolves.toBe(false);
  expect(mocks.reconcile).not.toHaveBeenCalled();
});
