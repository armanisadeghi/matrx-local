/** @vitest-environment jsdom */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import type { ScrapeTable } from "@/lib/scrape-extraction";
import { ScrapeDataTable } from "./ScrapeDataTable";

const table: ScrapeTable = {
  columns: ["Amount", "Amount"],
  rows: [["$1,299.00", "first"], ["(12)", "second"], ["", "third"], ["45%", "fourth"]],
  rowsTotal: 9,
};

describe("ScrapeDataTable", () => {
  let root: Root | undefined;
  let node: HTMLDivElement | undefined;

  afterEach(async () => {
    await act(async () => root?.unmount());
    node?.remove();
    root = undefined;
    node = undefined;
  });

  it("uses the canonical table for sorting, searching, and Alchemy transfer", async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div");
    document.body.append(node);
    root = createRoot(node);
    await act(async () => { root!.render(<ScrapeDataTable table={table} index={0} />); });

    expect(node.querySelector("[data-matrx-table]")).not.toBeNull();
    expect(node.textContent).toContain("Table 1");
    expect(node.querySelector('input[placeholder="Search this table"]')).not.toBeNull();
    expect(node.querySelector('button[aria-label="Copy, transform or export Table 1"]')).not.toBeNull();
    expect(node.textContent).toContain("Showing the first 4 of 9 rows");

    const amountHeaders = [...node.querySelectorAll("button")].filter(
      (button) => button.textContent?.trim() === "Amount",
    );
    expect(amountHeaders).toHaveLength(2);
    const amountHeader = amountHeaders[0];
    await act(async () => { amountHeader!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    const sortedRows = [...node.querySelectorAll("tbody tr")].map((row) => row.textContent);
    expect(sortedRows).toEqual(expect.arrayContaining([expect.stringContaining("(12)"), expect.stringContaining("$1,299.00")]));
    expect(sortedRows[0]).toContain("(12)");
    expect(sortedRows[sortedRows.length - 1]).toContain("$1,299.00");

    await act(async () => { amountHeader!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await act(async () => { amountHeader!.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(node.querySelector("tbody")?.textContent).toContain("$1,299.00first");

    const search = node.querySelector<HTMLInputElement>('input[placeholder="Search this table"]')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(search, "fourth");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(node.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(node.querySelector("tbody")?.textContent).toContain("fourth");
  });
});
