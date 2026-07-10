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

import { useState, useEffect, useCallback } from "react";
import {
  AlertCircle,
  Check,
  Copy,
  Film,
  FolderOpen,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
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
import type { MediaLibraryItem } from "@/lib/api";
import { ErrorNote } from "./shared";

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
  fileUrls,
  getFileUrl,
  onOpen,
}: {
  item: MediaLibraryItem;
  fileUrls: Record<string, string>;
  getFileUrl: MediaLibraryActions["getFileUrl"];
  onOpen: (item: MediaLibraryItem) => void;
}) {
  return (
    <button
      onClick={() => onOpen(item)}
      className="group rounded-lg border bg-card text-left overflow-hidden transition-colors hover:bg-muted/10 hover:border-violet-500/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
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

export function MediaLibrarySection() {
  const [state, actions] = useMediaLibrary();
  const { items, filter, loading, loadingMore, hasMore, error, fileUrls } =
    state;
  const { refresh, setFilter, loadMore, getFileUrl, deleteItem, clearError } =
    actions;
  const [selected, setSelected] = useState<MediaLibraryItem | null>(null);

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
        <Button size="sm" variant="ghost" onClick={() => void refresh()}>
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Refresh
        </Button>
      </div>

      {error && <ErrorNote message={error} onDismiss={clearError} />}

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
            {items.map((item) => (
              <LibraryCard
                key={item.id}
                item={item}
                fileUrls={fileUrls}
                getFileUrl={getFileUrl}
                onOpen={setSelected}
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
    </div>
  );
}
