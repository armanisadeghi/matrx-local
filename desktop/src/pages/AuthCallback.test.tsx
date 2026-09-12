/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  clearOAuthState: vi.fn(),
}));

vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("@/lib/oauth", () => ({
  clearOAuthState: mocks.clearOAuthState,
  exchangeOAuthCode: vi.fn(),
}));
vi.mock("@/lib/supabase", () => ({
  default: { auth: { setSession: vi.fn() } },
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
  window.history.replaceState({}, "", "/#/auth/callback?state=only-state");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
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
