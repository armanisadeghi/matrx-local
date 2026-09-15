/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getWebAppOrigin: vi.fn(),
  location: { pathname: "/aimatrx" },
}));

vi.mock("react-router-dom", () => ({ useLocation: () => mocks.location }));
vi.mock("@/lib/app-config", () => ({
  getAppRuntimeConfig: () => ({ webAppOrigin: "https://web.example.test" }),
  getWebAppOrigin: mocks.getWebAppOrigin,
}));
vi.mock("@ai-matrx/design-system", () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button>,
}));

import { AiMatrx, localToolsIframeUrl } from "./AiMatrx";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.getWebAppOrigin.mockReset().mockResolvedValue("https://web.example.test/");
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it("uses a credential-free Local Tools iframe URL even when the desktop is signed in", async () => {
  await act(async () => {
    root.render(<AiMatrx />);
    await Promise.resolve();
    await Promise.resolve();
  });

  const iframe = container.querySelector("iframe");
  expect(iframe?.getAttribute("src")).toBe("https://web.example.test/demos/local-tools");
  expect(iframe?.getAttribute("src")).not.toMatch(/access_token|refresh_token|token=/);
  expect(mocks.getWebAppOrigin).toHaveBeenCalledOnce();
});

it("preserves the fixed Local Tools route when normalizing the web origin", () => {
  expect(localToolsIframeUrl("https://web.example.test/")).toBe("https://web.example.test/demos/local-tools");
});
