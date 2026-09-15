/**
 * The last list this Mac saw, kept so Refresh never shows a blank screen.
 *
 * Reading Claude's on-disk index takes seconds (measured 4.27s on 1.4.110),
 * and the old screen threw the rows away while it waited. The cache is the
 * previous answer, always labelled with its age — never presented as current.
 */

import type { ClaudeOverview } from "@/lib/api";

const KEY = "matrx.coding-sessions.overview.v1";
/** Rows kept in the cache. The header says so when the real list is longer. */
export const CACHED_ROW_LIMIT = 1000;

export interface CachedOverview {
  overview: ClaudeOverview;
  at: number;
  /** True when the cache holds fewer rows than the overview it came from. */
  truncated: boolean;
}

interface Store {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function defaultStore(): Store | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export function readCachedOverview(store: Store | null = defaultStore()): CachedOverview | null {
  if (!store) return null;
  let raw: string | null;
  try {
    raw = store.getItem(KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<CachedOverview>;
    if (!parsed || typeof parsed.at !== "number" || !parsed.overview) return null;
    if (!Array.isArray(parsed.overview.conversations)) return null;
    return {
      overview: parsed.overview,
      at: parsed.at,
      truncated: Boolean(parsed.truncated),
    };
  } catch {
    return null;
  }
}

/**
 * Keep the newest rows. Returns the failure so the screen can say the cache
 * is off rather than silently losing it.
 */
export function writeCachedOverview(
  overview: ClaudeOverview,
  at: number,
  store: Store | null = defaultStore(),
): string | null {
  if (!store) return "This app has no local storage, so the last list cannot be kept between visits.";
  const truncated = overview.conversations.length > CACHED_ROW_LIMIT;
  const payload: CachedOverview = {
    overview: truncated
      ? { ...overview, conversations: overview.conversations.slice(0, CACHED_ROW_LIMIT) }
      : overview,
    at,
    truncated,
  };
  try {
    store.setItem(KEY, JSON.stringify(payload));
    return null;
  } catch (error: unknown) {
    try {
      store.removeItem(KEY);
    } catch {
      /* the cache is gone either way; the message below is the report */
    }
    return error instanceof Error
      ? `The last list could not be kept on this Mac: ${error.message}`
      : "The last list could not be kept on this Mac.";
  }
}
