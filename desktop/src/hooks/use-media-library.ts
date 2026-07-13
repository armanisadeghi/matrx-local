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
import {
  announceMediaItemsRemoved,
  onMediaItemsAdded,
  onMediaItemsRemoved,
} from "@/lib/media-events";

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
  /**
   * Drop items from the local list WITHOUT calling the engine — the state
   * update for an item that left the library some other way (moved into the
   * Private vault). Revokes their blob URLs and announces the removal so every
   * other store holding the same ids (job thumbnails, open lightboxes) drops
   * them in the same tick. Never leaves a half-updated grid behind.
   */
  removeItems: (itemIds: string[], reason: "deleted" | "vaulted") => void;
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
  /** Bumped whenever items are removed. A byte-fetch that started before the
   * current epoch must not install a URL for an item that is now gone. */
  const removalEpochRef = useRef(0);
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
        // Sync the ref now (not via the post-render effect) so a getFileUrl
        // resolving in this same tick sees the true current list.
        itemsRef.current = replace
          ? page.items
          : [...itemsRef.current, ...page.items];
        setItems((prev) => (replace ? page.items : [...prev, ...page.items]));
        if (replace) {
          // A replace (refresh / filter switch) drops items from the list. This
          // provider lives at the app root and NEVER unmounts, so the unmount
          // cleanup can no longer be what frees their blob URLs — without this,
          // toggling the filter a few times over a large library retains every
          // multi-MB PNG for the whole session.
          // A refresh drops items too — bump the epoch so a getFileUrl still in
          // flight for one of them throws its URL away instead of caching a
          // blob nothing will render or revoke.
          removalEpochRef.current += 1;
          const keep = new Set(page.items.map((i) => i.id));
          setFileUrls((prev) => {
            let changed = false;
            const next = { ...prev };
            for (const [id, url] of Object.entries(prev)) {
              if (!keep.has(id)) {
                URL.revokeObjectURL(url);
                delete next[id];
                changed = true;
              }
            }
            return changed ? next : prev;
          });
        }
        setHasMore(offset + page.items.length < page.total);
        setError(null);
      } catch (e) {
        // A TypeError from fetch is a network-level failure — the engine
        // process is gone (connection refused), not an API error. Normalize
        // it to the sentinel so the reconnect retry below arms; the raw
        // "Failed to fetch" string never matched and the tab stayed dead
        // until a full app restart.
        const msg =
          e instanceof TypeError
            ? ENGINE_NOT_CONNECTED
            : e instanceof Error
              ? e.message
              : "Failed to load media library";
        emitClientLog(
          "error",
          `[media-library] list failed (offset ${offset}): ${String(e)}`,
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
      const epoch = removalEpochRef.current;
      const task = (async () => {
        try {
          const url = await fetchMediaLibraryFile(base, itemId);
          if (removalEpochRef.current !== epoch && !itemsRef.current.some((i) => i.id === itemId)) {
            // The item was deleted / vaulted while its bytes were in flight.
            // Caching the URL now would leak it (nothing will ever render or
            // revoke it) and resurrect a dead id in the viewing set.
            URL.revokeObjectURL(url);
            return null;
          }
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

  /**
   * The ONE local-removal path. Drops the items, revokes their blob URLs, and
   * announces the ids so the job-queue thumbnails and any open lightbox drop
   * them too. Called by deleteItem (after the engine confirms) and by the
   * vault move (the items are gone from the library once vaulted).
   */
  const removeLocal = useCallback(
    (itemIds: string[], reason: "deleted" | "vaulted") => {
      if (itemIds.length === 0) return;
      removalEpochRef.current += 1;
      const ids = new Set(itemIds);
      itemsRef.current = itemsRef.current.filter((i) => !ids.has(i.id));
      setItems((prev) => prev.filter((i) => !ids.has(i.id)));
      setFileUrls((prev) => {
        let changed = false;
        const next = { ...prev };
        for (const id of ids) {
          const url = next[id];
          if (url) {
            URL.revokeObjectURL(url);
            delete next[id];
            changed = true;
          }
        }
        return changed ? next : prev;
      });
      announceMediaItemsRemoved(itemIds, reason);
    },
    [],
  );

  const deleteItem = useCallback(
    async (itemId: string): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        setError(ENGINE_NOT_CONNECTED);
        return false;
      }
      try {
        await deleteMediaLibraryItem(base, itemId);
        removeLocal([itemId], "deleted");
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
    },
    [removeLocal],
  );

  const removeItems = useCallback(
    (itemIds: string[], reason: "deleted" | "vaulted") =>
      removeLocal(itemIds, reason),
    [removeLocal],
  );

  const clearError = useCallback(() => setError(null), []);

  // Init fetch — in the hook, on [] deps, per repo React rules. This was
  // MISSING (the docstring promised it): nothing loaded the library on mount,
  // so `loading` (initialized true) never cleared unless a job-completion
  // effect happened to fire — with a dead engine, none did, and the Library
  // tab showed "Loading media library…" forever. The shipped
  // forever-spinner bug of 2026-07-11/12 (MXL-D-038).
  useEffect(() => {
    void fetchPage(0, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Engine-reconnect retry — narrowly gated on the ENGINE_NOT_CONNECTED
  // sentinel (fetchPage normalizes network-level failures to it), same
  // pattern as use-media-vault. Clears itself the moment a fetch succeeds
  // (error → null) or fails for a non-connection reason.
  useEffect(() => {
    if (error !== ENGINE_NOT_CONNECTED) return;
    const id = setInterval(() => {
      void fetchPage(0, true);
    }, 3000);
    return () => clearInterval(id);
  }, [error, fetchPage]);

  // Items restored OUT of the vault are library items again — pull them in.
  useEffect(() => onMediaItemsAdded(() => void fetchPage(0, true)), [fetchPage]);

  // Another store removed items (the vault move announces; the vault's own
  // permanent delete announces). Drop them from the list and revoke their URLs.
  //
  // This MUST prune `items` regardless of whether a blob URL was ever cached:
  // the provider is mounted at the app root and lists items at boot, so an item
  // vaulted from the queue or the lightbox — surfaces that never rendered a
  // library tile — would otherwise stay in the grid and later render as a
  // broken "could not be loaded" tile.
  useEffect(
    () =>
      onMediaItemsRemoved(({ itemIds }) => {
        const ids = new Set(itemIds);
        if (!itemsRef.current.some((i) => ids.has(i.id))) {
          // Not ours (e.g. a vault-only permanent delete) — but a URL may still
          // be cached from a vault render; fall through to the revoke below.
          if (!itemIds.some((id) => id in fileUrlsRef.current)) return;
        }
        removalEpochRef.current += 1;
        itemsRef.current = itemsRef.current.filter((i) => !ids.has(i.id));
        setItems((prev) =>
          prev.some((i) => ids.has(i.id))
            ? prev.filter((i) => !ids.has(i.id))
            : prev,
        );
        setFileUrls((prev) => {
          let changed = false;
          const next = { ...prev };
          for (const id of ids) {
            const url = next[id];
            if (url) {
              URL.revokeObjectURL(url);
              delete next[id];
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      }),
    [],
  );

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
      removeItems,
      clearError,
    }),
    [
      refresh,
      setFilter,
      loadMore,
      getFileUrl,
      deleteItem,
      removeItems,
      clearError,
    ],
  );

  return [state, actions];
}
