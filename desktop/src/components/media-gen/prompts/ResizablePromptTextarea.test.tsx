/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  PROMPT_TEXTAREA_MIN_ROWS,
  ResizablePromptTextarea,
} from "./ResizablePromptTextarea";

const TEST_STORAGE_KEY = "resize-test";
const PERSISTED_KEY = `matrx-media-prompt-height:${TEST_STORAGE_KEY}`;

function dispatchPointer(
  element: HTMLElement,
  type: "pointerdown" | "pointermove" | "pointerup",
  clientY: number,
): void {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    clientY: { value: clientY },
    pointerId: { value: 7 },
  });
  element.dispatchEvent(event);
}

describe("ResizablePromptTextarea", () => {
  let container: HTMLDivElement;
  let root: Root;
  let storage: Map<string, string>;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    storage = new Map();
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: {
        clear: () => storage.clear(),
        getItem: (key: string) => storage.get(key) ?? null,
        removeItem: (key: string) => storage.delete(key),
        setItem: (key: string, value: string) => storage.set(key, value),
      },
    });
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderEditor(): Promise<{
    textarea: HTMLTextAreaElement;
    handle: HTMLDivElement;
  }> {
    await act(async () => {
      root.render(
        <ResizablePromptTextarea
          resizeStorageKey={TEST_STORAGE_KEY}
          aria-label="Test prompt"
        />,
      );
    });
    return {
      textarea: container.querySelector("textarea")!,
      handle: container.querySelector('[role="separator"]')!,
    };
  }

  it("starts at ten rows and exposes a full-width accessible resize grip", async () => {
    const { textarea, handle } = await renderEditor();

    expect(textarea.style.height).toBe("258px");
    expect(handle.getAttribute("aria-valuemin")).toBe(
      String(PROMPT_TEXTAREA_MIN_ROWS * 24 + 18),
    );
    expect(handle.className).toContain("w-full");
    expect(handle.tabIndex).toBe(0);
  });

  it("resizes by keyboard, persists the height, and restores it", async () => {
    const { textarea, handle } = await renderEditor();

    await act(async () => {
      handle.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
      );
    });
    expect(textarea.style.height).toBe("282px");
    expect(localStorage.getItem(PERSISTED_KEY)).toBe("282");

    await act(async () => {
      root.unmount();
    });
    root = createRoot(container);
    const restored = await renderEditor();
    expect(restored.textarea.style.height).toBe("282px");

    await act(async () => {
      restored.handle.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Home", bubbles: true }),
      );
    });
    expect(restored.textarea.style.height).toBe("258px");
    expect(localStorage.getItem(PERSISTED_KEY)).toBe("258");
  });

  it("supports pointer dragging and clamps at five rows", async () => {
    const { textarea, handle } = await renderEditor();
    let captured = false;
    handle.setPointerCapture = () => {
      captured = true;
    };
    handle.hasPointerCapture = () => captured;
    handle.releasePointerCapture = () => {
      captured = false;
    };

    await act(async () => {
      dispatchPointer(handle, "pointerdown", 300);
      dispatchPointer(handle, "pointermove", 40);
      dispatchPointer(handle, "pointerup", 40);
    });

    expect(textarea.style.height).toBe("138px");
    expect(localStorage.getItem(PERSISTED_KEY)).toBe("138");
    expect(captured).toBe(false);
  });

  it("resets to the configured default on double-click", async () => {
    const { textarea, handle } = await renderEditor();

    await act(async () => {
      handle.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "ArrowDown",
          shiftKey: true,
          bubbles: true,
        }),
      );
      handle.dispatchEvent(
        new MouseEvent("dblclick", { bubbles: true, cancelable: true }),
      );
    });

    expect(textarea.style.height).toBe("258px");
  });
});
