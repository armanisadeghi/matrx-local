/**
 * MediaLibrarySection — the "Library" tab of the Media Generation page.
 *
 * A responsive grid of every image/video the engine has persisted, with an
 * image/video filter, multi-select → "Move to Private", and the Private vault
 * panel.
 *
 * It owns NO image UI of its own. Every tile is a canonical <MediaItemThumb>
 * (click → the app-wide lightbox, right-click → the canonical context menu,
 * hover → info + "⋯"), and every action — download, copy image, copy prompt,
 * delete, vault, use-as-input, remix, show in folder — comes from
 * useMediaActions(). That is why the library, the gallery, the studio filmstrip
 * and a 24px icon all behave identically.
 *
 * Data comes from the ONE app-level media-library store (MediaLibraryContext),
 * so a delete or a vault move here updates every other surface in the same tick.
 */

import { useState, useCallback, useMemo, useRef } from "react";
import {
  CheckSquare,
  Film,
  Image as ImageIcon,
  Loader2,
  Lock,
  RefreshCw,
  Square,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaLibraryApp } from "@/contexts/MediaLibraryContext";
import { useMediaVaultApp } from "@/contexts/MediaVaultContext";
import type { MediaLibraryFilter } from "@/hooks/use-media-library";
import type { MediaLibraryItem } from "@/lib/api";
import { MediaItemThumb, viewingSetOf } from "@/components/media/MediaThumb";
import { CopyButton } from "@/components/media/MediaInfoDialog";
import {
  VAULT_MOVE_REQUESTED_EVENT,
  type VaultMoveRequest,
} from "@/components/media/MediaActionsProvider";
import { formatDate } from "@/components/media/types";
import { ErrorNote } from "./shared";
import { PrivateVaultPanel } from "./PrivateVaultPanel";

// ── Item card ────────────────────────────────────────────────────────────────

function LibraryCard({
  item,
  index,
  viewingSet,
  selecting,
  selected,
  onToggleSelect,
}: {
  item: MediaLibraryItem;
  index: number;
  viewingSet: ReturnType<typeof viewingSetOf>;
  selecting: boolean;
  selected: boolean;
  onToggleSelect: (index: number, shiftKey: boolean) => void;
}) {
  return (
    <div
      className={`group overflow-hidden rounded-lg border bg-card text-left transition-colors ${
        selected
          ? "border-violet-500 ring-1 ring-violet-500"
          : "hover:border-violet-500/40"
      }`}
    >
      <div className="relative aspect-square w-full overflow-hidden bg-muted/20">
        {selecting ? (
          // In selection mode the tile is a checkbox, not a viewer — clicking a
          // tile must never open the lightbox mid-selection.
          <button
            type="button"
            onClick={(e) => onToggleSelect(index, e.shiftKey)}
            aria-pressed={selected}
            aria-label={selected ? "Deselect" : "Select"}
            className="block h-full w-full"
          >
            <MediaItemThumb
              item={item}
              variant="card"
              chrome="none"
              className="pointer-events-none h-full w-full"
            />
            <span
              className={`absolute right-1.5 top-1.5 rounded p-0.5 ${
                selected
                  ? "bg-violet-600 text-white"
                  : "bg-black/50 text-white/80"
              }`}
            >
              {selected ? (
                <CheckSquare className="h-4 w-4" />
              ) : (
                <Square className="h-4 w-4" />
              )}
            </span>
          </button>
        ) : (
          <MediaItemThumb
            item={item}
            variant="card"
            viewingSet={viewingSet}
            className="h-full w-full"
          >
            <span className="pointer-events-none absolute left-1.5 top-1.5 flex items-center gap-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">
              {item.media_type === "video" ? (
                <Film className="h-2.5 w-2.5" />
              ) : (
                <ImageIcon className="h-2.5 w-2.5" />
              )}
              {item.width}×{item.height}
            </span>
          </MediaItemThumb>
        )}
      </div>
      <div className="space-y-1 p-2.5">
        {/* The prompt is clamped for layout, but NEVER a dead end: copy it here,
            or open Info for the full text. */}
        <div className="flex items-start gap-1.5">
          <p className="line-clamp-2 min-w-0 flex-1 break-words text-xs font-medium leading-snug">
            {item.prompt || "(no prompt)"}
          </p>
          {item.prompt && (
            <CopyButton
              value={item.prompt}
              label="Copy prompt"
              className="mt-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
            />
          )}
        </div>
        <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <span className="truncate" title={item.model_id}>
            {item.model_id}
          </span>
          <span className="shrink-0">{formatDate(item.created_at)}</span>
        </div>
      </div>
    </div>
  );
}

// ── Section ──────────────────────────────────────────────────────────────────

const FILTERS: { value: MediaLibraryFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "image", label: "Images" },
  { value: "video", label: "Videos" },
];

export function MediaLibrarySection() {
  const [state, actions] = useMediaLibraryApp();
  const { items, filter, loading, loadingMore, hasMore, error, fileUrls } =
    state;
  const { refresh, setFilter, loadMore, clearError } = actions;

  // The lightbox viewing set: every item whose bytes have resolved. The
  // lightbox re-anchors on the current item id as this grows, so paging never
  // jumps when a later thumbnail finishes loading.
  const viewingSet = useMemo(
    () => viewingSetOf(items, fileUrls, "library"),
    [items, fileUrls],
  );

  // ── Private vault (the ONE shared store) ─────────────────────────────────
  const [vault, vaultActions] = useMediaVaultApp();
  const [vaultOpen, setVaultOpen] = useState(false);

  // ── Multi-select state ─────────────────────────────────────────────────────
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const lastClickedIndex = useRef<number | null>(null);

  const exitSelection = useCallback(() => {
    setSelecting(false);
    setSelectedIds(new Set());
    lastClickedIndex.current = null;
  }, []);

  const toggleSelect = useCallback(
    (index: number, shiftKey: boolean) => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        const id = items[index]?.id;
        if (!id) return prev;
        if (
          shiftKey &&
          lastClickedIndex.current !== null &&
          lastClickedIndex.current !== index
        ) {
          const [lo, hi] = [
            Math.min(lastClickedIndex.current, index),
            Math.max(lastClickedIndex.current, index),
          ];
          for (let i = lo; i <= hi; i++) {
            const it = items[i];
            if (it) next.add(it.id);
          }
        } else if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return next;
      });
      lastClickedIndex.current = index;
    },
    [items],
  );

  /**
   * "Move to Private" for the multi-selection.
   *
   * The MOVE itself — including creating or unlocking the vault first, and
   * reporting per-item failures — is owned by MediaActionsProvider, which also
   * serves the single-item action from the "⋯" menu, the right-click menu and
   * the lightbox. This section only supplies the ids. One flow, one set of
   * dialogs, identical behavior everywhere.
   */
  const handleMoveToPrivate = useCallback(() => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    window.dispatchEvent(
      new CustomEvent<VaultMoveRequest>(VAULT_MOVE_REQUESTED_EVENT, {
        detail: { itemIds: ids },
      }),
    );
    exitSelection();
  }, [selectedIds, exitSelection]);

  if (loading && items.length === 0 && !error) {
    return (
      <div className="flex items-center justify-center gap-3 py-20 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="text-sm">Loading media library…</span>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                filter === f.value
                  ? "border-violet-500 bg-violet-500/10 text-violet-600 dark:text-violet-400"
                  : "hover:bg-muted/30"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          {items.length > 0 && (
            <Button
              size="sm"
              variant={selecting ? "secondary" : "outline"}
              onClick={() => (selecting ? exitSelection() : setSelecting(true))}
            >
              <CheckSquare className="mr-1.5 h-3.5 w-3.5" />
              {selecting ? "Cancel" : "Select"}
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => setVaultOpen(true)}>
            <Lock className="mr-1.5 h-3.5 w-3.5" />
            Private
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void refresh()}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      {selecting && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/20 px-3 py-2">
          <span className="text-xs font-medium">
            {selectedIds.size} selected
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            onClick={() => setSelectedIds(new Set(items.map((i) => i.id)))}
          >
            Select all
          </Button>
          <div className="flex-1" />
          <Button
            size="sm"
            className="h-7 text-xs"
            disabled={selectedIds.size === 0}
            onClick={handleMoveToPrivate}
          >
            <Lock className="mr-1.5 h-3.5 w-3.5" />
            Move to Private
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            onClick={exitSelection}
          >
            Cancel
          </Button>
        </div>
      )}

      {error && <ErrorNote message={error} onDismiss={clearError} />}
      {vault.error && (
        <ErrorNote message={vault.error} onDismiss={vaultActions.clearError} />
      )}
      {items.length === 0 && !loading ? (
        !error && (
          <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed py-20 text-muted-foreground">
            <ImageIcon className="h-10 w-10 opacity-20" />
            <span className="text-sm">
              {filter === "video"
                ? "No generated videos yet"
                : filter === "image"
                  ? "No generated images yet"
                  : "Nothing in your library yet"}
            </span>
            <span className="text-xs">
              Media you generate in the Images and Video tabs is saved here
              automatically.
            </span>
          </div>
        )
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {items.map((item, index) => (
              <LibraryCard
                key={item.id}
                item={item}
                index={index}
                viewingSet={viewingSet}
                selecting={selecting}
                selected={selectedIds.has(item.id)}
                onToggleSelect={toggleSelect}
              />
            ))}
          </div>
          {hasMore && (
            <div className="flex justify-center pt-2">
              <Button
                size="sm"
                variant="outline"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? (
                  <>
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    Loading…
                  </>
                ) : (
                  "Load more"
                )}
              </Button>
            </div>
          )}
        </>
      )}

      {/* Private vault panel — full-height dialog, reachable from every layout
          variant since they all reuse MediaLibrarySection. */}
      <Dialog
        open={vaultOpen}
        onOpenChange={setVaultOpen}
      >
        <DialogContent className="flex h-[85vh] max-w-3xl flex-col overflow-hidden">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Lock className="h-4 w-4 text-violet-500" />
              Private
            </DialogTitle>
            <DialogDescription className="text-xs">
              Password-protected, encrypted storage for your generated media.
            </DialogDescription>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto pr-1">
            <PrivateVaultPanel vault={vault} actions={vaultActions} />
          </div>
        </DialogContent>
      </Dialog>

    </div>
  );
}
