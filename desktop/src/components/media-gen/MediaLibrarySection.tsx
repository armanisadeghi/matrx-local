/**
 * MediaLibrarySection — the "Library" tab of the Media Generation page.
 *
 * Responsive grid of every image/video the engine has persisted, with an
 * image/video filter, a detail dialog (full-size preview + complete metadata
 * incl. pretty-printed params JSON) and per-item actions: copy prompt, show
 * in folder, delete (with confirm).
 *
 * Data comes from useMediaLibrary (auth'd blob URLs — a plain <img src>
 * cannot carry the Authorization header). Visual style mirrors
 * ImageGenSection.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  AlertCircle,
  Check,
  CheckSquare,
  Copy,
  Film,
  FolderOpen,
  Image as ImageIcon,
  Info,
  Loader2,
  Lock,
  RefreshCw,
  Square,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaLibrary } from "@/hooks/use-media-library";
import type {
  MediaLibraryActions,
  MediaLibraryFilter,
} from "@/hooks/use-media-library";
import { useMediaVault } from "@/hooks/use-media-vault";
import type { MediaLibraryItem, MediaVaultOpResult } from "@/lib/api";
import { ErrorNote } from "./shared";
import { PrivateVaultPanel, VaultUnlockForm } from "./PrivateVaultPanel";
import { MediaLightbox } from "./MediaLightbox";
import type { LightboxItem } from "./MediaLightbox";

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Open the folder containing `path` in the OS file manager (Finder etc.). */
async function showInFolder(path: string): Promise<void> {
  const dir = path.replace(/[/\\][^/\\]*$/, "");
  const { open } = await import("@tauri-apps/plugin-shell");
  await open(dir || path);
}

// ── Media preview (blob URL loader) ──────────────────────────────────────────

function MediaPreview({
  item,
  fileUrls,
  getFileUrl,
  className,
  controls = false,
}: {
  item: MediaLibraryItem;
  fileUrls: Record<string, string>;
  getFileUrl: MediaLibraryActions["getFileUrl"];
  className?: string;
  controls?: boolean;
}) {
  const url = fileUrls[item.id] ?? null;
  const [failed, setFailed] = useState(false);

  // Kick off the byte fetch once per item id. getFileUrl is stable and dedupes
  // in-flight fetches, so this never stampedes.
  useEffect(() => {
    let cancelled = false;
    if (!url) {
      void getFileUrl(item.id).then((result) => {
        if (!cancelled && result === null) setFailed(true);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [item.id, url, getFileUrl]);

  if (failed) {
    return (
      <div
        className={`flex items-center justify-center bg-muted/30 text-muted-foreground ${className ?? ""}`}
      >
        <AlertCircle className="h-5 w-5 opacity-40" />
      </div>
    );
  }
  if (!url) {
    return (
      <div
        className={`flex items-center justify-center bg-muted/30 text-muted-foreground ${className ?? ""}`}
      >
        <Loader2 className="h-5 w-5 animate-spin opacity-40" />
      </div>
    );
  }
  if (item.media_type === "video") {
    return (
      <video
        src={url}
        className={className}
        controls={controls}
        muted={!controls}
        loop={!controls}
        playsInline
        onMouseEnter={(e) => {
          if (!controls) void e.currentTarget.play().catch(() => undefined);
        }}
        onMouseLeave={(e) => {
          if (!controls) e.currentTarget.pause();
        }}
      />
    );
  }
  return (
    <img src={url} alt={item.prompt.slice(0, 80)} className={className} />
  );
}

// ── Item card ────────────────────────────────────────────────────────────────

function LibraryCard({
  item,
  index,
  fileUrls,
  getFileUrl,
  onOpen,
  onInfo,
  selecting,
  selected,
  onToggleSelect,
}: {
  item: MediaLibraryItem;
  index: number;
  fileUrls: Record<string, string>;
  getFileUrl: MediaLibraryActions["getFileUrl"];
  onOpen: (item: MediaLibraryItem) => void;
  /** Open the metadata detail dialog (info button; lightbox handles clicks). */
  onInfo: (item: MediaLibraryItem) => void;
  selecting: boolean;
  selected: boolean;
  onToggleSelect: (index: number, shiftKey: boolean) => void;
}) {
  return (
    <button
      onClick={(e) => {
        if (selecting) onToggleSelect(index, e.shiftKey);
        else onOpen(item);
      }}
      className={`group rounded-lg border bg-card text-left overflow-hidden transition-colors hover:bg-muted/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 ${
        selected
          ? "border-violet-500 ring-1 ring-violet-500"
          : "hover:border-violet-500/40"
      }`}
    >
      <div className="relative aspect-square w-full overflow-hidden bg-muted/20">
        <MediaPreview
          item={item}
          fileUrls={fileUrls}
          getFileUrl={getFileUrl}
          className="h-full w-full object-cover"
        />
        <span className="absolute left-1.5 top-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white flex items-center gap-1">
          {item.media_type === "video" ? (
            <Film className="h-2.5 w-2.5" />
          ) : (
            <ImageIcon className="h-2.5 w-2.5" />
          )}
          {item.width}×{item.height}
        </span>
        {selecting ? (
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
        ) : (
          <span
            role="button"
            tabIndex={0}
            aria-label="Item details"
            title="Details"
            onClick={(e) => {
              e.stopPropagation();
              onInfo(item);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.stopPropagation();
                e.preventDefault();
                onInfo(item);
              }
            }}
            className="absolute right-1.5 top-1.5 rounded bg-black/50 p-1 text-white/80 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-black/70 hover:text-white"
          >
            <Info className="h-3.5 w-3.5" />
          </span>
        )}
      </div>
      <div className="p-2.5 space-y-1">
        <p className="text-xs font-medium truncate" title={item.prompt}>
          {item.prompt || "(no prompt)"}
        </p>
        <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <span className="truncate" title={item.model_id}>
            {item.model_id}
          </span>
          <span className="shrink-0">{formatDate(item.created_at)}</span>
        </div>
      </div>
    </button>
  );
}

// ── Detail dialog ────────────────────────────────────────────────────────────

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 text-xs">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="text-right break-all">{value}</span>
    </div>
  );
}

function LibraryDetailDialog({
  item,
  fileUrls,
  getFileUrl,
  onDelete,
  onClose,
}: {
  item: MediaLibraryItem | null;
  fileUrls: Record<string, string>;
  getFileUrl: MediaLibraryActions["getFileUrl"];
  onDelete: (itemId: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Reset transient dialog state whenever a different item is shown.
  useEffect(() => {
    setCopied(false);
    setConfirmingDelete(false);
    setDeleting(false);
    setActionError(null);
  }, [item?.id]);

  const handleCopyPrompt = useCallback(async () => {
    if (!item) return;
    try {
      await navigator.clipboard.writeText(item.prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      setActionError(
        e instanceof Error ? e.message : "Failed to copy to clipboard",
      );
    }
  }, [item]);

  const handleShowInFolder = useCallback(async () => {
    if (!item) return;
    try {
      await showInFolder(item.file_path);
    } catch (e) {
      setActionError(
        e instanceof Error ? e.message : "Failed to open folder",
      );
    }
  }, [item]);

  const handleDelete = useCallback(async () => {
    if (!item) return;
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setDeleting(true);
    const ok = await onDelete(item.id);
    setDeleting(false);
    if (ok) onClose();
    else setConfirmingDelete(false);
  }, [item, confirmingDelete, onDelete, onClose]);

  return (
    <Dialog open={item !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
        {item && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-base">
                {item.media_type === "video" ? (
                  <Film className="h-4 w-4 text-violet-500" />
                ) : (
                  <ImageIcon className="h-4 w-4 text-violet-500" />
                )}
                {item.media_type === "video"
                  ? "Generated video"
                  : "Generated image"}
                <Badge variant="outline" className="text-[10px]">
                  {item.model_id}
                </Badge>
              </DialogTitle>
              <DialogDescription className="text-xs">
                {formatDate(item.created_at)} · {item.width}×{item.height} ·{" "}
                {formatBytes(item.file_size_bytes)} ·{" "}
                {item.elapsed_seconds.toFixed(1)}s
              </DialogDescription>
            </DialogHeader>

            <MediaPreview
              item={item}
              fileUrls={fileUrls}
              getFileUrl={getFileUrl}
              className="w-full max-h-[50vh] rounded-lg border object-contain bg-black/5"
              controls
            />

            <div className="space-y-3">
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  Prompt
                </p>
                <p className="text-sm leading-relaxed break-words">
                  {item.prompt || "(no prompt)"}
                </p>
              </div>
              {item.negative_prompt && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">
                    Negative prompt
                  </p>
                  <p className="text-sm leading-relaxed break-words">
                    {item.negative_prompt}
                  </p>
                </div>
              )}

              <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2 rounded-lg border bg-muted/20 p-3">
                <MetaRow label="Model" value={item.model_id} />
                <MetaRow
                  label="Seed"
                  value={item.seed !== null ? String(item.seed) : "random"}
                />
                <MetaRow
                  label="Dimensions"
                  value={`${item.width}×${item.height}`}
                />
                <MetaRow
                  label="Generation time"
                  value={`${item.elapsed_seconds.toFixed(1)}s`}
                />
                <MetaRow label="File size" value={formatBytes(item.file_size_bytes)} />
                <MetaRow label="Created" value={formatDate(item.created_at)} />
              </div>

              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  Parameters
                </p>
                <pre className="rounded-lg border bg-muted/20 p-3 text-[11px] leading-relaxed overflow-x-auto">
                  {JSON.stringify(item.params, null, 2)}
                </pre>
              </div>

              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  File path
                </p>
                <p className="text-[11px] font-mono text-muted-foreground break-all">
                  {item.file_path}
                </p>
              </div>

              {actionError && (
                <ErrorNote
                  message={actionError}
                  onDismiss={() => setActionError(null)}
                />
              )}

              <div className="flex flex-wrap gap-2 pt-1">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void handleCopyPrompt()}
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5 mr-1.5 text-green-500" />
                  ) : (
                    <Copy className="h-3.5 w-3.5 mr-1.5" />
                  )}
                  {copied ? "Copied" : "Copy prompt"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void handleShowInFolder()}
                >
                  <FolderOpen className="h-3.5 w-3.5 mr-1.5" />
                  Show in folder
                </Button>
                <div className="flex-1" />
                <Button
                  size="sm"
                  variant={confirmingDelete ? "destructive" : "outline"}
                  disabled={deleting}
                  onClick={() => void handleDelete()}
                >
                  {deleting ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                  )}
                  {confirmingDelete ? "Confirm delete" : "Delete"}
                </Button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── Section ──────────────────────────────────────────────────────────────────

const FILTERS: { value: MediaLibraryFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "image", label: "Images" },
  { value: "video", label: "Videos" },
];

/** Loud per-item failure list for a vault move (never silently skip items). */
function MoveFailuresNote({
  results,
  onDismiss,
}: {
  results: MediaVaultOpResult[];
  onDismiss: () => void;
}) {
  const failures = results.filter((r) => !r.ok);
  if (failures.length === 0) return null;
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive space-y-1">
      <div className="flex items-center gap-2">
        <AlertCircle className="h-3.5 w-3.5 shrink-0" />
        <span className="font-medium flex-1">
          Move to Private: {failures.length} of {results.length} item
          {results.length === 1 ? "" : "s"} failed
        </span>
        <button
          onClick={onDismiss}
          className="text-destructive/70 hover:text-destructive"
        >
          Dismiss
        </button>
      </div>
      <ul className="list-disc pl-5 space-y-0.5">
        {failures.map((f) => (
          <li key={f.item_id} className="break-all">
            {f.item_id}: {f.error ?? "unknown error"}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MediaLibrarySection() {
  const [state, actions] = useMediaLibrary();
  const { items, filter, loading, loadingMore, hasMore, error, fileUrls } =
    state;
  const { refresh, setFilter, loadMore, getFileUrl, deleteItem, clearError } =
    actions;
  const [selected, setSelected] = useState<MediaLibraryItem | null>(null);

  // ── Lightbox (click-to-popup viewer; detail dialog stays on the info btn) ──
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxStart, setLightboxStart] = useState(0);

  // Only items whose bytes are already loaded can be shown (the lightbox
  // contract requires a ready blob URL).
  const lightboxItems = useMemo<LightboxItem[]>(
    () =>
      items
        .filter((i) => fileUrls[i.id])
        .map((i) => ({
          id: i.id,
          kind: i.media_type,
          url: fileUrls[i.id],
          prompt: i.prompt,
          seed: i.seed,
          title: i.file_name,
          meta: {
            model: i.model_id,
            dimensions: `${i.width}×${i.height}`,
            created: i.created_at,
            ...i.params,
          },
        })),
    [items, fileUrls],
  );

  const openItem = useCallback(
    (item: MediaLibraryItem) => {
      const idx = lightboxItems.findIndex((li) => li.id === item.id);
      if (idx >= 0) {
        setLightboxStart(idx);
        setLightboxOpen(true);
      } else {
        // Bytes not loaded yet — fall back to the detail dialog, which
        // kicks off the fetch and shows a spinner.
        setSelected(item);
      }
    },
    [lightboxItems],
  );

  // ── Private vault (single hook instance, shared with the panel) ───────────
  const [vault, vaultActions] = useMediaVault();
  const [vaultOpen, setVaultOpen] = useState(false);
  const [unlockForMoveOpen, setUnlockForMoveOpen] = useState(false);

  // ── Multi-select state ─────────────────────────────────────────────────────
  const [selecting, setSelecting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [moving, setMoving] = useState(false);
  const [moveResults, setMoveResults] = useState<MediaVaultOpResult[] | null>(
    null,
  );
  const [moveSuccess, setMoveSuccess] = useState<string | null>(null);
  const lastClickedIndex = useRef<number | null>(null);
  // Item ids waiting on a vault create/unlock before the move can run.
  const pendingMoveIds = useRef<string[] | null>(null);

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
          for (let i = lo; i <= hi; i++) next.add(items[i].id);
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

  /** Actually POST the move — only called when the vault is unlocked. */
  const performMove = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      setMoving(true);
      setMoveResults(null);
      setMoveSuccess(null);
      const results = await vaultActions.move(ids);
      setMoving(false);
      if (results === null) {
        // Request-level failure: either vault.error carries the message, or a
        // 423 flipped state to locked — re-arm the unlock prompt so the user
        // isn't silently dropped.
        pendingMoveIds.current = ids;
        setUnlockForMoveOpen(true);
        return;
      }
      const okCount = results.filter((r) => r.ok).length;
      if (okCount > 0) {
        setMoveSuccess(
          `${okCount} item${okCount === 1 ? "" : "s"} moved to Private`,
        );
      }
      setMoveResults(results);
      exitSelection();
      await refresh(); // moved items disappear from the library grid
    },
    [vaultActions, refresh, exitSelection],
  );

  /** "Move to Private" — routes through create/unlock as needed. */
  const handleMoveToPrivate = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    // Get a fresh view of the vault (status may be stale or auto-locked).
    await vaultActions.refresh();
    const status = vault.status;
    // vault.status from this render may lag the refresh; decide off the
    // current snapshot but let the pending-move effect catch the transition.
    if (!status || !status.exists) {
      pendingMoveIds.current = ids;
      setVaultOpen(true); // panel shows the create flow
      return;
    }
    if (!status.unlocked) {
      pendingMoveIds.current = ids;
      setUnlockForMoveOpen(true);
      return;
    }
    await performMove(ids);
  }, [selectedIds, vault.status, vaultActions, performMove]);

  // When a move is pending and the vault becomes unlocked (user created it or
  // unlocked it in a dialog), run the move. Narrowly gated on the unlocked
  // boolean — never on the actions object.
  const vaultUnlocked = vault.status?.unlocked === true;
  useEffect(() => {
    if (!vaultUnlocked) return;
    const ids = pendingMoveIds.current;
    if (!ids || ids.length === 0) return;
    pendingMoveIds.current = null;
    setUnlockForMoveOpen(false);
    void performMove(ids);
  }, [vaultUnlocked, performMove]);

  if (loading && items.length === 0 && !error) {
    return (
      <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="text-sm">Loading media library…</span>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-8">
      <div className="flex items-center justify-between gap-3 flex-wrap">
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
              <CheckSquare className="h-3.5 w-3.5 mr-1.5" />
              {selecting ? "Cancel" : "Select"}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => setVaultOpen(true)}
          >
            <Lock className="h-3.5 w-3.5 mr-1.5" />
            Private
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void refresh()}>
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            Refresh
          </Button>
        </div>
      </div>

      {selecting && (
        <div className="flex items-center gap-2 flex-wrap rounded-lg border bg-muted/20 px-3 py-2">
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
            disabled={selectedIds.size === 0 || moving}
            onClick={() => void handleMoveToPrivate()}
          >
            {moving ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <Lock className="h-3.5 w-3.5 mr-1.5" />
            )}
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
      {moveSuccess && (
        <p className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1.5">
          <Check className="h-3.5 w-3.5" />
          {moveSuccess}
        </p>
      )}
      {moveResults && (
        <MoveFailuresNote
          results={moveResults}
          onDismiss={() => setMoveResults(null)}
        />
      )}

      {items.length === 0 && !loading ? (
        !error && (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-20 gap-3 text-muted-foreground">
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
          <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {items.map((item, index) => (
              <LibraryCard
                key={item.id}
                item={item}
                index={index}
                fileUrls={fileUrls}
                getFileUrl={getFileUrl}
                onOpen={openItem}
                onInfo={setSelected}
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
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
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

      <LibraryDetailDialog
        item={selected}
        fileUrls={fileUrls}
        getFileUrl={getFileUrl}
        onDelete={deleteItem}
        onClose={() => setSelected(null)}
      />

      <MediaLightbox
        open={lightboxOpen}
        items={lightboxItems}
        startIndex={lightboxStart}
        onClose={() => setLightboxOpen(false)}
        onDelete={(id) => void deleteItem(id)}
      />

      {/* Private vault panel — full-height dialog, reachable from every layout
          variant since they all reuse MediaLibrarySection. */}
      <Dialog
        open={vaultOpen}
        onOpenChange={(open) => {
          setVaultOpen(open);
          if (!open) pendingMoveIds.current = null; // disarm any pending move
        }}
      >
        <DialogContent className="max-w-3xl h-[85vh] flex flex-col overflow-hidden">
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

      {/* Inline unlock prompt for the "Move to Private" flow. */}
      <Dialog
        open={unlockForMoveOpen}
        onOpenChange={(open) => {
          setUnlockForMoveOpen(open);
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
              Unlock the vault to move the selected item
              {selectedIds.size === 1 ? "" : "s"} to Private.
            </DialogDescription>
          </DialogHeader>
          <VaultUnlockForm
            actions={vaultActions}
            busy={vault.busy}
            autoLockSeconds={vault.status?.auto_lock_seconds ?? null}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
