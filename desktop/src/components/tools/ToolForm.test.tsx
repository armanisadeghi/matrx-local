/** @vitest-environment jsdom */
/**
 * The real ToolForm, built from the live engine catalog, submits what a person
 * types. Guards the 2026-09-30 defect: Book Capture showed "default: 0.9" and
 * then refused 0.9 ("Enter a valid value"), and optional fields with a null
 * default blocked every local_* tool from submitting at all.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import engineSchemas from "@/lib/__fixtures__/engine-tool-schemas.json";
import { fromEngineSchema } from "@/lib/tool-registry";
import type { ToolUISchema } from "@/types/tool-schema";

import { ToolForm } from "./ToolForm";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
// Radix Select measures itself; jsdom has no ResizeObserver.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

function schemaFor(name: string): ToolUISchema {
  const raw = (engineSchemas as Parameters<typeof fromEngineSchema>[0][]).find(
    (s) => s.name === name,
  )!;
  return fromEngineSchema(raw);
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function type(name: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`#${name}`)!;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function submit() {
  await act(async () => {
    host.querySelector("form")!.requestSubmit();
  });
}

describe("ToolForm", () => {
  it("submits Book Capture's decimal values, defaults included", async () => {
    const onSubmit = vi.fn();
    act(() => root.render(<ToolForm schema={schemaFor("BookCapture")} onSubmit={onSubmit} />));

    type("app_name", "Kindle");
    type("page_delay_seconds", "0.9");
    type("page_change_threshold", "0.001");
    await submit();

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]![0]).toMatchObject({
      app_name: "Kindle",
      page_delay_seconds: 0.9,
      page_change_threshold: 0.001,
    });
  });

  it("submits a local_* tool whose optional fields default to null", async () => {
    const schema = schemaFor("local_window");
    // Stand-in for picking an action from the dropdown.
    schema.fields = schema.fields.map((f) =>
      f.name === "action" ? { ...f, defaultValue: "list" } : f,
    );
    const onSubmit = vi.fn();
    act(() => root.render(<ToolForm schema={schema} onSubmit={onSubmit} />));

    await submit();

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0]![0]).toMatchObject({ action: "list" });
    expect(onSubmit.mock.calls[0]![0]).not.toHaveProperty("window_title");
  });

  it("refuses a threshold of 1 with a visible reason", async () => {
    const onSubmit = vi.fn();
    act(() => root.render(<ToolForm schema={schemaFor("BookCapture")} onSubmit={onSubmit} />));

    type("app_name", "Kindle");
    type("page_change_threshold", "1");
    await submit();

    expect(onSubmit).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Too big");
  });

  it("keeps repeated numbers in a list (a crop region of 0, 0, 800, 600)", async () => {
    const onSubmit = vi.fn();
    act(() => root.render(<ToolForm schema={schemaFor("Screenshot")} onSubmit={onSubmit} />));

    const input = host.querySelector<HTMLInputElement>("#region")!;
    for (const v of ["0", "0", "800", "600"]) {
      type("region", v);
      act(() => {
        input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      });
    }
    await submit();

    expect(onSubmit.mock.calls[0]![0]).toMatchObject({ region: [0, 0, 800, 600] });
  });

  it("says Required for a blank required text box instead of sending nothing", async () => {
    const onSubmit = vi.fn();
    act(() => root.render(<ToolForm schema={schemaFor("Read")} onSubmit={onSubmit} />));

    type("file_path", "");
    await submit();

    expect(onSubmit).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Required");
  });

  it("refuses broken JSON with a visible reason", async () => {
    const onSubmit = vi.fn();
    act(() => root.render(<ToolForm schema={schemaFor("FetchUrl")} onSubmit={onSubmit} />));

    type("url", "https://example.com");
    const box = host.querySelector<HTMLTextAreaElement>("#headers")!;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    act(() => {
      setter.call(box, "{bad");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await submit();

    expect(onSubmit).not.toHaveBeenCalled();
  });
});

