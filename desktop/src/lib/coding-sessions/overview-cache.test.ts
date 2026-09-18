import { describe, expect, it } from "vitest";

import {
  CACHED_ROW_LIMIT,
  readCachedOverview,
  writeCachedOverview,
} from "@/lib/coding-sessions/overview-cache";
import type { CodingSessionsOverview } from "@/lib/api";

function memoryStore(failOnWrite = false) {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      if (failOnWrite) throw new Error("quota exceeded");
      values.set(key, value);
    },
    removeItem: (key: string) => void values.delete(key),
  };
}

function overview(count: number): CodingSessionsOverview {
  return {
    schema_version: 2,
    conversations: Array.from({ length: count }, (_, index) => ({
      session_id: `s${index}`,
    })),
  } as unknown as CodingSessionsOverview;
}

describe("overview cache", () => {
  it("round-trips the last list with the moment it was read", () => {
    const store = memoryStore();
    expect(writeCachedOverview(overview(3), 42, store)).toBeNull();
    const cached = readCachedOverview(store);
    expect(cached?.at).toBe(42);
    expect(cached?.overview.conversations).toHaveLength(3);
    expect(cached?.truncated).toBe(false);
  });

  it("marks a list it had to shorten instead of implying it is whole", () => {
    const store = memoryStore();
    writeCachedOverview(overview(CACHED_ROW_LIMIT + 5), 1, store);
    const cached = readCachedOverview(store);
    expect(cached?.overview.conversations).toHaveLength(CACHED_ROW_LIMIT);
    expect(cached?.truncated).toBe(true);
  });

  it("reports a write it could not make rather than failing silently", () => {
    const store = memoryStore(true);
    expect(writeCachedOverview(overview(1), 1, store)).toContain("could not be kept");
    expect(readCachedOverview(store)).toBeNull();
  });

  it("ignores junk in storage instead of throwing on start-up", () => {
    const store = memoryStore();
    store.values.set("matrx.coding-sessions.overview.v1", "{not json");
    expect(readCachedOverview(store)).toBeNull();
    store.values.set("matrx.coding-sessions.overview.v1", JSON.stringify({ at: 1 }));
    expect(readCachedOverview(store)).toBeNull();
  });
});
