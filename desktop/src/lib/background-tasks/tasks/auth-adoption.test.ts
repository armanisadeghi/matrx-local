import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  syncTokenToPython: vi.fn(),
  configureCloudSync: vi.fn(),
  cloudHeartbeat: vi.fn(),
  getSession: vi.fn(),
  generation: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ engine: mocks }));
vi.mock("@/lib/supabase", () => ({ default: { auth: { getSession: mocks.getSession } } }));
vi.mock("@/lib/native-vault-auth", () => ({
  isNativeVaultHostSessionAdopted: (subject: string) => mocks.generation(subject) !== null,
  nativeVaultAdoptedHostGeneration: mocks.generation,
  nativeVaultEngineTransitionContext: (subject: string) => {
    const generation = mocks.generation(subject);
    return generation === null ? null : { revision: generation, nextSubject: subject, isCurrent: () => mocks.generation(subject) === generation };
  },
}));

import { pushTokenToPython } from "./token-sync";
import { cloudHeartbeat, cloudSettingsSync } from "./cloud-sync";

beforeEach(() => {
  mocks.syncTokenToPython.mockReset();
  mocks.configureCloudSync.mockReset();
  mocks.cloudHeartbeat.mockReset();
  mocks.getSession.mockReset();
  mocks.generation.mockReset();
});

it("does not deliver a token read for A after B fences the adopted generation", async () => {
  let release!: (value: unknown) => void;
  mocks.getSession.mockReturnValue(new Promise((resolve) => { release = resolve; }));
  mocks.generation.mockReturnValueOnce(7).mockReturnValue(null);
  const pending = pushTokenToPython.fn();
  release({ data: { session: { access_token: "token-a", refresh_token: "refresh-a", expires_in: 3600, user: { id: "actor-a" } } } });
  await pending;
  expect(mocks.syncTokenToPython).not.toHaveBeenCalled();
});

it("applies the same generation check to configure and heartbeat after their session read", async () => {
  mocks.getSession.mockResolvedValue({ data: { session: { access_token: "token-a", user: { id: "actor-a" } } } });
  mocks.generation.mockReturnValueOnce(9).mockReturnValue(null);
  await cloudSettingsSync.fn();
  mocks.generation.mockReset().mockReturnValueOnce(10).mockReturnValue(null);
  await cloudHeartbeat.fn();
  expect(mocks.configureCloudSync).not.toHaveBeenCalled();
  expect(mocks.cloudHeartbeat).not.toHaveBeenCalled();
});
