/** @vitest-environment jsdom */
/**
 * design-system 0.38.0 made a desktop Dialog a window, not a wall: unless the
 * caller passes `modal` it no longer blocks the page, and a click outside no
 * longer closes it. This host adopts it per dialog: a confirmation BLOCKS
 * (`modal`), a quick picker CLOSES on a click outside (`dismissOnOutsideClick`).
 * These render the real components on the real package so a later package
 * change that breaks either contract turns this red.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/system-prompts", () => ({
  systemPrompts: {
    list: () => [],
    categories: () => [],
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  },
  builtinPrompts: () => [],
  refreshBuiltinPrompts: vi.fn(async () => undefined),
}));

import { BatchConfirmDialog } from "@/components/media-gen/core/PromptMatrix/BatchConfirmDialog";
import { PromptPicker } from "@/components/PromptPicker";
import type { MatrixPlan } from "@/lib/prompt-matrix";

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // A desktop-width window: the package decides "phone" from this query.
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  document.body.innerHTML = "";
});

function outsideClick() {
  const target = document.body;
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, button: 0 }));
  }
}

describe("dialog modality under design-system 0.38.0", () => {
  it("the batch confirmation blocks the page", () => {
    const combo = { label: "a", rendered: { prompt: "p" }, seed: 1 };
    const plan = {
      combinations: [combo, combo],
      total: 2,
      truncated: false,
      errors: [],
      warnings: [],
    } as unknown as MatrixPlan;
    act(() =>
      root.render(
        <BatchConfirmDialog
          open
          onOpenChange={() => {}}
          plan={plan}
          secondsPerRun={null}
          queuedAhead={0}
          submitting={false}
          onConfirm={() => {}}
        />,
      ),
    );
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
  });

  it("the prompt picker is a window that a click outside closes", async () => {
    act(() => root.render(<PromptPicker onSelect={() => {}} />));
    const trigger = host.querySelector("button");
    expect(trigger).not.toBeNull();
    await act(async () => trigger!.click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute("aria-modal")).not.toBe("true");
    // Radix arms its outside-pointer listener on the next tick after opening.
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    await act(async () => outsideClick());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});
