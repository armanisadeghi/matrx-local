/** @vitest-environment jsdom */
/**
 * The system-prompt picker tracks the chosen prompt by its id, never by
 * comparing text: a built-in whose Holder text has a variable is filled by the
 * engine when picked, so its run text differs from the listed template — and
 * the picker must still show the built-in's name, not "custom".
 */
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const BUILTIN = {
  id: "builtin-summarize",
  name: "Summarize",
  category: "Writing",
  mandateKey: "local.chat_persona_summarize",
  content: "Summarize for {{audience}}.",
};

vi.mock("@/lib/system-prompts", () => ({
  systemPrompts: { list: () => [] },
  builtinPrompts: () => [BUILTIN],
  builtinPromptRunText: vi.fn(async () => "Summarize for ."),
}));

import { SystemPromptSelector } from "@/components/llm/SystemPromptSelector";

function Harness() {
  const [value, setValue] = useState("");
  const [id, setId] = useState<string | null>(null);
  return (
    <SystemPromptSelector
      value={value}
      selectedId={id}
      onChange={(content: string, nextId?: string | null) => {
        setValue(content);
        setId(nextId ?? null);
      }}
    />
  );
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("SystemPromptSelector", () => {
  it("shows a picked built-in's name after the engine fills its variable", async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });
    const select = host.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = BUILTIN.id;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });

    const textarea = host.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Summarize for .");
    expect(select.value).toBe(BUILTIN.id);
  });

  it("an edit by hand makes the prompt the person's own (custom)", async () => {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });
    const select = host.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      select.value = BUILTIN.id;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const textarea = host.querySelector("textarea") as HTMLTextAreaElement;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      setter.call(textarea, "My own words");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(select.value).toBe("__custom__");
  });
});
