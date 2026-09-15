/**
 * THE AUTH-READINESS RULE — "no token" is an answer the session daemon gives,
 * never a race this app loses at mount.
 *
 * WHAT BREAKS THESE TESTS: restoring `if (!observed) return null;` in
 * `resolveNativeVaultEngineAccessToken` (i.e. concluding "signed out" from one
 * null read while the daemon's own snapshot still reports a signed-in user),
 * or deleting the `waitForCustodySettlement()` gate.
 *
 * WHY (measured 2026-09-15 on installed app 1.4.115,
 * common-docs/projects/coding-agent-bridge/verify-2026-09-15-matrx-local.md):
 * this one provider feeds every authenticated engine call in the app. It
 * answered null during app start, so pages that fetch at mount sent
 * unauthenticated requests, collected 401s, and rendered an error on a Mac
 * that was signed in the whole time.
 */

import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  snapshot: {
    signed_in: true,
    user_id: "actor-a",
    email: "a@test",
    state: "signed_in",
    state_reason: null,
    since: null,
    next_attempt_at: null,
    cloud_state_write_pending: false,
  } as Record<string, unknown>,
  // How many times getAuthedSession answers null before the grant lands —
  // the real startup gap between the daemon's snapshot and its token.
  nullReads: 0,
  reads: 0,
}));

vi.mock("@/lib/custodian", () => ({
  currentSession: () => mocks.snapshot,
  getSession: vi.fn(async () => mocks.snapshot),
  subscribeSession: vi.fn(() => () => undefined),
  sessionToMatrx: (s: any) => (s.signed_in ? { user: { id: s.user_id, email: s.email } } : null),
  getAuthedSession: vi.fn(async () => {
    mocks.reads += 1;
    if (!mocks.snapshot.signed_in) return null;
    if (mocks.reads <= mocks.nullReads) return null;
    return { user: { id: "actor-a" } };
  }),
  getToken: vi.fn(async () => (mocks.snapshot.signed_in ? "daemon-token" : null)),
}));
vi.mock("@/lib/sidecar", () => ({
  invalidateNativeVaultHostActor: async () => "unsupported_platform",
  reconcileNativeVaultHostActor: async () => "unsupported_platform",
}));
vi.mock("@/lib/api", () => ({
  engine: {
    prepareSessionTransition: async () => ({
      status: "aligned",
      origin: "http://engine",
      generation: "g",
      credentialRevision: 0,
      subject: "actor-a",
    }),
  },
}));

afterEach(() => {
  vi.resetModules();
  mocks.reads = 0;
  mocks.nullReads = 0;
  mocks.snapshot = { ...mocks.snapshot, signed_in: true, user_id: "actor-a", state: "signed_in" };
});

it("releases the daemon token even though the first session read was empty", async () => {
  mocks.nullReads = 1;
  const auth = await import("./native-vault-auth");
  auth.subscribeNativeVaultHostEvents(() => undefined);
  await new Promise((r) => setTimeout(r, 0));
  await expect(auth.resolveNativeVaultEngineAccessToken()).resolves.toBe(
    "daemon-token",
  );
});

it("still answers null when the daemon itself says there is no session", async () => {
  // A different expected value for a different real state, so no constant
  // return can satisfy both cases.
  mocks.snapshot = { ...mocks.snapshot, signed_in: false, user_id: null, state: "signed_out" };
  const auth = await import("./native-vault-auth");
  auth.subscribeNativeVaultHostEvents(() => undefined);
  await new Promise((r) => setTimeout(r, 0));
  await expect(auth.resolveNativeVaultEngineAccessToken()).resolves.toBeNull();
});

it("waits for the daemon's first answer before any consumer is told anything", async () => {
  const auth = await import("./native-vault-auth");
  // No subscription started by a consumer yet: the gate must start it itself
  // and resolve once the first envelope has been delivered and adopted.
  await expect(auth.waitForCustodySettlement(2_000)).resolves.toBe(true);
});

it("says the grant never arrived instead of claiming you are signed out", async () => {
  // Third distinct outcome: the daemon insists it is signed in but never hands
  // over a grant. That must be named, never dressed as a null token (which
  // would send the unauthenticated request all over again).
  mocks.nullReads = Number.MAX_SAFE_INTEGER;
  const auth = await import("./native-vault-auth");
  auth.subscribeNativeVaultHostEvents(() => undefined);
  await new Promise((r) => setTimeout(r, 0));
  await expect(auth.resolveNativeVaultEngineAccessToken()).rejects.toThrow(
    "has not handed over a session yet",
  );
}, 15_000);
