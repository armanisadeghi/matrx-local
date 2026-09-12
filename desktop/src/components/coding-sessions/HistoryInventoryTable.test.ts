import { describe, expect, it } from "vitest";

import type { ClaudeHistoryReview } from "@/lib/api";
import { DEFAULT_ARCHIVE_FILTER } from "@ai-matrx/design-system";
import {
  capHistorySelection,
  historyReviewCounts,
  inventoryQueryChanged,
  inventoryRequestFilters,
  inventorySortFromTableQuery,
} from "./history-inventory-table-adapter";

describe("durable history review evidence", () => {
  it("uses backend-wide scan counts instead of inferring from the visible page", () => {
    const review = {
      scan: {
        new_count: 12,
        content_changed_count: 3,
        metadata_changed_count: 4,
        missing_count: 5,
        unchanged_count: 900,
        blocked_count: 6,
      },
      items: [],
    } as unknown as ClaudeHistoryReview;

    expect(historyReviewCounts(review)).toEqual({
      new: 12,
      contentChanged: 3,
      metadataChanged: 4,
      missing: 5,
      unchanged: 900,
      blocked: 6,
    });
  });
});

/**
 * THE ARCHIVED-ITEMS LAW (Arman, 2026-09-09 —
 * ../../../../../common-docs/policies/archived-items.md) on the Claude History
 * Inventory. Register row C2(b).
 *
 * The defect this locks shut: the archive plumbing existed END TO END —
 * `archived?: boolean` on the client, `archived: bool | None` on the route, an
 * `is_archived = ?` clause in the SQL — and the table simply never set it, so
 * the request always said nothing and the engine added no clause. On this
 * machine that meant 1,671 archived Claude sessions rendering mixed in with
 * live ones, unlabelled (counted 2026-09-09 against the real index at
 * `~/Library/Application Support/Claude/claude-code-sessions`: 49,176 records,
 * 1,671 with `"isArchived": true`).
 *
 * The engine half — default hides, one state reveals, counts honest, an
 * unknown value refused — is proven against the real SQLite reader in
 * `tests/unit/test_claude_history_import.py::
 * test_inventory_hides_archived_by_default_and_reveals_on_request`.
 */
describe("THE ARCHIVED-ITEMS LAW: the table's request always carries an archive state", () => {
  const base = {
    cursor: undefined,
    limit: 50,
    query: "",
    changeFilter: "all",
    availability: "all",
    sortKey: "modified",
    direction: "desc",
  } as const;

  it("sends the platform default — hide archived — on a first open", () => {
    expect(inventoryRequestFilters({ ...base, archiveFilter: DEFAULT_ARCHIVE_FILTER }))
      .toMatchObject({ archived: "active" });
    // And the platform default IS "hide archived", not merely "whatever the
    // control opens on".
    expect(DEFAULT_ARCHIVE_FILTER).toBe("active");
  });

  it("carries every state the control can produce, unchanged", () => {
    for (const state of ["active", "archived", "all"] as const) {
      expect(inventoryRequestFilters({ ...base, archiveFilter: state }).archived).toBe(state);
    }
  });

  it("keeps the archive state when every other filter is engaged", () => {
    // The regression shape: an archive value quietly dropped once a search or a
    // change filter is in play.
    const filters = inventoryRequestFilters({
      cursor: "abc",
      limit: 200,
      query: "  matrx-frontend  ",
      changeFilter: "missing",
      availability: "available",
      archiveFilter: "archived",
      sortKey: "title",
      direction: "asc",
    });
    expect(filters).toEqual({
      cursor: "abc",
      limit: 200,
      search: "matrx-frontend",
      changeTypes: ["missing"],
      importable: true,
      archived: "archived",
      includeMissing: true,
      sort: "title",
      direction: "asc",
    });
  });

  it("FALSIFIABILITY: `archived` is never absent, for any input", () => {
    // A guard that cannot fail proves nothing: this is the exact assertion that
    // was FALSE for every call this component made before 2026-09-09.
    for (const changeFilter of ["all", "new", "missing"]) {
      for (const availability of ["all", "available", "blocked"]) {
        const filters = inventoryRequestFilters({
          ...base,
          changeFilter,
          availability,
          archiveFilter: "active",
        });
        expect(Object.keys(filters)).toContain("archived");
      }
    }
  });
});

describe("history inventory table adapter", () => {
  const baseQuery = {
    page: 1,
    pageSize: 50,
    search: "",
    anyOf: "",
    columnFilters: {},
    sort: { id: "modified", direction: "desc" as const },
  };

  it("maps package sorting to the server's fixed history vocabulary", () => {
    expect(inventorySortFromTableQuery({ ...baseQuery, sort: { id: "project", direction: "asc" } }))
      .toEqual({ sortKey: "project", direction: "asc" });
    expect(inventorySortFromTableQuery({ ...baseQuery, sort: null }))
      .toEqual({ sortKey: "modified", direction: "desc" });
  });

  it("resets the cursor chain only for a server-query shape change", () => {
    expect(inventoryQueryChanged(baseQuery, { ...baseQuery, page: 2 })).toBe(false);
    expect(inventoryQueryChanged(baseQuery, { ...baseQuery, search: "resume" })).toBe(true);
    expect(inventoryQueryChanged(baseQuery, { ...baseQuery, sort: { id: "bytes", direction: "desc" } })).toBe(true);
  });

  it("enforces the source selection cap for package select-all emissions", () => {
    expect([...capHistorySelection(["a", "b", "c"], new Set(["a"]), new Set(["a", "b", "c"]), 2)])
      .toEqual(["a", "b"]);
    expect([...capHistorySelection(["a", "outside"], new Set(["a", "outside"]), new Set(["a"]), 2)])
      .toEqual(["a", "outside"]);
  });
});
