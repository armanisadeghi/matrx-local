/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_AUTOSAVE_DELAY_MS } from "@/hooks/use-debounced-save";
import type { SavedPrompt } from "@/lib/saved-prompts/types";
import type { NamedList } from "@/lib/list-library/types";
import { TooltipProvider } from "@/components/ui/tooltip";

const updatePrompt = vi.fn(async () => true);
const refresh = vi.fn(async () => undefined);
const createPrompt = vi.fn(async () => null);
const deletePrompt = vi.fn(async () => true);
const duplicatePrompt = vi.fn(async () => null);
const clearError = vi.fn();

let prompts: SavedPrompt[] = [];
let lists: NamedList[] = [];

vi.mock("@/contexts/SavedPromptsContext", () => ({
  useSavedPromptsApp: () => [
    {
      prompts,
      promptsPath: null,
      loading: false,
      ready: true,
      error: null,
      saving: false,
    },
    {
      refresh,
      createPrompt,
      updatePrompt,
      deletePrompt,
      duplicatePrompt,
      clearError,
    },
  ],
}));

vi.mock("@/contexts/ListLibraryContext", () => ({
  useListLibraryApp: () => [
    {
      lists,
      listsPath: null,
      loading: false,
      ready: true,
      error: null,
      saving: false,
    },
    {},
  ],
}));

import { SavedPromptsCore } from "./SavedPromptsCore";

function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("SavedPromptsCore autosave", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    vi.clearAllMocks();
    prompts = [
      {
        id: "prompt-1",
        name: "First",
        prompt: "A test prompt",
        negativePrompt: "",
        createdAt: 1,
        updatedAt: 1,
      },
    ];
    lists = [
      {
        id: "camera-angles",
        name: "Camera angles",
        description: "",
        options: [
          { id: "wide", value: "wide shot", enabled: true },
        ],
        createdAt: 1,
        updatedAt: 1,
      },
    ];
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("does not replace a live title draft with a trimmed save response", async () => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <SavedPromptsCore showStoragePath={false} />
        </TooltipProvider>,
      );
    });

    const nameInput = container.querySelector<HTMLInputElement>("#prompt-name");
    expect(nameInput).not.toBeNull();

    await act(async () => {
      setInputValue(nameInput!, "First ");
    });
    expect(nameInput!.value).toBe("First ");
    expect(updatePrompt).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(400);
      await Promise.resolve();
    });
    expect(updatePrompt).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(DEFAULT_AUTOSAVE_DELAY_MS - 400);
      await Promise.resolve();
    });
    expect(updatePrompt).toHaveBeenLastCalledWith("prompt-1", {
      name: "First ",
      prompt: "A test prompt",
      negativePrompt: "",
    });

    // Simulate a persistence response/store refresh that normalized the name.
    prompts = [{ ...prompts[0]!, name: "First", updatedAt: 2 }];
    await act(async () => {
      root.render(
        <TooltipProvider>
          <SavedPromptsCore showStoragePath={false} />
        </TooltipProvider>,
      );
    });
    expect(nameInput!.value).toBe("First ");

    await act(async () => {
      setInputValue(nameInput!, "First second");
      vi.advanceTimersByTime(DEFAULT_AUTOSAVE_DELAY_MS);
      await Promise.resolve();
    });
    expect(updatePrompt).toHaveBeenLastCalledWith("prompt-1", {
      name: "First second",
      prompt: "A test prompt",
      negativePrompt: "",
    });
  });

  it("inserts a saved-list variable at the selection and previews its value", async () => {
    await act(async () => {
      root.render(
        <TooltipProvider>
          <SavedPromptsCore showStoragePath={false} />
        </TooltipProvider>,
      );
    });

    const textarea = container.querySelector<HTMLTextAreaElement>("#prompt-text");
    expect(textarea).not.toBeNull();
    await act(async () => {
      textarea!.focus();
      textarea!.setSelectionRange(2, 6);
      textarea!.dispatchEvent(new Event("select", { bubbles: true }));
    });

    const insertButton = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("Insert variable"),
    );
    expect(insertButton).toBeDefined();
    insertButton!.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, cancelable: true }),
    );
    await act(async () => insertButton!.click());

    const listButton = [...document.body.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("Camera angles"),
    );
    expect(listButton).toBeDefined();
    await act(async () => listButton!.click());

    expect(textarea!.value).toBe("A {{Camera angles}} prompt");
    expect(updatePrompt).not.toHaveBeenCalled();

    const previewButton = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.includes("Test with list values"),
    );
    expect(previewButton).toBeDefined();
    await act(async () => previewButton!.click());

    expect(container.textContent).toContain("A wide shot prompt");
    const dynamic = [...container.querySelectorAll("span")].find(
      (span) => span.textContent === "wide shot",
    );
    expect(dynamic?.className).toContain("text-violet");
  });
});
