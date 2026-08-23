import { describe, expect, it } from "vitest";

import type { ClaudeHistoryReview } from "@/lib/api";
import { historyReviewCounts } from "./HistoryInventoryTable";

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
