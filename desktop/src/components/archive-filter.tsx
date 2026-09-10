/**
 * ArchiveFilter — THE platform archive filter control.
 *
 * > "everything should have an archive filter, and the default should always
 * >  hide archived, but seeing archived items should be one or two clicks
 * >  away … this is a system wide decision for every single item everywhere in
 * >  our system, for every single table and every single page."
 * >  — Arman, 2026-09-09 (`../../../common-docs/policies/archived-items.md`)
 *
 * 🚨 THIS FILE IS TEMPORARY AND MUST BE DELETED — it is not a host capability.
 *
 * The control belongs in `@ai-matrx/design-system`, and on 2026-09-09 it was
 * written there (`apps/shared/design-system/src/archive-filter.tsx`, version
 * 0.13.0) by the lane fixing the aidream clients. That version was not yet
 * PUBLISHED to npm when this repo needed the control, and THE LATEST LAW
 * forbids consuming an unpublished package. So this is the same body, with the
 * same exported names and the same prop shape, over the same
 * `SegmentedControl` primitive — an adoption seam, not a fork.
 *
 * WHEN `@ai-matrx/design-system` ≥ 0.13.0 IS INSTALLED HERE:
 *   1. delete this file,
 *   2. change the import in `coding-sessions/HistoryInventoryTable.tsx` to
 *      `from "@ai-matrx/design-system"`,
 *   3. nothing else — the API is identical by construction.
 * Register row C2, `common-docs/projects/archived-items-law/STATUS.md`.
 *
 * The filter is a REQUEST, never a client-side sieve: its value goes to the
 * READER (here, the engine's `archived=` query parameter) so counts, badges and
 * pagination describe what the list actually renders.
 */

import * as React from "react";

import { SegmentedControl, cn } from "@ai-matrx/design-system";
import type { ArchiveFilterValue } from "@/lib/api";

/** The three states, in display order. */
export const ARCHIVE_FILTER_VALUES = ["active", "archived", "all"] as const;

/** The platform default: a list hides archived rows until asked. */
export const DEFAULT_ARCHIVE_FILTER: ArchiveFilterValue = "active";

/** The one wording. Hosts pass `labels` only to shorten, never to rename. */
export const ARCHIVE_FILTER_LABELS: Record<ArchiveFilterValue, string> = {
  active: "Active only",
  archived: "Archived only",
  all: "Active + archived",
};

/**
 * Narrow an untrusted value (a URL param, a stored preference, an API echo) to
 * the tri-state. Anything unrecognised falls back to the platform default
 * rather than silently widening a list to archived rows.
 */
export function toArchiveFilter(
  value: unknown,
  fallback: ArchiveFilterValue = DEFAULT_ARCHIVE_FILTER,
): ArchiveFilterValue {
  return (ARCHIVE_FILTER_VALUES as readonly string[]).includes(value as string)
    ? (value as ArchiveFilterValue)
    : fallback;
}

export interface ArchiveFilterProps {
  value: ArchiveFilterValue;
  onValueChange: (value: ArchiveFilterValue) => void;
  /**
   * Per-state row counts, when the reader returns them. A count that is not
   * honest is worse than none, so pass a key only when the number describes
   * what the list would actually render.
   */
  counts?: Partial<Record<ArchiveFilterValue, number>>;
  /** Shorter wording for a dense toolbar. Never a different meaning. */
  labels?: Partial<Record<ArchiveFilterValue, string>>;
  size?: "sm" | "md" | "lg";
  className?: string;
  "aria-label"?: string;
  disabled?: boolean;
}

export function ArchiveFilter({
  value,
  onValueChange,
  counts,
  labels,
  size = "sm",
  className,
  "aria-label": ariaLabel = "Archive filter",
  disabled = false,
}: ArchiveFilterProps) {
  const data = React.useMemo(
    () =>
      ARCHIVE_FILTER_VALUES.map((state) => {
        const text = labels?.[state] ?? ARCHIVE_FILTER_LABELS[state];
        const count = counts?.[state];
        return {
          value: state,
          disabled,
          label:
            typeof count === "number" ? (
              <span className="inline-flex items-center gap-1.5">
                <span>{text}</span>
                <span className="text-muted-foreground tabular-nums">{count}</span>
              </span>
            ) : (
              text
            ),
        };
      }),
    [counts, labels, disabled],
  );

  return (
    <div role="group" aria-label={ariaLabel} className={cn("inline-flex", className)}>
      <SegmentedControl
        value={value}
        onValueChange={(next: string) => onValueChange(toArchiveFilter(next, value))}
        data={data}
        size={size}
      />
    </div>
  );
}
