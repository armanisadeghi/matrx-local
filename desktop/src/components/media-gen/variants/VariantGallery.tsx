/**
 * VariantGallery — "Gallery first" media-gen layout (UI bake-off variant).
 *
 * Your creations ARE the interface (Midjourney-feed style):
 *  - Top: a full-width COMPOSER BAR — big prompt input, Image|Video mode
 *    toggle, loaded-model chip (→ model-picker popover), Settings popover
 *    (common params via shared.tsx controls, badge when off-defaults),
 *    Advanced popover (AdvancedParamsEditor, badge when overrides active),
 *    and Generate + Add-to-queue actions.
 *  - Under it: a QUEUE STRIP of job chips (thumbnail on completion, progress
 *    while running, cancel X, seed chip) + the single active video job card.
 *  - Everything else: the media library as an immersive masonry grid
 *    (newest first) with image/video filter pills. New generations surface at
 *    the top as jobs complete (narrowly-gated refresh effects). Click any
 *    tile → detail dialog (full preview, params JSON, copy prompt, reuse
 *    seed → composer form, delete).
 *
 * State doctrine (repo CLAUDE.md → React Patterns, obeyed strictly):
 *  - ALL prompt/params state lives in MediaGenContext (imageForm/videoForm
 *    via setImageForm/setVideoForm) — switching layout variants preserves
 *    work. Local useState is ONLY popover/dialog open state + grid selection.
 *  - No `actions` object in any effect dep list — only specific stable
 *    callbacks. Library-refresh effects are gated on completion COUNTS /
 *    completed-job ids, never on broad objects.
 */

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  Copy,
  Cpu,
  Download,
  Film,
  FolderOpen,
  Image as ImageIcon,
  ListPlus,
  Loader2,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { useMediaLibrary } from "@/hooks/use-media-library";
import type {
  MediaLibraryActions,
  MediaLibraryFilter,
} from "@/hooks/use-media-library";
import type {
  ImageGenModelInfo,
  VideoGenModelInfo,
  ImageGenJob,
  VideoGenJob,
  MediaLibraryItem,
  VideoGenRequest,
} from "@/lib/api";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";
import { ImageGenInstaller } from "../ImageGenInstaller";
import {
  CancelableGenerateButton,
  ErrorNote,
  InlineProgressBar,
  SeedInput,
  SeedChip,
  ResetButton,
  NumberSliderField,
  DimensionPicker,
  dimensionError,
  NegativePromptField,
  AdvancedParamsEditor,
  ParamsErrorBanner,
  GeneratedImageView,
  computeAdvancedOverrides,
  parseSeedText,
  randomSeed,
  formatGb,
} from "../shared";
import type { SizePreset } from "../shared";

// ── Local helpers ────────────────────────────────────────────────────────────

type ComposerMode = "image" | "video";

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
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Open the folder containing `path` in the OS file manager. */
async function showInFolder(path: string): Promise<void> {
  const dir = path.replace(/[/\\][^/\\]*$/, "");
  const { open } = await import("@tauri-apps/plugin-shell");
  await open(dir || path);
}

const IMAGE_SIZE_PRESETS: SizePreset[] = [
  { label: "512", width: 512, height: 512 },
  { label: "768", width: 768, height: 768 },
  { label: "1024", width: 1024, height: 1024 },
  { label: "Portrait 832×1216", width: 832, height: 1216 },
  { label: "Landscape 1216×832", width: 1216, height: 832 },
];

// ── Model picker (popover body) ──────────────────────────────────────────────

function ModelPickerRow({
  name,
  provider,
  sizeGb,
  isDownloaded,
  isLoaded,
  requiresToken,
  hardwareOk,
  hardwareReason,
  isLoadingThis,
  anyLoadInFlight,
  onLoad,
  onDownload,
}: {
  name: string;
  provider: string;
  sizeGb: number;
  isDownloaded: boolean;
  isLoaded: boolean;
  requiresToken: boolean;
  hardwareOk: boolean;
  hardwareReason: string | null;
  isLoadingThis: boolean;
  anyLoadInFlight: boolean;
  onLoad: () => void;
  onDownload: () => void;
}) {
  return (
    <div
      className={`rounded-lg border px-3 py-2 space-y-1.5 ${
        isLoaded ? "border-violet-500/50 bg-violet-500/5" : "bg-card"
      } ${!hardwareOk ? "opacity-60" : ""}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium">{name}</p>
          <p className="truncate text-[10px] text-muted-foreground">
            {provider} · {formatGb(sizeGb)}
            {requiresToken ? " · HF token" : ""}
          </p>
        </div>
        <div className="shrink-0">
          {isLoaded ? (
            <Badge className="gap-1 border-green-500/30 bg-green-500/15 text-[10px] text-green-600 dark:text-green-400">
              <CheckCircle2 className="h-3 w-3" />
              Loaded
            </Badge>
          ) : !isDownloaded ? (
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[11px]"
              disabled={!hardwareOk || anyLoadInFlight}
              onClick={onDownload}
            >
              <Download className="mr-1 h-3 w-3" />
              Download
            </Button>
          ) : (
            <Button
              size="sm"
              className="h-6 px-2 text-[11px]"
              disabled={!hardwareOk || anyLoadInFlight}
              onClick={onLoad}
            >
              {isLoadingThis ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                "Load"
              )}
            </Button>
          )}
        </div>
      </div>
      {!hardwareOk && (
        <p className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <AlertCircle className="h-3 w-3 shrink-0 text-amber-500" />
          {hardwareReason ?? "Hardware does not meet this model's requirements."}
        </p>
      )}
    </div>
  );
}

// ── Queue strip pieces ───────────────────────────────────────────────────────

function ImageJobChip({
  job,
  thumbUrl,
  onCancel,
  onReuseSeed,
}: {
  job: ImageGenJob;
  thumbUrl: string | null;
  onCancel: (jobId: string) => void;
  onReuseSeed: (seed: number) => void;
}) {
  const active = job.status === "queued" || job.status === "running";
  return (
    <div className="flex w-64 shrink-0 flex-col gap-1.5 rounded-lg border bg-card px-2.5 py-2">
      <div className="flex items-center gap-2">
        {thumbUrl ? (
          <img
            src={thumbUrl}
            alt="Generated"
            className="h-9 w-9 shrink-0 rounded border object-cover"
          />
        ) : (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded border bg-muted/30">
            {job.status === "failed" ? (
              <AlertCircle className="h-4 w-4 text-destructive" />
            ) : job.status === "cancelled" ? (
              <X className="h-4 w-4 text-muted-foreground" />
            ) : job.status === "completed" ? (
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            ) : (
              <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
            )}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px]" title={job.prompt}>
            {job.prompt || "(no prompt)"}
          </p>
          <p className="truncate text-[10px] text-muted-foreground">
            {active && job.cancel_requested ? "cancelling…" : job.status}
            {job.status === "failed" && job.error ? ` — ${job.error}` : ""}
          </p>
        </div>
        {active && job.cancel_requested ? (
          <span
            className="flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground"
            title="Cancel requested — the current step is finishing"
          >
            <Loader2 className="h-3 w-3 animate-spin" />
            Cancelling…
          </span>
        ) : (
          <button
            type="button"
            onClick={() => onCancel(job.job_id)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={active ? "Cancel job" : "Remove job"}
            title={active ? "Cancel this job" : "Remove from the queue"}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {job.status === "running" && (
        <InlineProgressBar
          percent={(job.progress ?? 0) * 100}
          indeterminate={(job.progress ?? 0) <= 0}
        />
      )}
      {job.status === "completed" && typeof job.seed === "number" && (
        <div>
          <SeedChip seed={job.seed} onReuse={onReuseSeed} />
        </div>
      )}
    </div>
  );
}

function VideoJobCard({
  job,
  onDismiss,
  onCancel,
}: {
  job: VideoGenJob;
  onDismiss: () => void;
  /** Cancel the queued/running job (now allowed by the engine). */
  onCancel: (jobId: string) => void;
}) {
  const active = job.status === "queued" || job.status === "running";
  const cancelling = active && !!job.cancel_requested;
  return (
    <div className="flex w-72 shrink-0 flex-col gap-1.5 rounded-lg border border-violet-500/30 bg-violet-500/5 px-2.5 py-2">
      <div className="flex items-center gap-2">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded border bg-muted/30">
          {job.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          ) : job.status === "failed" ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : (
            <Film className="h-4 w-4 text-violet-500" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px]" title={job.prompt}>
            <span className="font-medium">Video</span> ·{" "}
            {job.prompt || "(no prompt)"}
          </p>
          <p className="truncate text-[10px] text-muted-foreground">
            {cancelling ? "cancelling…" : job.status}
            {active && !cancelling && job.total_steps > 0
              ? ` — step ${job.current_step}/${job.total_steps}`
              : ""}
            {job.status === "failed" && job.error ? ` — ${job.error}` : ""}
          </p>
        </div>
        {active &&
          (cancelling ? (
            <span
              className="flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground"
              title="Cancel requested — the current step is finishing"
            >
              <Loader2 className="h-3 w-3 animate-spin" />
              Cancelling…
            </span>
          ) : (
            <button
              type="button"
              onClick={() => onCancel(job.job_id)}
              className="shrink-0 text-muted-foreground hover:text-destructive"
              aria-label="Cancel video job"
              title="Cancel this video job"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          ))}
        {!active && (
          <button
            type="button"
            onClick={onDismiss}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label="Dismiss video job"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {active && (
        <InlineProgressBar
          percent={(job.progress ?? 0) * 100}
          indeterminate={(job.progress ?? 0) <= 0}
        />
      )}
    </div>
  );
}

// ── Masonry tile (auth'd blob loader) ────────────────────────────────────────

function GalleryTile({
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
  const url = fileUrls[item.id] ?? null;
  const [failed, setFailed] = useState(false);

  // Kick off the byte fetch once per item id; getFileUrl is stable and
  // dedupes in-flight fetches, so this never stampedes.
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

  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      className="group relative mb-3 block w-full break-inside-avoid overflow-hidden rounded-xl border bg-card text-left transition-colors hover:border-violet-500/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
    >
      {failed ? (
        <div className="flex aspect-square w-full items-center justify-center bg-muted/30 text-muted-foreground">
          <AlertCircle className="h-5 w-5 opacity-40" />
        </div>
      ) : !url ? (
        <div
          className="flex w-full items-center justify-center bg-muted/30 text-muted-foreground"
          style={{
            aspectRatio:
              item.width > 0 && item.height > 0
                ? `${item.width} / ${item.height}`
                : "1 / 1",
          }}
        >
          <Loader2 className="h-5 w-5 animate-spin opacity-40" />
        </div>
      ) : item.media_type === "video" ? (
        <video
          src={url}
          className="w-full"
          muted
          loop
          playsInline
          onMouseEnter={(e) => {
            void e.currentTarget.play().catch(() => undefined);
          }}
          onMouseLeave={(e) => e.currentTarget.pause()}
        />
      ) : (
        <img
          src={url}
          alt={item.prompt.slice(0, 80)}
          className="w-full"
          loading="lazy"
        />
      )}
      {/* Hover overlay: prompt + meta, Midjourney-feed style */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 translate-y-1 bg-gradient-to-t from-black/75 to-transparent p-2.5 pt-8 opacity-0 transition-all group-hover:translate-y-0 group-hover:opacity-100">
        <p className="line-clamp-2 text-[11px] leading-snug text-white">
          {item.prompt || "(no prompt)"}
        </p>
        <p className="mt-0.5 flex items-center gap-1 text-[10px] text-white/70">
          {item.media_type === "video" ? (
            <Film className="h-2.5 w-2.5" />
          ) : (
            <ImageIcon className="h-2.5 w-2.5" />
          )}
          {item.width}×{item.height} · {formatDate(item.created_at)}
        </p>
      </div>
    </button>
  );
}

// ── Detail dialog (slim rebuild of the library detail experience) ────────────

function GalleryDetailDialog({
  item,
  fileUrls,
  getFileUrl,
  onDelete,
  onReuseSeed,
  onClose,
}: {
  item: MediaLibraryItem | null;
  fileUrls: Record<string, string>;
  getFileUrl: MediaLibraryActions["getFileUrl"];
  onDelete: (itemId: string) => Promise<boolean>;
  onReuseSeed: (item: MediaLibraryItem, seed: number) => void;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const url = item ? (fileUrls[item.id] ?? null) : null;

  // Reset transient dialog state when a different item is shown.
  useEffect(() => {
    setCopied(false);
    setConfirmingDelete(false);
    setDeleting(false);
    setActionError(null);
  }, [item?.id]);

  // Ensure the full-size bytes are available (cache hit when the tile
  // already fetched them). Stable + deduped, per the library hook contract.
  useEffect(() => {
    const id = item?.id;
    if (!id || url) return;
    void getFileUrl(id);
  }, [item?.id, url, getFileUrl]);

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
      setActionError(e instanceof Error ? e.message : "Failed to open folder");
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
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
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

            {!url ? (
              <div className="flex aspect-video w-full items-center justify-center rounded-lg border bg-muted/20 text-muted-foreground">
                <Loader2 className="h-6 w-6 animate-spin opacity-50" />
              </div>
            ) : item.media_type === "video" ? (
              <video
                src={url}
                className="max-h-[52vh] w-full rounded-lg border bg-black/5 object-contain"
                controls
                playsInline
              />
            ) : (
              <img
                src={url}
                alt={item.prompt.slice(0, 80)}
                className="max-h-[52vh] w-full rounded-lg border bg-black/5 object-contain"
              />
            )}

            <div className="space-y-3">
              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  Prompt
                </p>
                <p className="break-words text-sm leading-relaxed">
                  {item.prompt || "(no prompt)"}
                </p>
              </div>
              {item.negative_prompt && (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-muted-foreground">
                    Negative prompt
                  </p>
                  <p className="break-words text-sm leading-relaxed">
                    {item.negative_prompt}
                  </p>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2">
                {item.seed !== null && (
                  <SeedChip
                    seed={item.seed}
                    onReuse={(seed) => onReuseSeed(item, seed)}
                  />
                )}
                <span className="text-[11px] text-muted-foreground">
                  {item.seed === null ? "Seed: random (not recorded)" : ""}
                </span>
              </div>

              <div className="space-y-1">
                <p className="text-xs font-medium text-muted-foreground">
                  Parameters
                </p>
                <pre className="overflow-x-auto rounded-lg border bg-muted/20 p-3 text-[11px] leading-relaxed">
                  {JSON.stringify(item.params, null, 2)}
                </pre>
              </div>

              <p
                className="break-all font-mono text-[10px] text-muted-foreground/70"
                title="Saved in your media library"
              >
                {item.file_path}
              </p>

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
                    <Check className="mr-1.5 h-3.5 w-3.5 text-green-500" />
                  ) : (
                    <Copy className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  {copied ? "Copied" : "Copy prompt"}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void handleShowInFolder()}
                >
                  <FolderOpen className="mr-1.5 h-3.5 w-3.5" />
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
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="mr-1.5 h-3.5 w-3.5" />
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

// ── Filter pills ─────────────────────────────────────────────────────────────

const FILTERS: { value: MediaLibraryFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "image", label: "Images" },
  { value: "video", label: "Videos" },
];

// ── The variant ──────────────────────────────────────────────────────────────

export function VariantGallery() {
  const [mediaGen, mediaGenActions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading,
    loadingImageModelId,
    imageGenerating,
    imageCancelling,
    imageGenStartedAt,
    imageGenError,
    imageResult,
    selectedImageModelId,
    imageForm,
    imageJobs,
    imageJobsError,
    imageJobThumbs,
    videoStatus,
    videoModels,
    videoStatusError,
    videoModelLoading,
    loadingVideoModelId,
    videoGenerating,
    videoCancelling,
    videoGenError,
    activeJob,
    videoForm,
  } = mediaGen;
  const {
    refreshImage,
    loadImageModel,
    downloadImageModel,
    generateImage,
    cancelImageGeneration,
    clearImageResult,
    clearImageGenError,
    setImageForm,
    prepareImageGenerate,
    resetImageCommon,
    resetImageAdvanced,
    enqueueImageJob,
    cancelImageJob,
    refreshVideo,
    loadVideoModel,
    downloadVideoModel,
    generateVideo,
    cancelVideoGeneration,
    cancelVideoJob,
    clearActiveJob,
    clearVideoGenError,
    setVideoForm,
    prepareVideoGenerate,
    resetVideoCommon,
    resetVideoAdvanced,
  } = mediaGenActions;

  const [library, libraryActions] = useMediaLibrary();
  const {
    items,
    filter,
    loading: libraryLoading,
    loadingMore,
    hasMore,
    error: libraryError,
    fileUrls,
  } = library;
  const {
    refresh: refreshLibrary,
    setFilter,
    loadMore,
    getFileUrl,
    deleteItem,
    clearError: clearLibraryError,
  } = libraryActions;

  // Local UI state ONLY: mode toggle, popover/dialog open flags, selection.
  const [mode, setMode] = useState<ComposerMode>("image");
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);
  const [selected, setSelected] = useState<MediaLibraryItem | null>(null);

  // ── Model resolution ──────────────────────────────────────────────────────
  const imageModelId =
    imageForm.defaults?.modelId ??
    selectedImageModelId ??
    imageStatus?.loaded_model_id ??
    null;
  const currentImageModel = useMemo(
    () => imageModels.find((m) => m.model_id === imageModelId) ?? null,
    [imageModels, imageModelId],
  );
  const videoModelId =
    videoForm.defaults?.modelId ?? videoStatus?.loaded_model_id ?? null;
  const currentVideoModel = useMemo(
    () => videoModels.find((m) => m.model_id === videoModelId) ?? null,
    [videoModels, videoModelId],
  );

  // If a model is loaded but the form defaults belong to another model (or
  // none — e.g. loaded before this layout mounted), fetch its full parameter
  // schema. Guarded so it runs once per model change, never in a loop.
  useEffect(() => {
    if (imageForm.paramsLoading) return;
    if (!currentImageModel) return;
    if (imageForm.defaults?.modelId === currentImageModel.model_id) return;
    void prepareImageGenerate(currentImageModel);
  }, [
    imageForm.paramsLoading,
    imageForm.defaults?.modelId,
    currentImageModel,
    prepareImageGenerate,
  ]);
  useEffect(() => {
    if (videoForm.paramsLoading) return;
    if (!currentVideoModel) return;
    if (videoForm.defaults?.modelId === currentVideoModel.model_id) return;
    void prepareVideoGenerate(currentVideoModel);
  }, [
    videoForm.paramsLoading,
    videoForm.defaults?.modelId,
    currentVideoModel,
    prepareVideoGenerate,
  ]);

  // ── Library freshness: new generations surface at the top ────────────────
  // Refresh when the COUNT of completed image-queue jobs increases (narrow
  // gate — never the jobs array itself, and only on a rising edge).
  const completedImageJobCount = useMemo(
    () => imageJobs.filter((j) => j.status === "completed").length,
    [imageJobs],
  );
  const prevCompletedCountRef = useRef(completedImageJobCount);
  useEffect(() => {
    if (completedImageJobCount > prevCompletedCountRef.current) {
      void refreshLibrary();
    }
    prevCompletedCountRef.current = completedImageJobCount;
  }, [completedImageJobCount, refreshLibrary]);

  // Refresh once per completed video job id.
  const completedVideoJobId =
    activeJob?.status === "completed" ? activeJob.job_id : null;
  useEffect(() => {
    if (!completedVideoJobId) return;
    void refreshLibrary();
  }, [completedVideoJobId, refreshLibrary]);

  // Refresh once per persisted foreground-generate result.
  const freshResultItemId = imageResult?.itemId ?? null;
  useEffect(() => {
    if (!freshResultItemId) return;
    void refreshLibrary();
  }, [freshResultItemId, refreshLibrary]);

  // ── Form plumbing (ALL content state lives in context) ───────────────────
  const form = mode === "image" ? imageForm : videoForm;
  const prompt = form.prompt;
  const setPrompt = useCallback(
    (value: string) => {
      if (mode === "image") setImageForm({ prompt: value });
      else setVideoForm({ prompt: value });
    },
    [mode, setImageForm, setVideoForm],
  );

  const imageDefaults = imageForm.defaults;
  const videoDefaults = videoForm.defaults;

  const imageAdvanced = useMemo(
    () =>
      computeAdvancedOverrides(
        imageForm.advancedText,
        imageDefaults?.advanced ?? {},
      ),
    [imageForm.advancedText, imageDefaults?.advanced],
  );
  const videoAdvanced = useMemo(
    () =>
      computeAdvancedOverrides(
        videoForm.advancedText,
        videoDefaults?.advanced ?? {},
      ),
    [videoForm.advancedText, videoDefaults?.advanced],
  );

  // Settings-off-defaults badge counts.
  const imageSettingsDirty = useMemo(() => {
    const d = imageDefaults;
    if (!d) return 0;
    let n = 0;
    if (imageForm.steps !== d.steps) n++;
    if (imageForm.guidance !== d.guidance) n++;
    if (imageForm.width !== d.width || imageForm.height !== d.height) n++;
    if (imageForm.negativePrompt.trim() !== d.negativePrompt.trim()) n++;
    if (imageForm.seedText.trim() !== "") n++;
    return n;
  }, [
    imageDefaults,
    imageForm.steps,
    imageForm.guidance,
    imageForm.width,
    imageForm.height,
    imageForm.negativePrompt,
    imageForm.seedText,
  ]);
  const videoSettingsDirty = useMemo(() => {
    const d = videoDefaults;
    if (!d) return 0;
    let n = 0;
    if (videoForm.steps !== d.steps) n++;
    if (videoForm.guidance !== d.guidance) n++;
    if (videoForm.width !== d.width || videoForm.height !== d.height) n++;
    if (videoForm.numFrames !== d.numFrames) n++;
    if (videoForm.fps !== d.fps) n++;
    if (videoForm.negativePrompt.trim() !== d.negativePrompt.trim()) n++;
    if (videoForm.seedText.trim() !== "") n++;
    return n;
  }, [
    videoDefaults,
    videoForm.steps,
    videoForm.guidance,
    videoForm.width,
    videoForm.height,
    videoForm.numFrames,
    videoForm.fps,
    videoForm.negativePrompt,
    videoForm.seedText,
  ]);

  const settingsDirty = mode === "image" ? imageSettingsDirty : videoSettingsDirty;
  const advancedResult = mode === "image" ? imageAdvanced : videoAdvanced;
  const advancedCount = advancedResult.ok ? advancedResult.count : 0;

  // ── Validation ────────────────────────────────────────────────────────────
  const imageDimError = dimensionError(imageForm.width, imageForm.height);
  const videoDimError = dimensionError(videoForm.width, videoForm.height);
  const videoJobActive =
    activeJob?.status === "queued" || activeJob?.status === "running";

  const imageReady =
    !!imageDefaults &&
    !!prompt.trim() &&
    imageAdvanced.ok &&
    imageDimError === null;
  const videoReady =
    !!videoDefaults &&
    !!prompt.trim() &&
    videoAdvanced.ok &&
    videoDimError === null &&
    !videoJobActive;

  // ── Request building + submit ────────────────────────────────────────────
  const buildImageInput = useCallback((): ImageGenerateInput | null => {
    const d = imageForm.defaults;
    if (!d) return null;
    const adv = computeAdvancedOverrides(imageForm.advancedText, d.advanced);
    if (!adv.ok) return null;
    // Resolve a concrete seed even for "random" so every result is
    // reproducible — the used seed lands on the queue chip and in the library.
    const seed = parseSeedText(imageForm.seedText) ?? randomSeed();
    return {
      prompt: imageForm.prompt.trim(),
      model_id: d.modelId,
      negative_prompt: d.supportsNegativePrompt
        ? imageForm.negativePrompt.trim() || undefined
        : undefined,
      steps: imageForm.steps,
      guidance: imageForm.guidance,
      width: imageForm.width,
      height: imageForm.height,
      seed,
      extra_params: adv.count > 0 ? adv.overrides : undefined,
    };
  }, [imageForm]);

  const buildVideoRequest = useCallback((): VideoGenRequest | null => {
    const d = videoForm.defaults;
    if (!d) return null;
    const adv = computeAdvancedOverrides(videoForm.advancedText, d.advanced);
    if (!adv.ok) return null;
    const seed = parseSeedText(videoForm.seedText) ?? randomSeed();
    return {
      prompt: videoForm.prompt.trim(),
      model_id: d.modelId,
      negative_prompt: d.supportsNegativePrompt
        ? videoForm.negativePrompt.trim() || undefined
        : undefined,
      steps: videoForm.steps,
      guidance: videoForm.guidance,
      width: videoForm.width,
      height: videoForm.height,
      num_frames: videoForm.numFrames,
      fps: videoForm.fps,
      seed,
      image_base64: videoForm.sourceImage?.base64,
      extra_params: adv.count > 0 ? adv.overrides : undefined,
    };
  }, [videoForm]);

  const handleGenerate = useCallback(async () => {
    if (mode === "image") {
      const input = buildImageInput();
      if (!input) return;
      const ok = await generateImage(input);
      if (ok) setResultOpen(true);
    } else {
      const req = buildVideoRequest();
      if (!req) return;
      await generateVideo(req);
    }
  }, [mode, buildImageInput, buildVideoRequest, generateImage, generateVideo]);

  const handleEnqueue = useCallback(async () => {
    const input = buildImageInput();
    if (!input) return;
    // Queue and clear nothing — the prompt stays editable for the next one.
    await enqueueImageJob(input);
  }, [buildImageInput, enqueueImageJob]);

  const reuseImageSeed = useCallback(
    (seed: number) => setImageForm({ seedText: String(seed) }),
    [setImageForm],
  );
  const reuseSeedFromLibrary = useCallback(
    (item: MediaLibraryItem, seed: number) => {
      if (item.media_type === "video") {
        setVideoForm({ seedText: String(seed) });
        setMode("video");
      } else {
        setImageForm({ seedText: String(seed) });
        setMode("image");
      }
      setSelected(null);
    },
    [setImageForm, setVideoForm],
  );

  // ── Model picker handlers ────────────────────────────────────────────────
  const handleLoadImageModel = useCallback(
    async (model: ImageGenModelInfo) => {
      const result = await loadImageModel(model.model_id);
      if (result.success) {
        await prepareImageGenerate(model);
        setModelPickerOpen(false);
      }
    },
    [loadImageModel, prepareImageGenerate],
  );
  const handleLoadVideoModel = useCallback(
    async (model: VideoGenModelInfo) => {
      const result = await loadVideoModel(model.model_id);
      if (result.success) {
        await prepareVideoGenerate(model);
        setModelPickerOpen(false);
      }
    },
    [loadVideoModel, prepareVideoGenerate],
  );

  // ── Not-ready states ─────────────────────────────────────────────────────
  const packagesMissing = imageStatus !== null && !imageStatus.available;
  const engineDown = imageStatusError !== null;
  const modeStatusError = mode === "image" ? imageStatusError : videoStatusError;
  const genError = mode === "image" ? imageGenError : videoGenError;
  const dismissGenError =
    mode === "image" ? clearImageGenError : clearVideoGenError;
  const modelLoading = mode === "image" ? imageModelLoading : videoModelLoading;
  const currentModelName =
    mode === "image" ? currentImageModel?.name : currentVideoModel?.name;

  const activeQueueCount = imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  ).length;
  const queueJobs = imageJobs.slice(0, 12);
  const showQueueStrip =
    queueJobs.length > 0 || activeJob !== null || imageResult !== null;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      {/* ══ Composer bar ══════════════════════════════════════════════════ */}
      <div className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto max-w-6xl space-y-2.5 px-4 py-3">
          {engineDown && (
            <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0 flex-1 break-words">
                {imageStatusError}
              </span>
              <Button
                size="sm"
                variant="outline"
                className="h-6 shrink-0 text-[11px]"
                onClick={() => {
                  void refreshImage();
                  void refreshVideo();
                }}
              >
                <RefreshCw className="mr-1 h-3 w-3" />
                Retry
              </Button>
            </div>
          )}

          {!engineDown && packagesMissing ? (
            /* Packages missing → the existing one-click installer flow. */
            <ImageGenInstaller
              models={imageModels}
              onInstallComplete={() => void refreshImage()}
            />
          ) : (
            <>
              {/* Row 1: mode toggle · prompt · actions */}
              <div className="flex items-start gap-2.5">
                {/* Mode toggle */}
                <div className="flex shrink-0 rounded-lg border bg-muted/30 p-0.5">
                  {(
                    [
                      { id: "image" as const, label: "Image", Icon: ImageIcon },
                      { id: "video" as const, label: "Video", Icon: Film },
                    ] satisfies {
                      id: ComposerMode;
                      label: string;
                      Icon: typeof ImageIcon;
                    }[]
                  ).map(({ id, label, Icon }) => (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setMode(id)}
                      aria-pressed={mode === id}
                      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                        mode === id
                          ? "bg-background text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      <Icon className="h-3.5 w-3.5" />
                      {label}
                    </button>
                  ))}
                </div>

                {/* Big prompt input (context-backed — survives navigation) */}
                <Textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={
                    mode === "image"
                      ? "Describe the image you want to create…"
                      : "Describe the video you want to create…"
                  }
                  rows={1}
                  className="min-h-[38px] flex-1 resize-none text-sm"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (mode === "image" ? imageReady && !imageGenerating : videoReady && !videoGenerating) {
                        void handleGenerate();
                      }
                    }
                  }}
                  aria-label="Prompt"
                />

                {/* Generate + queue */}
                <div className="flex shrink-0 items-start gap-1.5">
                  <CancelableGenerateButton
                    generating={
                      mode === "image"
                        ? imageGenerating
                        : videoGenerating || videoJobActive
                    }
                    cancelling={
                      mode === "image"
                        ? imageCancelling
                        : videoCancelling || !!activeJob?.cancel_requested
                    }
                    startedAt={mode === "image" ? imageGenStartedAt : null}
                    elapsedSeconds={
                      mode === "video" && videoJobActive
                        ? (activeJob?.elapsed_seconds ?? null)
                        : null
                    }
                    disabled={mode === "image" ? !imageReady : !videoReady}
                    onGenerate={() => void handleGenerate()}
                    onCancel={() =>
                      void (mode === "image"
                        ? cancelImageGeneration()
                        : cancelVideoGeneration())
                    }
                    buttonClassName="h-[38px]"
                    cancelLabel="Cancel"
                    workingLabel={
                      mode === "image" ? "Generating" : "Generating video"
                    }
                    idleContent={
                      <>
                        <Sparkles className="mr-1.5 h-4 w-4" />
                        Generate
                      </>
                    }
                  />
                  {mode === "image" && (
                    <Button
                      variant="outline"
                      disabled={!imageReady}
                      onClick={() => void handleEnqueue()}
                      className="h-[38px]"
                      title="Queue this generation and keep editing"
                    >
                      <ListPlus className="mr-1.5 h-4 w-4" />
                      Queue
                    </Button>
                  )}
                </div>
              </div>

              {/* Row 2: model chip · settings · advanced · inline status */}
              <div className="flex flex-wrap items-center gap-1.5">
                {/* Model chip → picker popover */}
                <Popover open={modelPickerOpen} onOpenChange={setModelPickerOpen}>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors ${
                        currentModelName
                          ? "border-violet-500/40 bg-violet-500/10 text-violet-600 hover:bg-violet-500/15 dark:text-violet-400"
                          : "animate-pulse border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                      }`}
                    >
                      <Cpu className="h-3 w-3" />
                      {modelLoading ? (
                        <span className="flex items-center gap-1">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          Loading model…
                        </span>
                      ) : (
                        (currentModelName ?? "Choose a model to start")
                      )}
                      <ChevronDown className="h-3 w-3 opacity-60" />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-96 p-3">
                    <div className="space-y-2">
                      <p className="text-xs font-semibold">
                        {mode === "image" ? "Image models" : "Video models"}
                      </p>
                      {mode === "video" &&
                        videoStatus &&
                        !videoStatus.hardware_supported && (
                          <p className="flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 px-2.5 py-2 text-[11px] text-muted-foreground">
                            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" />
                            {videoStatus.hardware_reason ??
                              "Video generation is not supported on this hardware."}
                          </p>
                        )}
                      {modeStatusError && (
                        <ErrorNote message={modeStatusError} />
                      )}
                      <div className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
                        {mode === "image"
                          ? imageModels.map((m) => (
                              <ModelPickerRow
                                key={m.model_id}
                                name={m.name}
                                provider={m.provider}
                                sizeGb={m.download_size_gb}
                                isDownloaded={m.is_downloaded}
                                isLoaded={
                                  imageStatus?.loaded_model_id === m.model_id
                                }
                                requiresToken={m.requires_hf_token}
                                hardwareOk={m.hardware_ok}
                                hardwareReason={m.hardware_reason}
                                isLoadingThis={loadingImageModelId === m.model_id}
                                anyLoadInFlight={
                                  imageModelLoading ||
                                  !!imageStatus?.is_loading
                                }
                                onLoad={() => void handleLoadImageModel(m)}
                                onDownload={() =>
                                  void downloadImageModel(m.model_id)
                                }
                              />
                            ))
                          : videoModels.map((m) => (
                              <ModelPickerRow
                                key={m.model_id}
                                name={m.name}
                                provider={m.provider}
                                sizeGb={m.download_size_gb}
                                isDownloaded={m.is_downloaded}
                                isLoaded={
                                  videoStatus?.loaded_model_id === m.model_id
                                }
                                requiresToken={m.requires_hf_token}
                                hardwareOk={m.hardware_ok}
                                hardwareReason={m.hardware_reason}
                                isLoadingThis={loadingVideoModelId === m.model_id}
                                anyLoadInFlight={
                                  videoModelLoading ||
                                  !!videoStatus?.is_loading
                                }
                                onLoad={() => void handleLoadVideoModel(m)}
                                onDownload={() =>
                                  void downloadVideoModel(m.model_id)
                                }
                              />
                            ))}
                        {(mode === "image" ? imageModels : videoModels)
                          .length === 0 && (
                          <p className="py-4 text-center text-xs text-muted-foreground">
                            {imageStatusLoading
                              ? "Loading model catalog…"
                              : "No models available."}
                          </p>
                        )}
                      </div>
                      <p className="text-[10px] text-muted-foreground">
                        Download progress appears in the Download Manager.
                      </p>
                    </div>
                  </PopoverContent>
                </Popover>

                {/* Settings popover (common params) */}
                <Popover open={settingsOpen} onOpenChange={setSettingsOpen}>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      disabled={!form.defaults}
                      className="relative flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors hover:bg-muted/30 disabled:opacity-50"
                    >
                      <Settings2 className="h-3 w-3" />
                      Settings
                      {settingsDirty > 0 && (
                        <span className="rounded-full bg-violet-500/15 px-1.5 text-[10px] tabular-nums text-violet-600 dark:text-violet-400">
                          {settingsDirty}
                        </span>
                      )}
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-[380px] p-4">
                    {mode === "image" && imageDefaults ? (
                      <div className="space-y-3.5">
                        {imageForm.paramsError && currentImageModel && (
                          <ParamsErrorBanner
                            error={imageForm.paramsError}
                            onRetry={() =>
                              void prepareImageGenerate(currentImageModel)
                            }
                          />
                        )}
                        <div className="grid grid-cols-2 gap-3">
                          <NumberSliderField
                            label="Steps"
                            value={imageForm.steps}
                            onChange={(v) => setImageForm({ steps: v })}
                            min={1}
                            max={
                              currentImageModel?.pipeline_type.startsWith(
                                "flux",
                              )
                                ? 50
                                : 100
                            }
                            step={1}
                            defaultValue={imageDefaults.steps}
                          />
                          <NumberSliderField
                            label="Guidance"
                            value={imageForm.guidance}
                            onChange={(v) => setImageForm({ guidance: v })}
                            min={0}
                            max={20}
                            step={0.5}
                            defaultValue={imageDefaults.guidance}
                          />
                        </div>
                        <DimensionPicker
                          width={imageForm.width}
                          height={imageForm.height}
                          onChange={(w, h) =>
                            setImageForm({ width: w, height: h })
                          }
                          presets={[
                            {
                              label: `Default ${imageDefaults.width}×${imageDefaults.height}`,
                              width: imageDefaults.width,
                              height: imageDefaults.height,
                            },
                            ...IMAGE_SIZE_PRESETS.filter(
                              (p) =>
                                p.width !== imageDefaults.width ||
                                p.height !== imageDefaults.height,
                            ),
                          ]}
                        />
                        <div className="space-y-1.5">
                          <Label className="text-xs">
                            Seed{" "}
                            <span className="text-muted-foreground">
                              (blank = random; the used seed is always shown)
                            </span>
                          </Label>
                          <SeedInput
                            value={imageForm.seedText}
                            onChange={(seedText) => setImageForm({ seedText })}
                          />
                        </div>
                        <NegativePromptField
                          supported={imageDefaults.supportsNegativePrompt}
                          value={imageForm.negativePrompt}
                          onChange={(v) => setImageForm({ negativePrompt: v })}
                        />
                        <div className="flex justify-end">
                          <ResetButton
                            onClick={resetImageCommon}
                            label="Reset to model defaults"
                          />
                        </div>
                      </div>
                    ) : mode === "video" && videoDefaults ? (
                      <div className="space-y-3.5">
                        {videoForm.paramsError && currentVideoModel && (
                          <ParamsErrorBanner
                            error={videoForm.paramsError}
                            onRetry={() =>
                              void prepareVideoGenerate(currentVideoModel)
                            }
                          />
                        )}
                        <div className="grid grid-cols-2 gap-3">
                          <NumberSliderField
                            label="Steps"
                            value={videoForm.steps}
                            onChange={(v) => setVideoForm({ steps: v })}
                            min={1}
                            max={100}
                            step={1}
                            defaultValue={videoDefaults.steps}
                          />
                          <NumberSliderField
                            label="Guidance"
                            value={videoForm.guidance}
                            onChange={(v) => setVideoForm({ guidance: v })}
                            min={0}
                            max={20}
                            step={0.5}
                            defaultValue={videoDefaults.guidance}
                          />
                          <NumberSliderField
                            label="Frames"
                            value={videoForm.numFrames}
                            onChange={(v) => setVideoForm({ numFrames: v })}
                            min={8}
                            max={currentVideoModel?.max_num_frames ?? 129}
                            step={1}
                            defaultValue={videoDefaults.numFrames}
                          />
                          <NumberSliderField
                            label="FPS"
                            value={videoForm.fps}
                            onChange={(v) => setVideoForm({ fps: v })}
                            min={4}
                            max={60}
                            step={1}
                            defaultValue={videoDefaults.fps}
                          />
                        </div>
                        <DimensionPicker
                          width={videoForm.width}
                          height={videoForm.height}
                          onChange={(w, h) =>
                            setVideoForm({ width: w, height: h })
                          }
                          presets={[
                            {
                              label: `Default ${videoDefaults.width}×${videoDefaults.height}`,
                              width: videoDefaults.width,
                              height: videoDefaults.height,
                            },
                          ]}
                        />
                        <div className="space-y-1.5">
                          <Label className="text-xs">
                            Seed{" "}
                            <span className="text-muted-foreground">
                              (blank = random)
                            </span>
                          </Label>
                          <SeedInput
                            value={videoForm.seedText}
                            onChange={(seedText) => setVideoForm({ seedText })}
                          />
                        </div>
                        <NegativePromptField
                          supported={videoDefaults.supportsNegativePrompt}
                          value={videoForm.negativePrompt}
                          onChange={(v) => setVideoForm({ negativePrompt: v })}
                        />
                        <div className="flex justify-end">
                          <ResetButton
                            onClick={resetVideoCommon}
                            label="Reset to model defaults"
                          />
                        </div>
                      </div>
                    ) : (
                      <p className="py-2 text-xs text-muted-foreground">
                        Load a model first — its settings appear here.
                      </p>
                    )}
                  </PopoverContent>
                </Popover>

                {/* Advanced popover (every remaining pipeline kwarg) */}
                <Popover open={advancedOpen} onOpenChange={setAdvancedOpen}>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      disabled={!form.defaults}
                      className="relative flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors hover:bg-muted/30 disabled:opacity-50"
                    >
                      <SlidersHorizontal className="h-3 w-3" />
                      Advanced
                      {!advancedResult.ok ? (
                        <span className="rounded-full bg-destructive/15 px-1.5 text-[10px] font-medium text-destructive">
                          !
                        </span>
                      ) : advancedCount > 0 ? (
                        <span className="rounded-full bg-violet-500/15 px-1.5 text-[10px] tabular-nums text-violet-600 dark:text-violet-400">
                          {advancedCount}
                        </span>
                      ) : null}
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-[440px] p-4">
                    {mode === "image" && imageDefaults ? (
                      <AdvancedParamsEditor
                        defaults={imageDefaults.advanced}
                        text={imageForm.advancedText}
                        onChange={(advancedText) =>
                          setImageForm({ advancedText })
                        }
                        onReset={resetImageAdvanced}
                      />
                    ) : mode === "video" && videoDefaults ? (
                      <AdvancedParamsEditor
                        defaults={videoDefaults.advanced}
                        text={videoForm.advancedText}
                        onChange={(advancedText) =>
                          setVideoForm({ advancedText })
                        }
                        onReset={resetVideoAdvanced}
                      />
                    ) : (
                      <p className="py-2 text-xs text-muted-foreground">
                        Load a model first — its advanced parameters appear
                        here.
                      </p>
                    )}
                  </PopoverContent>
                </Popover>

                {/* Inline validation hint */}
                {(mode === "image" ? imageDimError : videoDimError) && (
                  <span className="flex items-center gap-1 text-[11px] text-destructive">
                    <AlertCircle className="h-3 w-3" />
                    {mode === "image" ? imageDimError : videoDimError}
                  </span>
                )}
              </div>

              {genError && (
                <ErrorNote message={genError} onDismiss={dismissGenError} />
              )}
            </>
          )}

          {/* ══ Queue strip ══════════════════════════════════════════════ */}
          {!packagesMissing && showQueueStrip && (
            <div className="space-y-1">
              {activeQueueCount > 0 && (
                <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Queue · {activeQueueCount} active
                </p>
              )}
              {imageJobsError && <ErrorNote message={imageJobsError} />}
              <div className="flex gap-2 overflow-x-auto pb-1">
                {/* Fresh foreground result */}
                {imageResult && (
                  <button
                    type="button"
                    onClick={() => setResultOpen(true)}
                    className="flex w-56 shrink-0 items-center gap-2 rounded-lg border border-green-500/40 bg-green-500/5 px-2.5 py-2 text-left transition-colors hover:bg-green-500/10"
                    title="View the result you just generated"
                  >
                    <img
                      src={`data:image/png;base64,${imageResult.b64}`}
                      alt="Fresh result"
                      className="h-9 w-9 shrink-0 rounded border object-cover"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-medium">Just generated</p>
                      <p className="text-[10px] text-muted-foreground">
                        {imageResult.width}×{imageResult.height} ·{" "}
                        {imageResult.elapsed.toFixed(1)}s
                      </p>
                    </div>
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
                  </button>
                )}
                {/* Single active/most-recent video job */}
                {activeJob && (
                  <VideoJobCard
                    job={activeJob}
                    onDismiss={clearActiveJob}
                    onCancel={(id) => void cancelVideoJob(id)}
                  />
                )}
                {/* Image queue chips */}
                {queueJobs.map((j) => (
                  <ImageJobChip
                    key={j.job_id}
                    job={j}
                    thumbUrl={imageJobThumbs[j.job_id] ?? null}
                    onCancel={(id) => void cancelImageJob(id)}
                    onReuseSeed={reuseImageSeed}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ══ The feed: media library as masonry ═══════════════════════════ */}
      <div className="mx-auto max-w-6xl px-4 py-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex gap-1.5">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
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
          <Button size="sm" variant="ghost" onClick={() => void refreshLibrary()}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>

        {libraryError && (
          <div className="mb-3">
            <ErrorNote message={libraryError} onDismiss={clearLibraryError} />
          </div>
        )}

        {libraryLoading && items.length === 0 ? (
          <div className="flex items-center justify-center gap-3 py-20 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Loading your creations…</span>
          </div>
        ) : items.length === 0 ? (
          !libraryError && (
            <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed py-24 text-muted-foreground">
              <Sparkles className="h-10 w-10 opacity-20" />
              <span className="text-sm font-medium">
                Your gallery is empty
              </span>
              <span className="max-w-sm text-center text-xs">
                Write a prompt in the composer above and hit Generate —
                everything you create lands here, newest first.
              </span>
            </div>
          )
        ) : (
          <>
            <div className="columns-2 gap-3 sm:columns-3 lg:columns-4 xl:columns-5">
              {items.map((item) => (
                <GalleryTile
                  key={item.id}
                  item={item}
                  fileUrls={fileUrls}
                  getFileUrl={getFileUrl}
                  onOpen={setSelected}
                />
              ))}
            </div>
            {hasMore && (
              <div className="flex justify-center pt-4">
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
      </div>

      {/* ══ Dialogs ═══════════════════════════════════════════════════════ */}
      <GalleryDetailDialog
        item={selected}
        fileUrls={fileUrls}
        getFileUrl={getFileUrl}
        onDelete={deleteItem}
        onReuseSeed={reuseSeedFromLibrary}
        onClose={() => setSelected(null)}
      />

      <Dialog
        open={resultOpen && imageResult !== null}
        onOpenChange={(open) => !open && setResultOpen(false)}
      >
        <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Sparkles className="h-4 w-4 text-violet-500" />
              Fresh result
            </DialogTitle>
            <DialogDescription className="text-xs">
              Also saved to your gallery below.
            </DialogDescription>
          </DialogHeader>
          {imageResult && (
            <GeneratedImageView
              result={imageResult}
              onClear={() => {
                clearImageResult();
                setResultOpen(false);
              }}
              onReuseSeed={reuseImageSeed}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
