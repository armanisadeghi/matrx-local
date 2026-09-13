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
}));

vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("@/lib/oauth", () => ({
  clearOAuthState: mocks.clearOAuthState,
  exchangeOAuthCode: mocks.exchangeOAuthCode,
}));
vi.mock("@/lib/supabase", () => ({
  default: { auth: { setSession: mocks.setSession, getSession: mocks.getSession } },
}));

// Intentionally do not mock native-vault-auth or sidecar. jsdom is the real
// browser/dev boundary, so the coordinator must receive unsupported_platform.
import { AuthCallback } from "./AuthCallback";

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.navigate.mockReset();
  mocks.clearOAuthState.mockReset();
  mocks.exchangeOAuthCode.mockResolvedValue({ access_token: "access", refresh_token: "refresh" });
  mocks.setSession.mockResolvedValue({ error: null });
  mocks.getSession.mockResolvedValue({ data: { session: { user: { id: "browser-actor" } } } });
  window.history.replaceState({}, "", "/#/auth/callback?code=code&state=state");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it("completes a browser-dev callback through the real native fence", async () => {
  await act(async () => {
    root.render(<AuthCallback />);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(mocks.setSession).toHaveBeenCalledOnce();
  expect(mocks.navigate).toHaveBeenCalledWith("/", { replace: true });
  expect(container.textContent).not.toContain("Token exchange failed");
});
