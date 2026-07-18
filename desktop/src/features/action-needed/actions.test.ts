import { describe, expect, it, vi } from "vitest";

import { navigateForActionNeeded, type NavigationRuntime } from "./actions";

function runtime(
  overrides: Partial<NavigationRuntime> = {},
): NavigationRuntime {
  return {
    fullWindow: true,
    tauri: true,
    setHash: vi.fn(),
    focus: vi.fn(async () => undefined),
    openPeer: vi.fn(async () => "peer-1"),
    emitTo: vi.fn(async () => undefined),
    persistPendingRoute: vi.fn(),
    ...overrides,
  };
}

describe("action-needed navigation", () => {
  it("navigates directly in a full app window", async () => {
    const host = runtime();
    await navigateForActionNeeded("settings?tab=api-keys", host);
    expect(host.setHash).toHaveBeenCalledWith("#/settings?tab=api-keys");
    expect(host.emitTo).not.toHaveBeenCalled();
  });

  it("hands the exact route from a panel to the main window", async () => {
    const host = runtime({ fullWindow: false });
    await navigateForActionNeeded("/settings?tab=api-keys&provider=openai", host);
    expect(host.focus).toHaveBeenCalledWith("main");
    expect(host.persistPendingRoute).toHaveBeenCalledWith(
      "/settings?tab=api-keys&provider=openai",
    );
    expect(host.emitTo).toHaveBeenCalledWith(
      "main",
      "action-needed://navigate",
      "/settings?tab=api-keys&provider=openai",
    );
  });

  it("opens a peer and hands off when the main window no longer exists", async () => {
    const host = runtime({
      fullWindow: false,
      focus: vi.fn(async () => {
        throw new Error("missing");
      }),
    });
    await navigateForActionNeeded("/settings", host);
    expect(host.openPeer).toHaveBeenCalledOnce();
    expect(host.emitTo).toHaveBeenCalledWith(
      "peer-1",
      "action-needed://navigate",
      "/settings",
    );
  });
});
