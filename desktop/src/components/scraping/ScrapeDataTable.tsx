/** Read-only materialized tables from a scrape. */

import { useMemo, useState } from "react";
import {
  MatrxDataTable,
  type MatrxColumnDef,
  type MatrxDataTableQueryState,
} from "@ai-matrx/design-system/data-table";
import type { ScrapeTable } from "@/lib/scrape-extraction";
import {
  isWholeColumnNumeric,
  materializeScrapeTableRows,
  parseScrapeNumber,
  type ScrapeTableRow,
} from "./scrape-data-table-adapter";

const INITIAL_QUERY: MatrxDataTableQueryState = {
  page: 1,
  pageSize: 0,
  search: "",
  anyOf: "",
  columnFilters: {},
  sort: null,
};

export function ScrapeDataTable({
  table,
  index,
}: {
  table: ScrapeTable;
  index: number;
}) {
  const [query, setQuery] = useState<MatrxDataTableQueryState>(INITIAL_QUERY);
  const rows = useMemo(() => materializeScrapeTableRows(table, index), [index, table]);
  const columns = useMemo<MatrxColumnDef<ScrapeTableRow>[]>(() =>
    table.columns.map((label, column) => {
      const numeric = isWholeColumnNumeric(rows, column);
      return {
        id: `column-${column}`,
        header: label,
        label,
        accessorFn: (row) => row.cells[column] ?? "",
        // The prior renderer treated blank numeric cells as zero while sorting.
        sortValue: numeric
          ? (row) => parseScrapeNumber(row.cells[column] ?? "") ?? 0
          : (row) => row.cells[column] ?? "",
        cell: (row) => (
          <span className="block max-w-[28rem] whitespace-pre-wrap break-words leading-relaxed">
            {row.cells[column] ?? ""}
          </span>
        ),
      };
    }),
  [rows, table.columns]);
  const truncatedRows = table.rowsTotal > rows.length;

  return (
    <section className="min-w-0 rounded-lg border p-3" aria-label={`Table ${index + 1}`}>
      <div className="h-[min(28rem,65dvh)] min-h-52">
        <MatrxDataTable
          data={rows}
          columns={columns}
          getRowId={(row) => row.id}
          query={{
            mode: "controlled-local",
            state: query,
            onStateChange: setQuery,
            sourceProcessing: { sourceTotal: table.rowsTotal },
          }}
          hidePagination
          detail={{ enabled: false }}
          window={{ enabled: false }}
          zebra
          toolbar={{ title: `Table ${index + 1}`, searchPlaceholder: "Search this table" }}
          copy={{
            label: `Table ${index + 1}`,
            listLabel: `Table ${index + 1}`,
            location: "Scrape result",
            rowKind: "scrape-table-row",
            listKind: "scrape-table-view",
            humanRow: (row) => row.cells.join(" | "),
            agentRow: (row) => Object.fromEntries(
              table.columns.map((label, column) => [`${column}:${label}`, row.cells[column] ?? ""]),
            ),
            listContext: () => ({
              materialized_rows: rows.length,
              engine_rows_total: table.rowsTotal,
              coverage: truncatedRows ? "engine-capped-materialized-slice" : "complete-materialized-result",
            }),
          }}
        />
      </div>
      {truncatedRows && (
        <p className="pt-2 text-xs text-muted-foreground">
          Showing the first {table.rows.length} of {table.rowsTotal} rows — the
          engine caps table rows so a single huge table cannot stall the app.
        </p>
      )}
    </section>
  );
}
