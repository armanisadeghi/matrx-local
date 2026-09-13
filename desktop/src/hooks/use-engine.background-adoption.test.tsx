/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const listeners: Array<(event: string, session: any) => void> = [];
  const session = { access_token: "access-a", refresh_token: "refresh-a", expires_in: 3600, user: { id: "actor-a" } };
  const engine = {
    setTokenProvider: vi.fn(), isHealthy: vi.fn(async () => true), discover: vi.fn(async () => "http://engine.test"),
    getPlatformContext: vi.fn(async () => ({})), listTools: vi.fn(async () => []), getVersion: vi.fn(async () => "test"),
    getSystemInfo: vi.fn(async () => ({})), getBrowserStatus: vi.fn(async () => ({})), connectWebSocket: vi.fn(async () => undefined),
    disconnect: vi.fn(), clearPythonToken: vi.fn(async () => undefined), syncTokenToPython: vi.fn(async () => undefined),
    configureCloudSync: vi.fn(async () => undefined), reconfigureCloudSync: vi.fn(async () => undefined), cloudHeartbeat: vi.fn(async () => undefined), prepareSessionTransition: vi.fn(async () => ({ status: "aligned", origin: "http://engine.test", generation: "g", credentialRevision: 0, subject: null })),
    get: vi.fn(async () => ({})), getInstanceInfo: vi.fn(async () => ({})), listInstances: vi.fn(async () => []), getHardware: vi.fn(async () => ({})),
    on: vi.fn(() => () => undefined),
  };
  const auth = {
    getSession: vi.fn(async () => ({ data: { session } })),
    onAuthStateChange: vi.fn((callback) => { listeners.push(callback); return { data: { subscription: { unsubscribe: vi.fn() } } }; }),
    refreshSession: vi.fn(), signOut: vi.fn(),
  };
  return { listeners, session, engine, auth, reconcile: vi.fn(async (subject: string | null) => subject === "actor-b" ? "state_corrupt" : "applied") };
});

vi.mock("@/lib/api", () => ({ engine: mocks.engine }));
vi.mock("@/lib/supabase", () => ({ default: { auth: mocks.auth } }));
vi.mock("@/lib/sidecar", () => ({
  ENGINE_STARTUP_TIMEOUT_SECONDS: 1, isTauri: () => false, startSidecar: vi.fn(), stopSidecar: vi.fn(), waitForOwnedEngine: vi.fn(),
  reconcileNativeVaultHostActor: mocks.reconcile, invalidateNativeVaultHostActor: vi.fn(async () => "applied"),
}));
vi.mock("@/lib/platformCtx", () => ({ initPlatformCtx: vi.fn() }));
vi.mock("@/lib/settings", () => ({ hydrateFromEngine: vi.fn(async () => undefined), syncAllSettings: vi.fn(async () => undefined) }));
vi.mock("@/hooks/use-window-leader", () => ({ useWindowLeader: () => true }));
vi.mock("@/hooks/use-client-log", () => ({ emitClientLog: vi.fn() }));
vi.mock("@/hooks/use-unified-log", () => ({ emitClientLog: vi.fn() }));

import { useEngine } from "./use-engine";

let root: Root;
let container: HTMLDivElement;
function Subject() { useEngine(); return null; }

beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  mocks.reconcile.mockClear();
  Object.values(mocks.engine).forEach((value) => {
    if (typeof value === "function" && "mockClear" in value) (value as any).mockClear();
  });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => { root.render(<Subject />); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

it("cancels scheduled cloud work before a failed actor fence can hand off credentials", async () => {
  expect(mocks.listeners).toHaveLength(1);
  mocks.engine.syncTokenToPython.mockClear();
  mocks.engine.configureCloudSync.mockClear();
  mocks.engine.cloudHeartbeat.mockClear();
  act(() => mocks.listeners[0]!("SIGNED_IN", { ...mocks.session, access_token: "access-b", user: { id: "actor-b" } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(mocks.engine.syncTokenToPython).not.toHaveBeenCalled();
  expect(mocks.engine.configureCloudSync).not.toHaveBeenCalled();
  expect(mocks.engine.cloudHeartbeat).not.toHaveBeenCalled();
});

it("restarts the real idle queue after accepted same-actor reconciliation", async () => {
  mocks.engine.syncTokenToPython.mockClear();
  mocks.engine.configureCloudSync.mockClear();
  mocks.engine.cloudHeartbeat.mockClear();
  act(() => mocks.listeners[0]!("TOKEN_REFRESHED", mocks.session));
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(mocks.engine.syncTokenToPython).toHaveBeenCalledWith("access-a", "actor-a", expect.any(Object), "refresh-a", 3600);
  expect(mocks.engine.configureCloudSync).toHaveBeenCalledWith("access-a", "actor-a", expect.any(Object));
  expect(mocks.engine.cloudHeartbeat).toHaveBeenCalled();
});
