import type { ArchiveFilterValue, ClaudeHistoryChangeType, ClaudeHistoryReview } from "@/lib/api";

export type HistoryInventorySortKey = "modified" | "title" | "project" | "bytes" | "change";
export type HistoryInventorySortDirection = "asc" | "desc";

/** Structural subset of the portable table query state, kept dependency-free for adapter tests. */
export interface HistoryInventoryTableQuery {
  page: number;
  pageSize: number;
  search: string;
  anyOf: string;
  columnFilters: Record<string, unknown>;
  sort: { id: string; direction: HistoryInventorySortDirection } | null;
}

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

/** Maps portable table sort state to the inventory API's fixed server vocabulary. */
export function inventorySortFromTableQuery(query: HistoryInventoryTableQuery): {
  sortKey: HistoryInventorySortKey;
  direction: HistoryInventorySortDirection;
} {
  const sort = query.sort;
  const id = sort?.id;
  if (sort && (id === "title" || id === "project" || id === "bytes" || id === "change" || id === "modified")) {
    return { sortKey: id, direction: sort.direction };
  }
  return { sortKey: "modified", direction: "desc" };
}

export function inventoryQueryChanged(
  previous: HistoryInventoryTableQuery,
  next: HistoryInventoryTableQuery,
): boolean {
  return previous.search !== next.search || previous.pageSize !== next.pageSize ||
    previous.sort?.id !== next.sort?.id || previous.sort?.direction !== next.sort?.direction;
}

/**
 * Preserve the source's selected-session ceiling even when table selection
 * emits a select-all set in one event. Existing selected ids keep their place;
 * newly selected rows are accepted in server-page order up to the limit.
 */
export function capHistorySelection(
  proposedIds: readonly string[],
  currentlySelected: ReadonlySet<string>,
  selectableIds: ReadonlySet<string>,
  maxSelected: number,
): Set<string> {
  const proposed = new Set(proposedIds);
  const next = new Set<string>();
  for (const id of currentlySelected) {
    if (proposed.has(id)) next.add(id);
  }
  for (const id of proposedIds) {
    if (next.has(id) || !selectableIds.has(id) || next.size >= maxSelected) continue;
    next.add(id);
  }
  return next;
}

/** The engine request assembled by the source-owned cursor/query adapter. */
export function inventoryRequestFilters(state: {
  cursor: string | undefined;
  limit: number;
  query: string;
  changeFilter: string;
  availability: string;
  archiveFilter: ArchiveFilterValue;
  sortKey: HistoryInventorySortKey;
  direction: HistoryInventorySortDirection;
}) {
  const changeTypes = state.changeFilter === "all" ? undefined : [state.changeFilter as ClaudeHistoryChangeType];
  return {
    ...(state.cursor ? { cursor: state.cursor } : {}),
    limit: state.limit,
    ...(state.query.trim() ? { search: state.query.trim() } : {}),
    ...(changeTypes ? { changeTypes } : {}),
    ...(state.availability === "all" ? {} : { importable: state.availability === "available" }),
    archived: state.archiveFilter,
    includeMissing: state.changeFilter === "missing",
    sort: state.sortKey,
    direction: state.direction,
  };
}
