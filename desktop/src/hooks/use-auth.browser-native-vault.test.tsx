/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  signInWithPassword: vi.fn(),
}));

vi.mock("@/lib/supabase", () => ({
  default: { auth: mocks },
}));
vi.mock("@/lib/oauth", () => ({
  buildOAuthAuthorizeUrl: vi.fn(), saveOAuthState: vi.fn(), clearOAuthState: vi.fn(),
  setOAuthPending: vi.fn(), clearOAuthPending: vi.fn(), isOAuthPending: () => false,
  exchangeOAuthCode: vi.fn(),
}));
vi.mock("@/features/content-ir/runtime/registry", () => ({ resetContentIr: vi.fn(), warmContentIr: vi.fn() }));
vi.mock("@/hooks/use-client-log", () => ({ emitClientLog: vi.fn() }));

// Intentionally no native-vault-auth or sidecar mock: this is the browser
// runtime boundary whose unsupported_platform transition must be accepted.
import { useAuth } from "./use-auth";

let root: Root;
let container: HTMLDivElement;
let auth: ReturnType<typeof useAuth>;
function Subject() { auth = useAuth(); return null; }

beforeEach(async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.getSession.mockResolvedValue({ data: { session: null } });
  mocks.onAuthStateChange.mockImplementation(() => ({ data: { subscription: { unsubscribe: vi.fn() } } }));
  mocks.signInWithPassword.mockResolvedValue({ error: null });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => { root.render(<Subject />); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
});

afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("starts browser-dev password sign-in through the real native fence", async () => {
  await act(async () => { await auth.signInWithEmail("browser@example.test", "password"); });
  expect(mocks.signInWithPassword).toHaveBeenCalledWith({ email: "browser@example.test", password: "password" });
  expect(auth.error).toBeNull();
});
