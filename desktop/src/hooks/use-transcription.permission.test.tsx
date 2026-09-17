// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  invoke: vi.fn<(command: string, ...args: unknown[]) => Promise<unknown>>(async () => null),
  listen: vi.fn(async () => vi.fn()),
  readMicrophone: vi.fn(async () => "granted"),
  requestMicrophone: vi.fn(async () => "granted"),
  prerequisiteError: vi.fn((status: string) =>
    status === "granted"
      ? null
      : "Microphone access is required for transcription. Please allow access when prompted.",
  ),
  enqueue: vi.fn(() => true),
  loadSettings: vi.fn(async () => ({ transcriptionAutoInit: false })),
}));

vi.mock("@/lib/sidecar", () => ({ isTauri: () => true }));
vi.mock("@/lib/platformCtx", () => ({ PLATFORM: { is_mac: true } }));
vi.mock("@/lib/error-outbox", () => ({ enqueueDurableClientError: mocks.enqueue }));
vi.mock("@/lib/settings", () => ({ loadSettings: mocks.loadSettings }));
vi.mock("@/lib/transcription/catalog", () => ({ overlayWhisperCatalog: async (value: unknown) => value }));
vi.mock("@/hooks/use-permissions", () => ({
  readPluginPermissionStatus: mocks.readMicrophone,
  requestPluginMicrophonePermission: mocks.requestMicrophone,
  transcriptionMicrophonePrerequisiteError: mocks.prerequisiteError,
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));

import { useTranscription } from "./use-transcription";

let host: HTMLDivElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;
let actions: ReturnType<typeof useTranscription>[1];
let state: ReturnType<typeof useTranscription>[0];

function Subject() {
  [state, actions] = useTranscription();
  return null;
}

async function mount() {
  host = document.createElement("div");
  root = createRoot(host);
  await act(async () => {
    root!.render(<Subject />);
    await Promise.resolve();
  });
}

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  root = null;
  host = null;
  mocks.invoke.mockClear();
  mocks.listen.mockClear();
  mocks.readMicrophone.mockReset();
  mocks.requestMicrophone.mockReset();
  mocks.prerequisiteError.mockClear();
  mocks.enqueue.mockClear();
  mocks.loadSettings.mockReset();
  mocks.loadSettings.mockResolvedValue({ transcriptionAutoInit: false });
});

describe("useTranscription microphone prerequisite", () => {
  it("starts native capture only after a granted microphone check", async () => {
    mocks.readMicrophone.mockResolvedValue("granted");
    mocks.invoke.mockImplementation(async (command: string) => {
      if (command === "start_transcription") return undefined;
      return null;
    });
    await mount();

    await act(async () => actions.startRecording());

    expect(mocks.readMicrophone).toHaveBeenCalledWith("microphone");
    expect(mocks.invoke).toHaveBeenCalledWith("start_transcription", { deviceName: null });
    expect(state.isRecording).toBe(true);
  });

  it("prompts on first use, rechecks, and starts capture after the grant", async () => {
    mocks.readMicrophone.mockResolvedValue("not_determined");
    mocks.requestMicrophone.mockResolvedValue("granted");
    await mount();

    await act(async () => actions.startRecording());

    expect(mocks.requestMicrophone).toHaveBeenCalledTimes(1);
    expect(mocks.invoke).toHaveBeenCalledWith("start_transcription", { deviceName: null });
    expect(state.isRecording).toBe(true);
  });

  it("stops before native capture when the first-use prompt is declined", async () => {
    mocks.readMicrophone.mockResolvedValue("not_determined");
    mocks.requestMicrophone.mockResolvedValue("denied");
    await mount();

    await act(async () => actions.startRecording());

    expect(mocks.invoke).not.toHaveBeenCalledWith("start_transcription", expect.anything());
    expect(mocks.listen).not.toHaveBeenCalledWith("whisper-segment", expect.anything());
    expect(state.error).toContain("Microphone access is required");
  });

  it("keeps an actual native startup failure distinct from a permission prerequisite", async () => {
    mocks.readMicrophone.mockResolvedValue("granted");
    mocks.invoke.mockImplementation(async (command: string) => {
      if (command === "start_transcription") throw new Error("native audio startup failed");
      return null;
    });
    await mount();

    await act(async () => actions.startRecording());

    expect(mocks.invoke).toHaveBeenCalledWith("start_transcription", { deviceName: null });
    expect(state.error).toBe("native audio startup failed");
  });

  it("captures a broken plugin probe once and never falls through to native capture", async () => {
    mocks.readMicrophone.mockRejectedValue(new Error("bridge unavailable"));
    await mount();

    await act(async () => actions.startRecording());
    await act(async () => actions.startRecording());

    expect(mocks.invoke).not.toHaveBeenCalledWith("start_transcription", expect.anything());
    expect(mocks.enqueue).toHaveBeenCalledTimes(1);
    expect(mocks.enqueue).toHaveBeenCalledWith(expect.objectContaining({
      source: "permission-probe:plugin:microphone",
      requireIdentity: true,
    }));
  });

  it("retries a probe failure after identity-gated capture is refused", async () => {
    mocks.readMicrophone.mockRejectedValue(new Error("bridge unavailable"));
    mocks.enqueue.mockReturnValueOnce(false).mockReturnValue(true);
    await mount();

    await act(async () => actions.startRecording());
    await act(async () => actions.startRecording());

    expect(mocks.enqueue).toHaveBeenCalledTimes(2);
  });
});
