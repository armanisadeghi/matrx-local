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
  ChevronDown,
  Cpu,
  Film,
  Image as ImageIcon,
  ListPlus,
  Loader2,
  RefreshCw,
  Settings2,
  SlidersHorizontal,
  Sparkles,
  CheckCircle2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
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
import { useMediaLibraryApp } from "@/contexts/MediaLibraryContext";
import type { MediaLibraryFilter } from "@/hooks/use-media-library";
import type { MediaLibraryItem } from "@/lib/api";
import { MediaItemThumb, viewingSetOf } from "@/components/media/MediaThumb";
import { formatDate, type MediaDescriptor } from "@/components/media/types";
import { ImageGenInstaller } from "../ImageGenInstaller";
import {
  CancelableGenerateButton,
  ErrorNote,
  PromptCapacityHint,
  QueueNotice,
} from "../shared";
import { useImageGenController } from "../core/imageController";
import { useVideoGenController } from "../core/videoController";
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

// ── Masonry tile ─────────────────────────────────────────────────────────────
//
// No bespoke image code here: MediaItemThumb IS the canonical tile (blob-URL
// resolution, click → the app-wide lightbox, right-click → the canonical
// context menu, hover → info + "⋯" with every action). The old GalleryTile and
// GalleryDetailDialog were forks of the Library's — they drifted, and each was
// missing actions the other had. They are gone.

function GalleryTile({
  item,
  viewingSet,
}: {
  item: MediaLibraryItem;
  viewingSet: MediaDescriptor[];
}) {
  return (
    <div className="group mb-3 block w-full break-inside-avoid overflow-hidden rounded-xl border bg-card transition-colors hover:border-violet-500/50">
      <MediaItemThumb
        item={item}
        variant="gallery"
        viewingSet={viewingSet}
        className="w-full"
      >
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
      </MediaItemThumb>
    </div>
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
  } = mediaGenActions;

  // The ONE app-level library store — shared with the Library tab and Studio,
  // so a delete or vault move anywhere updates this feed too.
  const [library, libraryActions] = useMediaLibraryApp();
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
    clearError: clearLibraryError,
  } = libraryActions;

  const viewingSet = useMemo(
    () => viewingSetOf(items, fileUrls, "library"),
    [items, fileUrls],
  );

  // Local UI state ONLY: mode toggle, popover/dialog open flags.
  const [mode, setMode] = useState<ComposerMode>("image");
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);

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
                  viewingSet={viewingSet}
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
