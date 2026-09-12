import { useEffect, useState } from "react";
import { Copy } from "lucide-react";
import { ArchiveFilter, Badge, Button, DEFAULT_ARCHIVE_FILTER } from "@ai-matrx/design-system";
import { MatrxDataTable, type MatrxColumnDef, type MatrxDataTableQueryState } from "@ai-matrx/design-system/data-table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { engine } from "@/lib/api";
import type { ArchiveFilterValue, ClaudeHistoryChangeType, ClaudeHistoryInventoryPage, ClaudeHistoryInventoryRow, ClaudeHistoryReview } from "@/lib/api";
import { formatCount, formatFileSize } from "@ai-matrx/kit/format";
import { capHistorySelection, inventoryQueryChanged, inventoryRequestFilters, inventorySortFromTableQuery } from "./history-inventory-table-adapter";

const INITIAL_QUERY: MatrxDataTableQueryState = { page: 1, pageSize: 50, search: "", anyOf: "", columnFilters: {}, sort: { id: "modified", direction: "desc" } };

function keyOf(session: { project_key: string; session_id: string }): string {
  return `${session.project_key}:${session.session_id}`;
}

function changeLabel(change: ClaudeHistoryChangeType) {
  switch (change) {
    case "new": return <Badge>New</Badge>;
    case "content_changed": return <Badge variant="secondary">Transcript changed</Badge>;
    case "metadata_changed": return <Badge variant="secondary">Details changed</Badge>;
    case "missing": return <Badge variant="destructive">Missing locally</Badge>;
    default: return <Badge variant="outline">Unchanged</Badge>;
  }
}

export function HistoryInventoryTable({ review, selected, onSelectedChange, onPageRowsChange, focusFilter, disabled }: {
  review: ClaudeHistoryReview;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onPageRowsChange: (page: ClaudeHistoryInventoryPage) => void;
  focusFilter?: { token: number; change?: ClaudeHistoryChangeType; availability?: "all" | "available" | "blocked" };
  disabled?: boolean;
}) {
  const [pageData, setPageData] = useState<ClaudeHistoryInventoryPage>(review);
  const [tableQuery, setTableQuery] = useState<MatrxDataTableQueryState>(INITIAL_QUERY);
  const [availability, setAvailability] = useState("all");
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilterValue>(DEFAULT_ARCHIVE_FILTER);
  const [changeFilter, setChangeFilter] = useState("all");
  const [cursors, setCursors] = useState<Array<string | undefined>>([undefined]);
  const [pageIndex, setPageIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null);
  const resetPaging = () => { setCursors([undefined]); setPageIndex(0); };

  useEffect(() => {
    setPageData(review);
    resetPaging();
    onPageRowsChange(review);
  }, [review.scan.scan_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!focusFilter) return;
    setChangeFilter(focusFilter.change ?? "all");
    setAvailability(focusFilter.availability ?? "all");
    resetPaging();
  }, [focusFilter?.token]); // eslint-disable-line react-hooks/exhaustive-deps

  const sourceSort = inventorySortFromTableQuery(tableQuery);
  useEffect(() => {
    const firstReviewPage = pageIndex === 0 && !tableQuery.search && availability === "all" && changeFilter === "all" && archiveFilter === DEFAULT_ARCHIVE_FILTER && sourceSort.sortKey === "modified" && sourceSort.direction === "desc" && tableQuery.pageSize >= review.items.length;
    if (firstReviewPage) return;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void engine.getClaudeHistoryInventoryPage(review.scan.scan_id, inventoryRequestFilters({
        cursor: cursors[pageIndex], limit: tableQuery.pageSize, query: tableQuery.search,
        changeFilter, availability, archiveFilter, ...sourceSort,
      })).then((next) => {
        setPageData(next);
        onPageRowsChange(next);
        onSelectedChange(new Set());
      }).catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError))).finally(() => setLoading(false));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [archiveFilter, availability, changeFilter, cursors, onPageRowsChange, onSelectedChange, pageIndex, review.items.length, review.scan.scan_id, sourceSort.direction, sourceSort.sortKey, tableQuery.pageSize, tableQuery.search]);

  const selectedBytes = pageData.items.filter((row) => selected.has(keyOf(row))).reduce((sum, row) => sum + row.bytes, 0);
  const selectableIds = new Set(pageData.items.filter((row) => row.present && row.import_available).map(keyOf));
  const columns: MatrxColumnDef<ClaudeHistoryInventoryRow>[] = [
    { id: "title", header: "Session", sortable: true, filter: false, sortValue: (row) => row.title, cell: (row) => <><div className="max-w-80 truncate font-medium">{row.title}</div><div className="font-mono text-[11px] text-muted-foreground">{row.session_id}</div></> },
    { id: "project", header: "Project", sortable: true, filter: false, sortValue: (row) => row.project_name, cell: (row) => <><div>{row.project_name}</div><div className="text-xs text-muted-foreground">{[row.git_branch, row.worktree_name].filter(Boolean).join(" · ") || "No branch metadata"}</div></> },
    { id: "change", header: "Change", sortable: true, filter: false, sortValue: (row) => row.change_type, cell: (row) => changeLabel(row.change_type) },
    { id: "modified", header: "Modified", sortable: true, filter: false, sortValue: (row) => row.last_modified_ns, cell: (row) => <span className="whitespace-nowrap">{new Date(row.last_modified_ns / 1_000_000).toLocaleString()}</span> },
    { id: "bytes", header: "Size", sortable: true, filter: false, sortValue: (row) => row.bytes, cell: (row) => <span className="whitespace-nowrap">{formatFileSize(row.bytes)}<span className="block text-xs text-muted-foreground">{row.file_count} file{row.file_count === 1 ? "" : "s"}</span></span> },
    { id: "import", header: "Import", sortable: false, filter: false, mobileHidden: true, cell: (row) => <>{row.import_available ? <Badge variant="outline">Ready</Badge> : <Badge variant="destructive">Blocked</Badge>}{!row.import_available && <div className="mt-1 max-w-52 text-xs text-muted-foreground">{row.import_blocked_reason ?? "No reason reported"}</div>}</> },
  ];
  const onTableQueryChange = (next: MatrxDataTableQueryState) => {
    const cursorQuery = { ...next, page: 1 };
    if (inventoryQueryChanged(tableQuery, cursorQuery)) resetPaging();
    setTableQuery(cursorQuery);
  };
  const nextPage = () => {
    if (!pageData.page.next_cursor) return;
    const next = [...cursors];
    next[pageIndex + 1] = pageData.page.next_cursor;
    setCursors(next);
    setPageIndex(pageIndex + 1);
  };
  const copyResume = async (sessionId: string) => {
    try {
      await navigator.clipboard.writeText(`claude --resume ${sessionId}`);
      setCopyFeedback(`Copied resume command for ${sessionId}`);
    } catch (reason) {
      setError(reason instanceof Error ? `Could not copy resume command: ${reason.message}` : "Could not copy resume command.");
    }
  };

  return <div className="space-y-3">
    {error && <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</div>}
    {copyFeedback && <div className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground" role="status">{copyFeedback}</div>}
    <MatrxDataTable
      data={pageData.items}
      columns={columns}
      getRowId={keyOf}
      isLoading={loading && pageData.items.length === 0}
      isFetching={loading && pageData.items.length > 0}
      query={{ mode: "controlled", state: tableQuery, totalItems: pageData.page.total, onStateChange: onTableQueryChange }}
      hidePagination
      detail={{ enabled: false }}
      emptyState={{ title: "No reviewed sessions match these filters." }}
      toolbar={{
        searchPlaceholder: "Search every reviewed title, project, branch, or session ID",
        leading: <>
          <Select value={availability} onValueChange={(value) => { setAvailability(value); resetPaging(); }}><SelectTrigger className="w-40"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All availability</SelectItem><SelectItem value="available">Importable</SelectItem><SelectItem value="blocked">Blocked</SelectItem></SelectContent></Select>
          <Select value={changeFilter} onValueChange={(value) => { setChangeFilter(value); resetPaging(); }}><SelectTrigger className="w-44"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All present sessions</SelectItem><SelectItem value="new">New</SelectItem><SelectItem value="content_changed">Transcript changed</SelectItem><SelectItem value="metadata_changed">Details changed</SelectItem><SelectItem value="missing">Missing locally</SelectItem><SelectItem value="unchanged">Unchanged</SelectItem></SelectContent></Select>
          <ArchiveFilter value={archiveFilter} onValueChange={(value) => { setArchiveFilter(value); resetPaging(); }} counts={pageData.archive_counts} labels={{ active: "Active", archived: "Archived", all: "All" }} aria-label="Show archived sessions" disabled={disabled ?? false} />
        </>,
      }}
      selection={{
        selectedIds: [...selected],
        onSelectedIdsChange: (ids) => onSelectedChange(capHistorySelection(ids, selected, selectableIds, review.limits.selected_sessions)),
        isRowSelectable: (row) => !disabled && row.present && row.import_available,
        noun: "session",
      }}
      rowActions={(row) => <Button variant="ghost" size="sm" onClick={() => void copyResume(row.session_id)}><Copy className="mr-1 h-3.5 w-3.5" />Resume</Button>}
    />
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm"><span className="text-muted-foreground">Page {pageIndex + 1} · {pageData.page.returned} rows · {formatCount(pageData.page.total)} match this view · {selected.size} selected ({formatFileSize(selectedBytes)} on this page)</span><div className="flex items-center gap-2"><Select value={String(tableQuery.pageSize)} onValueChange={(value) => { onTableQueryChange({ ...tableQuery, pageSize: Number(value) }); }}><SelectTrigger className="w-28"><SelectValue /></SelectTrigger><SelectContent>{[25, 50, 100, 200].map((size) => <SelectItem key={size} value={String(size)}>{size} rows</SelectItem>)}</SelectContent></Select><Button variant="outline" size="sm" disabled={pageIndex === 0 || loading} onClick={() => setPageIndex((value) => value - 1)}>Previous</Button><Button variant="outline" size="sm" disabled={!pageData.page.has_more || loading} onClick={nextPage}>Next</Button></div></div>
  </div>;
}
