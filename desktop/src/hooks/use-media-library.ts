/**
 * useMediaLibrary — data layer for the Media Generation "Library" tab.
 *
 * Lists every image/video the engine has persisted (`/media-library`),
 * resolves small gallery thumbs via a capped self-healing sweep
 * (`/media-library/thumb/{id}`), and fetches full file bytes only when
 * needed for lightbox / download / remix.
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
  fetchMediaLibraryThumb,
  deleteMediaLibraryItem,
} from "@/lib/api";
import type { MediaLibraryItem } from "@/lib/api";
import { createConcurrencyLimiter } from "@/lib/concurrency";
import { emitClientLog } from "@/hooks/use-unified-log";
import {
  announceMediaItemsRemoved,
  onMediaItemsAdded,
  onMediaItemsRemoved,
} from "@/lib/media-events";

const ENGINE_NOT_CONNECTED = "Engine not connected";
const PAGE_SIZE = 60;
/** Parallel thumb GETs — enough to fill a viewport, not enough to stall the engine. */
const THUMB_FETCH_CONCURRENCY = 6;

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
  /**
   * itemId → object URL of a small JPEG thumb (gallery / filmstrip).
   * Populated by the capped self-healing sweep — never full PNG/MP4 bytes.
   */
  thumbUrls: Record<string, string>;
  /** itemId → object URL of full media bytes (lightbox / download). */
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
   * Enqueue a gallery thumb for `itemId` (capped concurrency). The engine
   * generates + caches the JPEG on miss. Returns null on failure — the tile
   * keeps its placeholder and can retry later (self-healing).
   */
  getThumbUrl: (itemId: string) => Promise<string | null>;
  /**
   * Resolve (and cache) an auth'd blob URL for an item's FULL bytes.
   * Lightbox / download / remix only — grids must use getThumbUrl.
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

function revokeUrlMap(
  prev: Record<string, string>,
  drop: ReadonlySet<string>,
): Record<string, string> {
  let changed = false;
  const next = { ...prev };
  for (const id of drop) {
    const url = next[id];
    if (url) {
      URL.revokeObjectURL(url);
      delete next[id];
      changed = true;
    }
  }
  return changed ? next : prev;
}

export function useMediaLibrary(): [MediaLibraryState, MediaLibraryActions] {
  const [items, setItems] = useState<MediaLibraryItem[]>([]);
  const [filter, setFilterState] = useState<MediaLibraryFilter>("all");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thumbUrls, setThumbUrls] = useState<Record<string, string>>({});
  const [fileUrls, setFileUrls] = useState<Record<string, string>>({});

  // Refs so the stable callbacks always read the latest values.
  const filterRef = useRef<MediaLibraryFilter>("all");
  const itemsRef = useRef<MediaLibraryItem[]>([]);
  const thumbUrlsRef = useRef<Record<string, string>>({});
  const fileUrlsRef = useRef<Record<string, string>>({});
  const pendingThumbFetches = useRef<Map<string, Promise<string | null>>>(
    new Map(),
  );
  const pendingFileFetches = useRef<Map<string, Promise<string | null>>>(
    new Map(),
  );
  const limitThumbFetch = useRef(
    createConcurrencyLimiter(THUMB_FETCH_CONCURRENCY),
  );
  /** Bumped whenever items are removed. A byte-fetch that started before the
   * current epoch must not install a URL for an item that is now gone. */
  const removalEpochRef = useRef(0);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  useEffect(() => {
    thumbUrlsRef.current = thumbUrls;
  }, [thumbUrls]);
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
          setThumbUrls((prev) => {
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

  /**
   * Pull the newest page and prepend any items missing locally — without
   * setting `loading`, revoking cached URLs, or dropping load-more tail pages.
   * Used when generation (or a targeted add) announces fresh library ids.
   */
  const mergeNewItems = useCallback(async (_hintItemIds: string[]) => {
    const base = engine.engineUrl;
    if (!base) return;
    const existingIds = new Set(itemsRef.current.map((i) => i.id));
    try {
      const f = filterRef.current;
      const page = await listMediaLibraryItems(base, {
        ...(f === "all" ? {} : { media_type: f }),
        limit: PAGE_SIZE,
        offset: 0,
      });
      const toPrepend = page.items.filter((i) => !existingIds.has(i.id));
      if (toPrepend.length > 0) {
        itemsRef.current = [...toPrepend, ...itemsRef.current];
        setItems((prev) => {
          const known = new Set(prev.map((i) => i.id));
          const fresh = page.items.filter((i) => !known.has(i.id));
          return fresh.length > 0 ? [...fresh, ...prev] : prev;
        });
      }
      setHasMore(itemsRef.current.length < page.total);
      setError(null);
    } catch (e) {
      emitClientLog(
        "warn",
        `[media-library] merge-new-items failed: ${String(e)}`,
        "engine",
      );
    }
  }, []);

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

  const getThumbUrl = useCallback(
    async (itemId: string): Promise<string | null> => {
      const existing = thumbUrlsRef.current[itemId];
      if (existing) return existing;
      const pending = pendingThumbFetches.current.get(itemId);
      if (pending) return pending;
      const base = engine.engineUrl;
      if (!base) {
        console.error(
          "[media-library] thumb fetch blocked — engine not connected",
        );
        return null;
      }
      const epoch = removalEpochRef.current;
      const task = limitThumbFetch.current(async () => {
        try {
          const url = await fetchMediaLibraryThumb(base, itemId);
          if (
            removalEpochRef.current !== epoch &&
            !itemsRef.current.some((i) => i.id === itemId)
          ) {
            URL.revokeObjectURL(url);
            return null;
          }
          setThumbUrls((prev) => ({ ...prev, [itemId]: url }));
          return url;
        } catch (e) {
          emitClientLog(
            "error",
            `[media-library] thumb fetch failed for ${itemId}: ${String(e)}`,
            "engine",
          );
          return null;
        } finally {
          pendingThumbFetches.current.delete(itemId);
        }
      });
      pendingThumbFetches.current.set(itemId, task);
      return task;
    },
    [],
  );

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
          if (
            removalEpochRef.current !== epoch &&
            !itemsRef.current.some((i) => i.id === itemId)
          ) {
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
      setThumbUrls((prev) => revokeUrlMap(prev, ids));
      setFileUrls((prev) => revokeUrlMap(prev, ids));
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
  // forever-spinner bug of 2026-07-11/12 (MXL-D-038). At app boot this
  // provider mounts before engine discovery, so the mount call usually just
  // records ENGINE_NOT_CONNECTED — the "connected" subscription below is
  // what performs the real first load.
  useEffect(() => {
    void fetchPage(0, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // THE recovery path: reload whenever the engine (re)connects. Keying
  // recovery on CONNECTIVITY instead of an exact error string is what makes
  // it survive every failure flavor — a transient 500 during engine warm-up,
  // a timeout from a hung engine, or the user dismissing the error note
  // would each permanently disarm a string-gated retry loop.
  useEffect(
    () => engine.on("connected", () => void fetchPage(0, true)),
    [fetchPage],
  );

  // Belt-and-suspenders while the engine is genuinely unreachable: retry on
  // the ENGINE_NOT_CONNECTED sentinel (fetchPage normalizes network-level
  // failures to it), same pattern as use-media-vault. Covers the case where
  // the engine comes back without a fresh WebSocket "connected" event.
  useEffect(() => {
    if (error !== ENGINE_NOT_CONNECTED) return;
    const id = setInterval(() => {
      void fetchPage(0, true);
    }, 3000);
    return () => clearInterval(id);
  }, [error, fetchPage]);

  // Items restored OUT of the vault are library items again — pull them in.
  // Generation announcements use merge (prepend only); vault restore may
  // surface items that are not on the head page and requests a full reload.
  useEffect(
    () =>
      onMediaItemsAdded(({ itemIds, fullRefresh }) => {
        if (fullRefresh) {
          void fetchPage(0, true);
          return;
        }
        void mergeNewItems(itemIds);
      }),
    [fetchPage, mergeNewItems],
  );

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
          if (
            !itemIds.some(
              (id) => id in fileUrlsRef.current || id in thumbUrlsRef.current,
            )
          ) {
            return;
          }
        }
        removalEpochRef.current += 1;
        itemsRef.current = itemsRef.current.filter((i) => !ids.has(i.id));
        setItems((prev) =>
          prev.some((i) => ids.has(i.id))
            ? prev.filter((i) => !ids.has(i.id))
            : prev,
        );
        setThumbUrls((prev) => revokeUrlMap(prev, ids));
        setFileUrls((prev) => revokeUrlMap(prev, ids));
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
    thumbUrls,
    fileUrls,
  };

  const actions = useMemo<MediaLibraryActions>(
    () => ({
      refresh,
      setFilter,
      loadMore,
      getThumbUrl,
      getFileUrl,
      deleteItem,
      removeItems,
      clearError,
    }),
    [
      refresh,
      setFilter,
      loadMore,
      getThumbUrl,
      getFileUrl,
      deleteItem,
      removeItems,
      clearError,
    ],
  );

  return [state, actions];
}
