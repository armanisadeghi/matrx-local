/** @vitest-environment jsdom */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  unregister: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  engine: { getBrowserRuntimeStatus: mocks.status },
}));
vi.mock("@/features/action-needed/actions", () => ({
  navigateForActionNeeded: vi.fn(),
  registerActionNeededHandler: () => mocks.unregister,
}));

import {
  BrowserRuntimeProvider,
  useBrowserRuntimeConnectionRefresh,
  useBrowserRuntimeContext,
} from "./BrowserRuntimeContext";

let container: HTMLDivElement;
let root: Root;

function Snapshot() {
  const { loaded, status } = useBrowserRuntimeContext();
  return <output>{loaded ? status?.code : "checking"}</output>;
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
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
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
  expect(container.textContent).toBe("ready");

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
