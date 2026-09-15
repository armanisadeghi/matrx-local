import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  configureCloudSync: vi.fn(),
  cloudHeartbeat: vi.fn(),
  getSession: vi.fn(),
  generation: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ engine: mocks }));
vi.mock("@/lib/custodian", () => ({ getAuthedSession: mocks.getSession }));
vi.mock("@/lib/native-vault-auth", () => ({
  isNativeVaultHostSessionAdopted: (subject: string) => mocks.generation(subject) !== null,
  nativeVaultAdoptedHostGeneration: mocks.generation,
  nativeVaultEngineTransitionContext: (subject: string) => {
    const generation = mocks.generation(subject);
    return generation === null ? null : { revision: generation, nextSubject: subject, isCurrent: () => mocks.generation(subject) === generation };
  },
}));

import { cloudHeartbeat, cloudSettingsSync } from "./cloud-sync";

beforeEach(() => {
  mocks.configureCloudSync.mockReset();
  mocks.cloudHeartbeat.mockReset();
  mocks.getSession.mockReset();
  mocks.generation.mockReset();
});

// The "does not deliver a token read for A after B fences the adopted generation" case was
// deleted with the thing it guarded: `pushTokenToPython` pushed this window's JWT into the Python
// engine, and under FS-C5b there is no such push — the engine asks the sync daemon for its own
// token. The fence check it exercised is still proven below for the two cloud tasks, which do
// still read a session before doing I/O.
it("applies the same generation check to configure and heartbeat after their session read", async () => {
  mocks.getSession.mockResolvedValue({ access_token: "token-a", user: { id: "actor-a" } });
  mocks.generation.mockReturnValueOnce(9).mockReturnValue(null);
  await cloudSettingsSync.fn();
  mocks.generation.mockReset().mockReturnValueOnce(10).mockReturnValue(null);
  await cloudHeartbeat.fn();
  expect(mocks.configureCloudSync).not.toHaveBeenCalled();
  expect(mocks.cloudHeartbeat).not.toHaveBeenCalled();
});
