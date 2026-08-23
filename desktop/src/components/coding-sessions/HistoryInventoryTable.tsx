import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown, Copy, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  ClaudeHistoryPreview,
  ClaudeHistorySessionPreview,
} from "@/lib/api";

type SortKey = "modified" | "title" | "project" | "size";
type SortDirection = "asc" | "desc";
export type HistoryChange = "new" | "changed" | "unchanged" | "not_compared";

function sessionKey(session: ClaudeHistorySessionPreview): string {
  return `${session.project_key}:${session.session_id}`;
}

export function classifyHistoryChange(
  session: ClaudeHistorySessionPreview,
  previous: ClaudeHistoryPreview | null,
): HistoryChange {
  if (!previous) return "not_compared";
  const prior = previous.sessions.find((candidate) => sessionKey(candidate) === sessionKey(session));
  if (!prior) return "new";
  return prior.source_revision !== session.source_revision ||
    prior.title !== session.title ||
    prior.project_name !== session.project_name ||
    prior.git_branch !== session.git_branch ||
    prior.worktree_name !== session.worktree_name ||
    prior.is_archived !== session.is_archived
    ? "changed"
    : "unchanged";
}

export function historyChangeCounts(
  preview: ClaudeHistoryPreview,
  previous: ClaudeHistoryPreview | null,
) {
  const counts = { new: 0, changed: 0, unchanged: 0, noLongerReturned: 0 };
  for (const session of preview.sessions) {
    const change = classifyHistoryChange(session, previous);
    if (change !== "not_compared") counts[change] += 1;
  }
  if (previous) {
    const currentKeys = new Set(preview.sessions.map(sessionKey));
    counts.noLongerReturned = previous.sessions.filter(
      (session) => !currentKeys.has(sessionKey(session)),
    ).length;
  }
  return counts;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function SortButton({
  label,
  column,
  sortKey,
  direction,
  onSort,
}: {
  label: string;
  column: SortKey;
  sortKey: SortKey;
  direction: SortDirection;
  onSort: (column: SortKey) => void;
}) {
  const active = sortKey === column;
  const Icon = active ? (direction === "asc" ? ArrowUp : ArrowDown) : ChevronsUpDown;
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="-ml-2 h-8 px-2"
      onClick={() => onSort(column)}
      aria-label={`Sort by ${label}${active ? `, currently ${direction}ending` : ""}`}
    >
      {label}<Icon className="ml-1 h-3.5 w-3.5" />
    </Button>
  );
}

export function HistoryInventoryTable({
  preview,
  previousPreview,
  selected,
  onSelectedChange,
  disabled,
}: {
  preview: ClaudeHistoryPreview;
  previousPreview: ClaudeHistoryPreview | null;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [availability, setAvailability] = useState("all");
  const [changeFilter, setChangeFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("modified");
  const [direction, setDirection] = useState<SortDirection>("desc");
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const rows = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return preview.sessions
      .map((session) => ({
        session,
        key: sessionKey(session),
        change: classifyHistoryChange(session, previousPreview),
      }))
      .filter(({ session, change }) => {
        if (availability === "available" && !session.import_available) return false;
        if (availability === "blocked" && session.import_available) return false;
        if (changeFilter !== "all" && change !== changeFilter) return false;
        if (!normalized) return true;
        return [
          session.title,
          session.project_name,
          session.git_branch,
          session.worktree_name,
          session.session_id,
        ].some((value) => value?.toLocaleLowerCase().includes(normalized));
      })
      .sort((left, right) => {
        const a = left.session;
        const b = right.session;
        const compared = sortKey === "modified"
          ? a.last_modified_ns - b.last_modified_ns
          : sortKey === "size"
            ? a.bytes - b.bytes
            : (sortKey === "title" ? a.title : a.project_name).localeCompare(
                sortKey === "title" ? b.title : b.project_name,
              );
        return direction === "asc" ? compared : -compared;
      });
  }, [availability, changeFilter, direction, preview.sessions, previousPreview, query, sortKey]);

  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const visibleRows = rows.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const selectedBytes = preview.sessions
    .filter((session) => selected.has(sessionKey(session)))
    .reduce((total, session) => total + session.bytes, 0);
  const selectableVisible = visibleRows.filter(({ session, key }) =>
    session.import_available && (selected.has(key) || selected.size < preview.limits.selected_sessions),
  );
  const allVisibleSelected = selectableVisible.length > 0 && selectableVisible.every(({ key }) => selected.has(key));

  const sort = (column: SortKey) => {
    if (column === sortKey) setDirection((value) => value === "asc" ? "desc" : "asc");
    else {
      setSortKey(column);
      setDirection(column === "modified" || column === "size" ? "desc" : "asc");
    }
    setPage(1);
  };

  const toggleVisible = () => {
    const next = new Set(selected);
    if (allVisibleSelected) selectableVisible.forEach(({ key }) => next.delete(key));
    else {
      for (const { key, session } of selectableVisible) {
        if (next.size >= preview.limits.selected_sessions) break;
        if (selectedBytes + session.bytes <= preview.limits.import_bytes) next.add(key);
      }
    }
    onSelectedChange(next);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2" role="search" aria-label="Filter local coding sessions">
        <div className="relative min-w-64 flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => { setQuery(event.target.value); setPage(1); }}
            placeholder="Search title, project, branch, or session ID"
            className="pl-9"
            aria-label="Search sessions"
          />
        </div>
        <Select value={availability} onValueChange={(value) => { setAvailability(value); setPage(1); }}>
          <SelectTrigger className="w-40" aria-label="Import availability"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All availability</SelectItem>
            <SelectItem value="available">Importable</SelectItem>
            <SelectItem value="blocked">Blocked</SelectItem>
          </SelectContent>
        </Select>
        {previousPreview && (
          <Select value={changeFilter} onValueChange={(value) => { setChangeFilter(value); setPage(1); }}>
            <SelectTrigger className="w-40" aria-label="Review changes"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All changes</SelectItem>
              <SelectItem value="new">New</SelectItem>
              <SelectItem value="changed">Changed</SelectItem>
              <SelectItem value="unchanged">Unchanged</SelectItem>
            </SelectContent>
          </Select>
        )}
      </div>

      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="border-b bg-muted/40 text-left">
            <tr>
              <th className="w-12 px-3 py-2">
                <Checkbox checked={allVisibleSelected} onCheckedChange={toggleVisible} aria-label="Select visible importable sessions" disabled={disabled || selectableVisible.length === 0} />
              </th>
              <th className="px-3 py-2"><SortButton label="Session" column="title" sortKey={sortKey} direction={direction} onSort={sort} /></th>
              <th className="px-3 py-2"><SortButton label="Project" column="project" sortKey={sortKey} direction={direction} onSort={sort} /></th>
              <th className="px-3 py-2"><SortButton label="Modified" column="modified" sortKey={sortKey} direction={direction} onSort={sort} /></th>
              <th className="px-3 py-2"><SortButton label="Size" column="size" sortKey={sortKey} direction={direction} onSort={sort} /></th>
              <th className="px-3 py-2">Status</th>
              <th className="w-24 px-3 py-2"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {visibleRows.map(({ session, key, change }) => {
              const checked = selected.has(key);
              const canSelect = session.import_available && (checked || selected.size < preview.limits.selected_sessions);
              return (
                <tr key={key} className={checked ? "bg-blue-500/5" : "hover:bg-muted/30"}>
                  <td className="px-3 py-3 align-top"><Checkbox checked={checked} disabled={disabled || !preview.import_ready || !canSelect} onCheckedChange={() => {
                    const next = new Set(selected); checked ? next.delete(key) : next.add(key); onSelectedChange(next);
                  }} aria-label={`Select ${session.title}`} /></td>
                  <td className="max-w-80 px-3 py-3 align-top">
                    <div className="flex items-center gap-2 font-medium">
                      <span className="truncate">{session.title}</span>
                      {change === "new" && <Badge className="shrink-0">New</Badge>}
                      {change === "changed" && <Badge variant="secondary" className="shrink-0">Changed</Badge>}
                    </div>
                    <div className="mt-1 font-mono text-[11px] text-muted-foreground">{session.session_id}</div>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <div>{session.project_name}</div>
                    <div className="text-xs text-muted-foreground">{[session.git_branch, session.worktree_name ? `worktree ${session.worktree_name}` : null].filter(Boolean).join(" · ") || "No branch metadata"}</div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 align-top">{new Date(session.last_modified_ns / 1_000_000).toLocaleString()}</td>
                  <td className="whitespace-nowrap px-3 py-3 align-top">{formatBytes(session.bytes)}<div className="text-xs text-muted-foreground">{session.file_count} file{session.file_count === 1 ? "" : "s"}</div></td>
                  <td className="px-3 py-3 align-top">
                    {session.import_available ? <Badge variant="outline">Ready to copy</Badge> : <Badge variant="destructive">Blocked</Badge>}
                    {!session.import_available && <div className="mt-1 max-w-52 text-xs text-muted-foreground">{session.import_blocked_reason ?? "The engine did not report a reason."}</div>}
                  </td>
                  <td className="px-3 py-3 align-top">
                    <Button type="button" variant="ghost" size="sm" title="Copy the native Claude resume command" onClick={() => void navigator.clipboard.writeText(`claude --resume ${session.session_id}`)}><Copy className="mr-1 h-3.5 w-3.5" />Resume</Button>
                  </td>
                </tr>
              );
            })}
            {visibleRows.length === 0 && <tr><td colSpan={7} className="px-4 py-10 text-center text-muted-foreground">No sessions match these filters.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-muted-foreground">Showing {visibleRows.length === 0 ? 0 : (currentPage - 1) * pageSize + 1}–{Math.min(currentPage * pageSize, rows.length)} of {rows.length} returned sessions</span>
        <div className="flex items-center gap-2">
          <Select value={String(pageSize)} onValueChange={(value) => { setPageSize(Number(value)); setPage(1); }}>
            <SelectTrigger className="w-28" aria-label="Rows per page"><SelectValue /></SelectTrigger>
            <SelectContent>{[25, 50, 100].map((size) => <SelectItem key={size} value={String(size)}>{size} rows</SelectItem>)}</SelectContent>
          </Select>
          <Button type="button" variant="outline" size="sm" disabled={currentPage <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>Previous</Button>
          <span>Page {currentPage} of {pageCount}</span>
          <Button type="button" variant="outline" size="sm" disabled={currentPage >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>Next</Button>
        </div>
      </div>
    </div>
  );
}
