/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  requireOrg: vi.fn(async () => "org-a"),
  activeOrg: vi.fn(async () => "org-a"),
  hostListener: undefined as undefined | (() => void),
}));

vi.mock("@/lib/native-vault-file-import", () => ({ nativeVaultFileImport: mocks.invoke }));
vi.mock("@/lib/native-vault-auth", () => ({ subscribeNativeVaultHostEvents: vi.fn((listener) => { mocks.hostListener = listener; return () => { mocks.hostListener = undefined; }; }) }));
vi.mock("@/lib/org/active-org", () => ({
  ACTIVE_ORGANIZATION_CHANGE_EVENT: "test-active-org-change",
  getActiveOrganizationId: mocks.activeOrg,
  requireActiveOrganizationId: mocks.requireOrg,
}));

import { NativeVaultImport } from "./NativeVaultImport";

const operation = "123e4567-e89b-12d3-a456-426614174000";
const digest = "a".repeat(64);
const previewStatus = { operation_id: operation, phase: "preview" as const, total: 2, committed: 0, already_present: 0, unsupported: 0, failed: 0, uncertain: 0, not_attempted: 2, message: "Review the compatible passkeys." };
const preview = { operation_id: operation, preview_digest: digest, offset: 0, total: 2, slots: [
  { slot_id: "slot-a", title: "Example passkey", disposition: "eligible" as const, reason: null },
  { slot_id: "slot-b", title: "Old passkey", disposition: "unsupported" as const, reason: "Unsupported key algorithm" },
] };

let root: Root | undefined;
let container: HTMLDivElement;
function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((element) => element.textContent?.includes(label));
  if (!found) throw new Error(`Missing button: ${label}`);
  return found as HTMLButtonElement;
}
async function flush(): Promise<void> { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); }

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  container?.remove();
  root = undefined;
  mocks.hostListener = undefined;
  vi.useRealTimers();
  vi.clearAllMocks();
});

it("uses the native chooser, previews metadata, then binds the current organization before explicit confirmation", async () => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.invoke.mockResolvedValueOnce(previewStatus).mockResolvedValueOnce(preview)
    .mockResolvedValueOnce({ ...previewStatus, phase: "awaiting_confirmation" })
    .mockResolvedValueOnce({ ...previewStatus, phase: "completed", committed: 1, unsupported: 1, not_attempted: 0, message: "One imported; one unsupported." });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  expect(mocks.invoke).toHaveBeenNthCalledWith(1, { action: "begin_file_import", organization_id: "org-a" });
  expect(mocks.invoke).toHaveBeenNthCalledWith(2, { action: "preview", operation_id: operation, offset: 0 });
  expect(container.textContent).toContain("Example passkey");
  expect(container.textContent).toContain("Cannot import: Unsupported key algorithm");
  await act(async () => button("Confirm import").click()); await flush();
  expect(mocks.invoke).toHaveBeenNthCalledWith(3, { action: "choose_scope", operation_id: operation, organization_id: "org-a" });
  expect(mocks.invoke).toHaveBeenNthCalledWith(4, { action: "confirm", operation_id: operation, preview_digest: digest });
  expect(container.textContent).toContain("Imported 1 passkey.");
});

it("drops a late chooser response after the selected organization changes", async () => {
  let release: ((value: typeof previewStatus) => void) | undefined;
  mocks.invoke.mockImplementationOnce(() => new Promise<typeof previewStatus>((resolve) => { release = resolve; }));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click());
  await act(async () => window.dispatchEvent(new Event("test-active-org-change")));
  await act(async () => release?.(previewStatus)); await flush();
  expect(container.textContent).not.toContain("Review before importing");
  expect(mocks.invoke).toHaveBeenNthCalledWith(2, { action: "cancel", operation_id: operation });
});

it("loads every native preview page before it enables confirmation", async () => {
  const firstPage = { ...preview, total: 10, slots: Array.from({ length: 8 }, (_, index) => ({ slot_id: `slot-${index}`, title: `Passkey ${index}`, disposition: "eligible" as const, reason: null })) };
  const secondPage = { ...preview, offset: 8, total: 10, slots: Array.from({ length: 2 }, (_, index) => ({ slot_id: `slot-${index + 8}`, title: `Passkey ${index + 8}`, disposition: "unsupported" as const, reason: "Unsupported extension" })) };
  mocks.invoke.mockResolvedValueOnce({ ...previewStatus, total: 10 }).mockResolvedValueOnce(firstPage).mockResolvedValueOnce(secondPage);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  expect(mocks.invoke).toHaveBeenNthCalledWith(3, { action: "preview", operation_id: operation, offset: 8 });
  expect(container.textContent).toContain("Passkey 9");
  expect(container.textContent).toContain("complete file preview");
});

it("does not start a stale chooser after the organization changes during organization resolution", async () => {
  let resolveOrganization: ((value: string) => void) | undefined;
  mocks.requireOrg.mockImplementationOnce(() => new Promise<string>((resolve) => { resolveOrganization = resolve; }));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click());
  await act(async () => window.dispatchEvent(new Event("test-active-org-change")));
  await act(async () => resolveOrganization?.("org-a")); await flush();
  expect(mocks.invoke).not.toHaveBeenCalled();
  expect(container.textContent).toContain("selected organization changed");
});

it("refuses an overlapping preview page before it can enable confirmation", async () => {
  const firstPage = { ...preview, total: 10, slots: Array.from({ length: 8 }, (_, index) => ({ slot_id: `slot-${index}`, title: `Passkey ${index}`, disposition: "eligible" as const, reason: null })) };
  const overlap = { ...preview, offset: 8, total: 10, slots: Array.from({ length: 2 }, (_, index) => ({ slot_id: `slot-${index}`, title: `Duplicate ${index}`, disposition: "eligible" as const, reason: null })) };
  mocks.invoke.mockResolvedValueOnce({ ...previewStatus, total: 10 }).mockResolvedValueOnce(firstPage).mockResolvedValueOnce(overlap);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  expect(container.textContent).toContain("duplicate passkey preview item");
  expect(container.textContent).not.toContain("Confirm import");
});

it("keeps progress controls when native cancellation is still reconciling", async () => {
  mocks.invoke.mockResolvedValueOnce(previewStatus).mockResolvedValueOnce(preview)
    .mockResolvedValueOnce({ ...previewStatus, phase: "importing", uncertain: 1, not_attempted: 1, message: "Cancelling after receipt reconciliation." });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  await act(async () => button("Cancel").click()); await flush();
  expect(container.textContent).toContain("Cancelling after receipt reconciliation.");
  expect(button("Refresh progress")).toBeTruthy();
  expect(button("Cancel import")).toBeTruthy();
});

it("automatically advances an authorizing operation into a complete review preview", async () => {
  vi.useFakeTimers();
  mocks.invoke.mockResolvedValueOnce({ ...previewStatus, phase: "authorizing", message: "Opening the native file chooser." })
    .mockResolvedValueOnce(previewStatus).mockResolvedValueOnce(preview);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  expect(container.textContent).toContain("Opening the native file chooser.");
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(mocks.invoke).toHaveBeenNthCalledWith(2, { action: "status", operation_id: operation });
  expect(container.textContent).toContain("Review before importing");
});

it("keeps the active operation reachable after rejected preview, refresh, confirmation, and cancellation calls", async () => {
  mocks.invoke.mockResolvedValueOnce(previewStatus).mockRejectedValueOnce(new Error("Preview connection failed"));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  expect(container.textContent).toContain("Preview connection failed");
  expect(button("Refresh progress")).toBeTruthy();
  expect(button("Cancel import")).toBeTruthy();

  mocks.invoke.mockRejectedValueOnce(new Error("Progress connection failed"));
  await act(async () => button("Refresh progress").click()); await flush();
  expect(container.textContent).toContain("Progress connection failed");
  expect(button("Cancel import")).toBeTruthy();

  mocks.invoke.mockRejectedValueOnce(new Error("Cancellation connection failed"));
  await act(async () => button("Cancel import").click()); await flush();
  expect(container.textContent).toContain("Cancellation connection failed");
  expect(button("Refresh progress")).toBeTruthy();
});

it("keeps the active operation reachable after scope binding or confirmation fails", async () => {
  mocks.invoke.mockResolvedValueOnce(previewStatus).mockResolvedValueOnce(preview)
    .mockRejectedValueOnce(new Error("Scope binding failed"));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  await act(async () => button("Confirm import").click()); await flush();
  expect(container.textContent).toContain("Scope binding failed");
  expect(button("Refresh progress")).toBeTruthy();
  expect(button("Cancel import")).toBeTruthy();
});

it("keeps the active operation reachable after the native confirmation rejects", async () => {
  mocks.invoke.mockResolvedValueOnce(previewStatus).mockResolvedValueOnce(preview)
    .mockResolvedValueOnce({ ...previewStatus, phase: "awaiting_confirmation" })
    .mockRejectedValueOnce(new Error("Confirmation connection failed"));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  await act(async () => button("Confirm import").click()); await flush();
  expect(container.textContent).toContain("Confirmation connection failed");
  expect(button("Refresh progress")).toBeTruthy();
  expect(button("Cancel import")).toBeTruthy();
});

it("restarts bounded automatic progress after a manual refresh", async () => {
  vi.useFakeTimers();
  const authorizing = { ...previewStatus, phase: "authorizing" as const, message: "Still authorizing." };
  mocks.invoke.mockResolvedValueOnce(authorizing).mockResolvedValue(authorizing);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(container.textContent).toContain("Refresh progress to continue checking it.");
  const callsAtCap = mocks.invoke.mock.calls.length;
  await act(async () => button("Refresh progress").click()); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  expect(mocks.invoke.mock.calls.length).toBeGreaterThan(callsAtCap + 1);
});

it("stops pending recovery when the account changes and tears down its polling timer", async () => {
  vi.useFakeTimers();
  let releaseRecovery: ((value: unknown) => void) | undefined;
  mocks.invoke.mockImplementationOnce(() => new Promise<unknown>((resolve) => { releaseRecovery = resolve; }));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Recover import").click());
  await act(async () => mocks.hostListener?.());
  await act(async () => releaseRecovery?.({ ...previewStatus, phase: "authorizing" })); await flush();
  expect(container.textContent).toContain("account changed");
  expect(mocks.invoke).toHaveBeenCalledTimes(1);
  await act(async () => root?.unmount()); root = undefined;
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(mocks.invoke).toHaveBeenCalledTimes(1);
});

it("clears an active polling timer when the component unmounts", async () => {
  vi.useFakeTimers();
  mocks.invoke.mockResolvedValueOnce({ ...previewStatus, phase: "authorizing", message: "Waiting for native authorization." });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => root?.render(<NativeVaultImport />));
  await act(async () => button("Choose file").click()); await flush();
  await act(async () => root?.unmount()); root = undefined;
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(mocks.invoke).toHaveBeenCalledTimes(1);
});
