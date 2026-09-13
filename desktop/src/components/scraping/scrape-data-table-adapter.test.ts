import { describe, expect, it } from "vitest";
import type { ScrapeTable } from "@/lib/scrape-extraction";
import {
  isWholeColumnNumeric,
  materializeScrapeTableRows,
  parseScrapeNumber,
} from "./scrape-data-table-adapter";

const table: ScrapeTable = {
  columns: ["Amount", "Amount"],
  rows: [["$1,299.00", "A"], ["(12)", "A"], ["", "B"], ["45%", "B"]],
  rowsTotal: 9,
};

describe("scrape data-table adapter", () => {
  it("keeps duplicate source rows distinct and records original source order", () => {
    const rows = materializeScrapeTableRows(table, 2);
    expect(rows.map((row) => row.id)).toEqual([
      "scrape-table-2-row-0",
      "scrape-table-2-row-1",
      "scrape-table-2-row-2",
      "scrape-table-2-row-3",
    ]);
    expect(rows.map((row) => row.cells[1])).toEqual(["A", "A", "B", "B"]);
  });

  it("detects whole-column currency, percent, parentheses, and blanks as numeric", () => {
    const rows = materializeScrapeTableRows(table, 0);
    expect(isWholeColumnNumeric(rows, 0)).toBe(true);
    expect(isWholeColumnNumeric(rows, 1)).toBe(false);
    expect(rows.map((row) => parseScrapeNumber(row.cells[0] ?? "") ?? 0)).toEqual([1299, -12, 0, 45]);
  });
});
