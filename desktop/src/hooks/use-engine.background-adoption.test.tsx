/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const engine = {
    engineUrl: "http://engine.test",
    setTokenProvider: vi.fn(), isHealthy: vi.fn(async () => true), discover: vi.fn(async () => "http://engine.test"),
    getPlatformContext: vi.fn(async () => ({})), listTools: vi.fn(async () => []), getVersion: vi.fn(async () => "test"),
    getSystemInfo: vi.fn(async () => ({})), connectWebSocket: vi.fn(async () => undefined), disconnect: vi.fn(),
    configureCloudSync: vi.fn(async () => undefined), reconfigureCloudSync: vi.fn(async () => undefined),
    cloudHeartbeat: vi.fn(async () => undefined),
    prepareSessionTransition: vi.fn(async () => ({ status: "aligned", origin: "http://engine.test", generation: "process-a", credentialRevision: 1, subject: "actor-a" })),
    on: vi.fn(() => () => undefined),
  };
  return {
    engine,
    session: { access_token: "daemon-access", user: { id: "actor-a" } },
    listener: undefined as undefined | ((envelope: any) => void),
    getAuthedSession: vi.fn<() => Promise<any>>(),
    startBackgroundTasks: vi.fn(), stopBackgroundTasks: vi.fn(),
    context: { revision: 1, isCurrent: () => true, engineOrigin: "http://engine.test", engineGeneration: "process-a" },
  };
});

vi.mock("@/lib/api", () => ({ engine: mocks.engine }));
vi.mock("@/lib/local-browser-context", () => ({
  startLocalBrowserContextSynchronization: vi.fn(),
  synchronizeLocalBrowserContext: vi.fn(),
}));
vi.mock("@/lib/custodian", () => ({
  getAuthedSession: mocks.getAuthedSession,
  getToken: async () => mocks.session.access_token,
  subscribeSession: () => () => undefined,
}));
vi.mock("@/lib/native-vault-auth", () => ({
  resolveNativeVaultEngineAccessToken: vi.fn(async () => "daemon-access"),
  isNativeVaultHostRevisionCurrent: vi.fn(() => true),
  nativeVaultEngineTransitionContext: (subject: string) => subject === "actor-a" ? mocks.context : null,
  subscribeNativeVaultHostEvents: (callback: any) => { mocks.listener = callback; return () => undefined; },
}));
vi.mock("@/lib/sidecar", () => ({ ENGINE_STARTUP_TIMEOUT_SECONDS: 1, isTauri: () => false, startSidecar: vi.fn(), stopSidecar: vi.fn(), waitForOwnedEngine: vi.fn() }));
vi.mock("@/lib/platformCtx", () => ({ initPlatformCtx: vi.fn() }));
vi.mock("@/lib/background-tasks", () => ({ startBackgroundTasks: mocks.startBackgroundTasks, stopBackgroundTasks: mocks.stopBackgroundTasks }));
vi.mock("@/hooks/use-window-leader", () => ({ useWindowLeader: () => true }));
vi.mock("@/hooks/use-client-log", () => ({ emitClientLog: vi.fn() }));
vi.mock("@/hooks/use-unified-log", () => ({ emitClientLog: vi.fn() }));

import { useEngine } from "./use-engine";

let root: Root;
let container: HTMLDivElement;
function Subject() { useEngine(); return null; }

beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listener = undefined;
  mocks.getAuthedSession.mockReset();
  mocks.getAuthedSession.mockResolvedValue(null);
  mocks.context.revision = 1;
  Object.values(mocks.engine).forEach((value) => {
    if (typeof value === "function" && "mockClear" in value) (value as any).mockClear();
  });
  mocks.startBackgroundTasks.mockClear();
  mocks.stopBackgroundTasks.mockClear();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => { root.render(<Subject />); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it("stops local cloud work before a rejected daemon actor transition can configure the engine", async () => {
  expect(mocks.listener).toBeTypeOf("function");
  mocks.engine.configureCloudSync.mockClear();
  mocks.engine.reconfigureCloudSync.mockClear();
  mocks.engine.connectWebSocket.mockClear();
  act(() => mocks.listener!({ session: { user: { id: "actor-b" } }, revision: 2, completion: Promise.resolve({ accepted: false }) }));
  expect(mocks.stopBackgroundTasks).toHaveBeenCalled();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(mocks.engine.configureCloudSync).not.toHaveBeenCalled();
  expect(mocks.engine.reconfigureCloudSync).not.toHaveBeenCalled();
  expect(mocks.engine.connectWebSocket).not.toHaveBeenCalled();
});

it("restarts the leader queue after an accepted same-actor daemon transition", async () => {
  expect(mocks.listener).toBeTypeOf("function");
  mocks.getAuthedSession.mockResolvedValue(mocks.session);
  mocks.context.revision = 2;
  mocks.engine.configureCloudSync.mockClear();
  mocks.engine.reconfigureCloudSync.mockClear();
  mocks.engine.connectWebSocket.mockClear();
  mocks.startBackgroundTasks.mockClear();
  act(() => mocks.listener!({ session: mocks.session, revision: 2, completion: Promise.resolve({ accepted: true }) }));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(mocks.engine.configureCloudSync).toHaveBeenCalledWith("actor-a", mocks.context);
  expect(mocks.engine.connectWebSocket).toHaveBeenCalledWith(mocks.context);
  expect(mocks.startBackgroundTasks).toHaveBeenCalled();
});
