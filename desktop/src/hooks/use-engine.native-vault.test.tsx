/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const listeners: Array<(event: string, session: any) => void> = [];
  const engine = {
    engineUrl: "http://engine.test",
    setTokenProvider: vi.fn(), isHealthy: vi.fn(async () => true), discover: vi.fn(async () => "http://engine.test"),
    getPlatformContext: vi.fn(async () => ({})), listTools: vi.fn(async () => []), getVersion: vi.fn(async () => "test"),
    getSystemInfo: vi.fn(async () => ({})), getBrowserStatus: vi.fn(async () => ({})), connectWebSocket: vi.fn(async () => undefined),
    disconnect: vi.fn(), clearPythonToken: vi.fn(async () => undefined), syncTokenToPython: vi.fn(async () => undefined),
    configureCloudSync: vi.fn(async () => undefined), reconfigureCloudSync: vi.fn(async () => undefined), cloudHeartbeat: vi.fn(async () => undefined),
    on: vi.fn(() => () => undefined),
  };
  const auth = {
    getSession: vi.fn(async () => ({ data: { session: { access_token: "a", user: { id: "actor-a" } } } })),
    onAuthStateChange: vi.fn((callback) => { listeners.push(callback); return { data: { subscription: { unsubscribe: vi.fn() } } }; }),
    refreshSession: vi.fn(), signOut: vi.fn(),
  };
  return { listeners, engine, auth, reconcile: vi.fn(async (subject: string | null) => subject === "actor-b" ? "state_corrupt" : "applied") };
});

vi.mock("@/lib/api", () => ({ engine: mocks.engine }));
vi.mock("@/lib/supabase", () => ({ default: { auth: mocks.auth } }));
vi.mock("@/lib/sidecar", () => ({
  ENGINE_STARTUP_TIMEOUT_SECONDS: 1, isTauri: () => false, startSidecar: vi.fn(), stopSidecar: vi.fn(), waitForOwnedEngine: vi.fn(),
  reconcileNativeVaultHostActor: mocks.reconcile, invalidateNativeVaultHostActor: vi.fn(async () => "applied"),
}));
vi.mock("@/lib/platformCtx", () => ({ initPlatformCtx: vi.fn() }));
vi.mock("@/lib/background-tasks", () => ({ startBackgroundTasks: vi.fn(), stopBackgroundTasks: vi.fn() }));
vi.mock("@/hooks/use-window-leader", () => ({ useWindowLeader: () => true }));
vi.mock("@/hooks/use-client-log", () => ({ emitClientLog: vi.fn() }));

import { useEngine } from "./use-engine";

let root: Root;
let container: HTMLDivElement;

function Subject() { useEngine(); return null; }

beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listeners.length = 0;
  Object.values(mocks.engine).forEach((value) => { if (typeof value === "function" && "mockClear" in value) (value as any).mockClear(); });
  mocks.reconcile.mockClear();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => { root.render(<Subject />); await Promise.resolve(); await Promise.resolve(); });
});

afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("disconnects and clears the existing engine session immediately and never adopts a failed new actor", async () => {
  expect(mocks.listeners).toHaveLength(1);
  mocks.engine.disconnect.mockClear();
  mocks.engine.clearPythonToken.mockClear();
  mocks.engine.connectWebSocket.mockClear();
  mocks.engine.syncTokenToPython.mockClear();

  act(() => mocks.listeners[0]!("SIGNED_IN", { access_token: "b", user: { id: "actor-b" } }));
  expect(mocks.engine.disconnect).toHaveBeenCalledOnce();
  expect(mocks.engine.clearPythonToken).toHaveBeenCalledOnce();

  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); await Promise.resolve(); });
  expect(mocks.engine.connectWebSocket).not.toHaveBeenCalled();
  expect(mocks.engine.syncTokenToPython).not.toHaveBeenCalled();
});
