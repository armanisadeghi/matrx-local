import type { ScrapeTable } from "@/lib/scrape-extraction";

export type ScrapeTableRow = {
  id: string;
  sourceIndex: number;
  cells: readonly string[];
};

/** Parse numeric notation without changing the displayed scrape value. */
export function parseScrapeNumber(value: string): number | null {
  const cleaned = value.replace(/[\s,$%]/g, "").replace(/^\((.*)\)$/, "-$1");
  if (!cleaned) return null;
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : null;
}

export function isWholeColumnNumeric(rows: readonly ScrapeTableRow[], column: number): boolean {
  return rows.every((row) => {
    const value = row.cells[column] ?? "";
    return value === "" || parseScrapeNumber(value) !== null;
  });
}

/** Duplicate source rows remain distinct, even after the table changes order. */
export function materializeScrapeTableRows(table: ScrapeTable, tableIndex: number): ScrapeTableRow[] {
  return table.rows.map((cells, sourceIndex) => ({
    id: `scrape-table-${tableIndex}-row-${sourceIndex}`,
    sourceIndex,
    cells,
  }));
}
