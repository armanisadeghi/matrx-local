// @vitest-environment jsdom

import { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  invoke: vi.fn<(command: string, args?: unknown) => Promise<unknown>>(async () => null),
  listen: vi.fn(async () => vi.fn()),
  loadSettings: vi.fn(async () => ({ transcriptionAutoInit: true })),
}));

vi.mock("@/lib/sidecar", () => ({ isTauri: () => true }));
vi.mock("@/lib/platformCtx", () => ({ PLATFORM: { is_mac: false } }));
vi.mock("@/lib/error-outbox", () => ({ enqueueDurableClientError: vi.fn(() => true) }));
vi.mock("@/lib/transcription/catalog", () => ({ overlayWhisperCatalog: async (value: unknown) => value }));
vi.mock("@/hooks/use-permissions", () => ({
  readPluginPermissionStatus: vi.fn(),
  requestPluginMicrophonePermission: vi.fn(),
  transcriptionMicrophonePrerequisiteError: vi.fn(() => null),
}));
vi.mock("@/lib/settings", () => ({ loadSettings: mocks.loadSettings }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));

import { useTranscription } from "./use-transcription";

let host: HTMLDivElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;
let state: ReturnType<typeof useTranscription>[0];

function Subject() {
  [state] = useTranscription();
  return null;
}

async function mount(strictMode = false) {
  host = document.createElement("div");
  root = createRoot(host);
  await act(async () => {
    root!.render(strictMode ? <StrictMode><Subject /></StrictMode> : <Subject />);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function nativeResponses(activeModel: string | null) {
  mocks.invoke.mockImplementation(async (command: string) => {
    if (command === "get_voice_setup_status") {
      return { setup_complete: true, selected_model: "persisted.bin", downloaded_models: ["persisted.bin"] };
    }
    if (command === "auto_init_transcription" || command === "get_active_model") {
      return activeModel;
    }
    return null;
  });
}

beforeEach(() => {
  mocks.listen.mockResolvedValue(vi.fn());
  // Strict Mode may replay the listener effect after the dynamic import has
  // resolved outside Vitest's module mock boundary. Keep that replay inside a
  // harmless bridge stub; this test never talks to a native app.
  (window as unknown as {
    __TAURI_INTERNALS__: {
      transformCallback: () => number;
      unregisterCallback: () => void;
      invoke: () => Promise<number>;
    };
  }).__TAURI_INTERNALS__ = {
    transformCallback: () => 1,
    unregisterCallback: () => undefined,
    invoke: async () => 1,
  };
  (window as unknown as {
    __TAURI_EVENT_PLUGIN_INTERNALS__: { unregisterListener: () => void };
  }).__TAURI_EVENT_PLUGIN_INTERNALS__ = {
    unregisterListener: () => undefined,
  };
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  mocks.invoke.mockReset();
  mocks.listen.mockReset();
  mocks.loadSettings.mockReset();
});

describe("useTranscription startup auto-initialization", () => {
  it("reads settings before enabled auto-init and exposes the actual active model", async () => {
    mocks.loadSettings.mockResolvedValue({ transcriptionAutoInit: true });
    nativeResponses("loaded.bin");

    await mount();

    expect(mocks.invoke).toHaveBeenCalledWith("auto_init_transcription", { enabled: true });
    expect(state.activeModel).toBe("loaded.bin");
    expect(mocks.invoke).toHaveBeenCalledWith("get_active_model", undefined);
  });

  it("passes disabled through to native without treating configured selection as active", async () => {
    mocks.loadSettings.mockResolvedValue({ transcriptionAutoInit: false });
    nativeResponses(null);

    await mount();

    expect(mocks.invoke).toHaveBeenCalledWith("auto_init_transcription", { enabled: false });
    expect(state.activeModel).toBeNull();
  });

  it("does not request native auto-init when settings cannot be read", async () => {
    mocks.loadSettings.mockRejectedValue(new Error("settings unavailable"));
    nativeResponses("loaded.bin");

    await mount();

    expect(mocks.invoke).not.toHaveBeenCalledWith("auto_init_transcription", expect.anything());
  });

  it("does not restart auto-init on a root-context rerender", async () => {
    mocks.loadSettings.mockResolvedValue({ transcriptionAutoInit: true });
    nativeResponses("loaded.bin");
    await mount();

    await act(async () => {
      root!.render(<Subject />);
      await Promise.resolve();
    });

    expect(mocks.invoke.mock.calls.filter(([command]) => command === "auto_init_transcription")).toHaveLength(1);
  });

  it("uses the Strict Mode replay when settings resolve after the first effect is cancelled", async () => {
    const settings = deferred<{ transcriptionAutoInit: boolean }>();
    mocks.loadSettings.mockReturnValue(settings.promise);
    nativeResponses("loaded.bin");

    await mount(true);
    expect(mocks.invoke).not.toHaveBeenCalledWith("auto_init_transcription", expect.anything());

    await act(async () => {
      settings.resolve({ transcriptionAutoInit: true });
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.invoke.mock.calls.filter(([command]) => command === "auto_init_transcription")).toHaveLength(1);
  });

  it("does not refresh or update state after unmount while native auto-init resolves", async () => {
    const autoInit = deferred<string | null>();
    mocks.loadSettings.mockResolvedValue({ transcriptionAutoInit: true });
    mocks.invoke.mockImplementation(async (command: string) => {
      if (command === "auto_init_transcription") return autoInit.promise;
      if (command === "get_voice_setup_status") {
        return { setup_complete: true, selected_model: "persisted.bin", downloaded_models: [] };
      }
      return "loaded.bin";
    });
    await mount();
    expect(mocks.invoke).toHaveBeenCalledWith("auto_init_transcription", { enabled: true });

    await act(async () => root?.unmount());
    root = null;
    await act(async () => {
      autoInit.resolve("loaded.bin");
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.invoke).not.toHaveBeenCalledWith("get_voice_setup_status", undefined);
  });
});
