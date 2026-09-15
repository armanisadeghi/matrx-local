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

import { Login } from "./Login";

let root: Root;
let container: HTMLDivElement;
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

it("renders an exclusive account-connection recovery card", async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const retryAccountCleanup = vi.fn();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => {
    root.render(<Login auth={{ signInWithOAuth: vi.fn(), loading: false, error: "Your account is signed in, but its connection to Matrx Local is unavailable. Retry account connection.", accountConnectionUnavailable: true, retryAccountCleanup }} />);
  });

  const retry = [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("Retry account connection"));
  expect(retry).toBeDefined();
  expect(container.querySelectorAll("input")).toHaveLength(0);
  expect(container.textContent).not.toContain("Sign in with AI Matrx");
  expect(container.textContent).not.toContain("continue with email");
  await act(async () => retry?.click());
  expect(retryAccountCleanup).toHaveBeenCalledOnce();
});
