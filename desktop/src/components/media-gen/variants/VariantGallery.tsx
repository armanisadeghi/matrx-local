/**
 * VariantGallery — "Gallery first" media-gen layout (UI bake-off variant).
 *
 * Your creations ARE the interface (Midjourney-feed style): a full-width
 * composer bar (prompt + mode toggle + model/settings/advanced popovers +
 * generate/queue), a queue strip of job chips, and the media library as an
 * immersive masonry feed with a detail dialog.
 *
 * THIN layout shell: the model picker (now WITH download progress), settings
 * controls (now incl. img2img input + LoRA styles), advanced editor, queue
 * chips and video job card all come from media-gen/core.
 */

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  AlertCircle,
  Check,
  ChevronDown,
  Copy,
  Cpu,
  Film,
  FolderOpen,
  Image as ImageIcon,
  ImagePlus,
  ListPlus,
  Loader2,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  CheckCircle2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import type { MediaLibraryItem } from "@/lib/api";
import { ImageGenInstaller } from "../ImageGenInstaller";
import {
  CancelableGenerateButton,
  ErrorNote,
  PromptCapacityHint,
  QueueNotice,
  SeedChip,
} from "../shared";
import { useImageGenController } from "../core/imageController";
import { useVideoGenController } from "../core/videoController";
import { pickedImageFromUrl } from "../core/pickedImage";
import { ImageStatusErrorCard } from "../core/gates";
import { ImageModelPicker, VideoModelPicker } from "../core/ModelPicker";
import {
  ImageAdvancedSection,
  ImageCommonSettings,
  ImageParamsErrorNotice,
  InputImageControl,
  LoraStylesSection,
} from "../core/ImageGenerateForm";
import {
  SourceImageControl,
  VideoAdvancedSection,
  VideoCommonSettings,
  VideoParamsErrorNotice,
} from "../core/VideoGenerateForm";
import { ImageResultPane } from "../core/ResultView";
import { ImageQueuePanel } from "../core/ImageQueuePanel";
import { ActiveVideoJobCard } from "../core/VideoJobPanel";

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

// ── Detail dialog ────────────────────────────────────────────────────────────

function GalleryDetailDialog({
  item,
  fileUrls,
  getFileUrl,
  onDelete,
  onReuseSeed,
  onUseAsInput,
  onClose,
}: {
  item: MediaLibraryItem | null;
  fileUrls: Record<string, string>;
  getFileUrl: MediaLibraryActions["getFileUrl"];
  onDelete: (itemId: string) => Promise<boolean>;
  onReuseSeed: (item: MediaLibraryItem, seed: number) => void;
  /** Routes an IMAGE item into the img2img input slot. */
  onUseAsInput: (item: MediaLibraryItem, url: string) => void;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const url = item ? (fileUrls[item.id] ?? null) : null;

  useEffect(() => {
    setCopied(false);
    setConfirmingDelete(false);
    setDeleting(false);
    setActionError(null);
  }, [item?.id]);

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
                {item.media_type === "image" && url && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onUseAsInput(item, url)}
                    title="Use this image as the img2img input"
                  >
                    <ImagePlus className="mr-1.5 h-3.5 w-3.5" />
                    Use as input
                  </Button>
                )}
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
    imageStatusError,
    imageGenerating,
    imageCancelling,
    imageGenStartedAt,
    imageQueueNotice,
    imageResult,
    imageJobs,
    videoStatus,
    videoStatusError,
    imageModelLoading,
    videoModelLoading,
    videoGenerating,
    videoCancelling,
    activeJob,
  } = mediaGen;
  const {
    refreshImage,
    refreshVideo,
    cancelImageGeneration,
    cancelVideoGeneration,
    clearImageQueueNotice,
    setImageForm,
    setVideoForm,
    useImageAsInput,
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

  const closePicker = useCallback(() => setModelPickerOpen(false), []);
  const imageCtl = useImageGenController({ onAfterSelect: closePicker });
  const videoCtl = useVideoGenController({ onAfterSelect: closePicker });

  // ── Library freshness: new generations surface at the top ────────────────
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

  const completedVideoJobId =
    activeJob?.status === "completed" ? activeJob.job_id : null;
  useEffect(() => {
    if (!completedVideoJobId) return;
    void refreshLibrary();
  }, [completedVideoJobId, refreshLibrary]);

  const freshResultItemId = imageResult?.itemId ?? null;
  useEffect(() => {
    if (!freshResultItemId) return;
    void refreshLibrary();
  }, [freshResultItemId, refreshLibrary]);

  // ── Form plumbing (ALL content state lives in context) ───────────────────
  const isImage = mode === "image";
  const prompt = isImage ? imageCtl.form.prompt : videoCtl.form.prompt;
  const setPrompt = useCallback(
    (value: string) => {
      if (isImage) setImageForm({ prompt: value });
      else setVideoForm({ prompt: value });
    },
    [isImage, setImageForm, setVideoForm],
  );

  // Settings-off-defaults badge counts.
  const imageSettingsDirty = useMemo(() => {
    const d = imageCtl.defaults;
    const f = imageCtl.form;
    if (!d) return 0;
    let n = 0;
    if (f.steps !== d.steps) n++;
    if (f.guidance !== d.guidance) n++;
    if (f.width !== d.width || f.height !== d.height) n++;
    if (f.negativePrompt.trim() !== d.negativePrompt.trim()) n++;
    if (f.seedText.trim() !== "") n++;
    if (f.initImage !== null) n++;
    if (f.loras.some((l) => l.enabled)) n++;
    return n;
  }, [imageCtl.defaults, imageCtl.form]);
  const videoSettingsDirty = useMemo(() => {
    const d = videoCtl.defaults;
    const f = videoCtl.form;
    if (!d) return 0;
    let n = 0;
    if (f.steps !== d.steps) n++;
    if (f.guidance !== d.guidance) n++;
    if (f.width !== d.width || f.height !== d.height) n++;
    if (f.numFrames !== d.numFrames) n++;
    if (f.fps !== d.fps) n++;
    if (f.negativePrompt.trim() !== d.negativePrompt.trim()) n++;
    if (f.seedText.trim() !== "") n++;
    if (f.sourceImage !== null) n++;
    return n;
  }, [videoCtl.defaults, videoCtl.form]);

  const settingsDirty = isImage ? imageSettingsDirty : videoSettingsDirty;
  const advancedResult = isImage ? imageCtl.advanced : videoCtl.advanced;
  const advancedCount = advancedResult.ok ? advancedResult.count : 0;

  const videoJobActive = videoCtl.jobIsActive;
  const imageReady = !imageCtl.formInvalid;
  const videoReady = !videoCtl.formInvalid && !videoJobActive;

  const handleGenerate = useCallback(async () => {
    if (isImage) {
      const input = imageCtl.buildInput();
      if (!input) return;
      // Show the fresh result dialog after a successful foreground run.
      await imageCtl.handleGenerate();
      setResultOpen(true);
    } else {
      await videoCtl.handleGenerate();
    }
  }, [isImage, imageCtl, videoCtl]);

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

  const handleUseAsInputFromLibrary = useCallback(
    (item: MediaLibraryItem, url: string) => {
      void pickedImageFromUrl(url, item.file_name || `${item.id}.png`, (msg) =>
        imageCtl.setLocalError(msg),
      ).then((img) => {
        if (img) {
          useImageAsInput(img);
          setMode("image");
          setSelected(null);
        }
      });
    },
    [imageCtl, useImageAsInput],
  );

  // ── Not-ready states ─────────────────────────────────────────────────────
  const packagesMissing = imageStatus !== null && !imageStatus.available;
  const engineDown = imageStatusError !== null;
  const modeStatusError = isImage ? imageStatusError : videoStatusError;
  const modelLoading = isImage ? imageModelLoading : videoModelLoading;
  const currentModelName = isImage
    ? imageCtl.model?.name
    : videoCtl.model?.name;
  const genError = isImage ? imageCtl.genError : videoCtl.genError;
  const dismissGenError = isImage
    ? imageCtl.dismissGenError
    : videoCtl.dismissGenError;

  const showQueueStrip =
    imageJobs.length > 0 || activeJob !== null || imageResult !== null;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      {/* ══ Composer bar ══════════════════════════════════════════════════ */}
      <div className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto max-w-6xl space-y-2.5 px-4 py-3">
          {engineDown && (
            <ImageStatusErrorCard
              error={imageStatusError ?? ""}
              onRetry={() => {
                void refreshImage();
                void refreshVideo();
              }}
            />
          )}

          {!engineDown && packagesMissing ? (
            <ImageGenInstaller
              models={mediaGen.imageModels}
              onInstallComplete={() => void refreshImage()}
            />
          ) : !engineDown ? (
            <>
              {/* Row 1: mode toggle · prompt · actions */}
              <div className="flex items-start gap-2.5">
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

                <Textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={
                    isImage
                      ? "Describe the image you want to create…"
                      : "Describe the video you want to create…"
                  }
                  rows={1}
                  className="min-h-[38px] max-h-40 flex-1 resize-y text-sm"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (
                        isImage
                          ? imageReady && !imageGenerating
                          : videoReady && !videoGenerating
                      ) {
                        void handleGenerate();
                      }
                    }
                  }}
                  aria-label="Prompt"
                />

                <div className="flex shrink-0 items-start gap-1.5">
                  <CancelableGenerateButton
                    generating={
                      isImage
                        ? imageGenerating
                        : videoGenerating || videoJobActive
                    }
                    cancelling={
                      isImage
                        ? imageCancelling
                        : videoCancelling || !!activeJob?.cancel_requested
                    }
                    startedAt={isImage ? imageGenStartedAt : null}
                    elapsedSeconds={
                      !isImage && videoJobActive
                        ? (activeJob?.elapsed_seconds ?? null)
                        : null
                    }
                    disabled={isImage ? !imageReady : !videoReady}
                    onGenerate={() => void handleGenerate()}
                    onCancel={() =>
                      void (isImage
                        ? cancelImageGeneration()
                        : cancelVideoGeneration())
                    }
                    buttonClassName="h-[38px]"
                    cancelLabel="Cancel"
                    workingLabel={isImage ? "Generating" : "Generating video"}
                    idleContent={
                      <>
                        <Sparkles className="mr-1.5 h-4 w-4" />
                        Generate
                      </>
                    }
                  />
                  {isImage && (
                    <Button
                      variant="outline"
                      disabled={!imageReady}
                      onClick={() => void imageCtl.handleEnqueue()}
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
                <Popover
                  open={modelPickerOpen}
                  onOpenChange={setModelPickerOpen}
                >
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
                        {isImage ? "Image models" : "Video models"}
                      </p>
                      {!isImage &&
                        videoStatus &&
                        !videoStatus.hardware_supported && (
                          <p className="flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 px-2.5 py-2 text-[11px] text-muted-foreground">
                            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" />
                            {videoStatus.hardware_reason ??
                              "Video generation is not supported on this hardware."}
                          </p>
                        )}
                      {modeStatusError && <ErrorNote message={modeStatusError} />}
                      <div className="max-h-80 overflow-y-auto pr-1">
                        {isImage ? (
                          <ImageModelPicker
                            ctl={imageCtl}
                            layout="rows"
                            showHeading={false}
                            showLoadedBanner={false}
                          />
                        ) : (
                          <VideoModelPicker
                            ctl={videoCtl}
                            layout="rows"
                            showHeading={false}
                            showLoadedBanner={false}
                          />
                        )}
                      </div>
                    </div>
                  </PopoverContent>
                </Popover>

                {/* Settings popover (common params + image input) */}
                <Popover open={settingsOpen} onOpenChange={setSettingsOpen}>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      disabled={isImage ? !imageCtl.defaults : !videoCtl.defaults}
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
                  <PopoverContent
                    align="start"
                    className="max-h-[70vh] w-[400px] overflow-y-auto p-4"
                  >
                    {isImage && imageCtl.defaults ? (
                      <div className="space-y-3.5">
                        <ImageParamsErrorNotice ctl={imageCtl} />
                        <ImageCommonSettings ctl={imageCtl} />
                        <InputImageControl ctl={imageCtl} />
                        <LoraStylesSection ctl={imageCtl} />
                      </div>
                    ) : !isImage && videoCtl.defaults ? (
                      <div className="space-y-3.5">
                        <VideoParamsErrorNotice ctl={videoCtl} />
                        <VideoCommonSettings ctl={videoCtl} />
                        <SourceImageControl ctl={videoCtl} />
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
                      disabled={isImage ? !imageCtl.defaults : !videoCtl.defaults}
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
                    {isImage && imageCtl.defaults ? (
                      <ImageAdvancedSection ctl={imageCtl} />
                    ) : !isImage && videoCtl.defaults ? (
                      <VideoAdvancedSection ctl={videoCtl} />
                    ) : (
                      <p className="py-2 text-xs text-muted-foreground">
                        Load a model first — its advanced parameters appear
                        here.
                      </p>
                    )}
                  </PopoverContent>
                </Popover>

                {/* Inline validation hint */}
                {(isImage ? imageCtl.dimError : videoCtl.dimError) && (
                  <span className="flex items-center gap-1 text-[11px] text-destructive">
                    <AlertCircle className="h-3 w-3" />
                    {isImage ? imageCtl.dimError : videoCtl.dimError}
                  </span>
                )}

                {isImage && (
                  <PromptCapacityHint
                    pipelineType={imageCtl.model?.pipeline_type}
                    className="w-full"
                  />
                )}
              </div>

              {genError && (
                <ErrorNote message={genError} onDismiss={dismissGenError} />
              )}

              {isImage && imageQueueNotice && (
                <QueueNotice
                  message={imageQueueNotice}
                  onDismiss={clearImageQueueNotice}
                />
              )}
            </>
          ) : null}

          {/* ══ Queue strip ══════════════════════════════════════════════ */}
          {!packagesMissing && !engineDown && showQueueStrip && (
            <div className="space-y-1">
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
                  <div className="w-96 shrink-0">
                    <ActiveVideoJobCard />
                  </div>
                )}
              </div>
              <ImageQueuePanel layout="chips" limit={12} showHeading={false} />
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
              <span className="text-sm font-medium">Your gallery is empty</span>
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
        onUseAsInput={handleUseAsInputFromLibrary}
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
          {imageResult && <ImageResultPane hideIdle />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
