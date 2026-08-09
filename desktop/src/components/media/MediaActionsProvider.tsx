/**
 * MediaActionsProvider — THE action set for every image/video in the app.
 *
 * One provider owns: the app-wide lightbox, the app-wide info dialog, the
 * right-click context menu, and every action a user can take on a piece of
 * media. Any surface — a 20px favicon, a filmstrip frame, a gallery tile, a
 * queue chip, a fresh result — calls useMediaActions() and gets the SAME
 * behavior. There is no "this thumbnail can't do that" anywhere.
 *
 * Why it must be a provider and not a hook:
 *  - The lightbox and info dialog must survive the unmount of the thing that
 *    opened them (delete an item from the lightbox → the tile disappears →
 *    a locally-owned lightbox would unmount mid-interaction).
 *  - Delete / vault must run against the ONE library + vault store
 *    (MediaLibraryContext / MediaVaultContext), so every surface updates
 *    together. Partial state updates are the bug class this kills.
 *
 * Mounted in App.tsx inside MediaGenProvider/MediaLibraryProvider/
 * MediaVaultProvider. Navigation uses the hash directly (HashRouter lives
 * further down the tree than this provider).
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Check, Lock, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { engine, fetchMediaInitImage, MediaFileError } from "@/lib/api";
import { emitClientLog } from "@/hooks/use-unified-log";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { useMediaLibraryApp } from "@/contexts/MediaLibraryContext";
import { useMediaVaultApp } from "@/contexts/MediaVaultContext";
import { onMediaItemsRemoved, onVaultLocked } from "@/lib/media-events";
import { openExternal } from "@/lib/open-external";
import type { PickedImage } from "@/hooks/use-media-gen";
import { readPickedImage } from "@/components/media-gen/core/pickedImage";
import { MediaLightbox } from "@/components/media-gen/MediaLightbox";
import {
  VaultCreateFlow,
  VaultUnlockForm,
} from "@/components/media-gen/PrivateVaultPanel";
import { MediaInfoDialog } from "./MediaInfoDialog";
import { MediaContextMenu } from "./MediaContextMenu";
import {
  downloadName,
  findMediaIndexById,
  mediaFocusId,
  type MediaDescriptor,
} from "./types";

// ── Public contract ──────────────────────────────────────────────────────────

export interface MediaActions {
  /** Open the lightbox on `items[index]` — the canonical full-size view. */
  open: (
    items: MediaDescriptor[],
    index: number,
    targetId?: string,
  ) => number | null;
  /**
   * Replace the open lightbox's item list while preserving focus by id.
   * Used when sibling full-file URLs finish loading after a thumb-only grid
   * open — no-op when the lightbox is closed.
   */
  replaceLightboxItems: (
    items: MediaDescriptor[],
    targetId?: string,
    sessionId?: number,
  ) => void;
  /** Open the lightbox on a single item (icons, previews, one-off results). */
  openOne: (item: MediaDescriptor) => Promise<void>;
  /** Open the info/metadata dialog (full prompt, seed, every parameter). */
  info: (item: MediaDescriptor) => Promise<void>;
  /** Open the right-click menu at a screen position. */
  openContextMenu: (
    item: MediaDescriptor,
    position: { x: number; y: number },
  ) => void;
  download: (item: MediaDescriptor) => Promise<void>;
  /** Copy the image bytes to the clipboard (images only). */
  copyImage: (item: MediaDescriptor) => Promise<void>;
  copyPrompt: (item: MediaDescriptor) => Promise<void>;
  /** Delete from the library or the vault (whichever holds it). */
  remove: (item: MediaDescriptor) => Promise<boolean>;
  /** Move into the Private vault (unlocking/creating it if needed). */
  moveToVault: (item: MediaDescriptor) => Promise<void>;
  /** Restore a vaulted item back to the plaintext library. */
  restoreFromVault: (item: MediaDescriptor) => Promise<void>;
  /** Put this image into the img2img input slot. */
  useAsInput: (item: MediaDescriptor) => Promise<void>;
  /** Start a durable Z-Image/FLUX revision branch from this image. */
  iterate: (item: MediaDescriptor) => Promise<void>;
  /**
   * Reproduce EXACTLY what generated this: model, prompt, negative prompt,
   * seed, size, steps, guidance, strength, LoRAs, every advanced pipeline
   * kwarg, and the input image it was generated from.
   */
  remix: (item: MediaDescriptor) => Promise<void>;
  /** Put this item's seed into the generate form. */
  reuseSeed: (item: MediaDescriptor) => void;
  showInFolder: (item: MediaDescriptor) => Promise<void>;
  /** Open the media's own web address in the user's browser (`sourceUrl`). */
  openSource: (item: MediaDescriptor) => Promise<void>;
  /** Show a transient success/failure line (used by the menus). */
  notify: (message: string, kind?: "ok" | "error") => void;
}

const Ctx = createContext<MediaActions | null>(null);

/** The canonical media action set. Throws outside MediaActionsProvider. */
export function useMediaActions(): MediaActions {
  const ctx = useContext(Ctx);
  if (!ctx) {
    throw new Error("useMediaActions must be used within MediaActionsProvider");
  }
  return ctx;
}

/**
 * Signals the Private-vault UI that a vault unlock (or creation) is needed
 * before a queued move can run. MediaLibrarySection owns that dialog, so the
 * provider asks for it rather than duplicating the flow.
 */
export const VAULT_MOVE_REQUESTED_EVENT = "matrx:media-vault-move-requested";

export interface VaultMoveRequest {
  itemIds: string[];
}

// ── Toast ────────────────────────────────────────────────────────────────────

interface Toast {
  id: number;
  message: string;
  kind: "ok" | "error";
}

function ToastStack({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 left-1/2 z-[10001] flex -translate-x-1/2 flex-col items-center gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className={`pointer-events-auto flex max-w-md items-center gap-2 rounded-lg border px-3 py-2 text-xs shadow-lg ${
            t.kind === "error"
              ? "border-destructive/40 bg-destructive/10 text-destructive"
              : "border-green-500/40 bg-green-500/10 text-green-700 dark:text-green-400"
          }`}
        >
          {t.kind === "error" ? (
            <X className="h-3.5 w-3.5 shrink-0" />
          ) : (
            <Check className="h-3.5 w-3.5 shrink-0" />
          )}
          <span className="min-w-0 flex-1 break-words">{t.message}</span>
          <button
            type="button"
            onClick={() => onDismiss(t.id)}
            aria-label="Dismiss"
            className="shrink-0 opacity-60 hover:opacity-100"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}

// ── Provider ─────────────────────────────────────────────────────────────────

export function MediaActionsProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [, mediaGenActions] = useMediaGenApp();
  const {
    setImageForm,
    setVideoForm,
    useImageAsInput,
    beginImageRevision,
    prepareImageGenerate,
    remixImageForm,
    refreshImage,
    getImageModels,
  } = mediaGenActions;
  const [, libraryActions] = useMediaLibraryApp();
  const {
    deleteItem: deleteLibraryItem,
    getFileUrl: getLibraryFileUrl,
  } = libraryActions;
  const [vault, vaultActions] = useMediaVaultApp();
  const {
    deleteItem: deleteVaultItem,
    getFileUrl: getVaultFileUrl,
    move: vaultMove,
    restore: vaultRestore,
  } = vaultActions;

  // ── Overlay state ────────────────────────────────────────────────────────
  const [lightbox, setLightbox] = useState<{
    items: MediaDescriptor[];
    index: number;
    focusId: string | null;
    sessionId: number;
  } | null>(null);
  const [infoItem, setInfoItem] = useState<MediaDescriptor | null>(null);
  const [menu, setMenu] = useState<{
    item: MediaDescriptor;
    position: { x: number; y: number };
  } | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastSeq = useRef(0);
  const lightboxSessionSeq = useRef(0);
  const viewerRequestSeq = useRef(0);
  const infoRequestSeq = useRef(0);

  // ── Vault move flow (owned HERE so it works from every surface) ──────────
  const [unlockOpen, setUnlockOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  /** Ids waiting on a vault create/unlock before their move can run. */
  const pendingMoveIds = useRef<string[] | null>(null);

  const notify = useCallback((message: string, kind: "ok" | "error" = "ok") => {
    const id = ++toastSeq.current;
    setToasts((prev) => [...prev, { id, message, kind }]);
    window.setTimeout(
      () => setToasts((prev) => prev.filter((t) => t.id !== id)),
      kind === "error" ? 6000 : 2500,
    );
  }, []);

  const dismissToast = useCallback(
    (id: number) => setToasts((prev) => prev.filter((t) => t.id !== id)),
    [],
  );

  /** Drop every overlay descriptor matching `isGone`, clamping the lightbox. */
  const pruneOverlays = useCallback(
    (isGone: (d: MediaDescriptor) => boolean) => {
      setInfoItem((prev) => (prev && isGone(prev) ? null : prev));
      setMenu((prev) => (prev && isGone(prev.item) ? null : prev));
      setLightbox((prev) => {
        if (!prev) return prev;
        const kept = prev.items.filter((d) => !isGone(d));
        if (kept.length === prev.items.length) return prev;
        if (kept.length === 0) return null;
        // Stay on the same slot; clamp when the tail was removed.
        const index = Math.min(prev.index, kept.length - 1);
        const focused = kept[index];
        return {
          items: kept,
          index,
          focusId: prev.focusId ?? (focused ? mediaFocusId(focused) : null),
          sessionId: prev.sessionId,
        };
      });
    },
    [],
  );

  // An item that left the library/vault must vanish from every overlay that is
  // currently showing it — otherwise the lightbox keeps a revoked blob URL and
  // the info dialog describes a file the engine no longer has.
  useEffect(
    () =>
      onMediaItemsRemoved(({ itemIds }) => {
        const gone = new Set(itemIds);
        pruneOverlays((d) => d.itemId !== null && gone.has(d.itemId));
      }),
    [pruneOverlays],
  );

  // The vault LOCKED. Revoking a blob URL does not blank an <img> that already
  // rendered — so a vault image open in the lightbox (or the info dialog) would
  // stay fully visible after locking. Close them. This is the lock actually
  // meaning something.
  useEffect(
    () => onVaultLocked(() => pruneOverlays((d) => d.source === "vault")),
    [pruneOverlays],
  );

  // ── Actions ──────────────────────────────────────────────────────────────

  const resolveFullItem = useCallback(
    async (item: MediaDescriptor): Promise<MediaDescriptor | null> => {
      if (!item.itemId || item.source === "result") return item;
      const url =
        item.source === "vault"
          ? await getVaultFileUrl(item.itemId)
          : await getLibraryFileUrl(item.itemId);
      if (!url) return null;
      return { ...item, url };
    },
    [getLibraryFileUrl, getVaultFileUrl],
  );

  const open = useCallback(
    (items: MediaDescriptor[], index: number, targetId?: string) => {
      if (items.length === 0) return null;
      viewerRequestSeq.current += 1;
      const targetIndex = findMediaIndexById(items, targetId);
      const clampedIndex =
        targetIndex >= 0
          ? targetIndex
          : Math.min(Math.max(index, 0), items.length - 1);
      const focused = items[clampedIndex];
      if (!focused) return null;
      const sessionId = ++lightboxSessionSeq.current;
      setLightbox({
        items,
        index: clampedIndex,
        focusId: targetId ?? mediaFocusId(focused),
        sessionId,
      });
      return sessionId;
    },
    [],
  );

  const replaceLightboxItems = useCallback(
    (items: MediaDescriptor[], targetId?: string, sessionId?: number) => {
      setLightbox((prev) => {
        if (!prev || items.length === 0) return prev;
        if (sessionId !== undefined && prev.sessionId !== sessionId) {
          return prev;
        }
        if (targetId && prev.focusId !== targetId) return prev;
        if (prev.focusId && findMediaIndexById(items, prev.focusId) < 0) {
          return prev;
        }
        return {
          items,
          // The lightbox owns live navigation state. Keep the provider's seed
          // stable while background sibling file fetches expand the browse set;
          // MediaLightbox re-anchors the visible item by exact file id.
          index: Math.min(prev.index, items.length - 1),
          focusId: prev.focusId,
          sessionId: prev.sessionId,
        };
      });
    },
    [],
  );

  const openOne = useCallback(
    async (item: MediaDescriptor) => {
      const requestId = ++viewerRequestSeq.current;
      const fullItem = await resolveFullItem(item);
      if (requestId !== viewerRequestSeq.current) return;
      if (!fullItem) {
        notify("Could not load the full media file", "error");
        return;
      }
      const sessionId = ++lightboxSessionSeq.current;
      setLightbox({
        items: [fullItem],
        index: 0,
        focusId: mediaFocusId(fullItem),
        sessionId,
      });
    },
    [resolveFullItem, notify],
  );

  const info = useCallback(
    async (item: MediaDescriptor) => {
      const requestId = ++infoRequestSeq.current;
      const fullItem = await resolveFullItem(item);
      if (requestId !== infoRequestSeq.current) return;
      if (!fullItem) {
        notify("Could not load the full media file", "error");
        return;
      }
      setInfoItem(fullItem);
    },
    [resolveFullItem, notify],
  );

  const openContextMenu = useCallback(
    (item: MediaDescriptor, position: { x: number; y: number }) =>
      setMenu({ item, position }),
    [],
  );

  const download = useCallback(
    async (item: MediaDescriptor) => {
      try {
        const a = document.createElement("a");
        a.href = item.url;
        a.download = downloadName(item);
        a.click();
        notify("Download started");
      } catch (e) {
        notify(
          `Download failed: ${e instanceof Error ? e.message : String(e)}`,
          "error",
        );
      }
    },
    [notify],
  );

  const openSource = useCallback(
    async (item: MediaDescriptor) => {
      if (!item.sourceUrl) {
        notify("This media has no web address", "error");
        return;
      }
      try {
        await openExternal(item.sourceUrl);
      } catch (e) {
        notify(
          `Could not open the URL: ${e instanceof Error ? e.message : String(e)}`,
          "error",
        );
      }
    },
    [notify],
  );

  const copyImage = useCallback(
    async (item: MediaDescriptor) => {
      if (item.kind !== "image") {
        notify("Only images can be copied to the clipboard", "error");
        return;
      }
      try {
        const blob = await (await fetch(item.url)).blob();
        // Clipboard image support is PNG-only across browsers/webviews.
        const png =
          blob.type === "image/png"
            ? blob
            : new Blob([await blob.arrayBuffer()], { type: "image/png" });
        await navigator.clipboard.write([
          new ClipboardItem({ "image/png": png }),
        ]);
        notify("Image copied to clipboard");
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        emitClientLog(
          "error",
          `[media] copy image failed for ${item.id}: ${msg}`,
          "engine",
        );
        notify(`Could not copy the image: ${msg}`, "error");
      }
    },
    [notify],
  );

  const copyPrompt = useCallback(
    async (item: MediaDescriptor) => {
      const prompt = item.prompt?.trim();
      if (!prompt) {
        notify("This item has no prompt", "error");
        return;
      }
      try {
        await navigator.clipboard.writeText(prompt);
        notify("Prompt copied");
      } catch (e) {
        notify(
          `Could not copy the prompt: ${e instanceof Error ? e.message : String(e)}`,
          "error",
        );
      }
    },
    [notify],
  );

  const remove = useCallback(
    async (item: MediaDescriptor): Promise<boolean> => {
      if (!item.itemId) {
        notify("This item was never saved — nothing to delete", "error");
        return false;
      }
      const ok =
        item.source === "vault"
          ? await deleteVaultItem(item.itemId)
          : await deleteLibraryItem(item.itemId);
      // Both paths announce the removal, which prunes the lightbox, the info
      // dialog, the library grid and the job thumbnails in one tick.
      notify(ok ? "Deleted" : "Delete failed", ok ? "ok" : "error");
      return ok;
    },
    [deleteLibraryItem, deleteVaultItem, notify],
  );

  /**
   * Run a vault move for ids, once the vault is known to be unlocked.
   * Exported through the event below so the Library's multi-select uses the
   * same single implementation.
   */
  const performVaultMove = useCallback(
    async (itemIds: string[]) => {
      const results = await vaultMove(itemIds);
      if (results === null) {
        // Either a request failure, or a 423 that just flipped us to locked.
        // Re-arm the unlock prompt instead of dropping the user silently.
        pendingMoveIds.current = itemIds;
        setUnlockOpen(true);
        return;
      }
      const failures = results.filter((r) => !r.ok);
      const okCount = results.length - failures.length;
      if (failures.length > 0) {
        notify(
          `Move to Private failed for ${failures.length} item${
            failures.length === 1 ? "" : "s"
          }: ${failures[0]?.error ?? "unknown error"}`,
          "error",
        );
      }
      if (okCount > 0) {
        notify(`${okCount} item${okCount === 1 ? "" : "s"} moved to Private`);
      }
    },
    [vaultMove, notify],
  );

  /**
   * "Move to Private" from ANY surface. The vault may not exist yet, or may be
   * locked — this provider owns that flow, so the action works identically from
   * the library grid, a queue thumbnail, the lightbox or a right-click on a
   * page where the Library section is not even mounted.
   *
   * (It previously fired an event that only MediaLibrarySection listened for,
   * so on every other surface the menu item did nothing at all.)
   */
  const startVaultMove = useCallback(
    async (itemIds: string[]) => {
      if (itemIds.length === 0) return;
      // The cached status can be stale (the engine auto-locks on a timer), so
      // re-check before deciding. refresh() RETURNS the freshly-fetched status —
      // reading vaultStatusRef here would still hold the pre-refresh value,
      // because an await does not flush React's render + effect cycle.
      const status = await vaultActions.refresh();
      if (!status?.exists) {
        pendingMoveIds.current = itemIds;
        setCreateOpen(true);
        return;
      }
      if (!status.unlocked) {
        pendingMoveIds.current = itemIds;
        setUnlockOpen(true);
        return;
      }
      await performVaultMove(itemIds);
    },
    [vaultActions, performVaultMove],
  );

  const moveToVault = useCallback(
    async (item: MediaDescriptor) => {
      if (!item.itemId) {
        notify("This item was never saved — nothing to move", "error");
        return;
      }
      await startVaultMove([item.itemId]);
    },
    [startVaultMove, notify],
  );

  // The Library's multi-select "Move to Private" routes through the SAME flow.
  useEffect(() => {
    const onRequest = (e: Event) => {
      const detail = (e as CustomEvent<VaultMoveRequest>).detail;
      if (detail?.itemIds?.length) void startVaultMove(detail.itemIds);
    };
    window.addEventListener(VAULT_MOVE_REQUESTED_EVENT, onRequest);
    return () =>
      window.removeEventListener(VAULT_MOVE_REQUESTED_EVENT, onRequest);
  }, [startVaultMove]);

  // Vault became unlocked (the user created it or unlocked it in our dialog) →
  // run the move that was waiting on it. Narrowly gated on the boolean.
  const vaultUnlocked = vault.status?.unlocked === true;
  useEffect(() => {
    if (!vaultUnlocked) return;
    const ids = pendingMoveIds.current;
    if (!ids || ids.length === 0) return;
    pendingMoveIds.current = null;
    setUnlockOpen(false);
    setCreateOpen(false);
    void performVaultMove(ids);
  }, [vaultUnlocked, performVaultMove]);

  const restoreFromVault = useCallback(
    async (item: MediaDescriptor) => {
      if (!item.itemId) return;
      const results = await vaultRestore([item.itemId]);
      if (results === null) {
        notify(vault.error ?? "Restore failed", "error");
        return;
      }
      const failure = results.find((r) => !r.ok);
      notify(
        failure
          ? `Restore failed: ${failure.error ?? "unknown error"}`
          : "Restored to your library",
        failure ? "error" : "ok",
      );
    },
    [vaultRestore, vault.error, notify],
  );

  /** Read an image URL into a PickedImage (the img2img input shape). */
  const pickFromUrl = useCallback(
    async (url: string, name: string): Promise<PickedImage | null> => {
      try {
        const blob = await (await fetch(url)).blob();
        const file = new File([blob], name, {
          type: blob.type || "image/png",
        });
        return await new Promise<PickedImage | null>((resolve) => {
          readPickedImage(
            file,
            (img) => resolve(img),
            (msg) => {
              notify(msg, "error");
              resolve(null);
            },
          );
        });
      } catch (e) {
        notify(
          `Could not read the image: ${e instanceof Error ? e.message : String(e)}`,
          "error",
        );
        return null;
      }
    },
    [notify],
  );

  const useAsInput = useCallback(
    async (item: MediaDescriptor) => {
      if (item.kind !== "image") {
        notify("Only images can be used as an input image", "error");
        return;
      }
      const img = await pickFromUrl(
        item.url,
        item.fileName || `${item.id}.png`,
      );
      if (!img) return;
      useImageAsInput(img);
      setLightbox(null);
      setInfoItem(null);
      notify("Set as the input image");
      window.location.hash = "#/media-generation";
    },
    [pickFromUrl, useImageAsInput, notify],
  );

  const iterate = useCallback(
    async (item: MediaDescriptor) => {
      if (item.kind !== "image" || !item.itemId || !item.modelId) {
        notify("This image has no persisted generation record to iterate", "error");
        return;
      }
      const modelId = item.modelId;
      let model = getImageModels().find((m) => m.model_id === modelId);
      if (!model) {
        await refreshImage();
        model = getImageModels().find((m) => m.model_id === modelId);
      }
      if (!model) {
        notify(`The source model (${modelId}) is not in the local catalog.`, "error");
        return;
      }
      if (
        !model.supports_img2img ||
        !["z-image", "flux", "flux2-klein"].includes(model.pipeline_type)
      ) {
        notify("Iterative revision is currently available for Z-Image and FLUX models.", "error");
        return;
      }
      const image = await pickFromUrl(
        item.url,
        item.fileName || `${item.itemId}.png`,
      );
      if (!image) return;

      await prepareImageGenerate(model);
      remixImageForm({
        prompt: item.prompt ?? "",
        negativePrompt: item.negativePrompt ?? "",
        seed: null,
        ...(item.width ? { width: item.width } : {}),
        ...(item.height ? { height: item.height } : {}),
        ...(item.params ? { params: item.params } : {}),
      });
      const recordedRoot = item.params?.["revision_root_item_id"];
      beginImageRevision(
        image,
        item.itemId,
        typeof recordedRoot === "string" ? recordedRoot : item.itemId,
      );
      if (model.pipeline_type === "flux2-klein") {
        setImageForm({ prompt: "" });
      }
      setLightbox(null);
      setInfoItem(null);
      notify(
        model.pipeline_type === "flux2-klein"
          ? "Revision ready — describe the change, then Apply"
          : "Revision ready — adjust the prompt, then Apply",
      );
      window.location.hash = "#/media-generation";
    },
    [
      beginImageRevision,
      getImageModels,
      notify,
      pickFromUrl,
      prepareImageGenerate,
      refreshImage,
      remixImageForm,
      setImageForm,
    ],
  );

  const reuseSeed = useCallback(
    (item: MediaDescriptor) => {
      if (typeof item.seed !== "number") {
        notify("No seed was recorded for this item", "error");
        return;
      }
      if (item.kind === "video") setVideoForm({ seedText: String(item.seed) });
      else setImageForm({ seedText: String(item.seed) });
      notify(`Seed ${item.seed} put into the form`);
    },
    [setImageForm, setVideoForm, notify],
  );

  /**
   * Remix — the whole point of recording the full generation kwargs. Rebuilds
   * the form into EXACTLY the state that produced this item, including the
   * input image, then drops the user on the generate view.
   */
  const remix = useCallback(
    async (item: MediaDescriptor) => {
      if (item.kind === "video") {
        // The video form has no per-model advanced remix path yet; restore
        // everything the video form models, and say so rather than pretending.
        setVideoForm({
          ...(item.prompt !== undefined ? { prompt: item.prompt } : {}),
          ...(item.negativePrompt !== undefined
            ? { negativePrompt: item.negativePrompt }
            : {}),
          ...(typeof item.seed === "number"
            ? { seedText: String(item.seed) }
            : {}),
          ...(item.width ? { width: item.width } : {}),
          ...(item.height ? { height: item.height } : {}),
          ...(item.numFrames ? { numFrames: item.numFrames } : {}),
          ...(item.fps ? { fps: item.fps } : {}),
        });
        setLightbox(null);
        setInfoItem(null);
        notify("Video settings restored — pick the model and generate");
        window.location.hash = "#/media-generation";
        return;
      }

      if (!item.modelId) {
        notify("This item has no recorded model — cannot remix", "error");
        return;
      }
      const modelId = item.modelId;
      let model = getImageModels().find((m) => m.model_id === modelId);
      if (!model) {
        // The catalog may not have loaded yet on a cold page. getImageModels()
        // reads a ref the store updates SYNCHRONOUSLY on fetch, so it is fresh
        // right after the await — an effect-synced local ref would not be, and
        // a re-read of the render-captured `imageModels` never could be.
        await refreshImage();
        model = getImageModels().find((m) => m.model_id === modelId);
      }
      if (!model) {
        notify(
          `The model this was generated with (${modelId}) is not in the model catalog — cannot remix.`,
          "error",
        );
        return;
      }
      if (model.is_downloaded === false) {
        notify(
          `Settings restored, but ${model.name} is not downloaded on this machine — download it, then generate.`,
          "error",
        );
      }

      // Load the model's parameter schema and reset the form to its defaults;
      // everything below then overrides those defaults with what was actually
      // used. Doing it in this order means a param the model no longer accepts
      // simply falls back to the default instead of poisoning the request.
      await prepareImageGenerate(model);

      // One atomic patch, applied on top of the freshly-resolved model
      // defaults (the store builds the advanced-JSON from them — see
      // remixImageForm).
      remixImageForm({
        prompt: item.prompt ?? "",
        negativePrompt: item.negativePrompt ?? "",
        seed: item.seed ?? null,
        ...(item.width ? { width: item.width } : {}),
        ...(item.height ? { height: item.height } : {}),
        ...(item.params ? { params: item.params } : {}),
      });

      // Input image: restore the actual bytes the generation started from.
      //
      // We ATTEMPT the fetch whenever the generation used one and we know the
      // engine's id for it. Only the engine knows whether the bytes were
      // actually stored, so a 404 here is a normal answer ("not stored"), not a
      // failure — and it is the honest way to handle descriptors built from a
      // job or a fresh result, which cannot know.
      const usedInitImage = item.hasInitImage === true;
      if (usedInitImage && item.itemId) {
        const base = engine.engineUrl;
        if (!base) {
          notify(
            "Settings restored, but the input image could not be loaded — the engine is not connected.",
            "error",
          );
        } else {
          try {
            const blob = await fetchMediaInitImage(base, item.itemId);
            const url = URL.createObjectURL(blob);
            try {
              const img = await pickFromUrl(url, `${item.itemId}-input.png`);
              if (img) useImageAsInput(img);
            } finally {
              URL.revokeObjectURL(url);
            }
            notify(`Remixed — ${model.name}, input image and all settings`);
          } catch (e) {
            const notStored = e instanceof MediaFileError && e.status === 404;
            notify(
              notStored
                ? "Settings restored — but this image was made from an input image the engine did not keep, so add one back to reproduce it exactly."
                : `Settings restored, but the input image could not be loaded: ${
                    e instanceof Error ? e.message : String(e)
                  }`,
              "error",
            );
          }
        }
      } else if (usedInitImage) {
        notify(
          "Settings restored — but this image was made from an input image that is no longer available, so add one back to reproduce it exactly.",
          "error",
        );
      } else if (model.is_downloaded !== false) {
        notify(`Remixed — ${model.name} ready to generate`);
      }

      setLightbox(null);
      setInfoItem(null);
      window.location.hash = "#/media-generation";
    },
    [
      refreshImage,
      getImageModels,
      prepareImageGenerate,
      remixImageForm,
      setVideoForm,
      useImageAsInput,
      pickFromUrl,
      notify,
    ],
  );

  const showInFolder = useCallback(
    async (item: MediaDescriptor) => {
      if (!item.filePath) {
        notify("This item has no file on disk", "error");
        return;
      }
      try {
        const dir = item.filePath.replace(/[/\\][^/\\]*$/, "");
        const { open: openPath } = await import("@tauri-apps/plugin-shell");
        await openPath(dir || item.filePath);
      } catch (e) {
        notify(
          `Could not open the folder: ${e instanceof Error ? e.message : String(e)}`,
          "error",
        );
      }
    },
    [notify],
  );

  const actions = useMemo<MediaActions>(
    () => ({
      open,
      replaceLightboxItems,
      openOne,
      info,
      openContextMenu,
      download,
      copyImage,
      copyPrompt,
      remove,
      moveToVault,
      restoreFromVault,
      useAsInput,
      iterate,
      remix,
      reuseSeed,
      showInFolder,
      openSource,
      notify,
    }),
    [
      open,
      replaceLightboxItems,
      openOne,
      info,
      openContextMenu,
      download,
      copyImage,
      copyPrompt,
      remove,
      moveToVault,
      restoreFromVault,
      useAsInput,
      iterate,
      remix,
      reuseSeed,
      showInFolder,
      openSource,
      notify,
    ],
  );

  return (
    <Ctx.Provider value={actions}>
      {children}
      <MediaLightbox
        open={lightbox !== null}
        items={lightbox?.items ?? []}
        startIndex={lightbox?.index ?? 0}
        startId={lightbox?.focusId ?? null}
        onClose={() => setLightbox(null)}
      />
      <MediaInfoDialog item={infoItem} onClose={() => setInfoItem(null)} />
      {menu && (
        <MediaContextMenu
          item={menu.item}
          position={menu.position}
          onClose={() => setMenu(null)}
        />
      )}

      {/* Unlock / create prompts for "Move to Private". They live HERE, at the
          app level, so the action works from any surface — a right-click on a
          queue thumbnail on a page where the Library section isn't mounted used
          to do nothing at all. */}
      <Dialog
        open={unlockOpen}
        onOpenChange={(open) => {
          setUnlockOpen(open);
          if (!open) pendingMoveIds.current = null; // user backed out
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Lock className="h-4 w-4 text-violet-500" />
              Unlock Private
            </DialogTitle>
            <DialogDescription className="text-xs">
              Unlock the vault to move{" "}
              {(pendingMoveIds.current?.length ?? 1) === 1
                ? "this item"
                : `these ${pendingMoveIds.current?.length} items`}{" "}
              to Private.
            </DialogDescription>
          </DialogHeader>
          <VaultUnlockForm
            actions={vaultActions}
            busy={vault.busy}
            autoLockSeconds={vault.status?.auto_lock_seconds ?? null}
          />
        </DialogContent>
      </Dialog>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) pendingMoveIds.current = null;
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Lock className="h-4 w-4 text-violet-500" />
              Create your Private vault
            </DialogTitle>
            <DialogDescription className="text-xs">
              Pick a password. Your media is encrypted with it — there is no
              recovery if you forget it.
            </DialogDescription>
          </DialogHeader>
          <VaultCreateFlow actions={vaultActions} busy={vault.busy} />
        </DialogContent>
      </Dialog>

      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </Ctx.Provider>
  );
}
