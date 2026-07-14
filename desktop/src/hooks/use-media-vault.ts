/**
 * useMediaVault — data layer for the Private vault (password-protected,
 * engine-side-encrypted folder for generated media).
 *
 * Mirrors the use-media-library discipline exactly:
 *  - `actions` wrapped in useMemo, all callbacks stable (useCallback).
 *  - Init fetch (vault status) lives HERE on [] deps — never in a page effect.
 *  - NO polling. Auto-lock is handled reactively: after auto_lock_seconds the
 *    engine answers 423 to the next request, which flips local state to
 *    locked (items cleared, blob URLs revoked) — never an error toast.
 *  - Blob URLs are owned by the hook and revoked on lock/delete/unmount.
 */

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import {
  engine,
  getMediaVaultStatus,
  createMediaVault,
  unlockMediaVault,
  lockMediaVault,
  listMediaVaultItems,
  fetchMediaVaultFile,
  fetchMediaLibraryThumb,
  MediaFileError,
  moveToMediaVault,
  restoreFromMediaVault,
  deleteMediaVaultItem,
  changeMediaVaultPassword,
  VaultLockedError,
  VaultWrongPasswordError,
} from "@/lib/api";
import type {
  MediaLibraryItem,
  MediaVaultStatus,
  MediaVaultOpResult,
} from "@/lib/api";
import { createConcurrencyLimiter } from "@/lib/concurrency";
import { emitClientLog } from "@/hooks/use-unified-log";
import {
  announceMediaItemsAdded,
  announceMediaItemsRemoved,
  announceVaultLocked,
} from "@/lib/media-events";

const ENGINE_NOT_CONNECTED = "Engine not connected";
const THUMB_FETCH_CONCURRENCY = 6;

/** Fired on the window whenever the vault transitions locked → unlocked.
 * Anything that gave up on a 423 (vault-locked) media read listens for this to
 * retry — e.g. media-gen job thumbnails, whose items may live in the vault. */
export const VAULT_UNLOCKED_EVENT = "matrx:media-vault-unlocked";

function announceUnlocked(): void {
  window.dispatchEvent(new CustomEvent(VAULT_UNLOCKED_EVENT));
}

export interface MediaVaultState {
  /** null until the first /status fetch resolves. */
  status: MediaVaultStatus | null;
  /** True while the initial status fetch is in flight. */
  statusLoading: boolean;
  /** Vault items — only populated while unlocked. */
  items: MediaLibraryItem[];
  /** True while the item list is loading. */
  itemsLoading: boolean;
  /** True while a mutating operation (create/unlock/move/…) is in flight. */
  busy: boolean;
  /** Real failures only — 423 (locked) and 403 (wrong password) never land here. */
  error: string | null;
  /** itemId → small JPEG thumb (gallery). Full bytes stay in fileUrls. */
  thumbUrls: Record<string, string>;
  /** itemId → object URL of decrypted bytes (blob URLs, owned by this hook). */
  fileUrls: Record<string, string>;
}

export interface MediaVaultActions {
  /** Re-fetch status (and items when unlocked). Returns the freshly-fetched
   * status so a caller can act on it without waiting for a re-render. */
  refresh: () => Promise<MediaVaultStatus | null>;
  /** Create the vault (creates AND unlocks). Returns error message or null. */
  create: (password: string) => Promise<string | null>;
  /**
   * Unlock the vault. Returns null on success, or a user-facing error
   * message ("Wrong password" for 403).
   */
  unlock: (password: string) => Promise<string | null>;
  /** Lock now — clears items and revokes blob URLs locally too. */
  lock: () => Promise<void>;
  /**
   * Move library items INTO the vault. Returns per-item results (never
   * throws for partial failures) or null if the request itself failed —
   * in that case state.error carries the message. A 423 returns null and
   * flips state to locked.
   */
  move: (itemIds: string[]) => Promise<MediaVaultOpResult[] | null>;
  /** Restore vault items back to the library. Same result semantics as move. */
  restore: (itemIds: string[]) => Promise<MediaVaultOpResult[] | null>;
  /** Permanently delete a vault item. */
  deleteItem: (itemId: string) => Promise<boolean>;
  /** Change password. Returns null on success or a user-facing error message. */
  changePassword: (
    currentPassword: string,
    newPassword: string,
  ) => Promise<string | null>;
  /**
   * Resolve (and cache) an auth'd blob URL for a vault item's decrypted
   * bytes. Returns null on failure (logged, never silent; 423 flips to
   * locked instead).
   */
  getFileUrl: (itemId: string) => Promise<string | null>;
  /**
   * Small JPEG thumb via /media-library/thumb (resolves vaulted ids).
   * Capped concurrency; self-healing on the engine.
   */
  getThumbUrl: (itemId: string) => Promise<string | null>;
  clearError: () => void;
}

export function useMediaVault(): [MediaVaultState, MediaVaultActions] {
  const [status, setStatus] = useState<MediaVaultStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [items, setItems] = useState<MediaLibraryItem[]>([]);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thumbUrls, setThumbUrls] = useState<Record<string, string>>({});
  const [fileUrls, setFileUrls] = useState<Record<string, string>>({});

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
  /** Incremented on every lock. A fetch that started before the current epoch
   * must not install its (now-stale) decrypted blob URL. */
  const lockEpochRef = useRef(0);
  useEffect(() => {
    thumbUrlsRef.current = thumbUrls;
  }, [thumbUrls]);
  useEffect(() => {
    fileUrlsRef.current = fileUrls;
  }, [fileUrls]);

  /** Revoke every cached blob URL and clear the cache. */
  const revokeAllUrls = useCallback(() => {
    for (const url of Object.values(thumbUrlsRef.current)) {
      URL.revokeObjectURL(url);
    }
    for (const url of Object.values(fileUrlsRef.current)) {
      URL.revokeObjectURL(url);
    }
    thumbUrlsRef.current = {};
    fileUrlsRef.current = {};
    setThumbUrls({});
    setFileUrls({});
  }, []);

  /**
   * The single 423 handler: the vault locked (auto-lock or external lock).
   * Flip state to locked, drop items, revoke URLs. NOT an error.
   */
  const handleLocked = useCallback(() => {
    setStatus((prev) => (prev ? { ...prev, unlocked: false } : prev));
    setItems([]);
    revokeAllUrls();
    // Bump the epoch BEFORE announcing: any byte-fetch still in flight resolves
    // into a stale epoch and throws its decrypted blob away instead of caching
    // it (a decrypted image surviving a lock is the exact leak we are closing).
    lockEpochRef.current += 1;
    // Revoking a URL does not blank an <img> that is already on screen. Tell
    // every other store (the lightbox, the info dialog, the job thumbnails) to
    // drop its vault-derived bytes NOW.
    announceVaultLocked();
  }, [revokeAllUrls]);

  const fetchItems = useCallback(async (): Promise<void> => {
    const base = engine.engineUrl;
    if (!base) return;
    setItemsLoading(true);
    try {
      const list = await listMediaVaultItems(base);
      setItems(list);
      setError(null);
    } catch (e) {
      if (e instanceof VaultLockedError) {
        handleLocked();
        return;
      }
      const msg = e instanceof Error ? e.message : "Failed to load vault items";
      emitClientLog("error", `[media-vault] list failed: ${msg}`, "engine");
      setError(msg);
    } finally {
      setItemsLoading(false);
    }
  }, [handleLocked]);

  const fetchStatus =
    useCallback(async (): Promise<MediaVaultStatus | null> => {
      const base = engine.engineUrl;
      if (!base) {
        setError(ENGINE_NOT_CONNECTED);
        setStatusLoading(false);
        return null;
      }
      try {
        const s = await getMediaVaultStatus(base);
        setStatus(s);
        setError(null);
        return s;
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : "Failed to load vault status";
        emitClientLog("error", `[media-vault] status failed: ${msg}`, "engine");
        setError(msg);
        return null;
      } finally {
        setStatusLoading(false);
      }
    }, []);

  const refresh = useCallback(async (): Promise<MediaVaultStatus | null> => {
    const s = await fetchStatus();
    if (s?.unlocked) await fetchItems();
    return s;
  }, [fetchStatus, fetchItems]);

  const create = useCallback(
    async (password: string): Promise<string | null> => {
      const base = engine.engineUrl;
      if (!base) return ENGINE_NOT_CONNECTED;
      setBusy(true);
      try {
        await createMediaVault(base, password);
        // Create also unlocks — refresh status and load the (empty) list.
        await fetchStatus();
        await fetchItems();
        announceUnlocked();
        return null;
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : "Failed to create the vault";
        emitClientLog("error", `[media-vault] create failed: ${msg}`, "engine");
        return msg;
      } finally {
        setBusy(false);
      }
    },
    [fetchStatus, fetchItems],
  );

  const unlock = useCallback(
    async (password: string): Promise<string | null> => {
      const base = engine.engineUrl;
      if (!base) return ENGINE_NOT_CONNECTED;
      setBusy(true);
      try {
        await unlockMediaVault(base, password);
        await fetchStatus();
        await fetchItems();
        announceUnlocked();
        return null;
      } catch (e) {
        if (e instanceof VaultWrongPasswordError) return "Wrong password";
        const msg =
          e instanceof Error ? e.message : "Failed to unlock the vault";
        emitClientLog("error", `[media-vault] unlock failed: ${msg}`, "engine");
        return msg;
      } finally {
        setBusy(false);
      }
    },
    [fetchStatus, fetchItems],
  );

  const lock = useCallback(async (): Promise<void> => {
    const base = engine.engineUrl;
    if (!base) {
      setError(ENGINE_NOT_CONNECTED);
      return;
    }
    setBusy(true);
    try {
      await lockMediaVault(base);
    } catch (e) {
      // Even if the request fails, lock locally — never leave decrypted
      // bytes on screen after the user asked to lock.
      const msg = e instanceof Error ? e.message : "Failed to lock the vault";
      emitClientLog("error", `[media-vault] lock failed: ${msg}`, "engine");
      setError(msg);
    } finally {
      handleLocked();
      setBusy(false);
    }
  }, [handleLocked]);

  const move = useCallback(
    async (itemIds: string[]): Promise<MediaVaultOpResult[] | null> => {
      const base = engine.engineUrl;
      if (!base) {
        setError(ENGINE_NOT_CONNECTED);
        return null;
      }
      setBusy(true);
      try {
        const resp = await moveToMediaVault(base, itemIds);
        // The moved items are GONE from the plaintext library. Announce the
        // ones that actually moved so the library grid, the job thumbnails and
        // any open lightbox drop them in the same tick — a partial state update
        // here is what leaves a vaulted image rendering from a dead blob URL.
        announceMediaItemsRemoved(
          resp.results.filter((r) => r.ok).map((r) => r.item_id),
          "vaulted",
        );
        // New items landed in the vault — refresh count + list.
        await fetchStatus();
        await fetchItems();
        return resp.results;
      } catch (e) {
        if (e instanceof VaultLockedError) {
          handleLocked();
          return null;
        }
        const msg = e instanceof Error ? e.message : "Move to vault failed";
        emitClientLog("error", `[media-vault] move failed: ${msg}`, "engine");
        setError(msg);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [fetchStatus, fetchItems, handleLocked],
  );

  const restore = useCallback(
    async (itemIds: string[]): Promise<MediaVaultOpResult[] | null> => {
      const base = engine.engineUrl;
      if (!base) {
        setError(ENGINE_NOT_CONNECTED);
        return null;
      }
      setBusy(true);
      try {
        const resp = await restoreFromMediaVault(base, itemIds);
        const restored = new Set(
          resp.results.filter((r) => r.ok).map((r) => r.item_id),
        );
        setItems((prev) => prev.filter((i) => !restored.has(i.id)));
        setFileUrls((prev) => {
          const next = { ...prev };
          for (const id of restored) {
            const url = next[id];
            if (url) {
              URL.revokeObjectURL(url);
              delete next[id];
            }
          }
          return next;
        });
        // Restored items are plaintext library items again — pull them back
        // into the library grid instead of leaving it stale until a refresh.
        announceMediaItemsAdded([...restored]);
        await fetchStatus();
        return resp.results;
      } catch (e) {
        if (e instanceof VaultLockedError) {
          handleLocked();
          return null;
        }
        const msg =
          e instanceof Error ? e.message : "Restore from vault failed";
        emitClientLog(
          "error",
          `[media-vault] restore failed: ${msg}`,
          "engine",
        );
        setError(msg);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [fetchStatus, handleLocked],
  );

  const deleteItem = useCallback(
    async (itemId: string): Promise<boolean> => {
      const base = engine.engineUrl;
      if (!base) {
        setError(ENGINE_NOT_CONNECTED);
        return false;
      }
      try {
        await deleteMediaVaultItem(base, itemId);
        announceMediaItemsRemoved([itemId], "deleted");
        setItems((prev) => prev.filter((i) => i.id !== itemId));
        setFileUrls((prev) => {
          const url = prev[itemId];
          if (!url) return prev;
          URL.revokeObjectURL(url);
          const next = { ...prev };
          delete next[itemId];
          return next;
        });
        void fetchStatus();
        return true;
      } catch (e) {
        if (e instanceof VaultLockedError) {
          handleLocked();
          return false;
        }
        const msg = e instanceof Error ? e.message : "Failed to delete item";
        emitClientLog(
          "error",
          `[media-vault] delete failed for ${itemId}: ${msg}`,
          "engine",
        );
        setError(msg);
        return false;
      }
    },
    [fetchStatus, handleLocked],
  );

  const changePassword = useCallback(
    async (
      currentPassword: string,
      newPassword: string,
    ): Promise<string | null> => {
      const base = engine.engineUrl;
      if (!base) return ENGINE_NOT_CONNECTED;
      setBusy(true);
      try {
        await changeMediaVaultPassword(base, currentPassword, newPassword);
        return null;
      } catch (e) {
        if (e instanceof VaultWrongPasswordError)
          return "Current password is incorrect";
        if (e instanceof VaultLockedError) {
          handleLocked();
          return "The vault locked itself — unlock it and try again";
        }
        const msg =
          e instanceof Error ? e.message : "Failed to change the password";
        emitClientLog(
          "error",
          `[media-vault] change-password failed: ${msg}`,
          "engine",
        );
        return msg;
      } finally {
        setBusy(false);
      }
    },
    [handleLocked],
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
          "[media-vault] file fetch blocked — engine not connected",
        );
        return null;
      }
      const epoch = lockEpochRef.current;
      const task = (async () => {
        try {
          const url = await fetchMediaVaultFile(base, itemId);
          if (lockEpochRef.current !== epoch) {
            // The vault locked while these bytes were in flight. Never cache a
            // decrypted blob across a lock.
            URL.revokeObjectURL(url);
            return null;
          }
          setFileUrls((prev) => ({ ...prev, [itemId]: url }));
          return url;
        } catch (e) {
          if (e instanceof VaultLockedError) {
            handleLocked();
            return null;
          }
          emitClientLog(
            "error",
            `[media-vault] file fetch failed for ${itemId}: ${String(e)}`,
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
    [handleLocked],
  );

  const getThumbUrl = useCallback(
    async (itemId: string): Promise<string | null> => {
      const existing = thumbUrlsRef.current[itemId];
      if (existing) return existing;
      const pending = pendingThumbFetches.current.get(itemId);
      if (pending) return pending;
      const base = engine.engineUrl;
      if (!base) {
        console.error(
          "[media-vault] thumb fetch blocked — engine not connected",
        );
        return null;
      }
      const epoch = lockEpochRef.current;
      const task = limitThumbFetch.current(async () => {
        try {
          // /media-library/thumb resolves vaulted ids the same way /file does.
          const url = await fetchMediaLibraryThumb(base, itemId);
          if (lockEpochRef.current !== epoch) {
            URL.revokeObjectURL(url);
            return null;
          }
          setThumbUrls((prev) => ({ ...prev, [itemId]: url }));
          return url;
        } catch (e) {
          if (
            e instanceof VaultLockedError ||
            (e instanceof MediaFileError && e.isVaultLocked)
          ) {
            handleLocked();
            return null;
          }
          emitClientLog(
            "error",
            `[media-vault] thumb fetch failed for ${itemId}: ${String(e)}`,
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
    [handleLocked],
  );

  const clearError = useCallback(() => setError(null), []);

  // ── Init fetch (in the hook, [] deps) ──────────────────────────────────────
  useEffect(() => {
    void refresh();
  }, [refresh]); // refresh is stable (all deps are []-based useCallbacks)

  // Engine-reconnect retry — engine.engineUrl is set outside React state and
  // isn't reactive. Narrowly gated on the sentinel (same as use-media-library).
  useEffect(() => {
    if (error !== ENGINE_NOT_CONNECTED) return;
    const id = window.setInterval(() => {
      if (engine.engineUrl) void refresh();
    }, 2500);
    return () => window.clearInterval(id);
  }, [error, refresh]);

  // Revoke every object URL on final unmount to avoid leaks.
  useEffect(() => {
    return () => {
      for (const url of Object.values(thumbUrlsRef.current)) {
        URL.revokeObjectURL(url);
      }
      for (const url of Object.values(fileUrlsRef.current)) {
        URL.revokeObjectURL(url);
      }
    };
  }, []);

  const state: MediaVaultState = {
    status,
    statusLoading,
    items,
    itemsLoading,
    busy,
    error,
    thumbUrls,
    fileUrls,
  };

  const actions = useMemo<MediaVaultActions>(
    () => ({
      refresh,
      create,
      unlock,
      lock,
      move,
      restore,
      deleteItem,
      changePassword,
      getFileUrl,
      getThumbUrl,
      clearError,
    }),
    [
      refresh,
      create,
      unlock,
      lock,
      move,
      restore,
      deleteItem,
      changePassword,
      getFileUrl,
      getThumbUrl,
      clearError,
    ],
  );

  return [state, actions];
}
