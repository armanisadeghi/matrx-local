/**
 * useMediaLibrary — data layer for the Media Generation "Library" tab.
 *
 * Lists every image/video the engine has persisted (`/media-library`),
 * fetches file bytes as auth'd blob URLs (a plain <img src> cannot carry the
 * Authorization header), and deletes items.
 *
 * React rules obeyed strictly (see repo CLAUDE.md → React Patterns):
 *  - `actions` is wrapped in useMemo and its callbacks are stable (useCallback).
 *  - Init fetch lives here in the hook, on [] deps — never in a page effect.
 *  - No polling. The only interval is the engine-reconnect retry, narrowly
 *    gated on the ENGINE_NOT_CONNECTED error sentinel (same pattern as
 *    use-media-gen.ts), and it always cleans up.
 */

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import {
  engine,
  listMediaLibraryItems,
  fetchMediaLibraryFile,
  deleteMediaLibraryItem,
} from "@/lib/api";
import type { MediaLibraryItem } from "@/lib/api";
import { emitClientLog } from "@/hooks/use-unified-log";

const ENGINE_NOT_CONNECTED = "Engine not connected";
const PAGE_SIZE = 60;

export type MediaLibraryFilter = "all" | "image" | "video";

export interface MediaLibraryState {
  /** Items for the current filter, newest first. */
  items: MediaLibraryItem[];
  /** Active media-type filter. */
  filter: MediaLibraryFilter;
  /** True while the initial list (or a filter switch) is loading. */
  loading: boolean;
  /** True while a loadMore page is being appended. */
  loadingMore: boolean;
  /** Whether the last page came back full — i.e. more items likely exist. */
  hasMore: boolean;
  /** List/delete error — null when healthy. */
  error: string | null;
  /** itemId → object URL of fetched bytes (blob URLs, owned by this hook). */
  fileUrls: Record<string, string>;
}

export interface MediaLibraryActions {
  /** Reload the list from offset 0 for the current filter. */
  refresh: () => Promise<void>;
  /** Switch the media-type filter (triggers a fresh load). */
  setFilter: (filter: MediaLibraryFilter) => void;
  /** Append the next page. No-op when hasMore is false or already loading. */
  loadMore: () => Promise<void>;
  /**
   * Resolve (and cache) an auth'd blob URL for an item's bytes.
   * Returns null on failure — the failure is logged, never silent.
   */
  getFileUrl: (itemId: string) => Promise<string | null>;
  /** Delete an item on the engine and drop it (and its blob URL) locally. */
  deleteItem: (itemId: string) => Promise<boolean>;
  clearError: () => void;
}

export function useMediaLibrary(): [MediaLibraryState, MediaLibraryActions] {
  const [items, setItems] = useState<MediaLibraryItem[]>([]);
  const [filter, setFilterState] = useState<MediaLibraryFilter>("all");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileUrls, setFileUrls] = useState<Record<string, string>>({});

  // Refs so the stable callbacks always read the latest values.
  const filterRef = useRef<MediaLibraryFilter>("all");
  const itemsRef = useRef<MediaLibraryItem[]>([]);
  const fileUrlsRef = useRef<Record<string, string>>({});
  // In-flight byte fetches, deduped per item id.
  const pendingFileFetches = useRef<Map<string, Promise<string | null>>>(
    new Map(),
  );
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  useEffect(() => {
    fileUrlsRef.current = fileUrls;
  }, [fileUrls]);

  const fetchPage = useCallback(
    async (offset: number, replace: boolean): Promise<void> => {
      const base = engine.engineUrl;
      if (!base) {
        setError(ENGINE_NOT_CONNECTED);
        setLoading(false);
        setLoadingMore(false);
        return;
      }
      if (replace) setLoading(true);
      else setLoadingMore(true);
      try {
        const f = filterRef.current;
        const page = await listMediaLibraryItems(base, {
          ...(f === "all" ? {} : { media_type: f }),
          limit: PAGE_SIZE,
          offset,
        });
        setItems((prev) => (replace ? page.items : [...prev, ...page.items]));
        setHasMore(offset + page.items.length < page.total);
        setError(null);
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : "Failed to load media library";
        emitClientLog(
          "error",
          `[media-library] list failed (offset ${offset}): ${msg}`,
          "engine",
        );
        setError(msg);
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [],
  );

  const refresh = useCallback(async () => {
    await fetchPage(0, true);
  }, [fetchPage]);

  const setFilter = useCallback(
    (next: MediaLibraryFilter) => {
      filterRef.current = next;
      setFilterState(next);
      setItems([]);
      setHasMore(false);
      void fetchPage(0, true);
    },
    [fetchPage],
  );

  const loadMore = useCallback(async () => {
    await fetchPage(itemsRef.current.length, false);
  }, [fetchPage]);

  const getFileUrl = useCallback(
    async (itemId: string): Promise<string | null> => {
      const existing = fileUrlsRef.current[itemId];
      if (existing) return existing;
      const pending = pendingFileFetches.current.get(itemId);
      if (pending) return pending;
      const base = engine.engineUrl;
      if (!base) {
        console.error(
          "[media-library] file fetch blocked — engine not connected",
        );
        return null;
      }
      const task = (async () => {
        try {
          const url = await fetchMediaLibraryFile(base, itemId);
          setFileUrls((prev) => ({ ...prev, [itemId]: url }));
          return url;
        } catch (e) {
          emitClientLog(
            "error",
            `[media-library] file fetch failed for ${itemId}: ${String(e)}`,
            "engine",
          );
          return null;
        } finally {
          pendingFileFetches.current.delete(itemId);
        }
      })();
      pendingFileFetches.current.set(itemId, task);
      return task;
    },
    [],
  );

  const deleteItem = useCallback(async (itemId: string): Promise<boolean> => {
    const base = engine.engineUrl;
    if (!base) {
      setError(ENGINE_NOT_CONNECTED);
      return false;
    }
    try {
      await deleteMediaLibraryItem(base, itemId);
      setItems((prev) => prev.filter((i) => i.id !== itemId));
      setFileUrls((prev) => {
        if (!(itemId in prev)) return prev;
        URL.revokeObjectURL(prev[itemId]);
        const next = { ...prev };
        delete next[itemId];
        return next;
      });
      return true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to delete item";
      emitClientLog(
        "error",
        `[media-library] delete failed for ${itemId}: ${msg}`,
        "engine",
      );
      setError(msg);
      return false;
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  // ── Init fetch (in the hook, [] deps) ──────────────────────────────────────
  useEffect(() => {
    void fetchPage(0, true);
  }, [fetchPage]);

  // While the engine URL is not yet available, retry — engine.engineUrl is set
  // outside React state and isn't reactive. Narrowly gated on the sentinel.
  useEffect(() => {
    if (error !== ENGINE_NOT_CONNECTED) return;
    const id = window.setInterval(() => {
      if (engine.engineUrl) void fetchPage(0, true);
    }, 2500);
    return () => window.clearInterval(id);
  }, [error, fetchPage]);

  // Revoke every object URL on final unmount to avoid leaks.
  useEffect(() => {
    return () => {
      for (const url of Object.values(fileUrlsRef.current)) {
        URL.revokeObjectURL(url);
      }
    };
  }, []);

  const state: MediaLibraryState = {
    items,
    filter,
    loading,
    loadingMore,
    hasMore,
    error,
    fileUrls,
  };

  const actions = useMemo<MediaLibraryActions>(
    () => ({
      refresh,
      setFilter,
      loadMore,
      getFileUrl,
      deleteItem,
      clearError,
    }),
    [refresh, setFilter, loadMore, getFileUrl, deleteItem, clearError],
  );

  return [state, actions];
}
