/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  clearOAuthState: vi.fn(),
  exchangeOAuthCode: vi.fn(),
  setSession: vi.fn(),
  getSession: vi.fn(),
  invalidate: vi.fn(),
  reconcile: vi.fn(),
}));

vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("@/lib/oauth", () => ({
  clearOAuthState: mocks.clearOAuthState,
  exchangeOAuthCode: mocks.exchangeOAuthCode,
}));
vi.mock("@/lib/supabase", () => ({
  default: { auth: { setSession: mocks.setSession, getSession: mocks.getSession } },
}));
vi.mock("@/lib/native-vault-auth", () => ({
  invalidateNativeVaultBeforeHostMutation: mocks.invalidate,
  reconcileNativeVaultAfterHostSession: mocks.reconcile,
}));

import { AuthCallback } from "./AuthCallback";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  mocks.navigate.mockReset();
  mocks.clearOAuthState.mockReset();
  mocks.exchangeOAuthCode.mockReset();
  mocks.setSession.mockReset();
  mocks.getSession.mockReset();
  mocks.invalidate.mockReset();
  mocks.reconcile.mockReset();
  mocks.invalidate.mockResolvedValue(undefined);
  window.history.replaceState({}, "", "/#/auth/callback?state=only-state");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

it("does not navigate after a successful token exchange until native reconciliation accepts", async () => {
  let rejectFence!: (error: Error) => void;
  const pendingFence = new Promise<void>((_resolve, reject) => { rejectFence = reject; });
  mocks.exchangeOAuthCode.mockResolvedValue({ access_token: "test-access", refresh_token: "test-refresh" });
  mocks.setSession.mockResolvedValue({ error: null });
  mocks.getSession.mockResolvedValue({ data: { session: { user: { id: "user-a" } } } });
  mocks.reconcile.mockReturnValue(pendingFence);
  window.history.replaceState({}, "", "/#/auth/callback?code=code&state=state");

  await act(async () => { root.render(<AuthCallback />); });
  expect(mocks.setSession).toHaveBeenCalledOnce();
  expect(mocks.navigate).not.toHaveBeenCalled();

  rejectFence(new Error("state corrupt"));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(mocks.navigate).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Token exchange failed");
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

it("clears an incomplete web callback transaction and returns to login", async () => {
  await act(async () => {
    root.render(<AuthCallback />);
  });

  expect(mocks.clearOAuthState).toHaveBeenCalledOnce();
  expect(container.textContent).toContain("Sign-in callback is incomplete");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(mocks.navigate).toHaveBeenCalledWith("/login", { replace: true });
});
