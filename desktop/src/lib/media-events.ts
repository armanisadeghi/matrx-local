/**
 * Cross-store media events.
 *
 * The media library, the private vault and the media-gen job queue are three
 * separate stores that all reference the SAME engine item ids. When an item
 * leaves the library (deleted, or moved into the vault), every other store
 * holding a reference to that id must update in the same tick — otherwise you
 * get the partial-state bugs: a deleted image still in the queue thumbnails, a
 * vaulted image still rendering from a revoked blob URL, a lightbox pointing at
 * an id the engine no longer serves.
 *
 * Leaf module (no imports) so any store can listen without an import cycle.
 */

/** Item ids that just left the plaintext media library (deleted or vaulted). */
export const MEDIA_ITEMS_REMOVED_EVENT = "matrx:media-items-removed";

export interface MediaItemsRemovedDetail {
  itemIds: string[];
  reason: "deleted" | "vaulted";
}

export function announceMediaItemsRemoved(
  itemIds: string[],
  reason: MediaItemsRemovedDetail["reason"],
): void {
  if (itemIds.length === 0) return;
  window.dispatchEvent(
    new CustomEvent<MediaItemsRemovedDetail>(MEDIA_ITEMS_REMOVED_EVENT, {
      detail: { itemIds, reason },
    }),
  );
}

export function onMediaItemsRemoved(
  handler: (detail: MediaItemsRemovedDetail) => void,
): () => void {
  const listener = (e: Event) => {
    const detail = (e as CustomEvent<MediaItemsRemovedDetail>).detail;
    if (detail?.itemIds?.length) handler(detail);
  };
  window.addEventListener(MEDIA_ITEMS_REMOVED_EVENT, listener);
  return () => window.removeEventListener(MEDIA_ITEMS_REMOVED_EVENT, listener);
}

/**
 * The Private vault just LOCKED (user action, or the engine auto-locked and
 * answered 423).
 *
 * Revoking a blob URL does not blank an <img> that is already rendered. So a
 * lock that only revokes URLs leaves decrypted vault images on screen — in an
 * open lightbox, in the info dialog, in the media-gen queue thumbnails — for
 * the rest of the session. Every store holding vault-derived bytes must drop
 * them when this fires. That is the whole point of a lock.
 */
export const MEDIA_VAULT_LOCKED_EVENT = "matrx:media-vault-locked";

export function announceVaultLocked(): void {
  window.dispatchEvent(new CustomEvent(MEDIA_VAULT_LOCKED_EVENT));
}

export function onVaultLocked(handler: () => void): () => void {
  window.addEventListener(MEDIA_VAULT_LOCKED_EVENT, handler);
  return () => window.removeEventListener(MEDIA_VAULT_LOCKED_EVENT, handler);
}

/** Item ids that just entered the plaintext media library. */
export const MEDIA_ITEMS_ADDED_EVENT = "matrx:media-items-added";

export interface MediaItemsAddedDetail {
  itemIds: string[];
  /**
   * Reload page 0 — vault restore can surface items that are not on the
   * newest-first head page. Generation announcements omit this so the library
   * can prepend without resetting filter, scroll, or load-more pages.
   */
  fullRefresh?: boolean;
}

export function announceMediaItemsAdded(
  itemIds: string[],
  opts?: Pick<MediaItemsAddedDetail, "fullRefresh">,
): void {
  if (itemIds.length === 0) return;
  const detail: MediaItemsAddedDetail = { itemIds };
  if (opts?.fullRefresh) detail.fullRefresh = true;
  window.dispatchEvent(
    new CustomEvent<MediaItemsAddedDetail>(MEDIA_ITEMS_ADDED_EVENT, {
      detail,
    }),
  );
}

export function onMediaItemsAdded(
  handler: (detail: MediaItemsAddedDetail) => void,
): () => void {
  const listener = (e: Event) => {
    const detail = (e as CustomEvent<MediaItemsAddedDetail>).detail;
    if (detail?.itemIds?.length) handler(detail);
  };
  window.addEventListener(MEDIA_ITEMS_ADDED_EVENT, listener);
  return () => window.removeEventListener(MEDIA_ITEMS_ADDED_EVENT, listener);
}
