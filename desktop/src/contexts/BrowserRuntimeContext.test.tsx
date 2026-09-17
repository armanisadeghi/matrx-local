/** @vitest-environment jsdom */

import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  unregister: vi.fn(),
  capture: vi.fn(() => true),
}));

vi.mock("@/lib/api", () => ({
  engine: { getBrowserRuntimeStatus: mocks.status },
}));
vi.mock("@/features/action-needed/actions", () => ({
  navigateForActionNeeded: vi.fn(),
  registerActionNeededHandler: () => mocks.unregister,
}));
vi.mock("@/lib/error-outbox", () => ({ enqueueDurableClientError: mocks.capture }));

import {
  BrowserRuntimeProvider,
  useBrowserRuntimeConnectionRefresh,
  useBrowserRuntimeContext,
} from "./BrowserRuntimeContext";

let container: HTMLDivElement;
let root: Root;

function Snapshot() {
  const { loaded, status, statusFetchFailed } = useBrowserRuntimeContext();
  return <output>{loaded ? `${status?.code}:${statusFetchFailed ? "stale" : "fresh"}` : "checking"}</output>;
}

function Subject({ connected }: { connected: boolean }) {
  useBrowserRuntimeConnectionRefresh(connected);
  return <Snapshot />;
}

function Harness({ connected }: { connected: boolean }) {
  return (
    <BrowserRuntimeProvider>
      <Subject connected={connected} />
    </BrowserRuntimeProvider>
  );
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.status.mockReset();
  mocks.unregister.mockReset();
  mocks.capture.mockReset();
  mocks.capture.mockReturnValue(true);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

it("retries a failed pre-discovery probe when the engine connects", async () => {
  mocks.status
    .mockRejectedValueOnce(new Error("Engine not discovered"))
    .mockResolvedValueOnce({
      available: true,
      code: "ready",
      install_percent: null,
      installing: false,
      install_message: null,
    })
    .mockResolvedValueOnce({
      available: true,
      code: "ready",
      install_percent: null,
      installing: false,
      install_message: null,
    });
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

  await act(async () => {
    root.render(<Harness connected={false} />);
    await Promise.resolve();
  });
  expect(container.textContent).toBe("checking");

  await act(async () => {
    root.render(<Harness connected />);
    await Promise.resolve();
  });
  expect(mocks.status).toHaveBeenCalledTimes(2);
  expect(container.textContent).toBe("ready:fresh");

  await act(async () => {
    root.render(<Harness connected />);
    await Promise.resolve();
  });
  expect(mocks.status).toHaveBeenCalledTimes(2);

  await act(async () => {
    root.render(<Harness connected={false} />);
    await Promise.resolve();
  });
  await act(async () => {
    root.render(<Harness connected />);
    await Promise.resolve();
  });
  expect(mocks.status).toHaveBeenCalledTimes(3);
  warn.mockRestore();
});

it("captures a connected status failure once, retains the prior state, and re-arms after recovery", async () => {
  mocks.status
    .mockResolvedValueOnce({ available: true, code: "ready", installing: false, install_percent: null, install_message: null })
    .mockRejectedValueOnce(new Error("network unavailable"))
    .mockRejectedValueOnce(new Error("network unavailable"))
    .mockResolvedValueOnce({ available: true, code: "ready", installing: false, install_percent: null, install_message: null })
    .mockRejectedValueOnce(new Error("network unavailable"));
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

  await act(async () => {
    root.render(<Harness connected={false} />);
    await Promise.resolve();
  });
  expect(container.textContent).toBe("ready:fresh");

  for (const connected of [true, false, true]) {
    await act(async () => {
      root.render(<Harness connected={connected} />);
      await Promise.resolve();
    });
  }
  expect(container.textContent).toBe("ready:stale");
  expect(mocks.capture).toHaveBeenCalledTimes(1);

  for (const connected of [false, true]) {
    await act(async () => {
      root.render(<Harness connected={connected} />);
      await Promise.resolve();
    });
  }
  expect(container.textContent).toBe("ready:fresh");

  for (const connected of [false, true]) {
    await act(async () => {
      root.render(<Harness connected={connected} />);
      await Promise.resolve();
    });
  }
  expect(mocks.capture).toHaveBeenCalledTimes(2);
  warn.mockRestore();
});

it("ignores a late pre-discovery failure after a connected probe succeeds", async () => {
  let rejectFirst: (reason?: unknown) => void = () => undefined;
  let resolveSecond: (value: unknown) => void = () => undefined;
  mocks.status
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectFirst = reject; }))
    .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);

  await act(async () => root.render(<Harness connected={false} />));
  await act(async () => root.render(<Harness connected />));
  await act(async () => {
    resolveSecond({ available: true, code: "ready", installing: false, install_percent: null, install_message: null });
    await Promise.resolve();
  });
  await act(async () => {
    rejectFirst(new Error("late pre-discovery failure"));
    await Promise.resolve();
  });

  expect(container.textContent).toBe("ready:fresh");
  expect(mocks.capture).not.toHaveBeenCalled();
  warn.mockRestore();
});

it("remains live through StrictMode replay", async () => {
  mocks.status.mockResolvedValue({ available: true, code: "ready", installing: false, install_percent: null, install_message: null });
  await act(async () => {
    root.render(<StrictMode><Harness connected={false} /></StrictMode>);
    await Promise.resolve();
  });
  expect(container.textContent).toBe("ready:fresh");
});

it("captures a failed install-progress poll and clears it on the next poll", async () => {
  vi.useFakeTimers();
  mocks.status
    .mockResolvedValueOnce({ available: true, code: "ready", installing: true, install_percent: 10, install_message: "Downloading" })
    .mockRejectedValueOnce(new Error("network unavailable"))
    .mockResolvedValueOnce({ available: true, code: "ready", installing: true, install_percent: 20, install_message: "Downloading" });
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  await act(async () => {
    root.render(<Harness connected={false} />);
    await Promise.resolve();
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(container.textContent).toBe("ready:stale");
  expect(mocks.capture).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(container.textContent).toBe("ready:fresh");
  warn.mockRestore();
});
