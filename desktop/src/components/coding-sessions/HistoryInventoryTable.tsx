import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, Copy, Loader2, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { engine } from "@/lib/api";
import type { ClaudeHistoryChangeType, ClaudeHistoryInventoryPage, ClaudeHistoryReview } from "@/lib/api";

type SortKey = "modified" | "title" | "project" | "bytes" | "change";
type SortDirection = "asc" | "desc";

export function historyReviewCounts(review: ClaudeHistoryReview) {
  return {
    new: review.scan.new_count,
    contentChanged: review.scan.content_changed_count,
    metadataChanged: review.scan.metadata_changed_count,
    missing: review.scan.missing_count,
    unchanged: review.scan.unchanged_count,
    blocked: review.scan.blocked_count,
  };
}

function keyOf(session: { project_key: string; session_id: string }): string {
  return `${session.project_key}:${session.session_id}`;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
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

export function HistoryInventoryTable({
  review,
  selected,
  onSelectedChange,
  onPageRowsChange,
  focusFilter,
  disabled,
}: {
  review: ClaudeHistoryReview;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onPageRowsChange: (page: ClaudeHistoryInventoryPage) => void;
  focusFilter?: { token: number; change?: ClaudeHistoryChangeType; availability?: "all" | "available" | "blocked" };
  disabled?: boolean;
}) {
  const [pageData, setPageData] = useState<ClaudeHistoryInventoryPage>(review);
  const [query, setQuery] = useState("");
  const [availability, setAvailability] = useState("all");
  const [changeFilter, setChangeFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("modified");
  const [direction, setDirection] = useState<SortDirection>("desc");
  const [pageSize, setPageSize] = useState(50);
  const [cursors, setCursors] = useState<Array<string | undefined>>([undefined]);
  const [pageIndex, setPageIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null);

  useEffect(() => {
    setPageData(review);
    setCursors([undefined]);
    setPageIndex(0);
    onPageRowsChange(review);
  }, [review.scan.scan_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!focusFilter) return;
    setChangeFilter(focusFilter.change ?? "all");
    setAvailability(focusFilter.availability ?? "all");
    setCursors([undefined]);
    setPageIndex(0);
  }, [focusFilter?.token]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const firstReviewPage = pageIndex === 0 && !query && availability === "all" && changeFilter === "all" && sortKey === "modified" && direction === "desc" && pageSize >= review.items.length;
    if (firstReviewPage) return;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      const changeTypes = changeFilter === "all" ? undefined : [changeFilter as ClaudeHistoryChangeType];
      void engine.getClaudeHistoryInventoryPage(review.scan.scan_id, {
        ...(cursors[pageIndex] ? { cursor: cursors[pageIndex] } : {}),
        limit: pageSize,
        ...(query.trim() ? { search: query.trim() } : {}),
        ...(changeTypes ? { changeTypes } : {}),
        ...(availability === "all" ? {} : { importable: availability === "available" }),
        includeMissing: changeFilter === "missing",
        sort: sortKey,
        direction,
      }).then((next) => {
        setPageData(next);
        onPageRowsChange(next);
        onSelectedChange(new Set());
      }).catch((nextError) => setError(nextError instanceof Error ? nextError.message : String(nextError))).finally(() => setLoading(false));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [availability, changeFilter, cursors, direction, onPageRowsChange, onSelectedChange, pageIndex, pageSize, query, review.items.length, review.scan.scan_id, sortKey]);

  const selectedBytes = useMemo(() => pageData.items.filter((row) => selected.has(keyOf(row))).reduce((sum, row) => sum + row.bytes, 0), [pageData.items, selected]);
  const selectable = pageData.items.filter((row) => row.present && row.import_available);
  const allSelected = selectable.length > 0 && selectable.every((row) => selected.has(keyOf(row)));

  const resetPaging = () => { setCursors([undefined]); setPageIndex(0); };
  const sort = (key: SortKey) => {
    if (key === sortKey) setDirection((value) => value === "asc" ? "desc" : "asc");
    else { setSortKey(key); setDirection(key === "title" || key === "project" ? "asc" : "desc"); }
    resetPaging();
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

  const Header = ({ label, value }: { label: string; value: SortKey }) => {
    const active = sortKey === value;
    const Icon = active ? (direction === "asc" ? ArrowUp : ArrowDown) : ChevronsUpDown;
    return <Button variant="ghost" size="sm" className="-ml-2 h-8 px-2" onClick={() => sort(value)}>{label}<Icon className="ml-1 h-3.5 w-3.5" /></Button>;
  };

  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2" role="search" aria-label="Search and filter reviewed sessions">
      <div className="relative min-w-64 flex-1"><Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" /><Input value={query} onChange={(event) => { setQuery(event.target.value); resetPaging(); }} placeholder="Search every reviewed title, project, branch, or session ID" className="pl-9" /></div>
      <Select value={availability} onValueChange={(value) => { setAvailability(value); resetPaging(); }}><SelectTrigger className="w-40"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All availability</SelectItem><SelectItem value="available">Importable</SelectItem><SelectItem value="blocked">Blocked</SelectItem></SelectContent></Select>
      <Select value={changeFilter} onValueChange={(value) => { setChangeFilter(value); resetPaging(); }}><SelectTrigger className="w-44"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">All present sessions</SelectItem><SelectItem value="new">New</SelectItem><SelectItem value="content_changed">Transcript changed</SelectItem><SelectItem value="metadata_changed">Details changed</SelectItem><SelectItem value="missing">Missing locally</SelectItem><SelectItem value="unchanged">Unchanged</SelectItem></SelectContent></Select>
    </div>
    {error && <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">{error}</div>}
    {copyFeedback && <div className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground" role="status">{copyFeedback}</div>}
    <div className="relative overflow-x-auto rounded-md border">
      {loading && <div className="absolute inset-x-0 top-0 z-10 flex justify-center bg-background/80 p-2 text-sm"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Loading exact rows…</div>}
      <table className="w-full min-w-[1000px] text-sm"><thead className="border-b bg-muted/40 text-left"><tr><th className="w-12 px-3 py-2"><Checkbox checked={allSelected} onCheckedChange={() => { const next = new Set(selected); if (allSelected) selectable.forEach((row) => next.delete(keyOf(row))); else for (const row of selectable) { if (next.size >= review.limits.selected_sessions) break; next.add(keyOf(row)); } onSelectedChange(next); }} aria-label="Select importable rows on this page" disabled={disabled || selectable.length === 0} /></th><th className="px-3 py-2"><Header label="Session" value="title" /></th><th className="px-3 py-2"><Header label="Project" value="project" /></th><th className="px-3 py-2"><Header label="Change" value="change" /></th><th className="px-3 py-2"><Header label="Modified" value="modified" /></th><th className="px-3 py-2"><Header label="Size" value="bytes" /></th><th className="px-3 py-2">Import</th><th className="px-3 py-2"><span className="sr-only">Actions</span></th></tr></thead>
      <tbody className="divide-y">{pageData.items.map((row) => { const key = keyOf(row); const checked = selected.has(key); return <tr key={key} className={checked ? "bg-blue-500/5" : "hover:bg-muted/30"}><td className="px-3 py-3 align-top"><Checkbox checked={checked} disabled={disabled || !row.import_available || !row.present || (!checked && selected.size >= review.limits.selected_sessions)} onCheckedChange={() => { const next = new Set(selected); checked ? next.delete(key) : next.add(key); onSelectedChange(next); }} aria-label={`Select ${row.title}`} /></td><td className="max-w-80 px-3 py-3 align-top"><div className="truncate font-medium">{row.title}</div><div className="font-mono text-[11px] text-muted-foreground">{row.session_id}</div></td><td className="px-3 py-3 align-top"><div>{row.project_name}</div><div className="text-xs text-muted-foreground">{[row.git_branch, row.worktree_name].filter(Boolean).join(" · ") || "No branch metadata"}</div></td><td className="px-3 py-3 align-top">{changeLabel(row.change_type)}</td><td className="whitespace-nowrap px-3 py-3 align-top">{new Date(row.last_modified_ns / 1_000_000).toLocaleString()}</td><td className="whitespace-nowrap px-3 py-3 align-top">{formatBytes(row.bytes)}<div className="text-xs text-muted-foreground">{row.file_count} file{row.file_count === 1 ? "" : "s"}</div></td><td className="px-3 py-3 align-top">{row.import_available ? <Badge variant="outline">Ready</Badge> : <Badge variant="destructive">Blocked</Badge>} {!row.import_available && <div className="mt-1 max-w-52 text-xs text-muted-foreground">{row.import_blocked_reason ?? "No reason reported"}</div>}</td><td className="px-3 py-3 align-top"><Button variant="ghost" size="sm" onClick={() => void copyResume(row.session_id)}><Copy className="mr-1 h-3.5 w-3.5" />Resume</Button></td></tr>; })}{pageData.items.length === 0 && <tr><td colSpan={8} className="px-4 py-10 text-center text-muted-foreground">No reviewed sessions match these filters.</td></tr>}</tbody></table>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm"><span className="text-muted-foreground">Page {pageIndex + 1} · {pageData.page.returned} rows · {pageData.page.total.toLocaleString()} match this view · {selected.size} selected ({formatBytes(selectedBytes)} on this page)</span><div className="flex items-center gap-2"><Select value={String(pageSize)} onValueChange={(value) => { setPageSize(Number(value)); resetPaging(); }}><SelectTrigger className="w-28"><SelectValue /></SelectTrigger><SelectContent>{[25, 50, 100, 200].map((size) => <SelectItem key={size} value={String(size)}>{size} rows</SelectItem>)}</SelectContent></Select><Button variant="outline" size="sm" disabled={pageIndex === 0 || loading} onClick={() => setPageIndex((value) => value - 1)}>Previous</Button><Button variant="outline" size="sm" disabled={!pageData.page.has_more || loading} onClick={nextPage}>Next</Button></div></div>
  </div>;
}
