/**
 * A scraped table, rendered as a real table.
 *
 * The scraper returns tables as `{columns, rows}` — until now the Scraping
 * page flattened them into the text blob, where a price list became an
 * unreadable run of words. Here the user can sort by any column and copy the
 * whole thing out as TSV (paste-ready for a spreadsheet) or as Markdown.
 *
 * Sorting is numeric when the whole column parses as numbers, otherwise it is
 * a locale string compare — a "Price" column must not sort 10 before 9.
 */

import { useCallback, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Check, ChevronsUpDown, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ScrapeTable } from "@/lib/scrape-extraction";

type SortDirection = "asc" | "desc";

function parseNumber(value: string): number | null {
  // Tolerate the punctuation real tables carry: $1,299.00 / 45% / (12)
  const cleaned = value.replace(/[\s,$%]/g, "").replace(/^\((.*)\)$/, "-$1");
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

function toTsv(table: ScrapeTable, rows: string[][]): string {
  const escape = (cell: string) => cell.replace(/\t/g, " ").replace(/\n/g, " ");
  return [
    table.columns.map(escape).join("\t"),
    ...rows.map((row) => row.map(escape).join("\t")),
  ].join("\n");
}

function toMarkdown(table: ScrapeTable, rows: string[][]): string {
  const escape = (cell: string) => cell.replace(/\|/g, "\\|").replace(/\n/g, " ");
  return [
    `| ${table.columns.map(escape).join(" | ")} |`,
    `| ${table.columns.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${row.map(escape).join(" | ")} |`),
  ].join("\n");
}

function CopyAs({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      })
      .catch(() => undefined);
  }, [text]);
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={copy}
      className={cn("h-6 gap-1 px-1.5 text-[10px]", copied && "text-emerald-500")}
      title={`Copy this table as ${label}`}
    >
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : label}
    </Button>
  );
}

export function ScrapeDataTable({
  table,
  index,
}: {
  table: ScrapeTable;
  index: number;
}) {
  const [sort, setSort] = useState<{ column: number; direction: SortDirection } | null>(
    null,
  );

  const rows = useMemo(() => {
    if (!sort) return table.rows;
    const { column, direction } = sort;
    const values = table.rows.map((row) => row[column] ?? "");
    const numeric = values.every((v) => v === "" || parseNumber(v) !== null);
    const sorted = [...table.rows].sort((a, b) => {
      const left = a[column] ?? "";
      const right = b[column] ?? "";
      if (numeric) {
        return (parseNumber(left) ?? 0) - (parseNumber(right) ?? 0);
      }
      return left.localeCompare(right, undefined, { numeric: true });
    });
    return direction === "asc" ? sorted : sorted.reverse();
  }, [table.rows, sort]);

  const toggleSort = useCallback((column: number) => {
    setSort((prev) => {
      if (!prev || prev.column !== column) return { column, direction: "asc" };
      if (prev.direction === "asc") return { column, direction: "desc" };
      return null; // third click restores the page's own order
    });
  }, []);

  const truncatedRows = table.rowsTotal > table.rows.length;

  return (
    <div className="min-w-0 rounded-lg border">
      <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
        <span className="text-[11px] font-semibold">Table {index + 1}</span>
        <span className="text-[10px] text-muted-foreground">
          {table.columns.length} cols ·{" "}
          {truncatedRows
            ? `${table.rows.length} of ${table.rowsTotal} rows`
            : `${table.rows.length} rows`}
        </span>
        <div className="ml-auto flex items-center gap-0.5">
          <CopyAs label="TSV" text={toTsv(table, rows)} />
          <CopyAs label="Markdown" text={toMarkdown(table, rows)} />
        </div>
      </div>
      {/* Wide tables scroll inside their own box — the page never scrolls
          sideways because one page had a 40-column table. */}
      <div className="max-h-[28rem] overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-background">
            <tr>
              {table.columns.map((column, i) => {
                const active = sort?.column === i;
                return (
                  <th
                    key={`${column}-${i}`}
                    scope="col"
                    className="border-b bg-muted/50 p-0 text-left align-bottom"
                  >
                    <button
                      type="button"
                      onClick={() => toggleSort(i)}
                      className="flex w-full items-center gap-1 px-2.5 py-1.5 text-left text-[11px] font-semibold hover:bg-accent/60"
                      title={`Sort by ${column}`}
                      aria-label={`Sort by ${column}`}
                    >
                      <span className="min-w-0 break-words">{column}</span>
                      {active ? (
                        sort.direction === "asc" ? (
                          <ArrowUp className="h-3 w-3 shrink-0 text-primary" />
                        ) : (
                          <ArrowDown className="h-3 w-3 shrink-0 text-primary" />
                        )
                      ) : (
                        <ChevronsUpDown className="h-3 w-3 shrink-0 opacity-25" />
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="even:bg-muted/20">
                {table.columns.map((_, c) => (
                  <td
                    key={c}
                    className="max-w-[28rem] border-b border-border/40 px-2.5 py-1.5 align-top leading-relaxed"
                  >
                    <span className="block whitespace-pre-wrap break-words">
                      {row[c] ?? ""}
                    </span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncatedRows && (
        <p className="border-t px-3 py-1.5 text-[10px] text-muted-foreground">
          Showing the first {table.rows.length} of {table.rowsTotal} rows — the
          engine caps table rows so a single huge table can't stall the app.
        </p>
      )}
    </div>
  );
}
