/**
 * MediaThumb — THE way an image/video is shown anywhere in the app.
 *
 * Four sizes, one behavior. Whether it is a 24px favicon in a row, a filmstrip
 * frame, a library card or a gallery tile:
 *   - click        → opens the full-size lightbox (with its viewing set)
 *   - right-click  → the canonical context menu (every action for that item)
 *   - hover        → an info button and the "⋯" menu (same actions)
 * There is no thumbnail in this app from which a user cannot reach the full
 * image and its metadata.
 *
 * MediaThumb is presentational (it needs a resolved URL). MediaItemThumb wraps
 * it for engine-persisted items: it shows a placeholder immediately, then a
 * capped self-healing thumb sweep fills in small JPEGs. Full media bytes are
 * fetched only when opening the lightbox.
 */

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Image as ImageIcon, Info } from "lucide-react";
import type { MediaLibraryItem } from "@/lib/api";
import { useMediaLibraryApp } from "@/contexts/MediaLibraryContext";
import { useMediaVaultApp } from "@/contexts/MediaVaultContext";
import { useMediaActions } from "./MediaActionsProvider";
import { MediaOverflowMenu } from "./MediaOverflowMenu";
import {
  descriptorFromLibraryItem,
  findMediaIndexById,
  mediaFocusId,
  type MediaDescriptor,
} from "./types";

let mediaItemOpenSequence = 0;

export type MediaThumbVariant =
  /** Tiny inline icon/favicon — hover chrome is a single expand affordance. */
  | "icon"
  /** Horizontal filmstrip frame. */
  | "filmstrip"
  /** Library grid card face (fixed aspect, object-cover). */
  | "card"
  /** Masonry gallery tile (natural aspect). */
  | "gallery";

const FIT: Record<MediaThumbVariant, string> = {
  icon: "h-full w-full object-cover",
  filmstrip: "h-full w-full object-cover",
  card: "h-full w-full object-cover",
  gallery: "w-full",
};

/**
 * Hover chrome on a thumbnail:
 *  - "full" — an info button AND the "⋯" menu (grids, tiles)
 *  - "menu" — just "⋯" (small frames; it still contains "Open full size" and
 *             "Info", so no thumbnail is ever a dead end)
 *  - "none" — no chrome (selection mode, decorative previews). Right-click
 *             still opens the canonical menu.
 */
export type MediaThumbChrome = "full" | "menu" | "none";

export function MediaThumb({
  item,
  variant = "card",
  /**
   * The other media the user can page through from here (a grid, a filmstrip,
   * a queue). Defaults to just this item — a lone thumbnail still opens.
   */
  viewingSet,
  renderKind,
  className = "",
  chrome,
  /**
   * Overrides what a plain click does. Surfaces where a click means something
   * else (a filmstrip frame selects the canvas image) pass this — full size is
   * then always still one step away via the "⋯" menu or right-click, never
   * lost.
   */
  onActivate,
  children,
}: {
  item: MediaDescriptor;
  variant?: MediaThumbVariant;
  viewingSet?: MediaDescriptor[];
  /** Override only the rendered element type, never the descriptor identity. */
  renderKind?: "image" | "video";
  className?: string;
  chrome?: MediaThumbChrome;
  onActivate?: () => void;
  /** Extra overlay content (badges, selection ticks). */
  children?: React.ReactNode;
}) {
  const actions = useMediaActions();
  const chromeMode: MediaThumbChrome =
    chrome ?? (variant === "icon" ? "none" : "full");
  const displayKind = renderKind ?? item.kind;

  const open = () => {
    if (onActivate) {
      onActivate();
      return;
    }
    const set = viewingSet && viewingSet.length > 0 ? viewingSet : [item];
    const index = findMediaIndexById(set, mediaFocusId(item));
    actions.open(set, index >= 0 ? index : 0, mediaFocusId(item));
  };

  // Gallery thumbs are always JPEG posters (including for videos). Full-file
  // video descriptors still use <video> so hover-preview keeps working.
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={onActivate ? "Select media" : `Open ${item.kind}`}
      title={item.prompt || "Open full size"}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        actions.openContextMenu(item, { x: e.clientX, y: e.clientY });
      }}
      className={`group/thumb relative cursor-zoom-in overflow-hidden focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${className}`}
    >
      {displayKind === "video" ? (
        <video
          src={item.url}
          className={FIT[variant]}
          muted
          loop
          playsInline
          onMouseEnter={(e) =>
            void e.currentTarget.play().catch(() => undefined)
          }
          onMouseLeave={(e) => e.currentTarget.pause()}
        />
      ) : (
        <img
          src={item.url}
          alt={item.prompt?.slice(0, 80) ?? "Generated image"}
          className={FIT[variant]}
          loading="lazy"
          draggable={false}
        />
      )}
      {children}
      {chromeMode !== "none" && (
        <div
          className="absolute right-1 top-1 flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover/thumb:opacity-100"
          onClick={(e) => e.stopPropagation()}
        >
          {chromeMode === "full" && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                actions.info(item);
              }}
              aria-label="Info & metadata"
              title="Info & metadata"
              className="flex h-7 w-7 items-center justify-center rounded-md bg-black/50 text-white/85 transition-colors hover:bg-black/70 hover:text-white"
            >
              <Info className="h-3.5 w-3.5" />
            </button>
          )}
          <MediaOverflowMenu item={item} tone="overlay" />
        </div>
      )}
    </div>
  );
}

/**
 * The engine-persisted twin: placeholder first, then a self-healing thumb
 * sweep fills the tile. Clicking fetches the FULL file and opens the lightbox.
 */
export function MediaItemThumb({
  item,
  source = "library",
  variant = "card",
  /** Sibling library items for lightbox paging (full files fetched on demand). */
  viewingItems,
  className = "",
  chrome,
  onActivate,
  children,
}: {
  item: MediaLibraryItem;
  source?: "library" | "vault";
  variant?: MediaThumbVariant;
  viewingItems?: MediaLibraryItem[];
  className?: string;
  chrome?: MediaThumbChrome;
  onActivate?: () => void;
  children?: React.ReactNode;
}) {
  const actions = useMediaActions();
  const [library, libraryActions] = useMediaLibraryApp();
  const [vault, vaultActions] = useMediaVaultApp();
  const isVault = source === "vault";
  const thumbUrls = isVault ? vault.thumbUrls : library.thumbUrls;
  const fileUrls = isVault ? vault.fileUrls : library.fileUrls;
  const getThumbUrl = isVault
    ? vaultActions.getThumbUrl
    : libraryActions.getThumbUrl;
  const getFileUrl = isVault
    ? vaultActions.getFileUrl
    : libraryActions.getFileUrl;

  const thumbUrl = thumbUrls[item.id] ?? null;
  const [failed, setFailed] = useState(false);
  const [opening, setOpening] = useState(false);
  /** Lightbox session this tile is allowed to enrich as sibling URLs arrive. */
  const enrichLightboxSessionRef = useRef<number | null>(null);

  // Placeholder → thumb. getThumbUrl is concurrency-capped and dedupes in
  // flight; the engine generates + caches the JPEG on miss (self-healing).
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    if (!thumbUrl) {
      void getThumbUrl(item.id).then((result) => {
        if (!cancelled && result === null) setFailed(true);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [item.id, thumbUrl, getThumbUrl]);

  // As sibling full files resolve, expand the lightbox viewing set.
  useEffect(() => {
    if (
      enrichLightboxSessionRef.current === null ||
      !viewingItems ||
      viewingItems.length === 0
    ) {
      return;
    }
    const set = viewingSetOf(viewingItems, fileUrls, source);
    if (set.some((d) => d.itemId === item.id)) {
      actions.replaceLightboxItems(
        set,
        item.id,
        enrichLightboxSessionRef.current ?? undefined,
      );
    }
  }, [fileUrls, viewingItems, item.id, source, actions]);

  const aspect =
    item.width > 0 && item.height > 0
      ? `${item.width} / ${item.height}`
      : "1 / 1";

  const openFull = async () => {
    if (onActivate) {
      onActivate();
      return;
    }
    const requestId = ++mediaItemOpenSequence;
    setOpening(true);
    try {
      const fullUrl = await getFileUrl(item.id);
      if (requestId !== mediaItemOpenSequence) return;
      if (!fullUrl) {
        setFailed(true);
        return;
      }
      const siblings =
        viewingItems && viewingItems.length > 0 ? viewingItems : [item];
      // Prefetch neighbors so chevrons work quickly; the rest fill in via the
      // effect above as fileUrls grow.
      const idx = siblings.findIndex((s) => s.id === item.id);
      const near = siblings.slice(Math.max(0, idx - 2), idx + 3);
      for (const sib of near) {
        if (sib.id !== item.id) void getFileUrl(sib.id);
      }
      for (const sib of siblings) {
        if (!near.some((n) => n.id === sib.id)) void getFileUrl(sib.id);
      }
      const clicked = descriptorFromLibraryItem(item, fullUrl, source);
      const initial = viewingSetOf(
        siblings,
        { ...fileUrls, [item.id]: fullUrl },
        source,
      );
      if (!initial.some((d) => d.itemId === item.id)) {
        initial.push(clicked);
      }
      const openIndex = findMediaIndexById(initial, item.id);
      enrichLightboxSessionRef.current = actions.open(
        initial,
        openIndex >= 0 ? openIndex : 0,
        item.id,
      );
    } finally {
      setOpening(false);
    }
  };

  if (failed && !thumbUrl) {
    return (
      <div
        className={`flex items-center justify-center bg-muted/30 text-muted-foreground ${className}`}
        style={variant === "gallery" ? { aspectRatio: aspect } : undefined}
        title="This media could not be loaded"
      >
        <AlertCircle className="h-5 w-5 opacity-40" />
      </div>
    );
  }

  if (!thumbUrl) {
    // Soft placeholder — the thumb sweep will replace this; never block the
    // grid on full-file downloads.
    return (
      <button
        type="button"
        className={`flex cursor-zoom-in items-center justify-center bg-muted/25 text-muted-foreground/50 ${className}`}
        style={variant === "gallery" ? { aspectRatio: aspect } : undefined}
        aria-label="Loading preview"
        onClick={() => void openFull()}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          // Context menu needs a descriptor with a URL — fetch full on demand.
          void getFileUrl(item.id).then((url) => {
            if (url) {
              actions.openContextMenu(
                descriptorFromLibraryItem(item, url, source),
                { x: e.clientX, y: e.clientY },
              );
            }
          });
        }}
      >
        <ImageIcon className="h-6 w-6 opacity-40" />
      </button>
    );
  }

  // Thumb JPEG for videos too — force image kind for the tile so we never
  // attach an <video> element to a JPEG blob.
  const tileItem = descriptorFromLibraryItem(item, thumbUrl, source);

  return (
    <MediaThumb
      item={tileItem}
      variant={variant}
      {...(item.media_type === "video" ? { renderKind: "image" as const } : {})}
      className={`${className}${opening ? " opacity-80" : ""}`}
      onActivate={() => void openFull()}
      {...(chrome !== undefined ? { chrome } : {})}
    >
      {children}
    </MediaThumb>
  );
}

/**
 * The viewing set for a list of engine items: descriptors for every item whose
 * FULL bytes are already resolved. Used by the lightbox (not the grid — grids
 * render via thumbUrls / MediaItemThumb placeholders).
 */
export function viewingSetOf(
  items: MediaLibraryItem[],
  fileUrls: Record<string, string>,
  source: "library" | "vault" = "library",
): MediaDescriptor[] {
  const out: MediaDescriptor[] = [];
  for (const item of items) {
    const url = fileUrls[item.id];
    if (url) out.push(descriptorFromLibraryItem(item, url, source));
  }
  return out;
}
