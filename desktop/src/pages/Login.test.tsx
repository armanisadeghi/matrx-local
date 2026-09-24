/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@ai-matrx/design-system", () => ({
  Button: ({ children, ...props }: any) => <button {...props}>{children}</button>,
  BasicInput: (props: any) => <input {...props} />,
  Label: ({ children, ...props }: any) => <label {...props}>{children}</label>,
  Separator: () => <hr />,
}));
vi.mock("@/components/ui/card", () => ({ Card: ({ children }: any) => <div>{children}</div>, CardContent: ({ children }: any) => <div>{children}</div> }));
vi.mock("@/lib/app-version", () => ({ AppVersion: () => <span>test</span> }));
vi.mock("lucide-react", () => ({
  AlertTriangle: () => <span data-icon="warn" />,
  RefreshCw: () => <span data-icon="refresh" />,
  Loader2: () => <span data-icon="spin" />,
  Zap: () => <span data-icon="zap" />,
}));

import { Login } from "./Login";
import type { SessionSnapshot } from "@/lib/custodian";

const NEVER_SIGNED_IN: SessionSnapshot = {
  signed_in: false, user_id: null, email: null, state: "signed_out",
  state_reason: "Sign in on this computer to start syncing your folders.",
  since: null, next_attempt_at: null, cloud_state_write_pending: false,
};

let root: Root;
let container: HTMLDivElement;
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("renders an exclusive account-connection recovery card", async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const retryAccountCleanup = vi.fn();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => {
    root.render(<Login auth={{ signInWithOAuth: vi.fn(), loading: false, error: "Your account is signed in, but its connection to Matrx Local is unavailable. Retry account connection.", accountConnectionUnavailable: true, retryAccountCleanup, startSync: vi.fn(), snapshot: NEVER_SIGNED_IN }} />);
  });

  const retry = [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("Retry account connection"));
  expect(retry).toBeDefined();
  expect(container.querySelectorAll("input")).toHaveLength(0);
  expect(container.textContent).not.toContain("Sign in with AI Matrx");
  expect(container.textContent).not.toContain("continue with email");
  await act(async () => retry?.click());
  expect(retryAccountCleanup).toHaveBeenCalledOnce();
});

/** CS-19 — a person signed in yesterday must be TOLD why they are looking at a sign-in screen.
 *  Proven failing before the fix: `Login` ignored `auth.snapshot` entirely, so the screen said
 *  only "Sign in to your workspace" after the custody cutover took the session away. */
async function renderLogin(snapshot: SessionSnapshot, startSync = vi.fn()) {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => {
    root.render(<Login auth={{ signInWithOAuth: vi.fn(), loading: false, error: null, accountConnectionUnavailable: false, retryAccountCleanup: vi.fn(), startSync, snapshot }} />);
  });
}

it("says why the session is gone, in the daemon's own words, with one click to fix it", async () => {
  const reason =
    "Sign in again to AI Matrx \u2014 this update changed how this computer keeps you signed in. " +
    "Nothing was lost: your folders, history and settings are exactly as you left them.";
  await renderLogin({ ...NEVER_SIGNED_IN, state: "sign_in_needed", email: "admin@admin.com", state_reason: reason });

  expect(container.textContent).toContain(reason);
  expect(container.textContent).toContain("Sign in again to continue");
  // The remedy is useless without the control that performs it.
  expect([...container.querySelectorAll("button")].some((b) => b.textContent?.includes("Sign in with AI Matrx"))).toBe(true);
});

it("shows a keychain refusal in the daemon's words rather than a bare sign-in screen", async () => {
  await renderLogin({
    ...NEVER_SIGNED_IN, state: "credential_store_unavailable", user_id: "u1",
    state_reason: "Open Keychain Access and unlock the login keychain, then choose Start sync.",
  });
  expect(container.textContent).toContain("Open Keychain Access");
});

it("leaves a genuine first run alone", async () => {
  await renderLogin(NEVER_SIGNED_IN);
  expect(container.textContent).toContain("Sign in to your workspace");
  expect(container.textContent).not.toContain("Sign in on this computer to start syncing");
});

/** cb53a722 — a daemon that cannot run must say WHY, offer the remedy, and offer the one control
 *  that acts on it. Proven failing before the fix: `Login` had no `daemon_not_running` branch, so
 *  this state rendered a live "Sign in with AI Matrx" that POSTed at a port nothing was listening
 *  on, and the only "Start sync" anywhere in the product was the word inside that sentence. */
const DAEMON_DOWN_SNAPSHOT: SessionSnapshot = {
  ...NEVER_SIGNED_IN,
  state: "daemon_not_running",
  state_reason:
    "This build's sync helper cannot start on this computer (bundled matrx-syncd --version was " +
    "killed by signal 9), so this computer cannot sign in or sync.",
  remedy: "Update AI Matrx to the latest version. If this keeps happening after updating, report it from Settings → Support.",
  // Running the same ladder again cannot make an unrunnable helper run.
  can_start_sync: false,
};

const startButton = () =>
  [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("Start sync"));

it("says exactly why sync is down, offers the remedy, and never offers a dead sign-in", async () => {
  await renderLogin(DAEMON_DOWN_SNAPSHOT);

  expect(container.textContent).toContain("killed by signal 9");
  expect(container.textContent).toContain("Update AI Matrx to the latest version");
  expect(container.textContent).toContain("Sync is not running");
  // The sign-in button would post at a daemon that is not there: it is ABSENT, not disabled.
  expect(container.textContent).not.toContain("Sign in with AI Matrx");
  // L2a-2: so is Start sync, in a state it cannot change. A remedy that cannot remedy is the
  // same lie as a dead control.
  expect(startButton()).toBeUndefined();
});

it("offers Start sync in the states it can actually change", async () => {
  const startSync = vi.fn();
  await renderLogin(
    {
      ...DAEMON_DOWN_SNAPSHOT,
      state_reason: "AI Matrx Sync started but never became ready (the new daemon did not report the expected version in time), so this computer cannot sign in or sync.",
      remedy: "Choose Start sync to try again. If it keeps failing, restart your computer and report it from Settings → Support.",
      can_start_sync: true,
    },
    startSync,
  );

  const start = startButton();
  expect(start).toBeDefined();
  await act(async () => start?.click());
  expect(startSync).toHaveBeenCalledOnce();
});

it("shows the NEW reason when Start sync fails again", async () => {
  await renderLogin({
    ...DAEMON_DOWN_SNAPSHOT,
    state_reason: "AI Matrx Sync started but never became ready (the new daemon did not report the expected version in time), so this computer cannot sign in or sync.",
    remedy: "Choose Start sync to try again. If it keeps failing, restart your computer and report it from Settings → Support.",
    can_start_sync: true,
  });
  expect(container.textContent).toContain("never became ready");
  expect(container.textContent).toContain("restart your computer");
  expect(startButton()).toBeDefined();
});
