import { describe, expect, it } from "vitest";
import { codexUsageRangeFor } from "./CodexUsage";

describe("codexUsageRangeFor", () => {
  it("makes a same-day custom range end at the following local midnight", () => {
    const range = codexUsageRangeFor("custom", "2026-09-12", "2026-09-12", new Date("2026-09-13T08:00:00Z"));
    expect(new Date(range.end).getTime() - new Date(range.start).getTime()).toBe(86_400_000);
  });

  it("keeps the last twelve hours relative to refresh time", () => {
    const now = new Date("2026-09-13T12:00:00Z");
    const range = codexUsageRangeFor("last12h", "", "", now);
    expect(range.end).toBe(now.toISOString());
    expect(new Date(range.end).getTime() - new Date(range.start).getTime()).toBe(43_200_000);
  });
});
