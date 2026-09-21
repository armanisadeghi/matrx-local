import { describe, expect, it, vi } from "vitest";

import {
  dispatchActionNeeded,
  navigateForActionNeeded,
  type NavigationRuntime,
} from "./actions";
import type { ActionNeeded } from "./types";

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
    persistPendingAction: vi.fn(),
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

  it("hands a complete unhandled action from a panel to a full window", async () => {
    const host = runtime({ fullWindow: false });
    const item: ActionNeeded = {
      fingerprint: "permission:camera",
      code: "camera_required",
      kind: "os_permission",
      feature: "Camera",
      title: "Camera access is needed",
      message: "Allow camera access.",
      action: {
        kind: "request_os_permission",
        label: "Allow Camera",
        permission_key: "camera",
      },
      source: "test",
      status: "active",
    };
    await dispatchActionNeeded(item, host);
    expect(host.persistPendingAction).toHaveBeenCalledWith(
      item,
      expect.any(String),
    );
    const handoffId = vi.mocked(host.persistPendingAction).mock.calls[0]?.[1];
    expect(host.emitTo).toHaveBeenCalledWith(
      "main",
      "action-needed://dispatch",
      JSON.stringify({ item, handoffId }),
    );
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

describe("action-needed choices", () => {
  const withChoices: ActionNeeded = {
    fingerprint: "coding-session:organization:required",
    code: "organization_required",
    kind: "organization",
    feature: "Coding sessions",
    title: "Your Claude Code sessions are waiting for an organization",
    message: "Pick an organization and delivery resumes by itself.",
    action: {
      kind: "choose_coding_session_organization",
      label: "Choose organization",
      route: "/coding-sessions",
      choices: [
        { id: "org-a", label: "All Green", description: "AG" },
        { id: "org-b", label: "AI Matrx", description: "AM" },
      ],
      choice_route: "/coding-session/connection/organization",
    },
    source: "coding_session_bridge",
    status: "active",
  };
  const [orgA, orgB] = withChoices.action.choices as [
    NonNullable<ActionNeeded["action"]["choices"]>[number],
    NonNullable<ActionNeeded["action"]["choices"]>[number],
  ];

  it("PUTs the chosen id to the item's choice_route on the engine", async () => {
    const { submitActionNeededChoice } = await import("./actions");
    const put = vi.fn(async () => ({}));
    await submitActionNeededChoice(withChoices, orgB, { put });
    expect(put).toHaveBeenCalledWith("/coding-session/connection/organization", {
      choice: "org-b",
    });
  });

  it("refuses to guess a route for a choice-shaped item that names none", async () => {
    const { submitActionNeededChoice } = await import("./actions");
    const put = vi.fn(async () => ({}));
    const routeless: ActionNeeded = {
      ...withChoices,
      action: { ...withChoices.action, choice_route: null },
    };
    await expect(
      submitActionNeededChoice(routeless, orgA, { put }),
    ).rejects.toThrow(/choice_route/);
    expect(put).not.toHaveBeenCalled();
  });

  it("lets the engine's refusal reach the caller verbatim", async () => {
    const { submitActionNeededChoice } = await import("./actions");
    const put = vi.fn(async () => {
      throw new Error("Organization x is not one of your memberships.");
    });
    await expect(
      submitActionNeededChoice(withChoices, orgA, { put }),
    ).rejects.toThrow("not one of your memberships");
  });
});
