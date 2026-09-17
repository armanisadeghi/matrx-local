/** @vitest-environment jsdom */

import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getDevicePermissions: vi.fn(),
  getDevicePermission: vi.fn(),
  capture: vi.fn(() => true),
}));

vi.mock("@/lib/api", () => ({
  engine: mocks,
}));
vi.mock("@/lib/error-outbox", () => ({ enqueueDurableClientError: mocks.capture }));
vi.mock("@/lib/sidecar", () => ({ isTauri: () => false }));
vi.mock("@/lib/platformCtx", () => ({
  PLATFORM: { is_mac: false, is_windows: false, is_linux: true },
}));

import { usePermissions, type UsePermissionsReturn } from "./use-permissions";

let container: HTMLDivElement;
let root: Root;
let value: UsePermissionsReturn | null = null;

function Subject() {
  value = usePermissions();
  return <output>{value.devicePermissionsFetchFailed ? "stale" : "fresh"}</output>;
}

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  value = null;
  mocks.getDevicePermissions.mockReset();
  mocks.getDevicePermission.mockReset().mockResolvedValue({ status: "granted", user_details: "" });
  mocks.capture.mockReset().mockReturnValue(true);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it("keeps the last snapshot, captures one failed refresh, and resets on recovery", async () => {
  mocks.getDevicePermissions
    .mockResolvedValueOnce({ permissions: [{ permission: "camera", status: "granted" }], platform: "Linux" })
    .mockRejectedValueOnce(new Error("network unavailable"))
    .mockRejectedValueOnce(new Error("network unavailable"))
    .mockResolvedValueOnce({ permissions: [{ permission: "camera", status: "denied" }], platform: "Linux" });

  const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
  await act(async () => root.render(<Subject />));
  await act(async () => { await value!.refreshDevicePermissions(); });
  expect(value!.devicePermissions).toHaveLength(1);

  await act(async () => { await value!.refreshDevicePermissions(); });
  expect(value!.devicePermissionsFetchFailed).toBe(true);
  expect(value!.devicePermissions[0]?.status).toBe("granted");
  expect(mocks.capture).toHaveBeenCalledTimes(1);

  await act(async () => { await value!.refreshDevicePermissions(); });
  expect(mocks.capture).toHaveBeenCalledTimes(1);

  await act(async () => { await value!.refreshDevicePermissions(); });
  expect(value!.devicePermissionsFetchFailed).toBe(false);
  expect(value!.devicePermissions[0]?.status).toBe("denied");
  error.mockRestore();
});

it("remains live through StrictMode replay", async () => {
  mocks.getDevicePermissions.mockResolvedValue({
    permissions: [{ permission: "camera", status: "granted" }],
    platform: "Linux",
  });
  await act(async () => root.render(<StrictMode><Subject /></StrictMode>));
  await act(async () => { await value!.refreshDevicePermissions(); });
  expect(value!.devicePermissionsFetchFailed).toBe(false);
  expect(value!.devicePermissions).toHaveLength(1);
});
