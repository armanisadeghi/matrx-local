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
 * it for engine-persisted items: it resolves the auth'd blob URL from the ONE
 * media-library / vault store, showing a spinner while it loads and a loud
 * failure marker when it cannot — never a silently broken image.
 */

import { useEffect, useState } from "react";
import { AlertCircle, Info, Loader2 } from "lucide-react";
import type { MediaLibraryItem } from "@/lib/api";
import { useMediaLibraryApp } from "@/contexts/MediaLibraryContext";
import { useMediaVaultApp } from "@/contexts/MediaVaultContext";
import { useMediaActions } from "./MediaActionsProvider";
import { MediaOverflowMenu } from "./MediaOverflowMenu";
import { descriptorFromLibraryItem, type MediaDescriptor } from "./types";

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
  className?: string;
  chrome?: MediaThumbChrome;
  onActivate?: () => void;
  /** Extra overlay content (badges, selection ticks). */
  children?: React.ReactNode;
}) {
  const actions = useMediaActions();
  const chromeMode: MediaThumbChrome =
    chrome ?? (variant === "icon" ? "none" : "full");

  const open = () => {
    if (onActivate) {
      onActivate();
      return;
    }
    const set = viewingSet && viewingSet.length > 0 ? viewingSet : [item];
    const index = set.findIndex((d) => d.id === item.id);
    actions.open(set, index >= 0 ? index : 0);
  };

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
      {item.kind === "video" ? (
        <video
          src={item.url}
          className={FIT[variant]}
          muted
          loop
          playsInline
          onMouseEnter={(e) => void e.currentTarget.play().catch(() => undefined)}
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
 * The engine-persisted twin: resolves the auth'd blob URL for a library/vault
 * item (a plain <img src> cannot carry the Authorization header), then renders
 * the canonical MediaThumb.
 */
export function MediaItemThumb({
  item,
  source = "library",
  variant = "card",
  viewingSet,
  className = "",
  chrome,
  onActivate,
  children,
}: {
  item: MediaLibraryItem;
  source?: "library" | "vault";
  variant?: MediaThumbVariant;
  viewingSet?: MediaDescriptor[];
  className?: string;
  chrome?: MediaThumbChrome;
  onActivate?: () => void;
  children?: React.ReactNode;
}) {
  const [library, libraryActions] = useMediaLibraryApp();
  const [vault, vaultActions] = useMediaVaultApp();
  const isVault = source === "vault";
  const fileUrls = isVault ? vault.fileUrls : library.fileUrls;
  const getFileUrl = isVault ? vaultActions.getFileUrl : libraryActions.getFileUrl;

  const url = fileUrls[item.id] ?? null;
  const [failed, setFailed] = useState(false);

  // One byte-fetch per item id. getFileUrl is stable and dedupes in-flight
  // fetches, so this never stampedes.
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    if (!url) {
      void getFileUrl(item.id).then((result) => {
        if (!cancelled && result === null) setFailed(true);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [item.id, url, getFileUrl]);

  const aspect =
    item.width > 0 && item.height > 0
      ? `${item.width} / ${item.height}`
      : "1 / 1";

  if (failed) {
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
  if (!url) {
    return (
      <div
        className={`flex items-center justify-center bg-muted/30 text-muted-foreground ${className}`}
        style={variant === "gallery" ? { aspectRatio: aspect } : undefined}
      >
        <Loader2 className="h-5 w-5 animate-spin opacity-40" />
      </div>
    );
  }

  return (
    <MediaThumb
      item={descriptorFromLibraryItem(item, url, source)}
      variant={variant}
      className={className}
      {...(viewingSet !== undefined ? { viewingSet } : {})}
      {...(chrome !== undefined ? { chrome } : {})}
      {...(onActivate !== undefined ? { onActivate } : {})}
    >
      {children}
    </MediaThumb>
  );
}

/**
 * The viewing set for a list of engine items: descriptors for every item whose
 * bytes are already resolved. Paging in the lightbox is limited to these (an
 * unresolved item has no URL to show), and the lightbox re-anchors on the
 * CURRENT item id when the set grows, so nothing jumps under the user.
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
