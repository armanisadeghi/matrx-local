/** @vitest-environment jsdom */
/**
 * The choice card: one button per `action.choices` entry, the pick PUT to the
 * item's `choice_route` on the engine, and a refusal shown verbatim. Fails
 * against the card as it shipped before D1 (2026-09-20): it rendered one
 * dispatch button and no way to answer a choice-shaped item.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { ButtonHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ put: vi.fn() }));

vi.mock("@/lib/api", () => ({ engine: { put: mocks.put } }));
vi.mock("@ai-matrx/design-system", () => ({
  Button: ({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}));

import { ActionNeededCard } from "./ActionNeededCard";
import type { ActionNeeded } from "./types";

const item: ActionNeeded = {
  fingerprint: "coding-session:organization:required",
  code: "organization_required",
  kind: "organization",
  feature: "Coding sessions",
  title: "Your Claude Code sessions are waiting for an organization",
  message: "Pick an organization and delivery resumes by itself.",
  action: {
    kind: "choose_coding_session_organization",
    label: "Choose organization",
    choices: [
      { id: "org-a", label: "All Green", description: "AG" },
      { id: "org-b", label: "AI Matrx", description: "AM" },
    ],
    choice_route: "/coding-session/connection/organization",
  },
  source: "coding_session_bridge",
  status: "active",
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  mocks.put.mockReset();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("ActionNeededCard with choices", () => {
  it("renders one button per choice and PUTs the pick to the item's route", async () => {
    mocks.put.mockResolvedValue({ organization_id: "org-b", blocker: null });
    await act(async () => root.render(<ActionNeededCard item={item} />));

    const buttons = Array.from(
      container.querySelectorAll<HTMLButtonElement>("[data-action-needed-choice]"),
    );
    expect(buttons.map((b) => b.textContent)).toEqual(["All Green", "AI Matrx"]);
    // No generic dispatch button beside a choice list — the choices ARE the action.
    expect(container.textContent).not.toContain("Choose organization");

    await act(async () => buttons[1]!.click());
    await flush();
    expect(mocks.put).toHaveBeenCalledWith("/coding-session/connection/organization", {
      choice: "org-b",
    });
  });

  it("shows the engine's refusal verbatim and keeps the choices", async () => {
    mocks.put.mockRejectedValue(
      new Error("PUT failed: Organization org-a is not one of your memberships."),
    );
    await act(async () => root.render(<ActionNeededCard item={item} />));
    const [first] = Array.from(
      container.querySelectorAll<HTMLButtonElement>("[data-action-needed-choice]"),
    );
    await act(async () => first!.click());
    await flush();
    expect(container.querySelector("[role=alert]")?.textContent).toContain(
      "not one of your memberships",
    );
    expect(container.querySelectorAll("[data-action-needed-choice]")).toHaveLength(2);
  });
});
