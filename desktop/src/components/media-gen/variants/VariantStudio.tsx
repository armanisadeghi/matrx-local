/**
 * VariantStudio — "Studio split-pane" UI bake-off variant.
 *
 * Pro image-gen tool layout (A1111 / Fooocus style):
 *  - LEFT: fixed-width scrollable control panel — Image|Video mode toggle,
 *    loaded-model indicator + model-picker dialog, the canonical generate
 *    form, and a sticky bottom action bar.
 *  - RIGHT: large centered canvas (result / filmstrip pick / video), a slim
 *    queue rail, and a horizontal filmstrip of recent library images.
 *  - Workflows and the full Library open in full-height dialogs.
 *
 * THIN layout shell: forms, pickers, queue chips, job cards and gates come from
 * media-gen/core; every image (canvas, filmstrip) is a canonical MediaThumb and
 * every action comes from useMediaActions(). Only the split-pane chrome is
 * Studio-specific.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Film,
  Image as ImageIcon,
  Layers,
  Loader2,
  Workflow,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { useMediaLibraryApp } from "@/contexts/MediaLibraryContext";
import type { MediaLibraryItem } from "@/lib/api";
import { useMediaActions } from "@/components/media/MediaActionsProvider";
import {
  MediaItemThumb,
  MediaThumb,
  viewingSetOf,
} from "@/components/media/MediaThumb";
import { MediaOverflowMenu } from "@/components/media/MediaOverflowMenu";
import { CopyButton } from "@/components/media/MediaInfoDialog";
import {
  descriptorFromLibraryItem,
  descriptorFromResult,
  type MediaDescriptor,
} from "@/components/media/types";
import { WorkflowSection } from "@/components/media-gen/WorkflowSection";
import { MediaLibrarySection } from "@/components/media-gen/MediaLibrarySection";
import { SeedChip, StillWorkingNote } from "@/components/media-gen/shared";
import { useImageGenController } from "@/components/media-gen/core/imageController";
import { useVideoGenController } from "@/components/media-gen/core/videoController";
import {
  ImageGenGate,
  VideoGenGate,
} from "@/components/media-gen/core/gates";
import {
  ImageModelPicker,
  VideoModelPicker,
} from "@/components/media-gen/core/ModelPicker";
import {
  ImageFormNotices,
  ImageGenerateActions,
  ImageGenerateForm,
  ImageParamsErrorNotice,
} from "@/components/media-gen/core/ImageGenerateForm";
import {
  VideoFormNotices,
  VideoGenerateActions,
  VideoGenerateForm,
  VideoParamsErrorNotice,
} from "@/components/media-gen/core/VideoGenerateForm";
import { ImageResultPane } from "@/components/media-gen/core/ResultView";
import { ImageQueuePanel } from "@/components/media-gen/core/ImageQueuePanel";
import {
  ActiveVideoJobCard,
  VideoJobsList,
  VideoPlayback,
} from "@/components/media-gen/core/VideoJobPanel";

type StudioMode = "image" | "video";

function ModeToggle({
  mode,
  onChange,
  videoBusy,
}: {
  mode: StudioMode;
  onChange: (m: StudioMode) => void;
  videoBusy: boolean;
}) {
  const base =
    "flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors";
  return (
    <div className="flex gap-1 rounded-lg border bg-muted/30 p-1" role="tablist">
      <button
        type="button"
        role="tab"
        aria-selected={mode === "image"}
        onClick={() => onChange("image")}
        className={`${base} ${
          mode === "image"
            ? "bg-background shadow-sm text-foreground"
            : "text-muted-foreground hover:text-foreground"
        }`}
      >
        <ImageIcon className="h-3.5 w-3.5" />
        Image
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === "video"}
        onClick={() => onChange("video")}
        className={`${base} ${
          mode === "video"
            ? "bg-background shadow-sm text-foreground"
            : "text-muted-foreground hover:text-foreground"
        }`}
      >
        <Film className="h-3.5 w-3.5" />
        Video
        {videoBusy && (
          <Loader2 className="h-3 w-3 animate-spin text-violet-500" />
        )}
      </button>
    </div>
  );
}

export function VariantStudio() {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageStatusError,
    imageResult,
    imageForm,
    imageJobs,
    videoStatus,
    videoStatusError,
    activeJob,
    videoForm,
  } = state;
  const { setImageForm } = actions;
  const mediaActions = useMediaActions();

  // ── Pure-presentation local state ────────────────────────────────────────
  const [mode, setMode] = useState<StudioMode>("image");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [workflowsOpen, setWorkflowsOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  const closePicker = useCallback(() => setPickerOpen(false), []);
  const imageCtl = useImageGenController({ onAfterSelect: closePicker });
  const videoCtl = useVideoGenController({ onAfterSelect: closePicker });

  // ── Library filmstrip (the ONE shared store, images only, first 20) ──────
  const [libState, libActions] = useMediaLibraryApp();
  const { getFileUrl, refresh: refreshLibrary } = libActions;
  const filmstripItems = useMemo(
    () => libState.items.filter((i) => i.media_type === "image").slice(0, 20),
    [libState.items],
  );
  const filmstripIds = useMemo(
    () => filmstripItems.map((i) => i.id).join("\n"),
    [filmstripItems],
  );
  useEffect(() => {
    if (!filmstripIds) return;
    for (const id of filmstripIds.split("\n")) void getFileUrl(id);
  }, [filmstripIds, getFileUrl]);

  // A fresh direct generation was persisted → the library has a new item.
  useEffect(() => {
    if (!imageResult?.itemId) return;
    void refreshLibrary();
  }, [imageResult?.itemId, refreshLibrary]);

  // Queue jobs completing also add library items. Gated on the COUNT.
  const completedImageJobCount = imageJobs.filter(
    (j) => j.status === "completed",
  ).length;
  useEffect(() => {
    if (completedImageJobCount === 0) return;
    void refreshLibrary();
  }, [completedImageJobCount, refreshLibrary]);

  // ── Viewing set: the fresh result first, then the filmstrip ─────────────
  //
  // Built from canonical descriptors — the lightbox, the context menu and the
  // info dialog all speak this one shape, so an image opened from the canvas
  // has exactly the same abilities as one opened from the Library tab.
  const resultDescriptor = useMemo<MediaDescriptor | null>(() => {
    if (!imageResult) return null;
    return descriptorFromResult(imageResult, {
      ...(imageForm.prompt.trim() ? { prompt: imageForm.prompt.trim() } : {}),
      ...(imageCtl.model ? { modelId: imageCtl.model.model_id } : {}),
    });
  }, [imageResult, imageForm.prompt, imageCtl.model]);

  const viewingSet = useMemo<MediaDescriptor[]>(() => {
    const strip = viewingSetOf(filmstripItems, libState.fileUrls, "library");
    if (!resultDescriptor) return strip;
    // The fresh result IS a library item once persisted — don't list it twice.
    return [
      resultDescriptor,
      ...strip.filter((d) => d.itemId !== resultDescriptor.itemId),
    ];
  }, [filmstripItems, libState.fileUrls, resultDescriptor]);

  const openViewer = useCallback(
    (id: string | null) => {
      if (viewingSet.length === 0) return;
      const idx = id ? viewingSet.findIndex((x) => x.id === id) : 0;
      mediaActions.open(viewingSet, idx >= 0 ? idx : 0);
    },
    [viewingSet, mediaActions],
  );

  // ── Derived presentation values ──────────────────────────────────────────
  const isImage = mode === "image";
  const videoJobActive =
    activeJob?.status === "queued" || activeJob?.status === "running";
  const imageReady = !!imageStatus?.available;
  const videoReady =
    !!videoStatus?.hardware_supported && !!videoStatus?.packages_installed;
  const modeReady = isImage ? imageReady : videoReady;

  const selectedItem: MediaLibraryItem | null = useMemo(
    () =>
      selectedItemId
        ? (libState.items.find((i) => i.id === selectedItemId) ?? null)
        : null,
    [selectedItemId, libState.items],
  );
  const selectedDescriptor = useMemo<MediaDescriptor | null>(() => {
    if (!selectedItem) return null;
    const url = libState.fileUrls[selectedItem.id];
    return url ? descriptorFromLibraryItem(selectedItem, url, "library") : null;
  }, [selectedItem, libState.fileUrls]);

  // ── Left-panel model indicator ───────────────────────────────────────────
  const modelIndicator = (() => {
    const name = isImage ? imageCtl.model?.name : videoCtl.model?.name;
    const loadedId = isImage
      ? imageStatus?.loaded_model_id
      : videoStatus?.loaded_model_id;
    return (
      <div className="flex items-center gap-2 rounded-lg border bg-card px-3 py-2">
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${
            loadedId ? "bg-green-500" : "bg-muted-foreground/30"
          }`}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium">
            {name ?? "No model selected"}
          </p>
          <p className="truncate text-[10px] text-muted-foreground">
            {loadedId ? `Loaded: ${loadedId}` : "Pick and load a model to start"}
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 text-xs"
          onClick={() => setPickerOpen(true)}
          disabled={!modeReady}
        >
          {name ? "Change" : "Choose model"}
        </Button>
      </div>
    );
  })();

  // ── Canvas ───────────────────────────────────────────────────────────────
  const imageGenerating = state.imageGenerating;
  const canvas = isImage ? (
    imageGenerating ? (
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <Loader2 className="h-9 w-9 animate-spin text-violet-500" />
        <span className="text-sm">Generating image…</span>
        <StillWorkingNote startedAt={state.imageGenStartedAt} />
        <span className="text-xs">
          Can take minutes on CPU — the Cancel button below stops it
        </span>
      </div>
    ) : selectedItem ? (
      <div className="flex h-full w-full min-h-0 flex-col items-center gap-3 lg:flex-row lg:items-stretch">
        <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center">
          {selectedDescriptor ? (
            <MediaThumb
              item={selectedDescriptor}
              variant="gallery"
              viewingSet={viewingSet}
              className="flex max-h-full max-w-full items-center justify-center rounded-lg border [&_img]:max-h-full [&_img]:w-auto [&_img]:object-contain"
            />
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading image…
            </div>
          )}
        </div>
        {/* Metadata side strip — the full prompt (wrapped + copyable, never a
            dead end), the seed, and the canonical action menu. */}
        <div className="w-full min-w-0 shrink-0 space-y-2 rounded-lg border bg-card/60 p-3 lg:w-60 lg:overflow-y-auto">
          <div className="flex items-center justify-between gap-1">
            <p className="min-w-0 flex-1 text-xs font-semibold">Library image</p>
            {selectedDescriptor && (
              <MediaOverflowMenu item={selectedDescriptor} omit={["open"]} />
            )}
            <button
              type="button"
              onClick={() => setSelectedItemId(null)}
              className="shrink-0 text-muted-foreground hover:text-foreground"
              aria-label="Back to latest result"
              title="Back to latest result"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="flex items-start gap-1.5">
            <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-muted-foreground">
              {selectedItem.prompt || "(no prompt)"}
            </p>
            {selectedItem.prompt && (
              <CopyButton value={selectedItem.prompt} label="Copy prompt" />
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {selectedItem.seed !== null && (
              <SeedChip seed={selectedItem.seed} onReuse={imageCtl.reuseSeed} />
            )}
          </div>
          <div className="space-y-1 text-[10px] text-muted-foreground">
            <p className="truncate" title={selectedItem.model_id}>
              Model: {selectedItem.model_id}
            </p>
            <p className="tabular-nums">
              {selectedItem.width}×{selectedItem.height} ·{" "}
              {selectedItem.elapsed_seconds.toFixed(1)}s
            </p>
            <p>{new Date(selectedItem.created_at).toLocaleString()}</p>
          </div>
          {selectedDescriptor && (
            <div className="flex gap-1.5">
              <Button
                size="sm"
                variant="outline"
                className="h-7 flex-1 text-xs"
                onClick={() => void mediaActions.remix(selectedDescriptor)}
                title="Reload everything that made this image"
              >
                Remix
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 flex-1 text-xs"
                onClick={() => mediaActions.info(selectedDescriptor)}
              >
                Info
              </Button>
            </div>
          )}
          <Button
            size="sm"
            variant="outline"
            className="h-7 w-full text-xs"
            onClick={() => {
              setImageForm({
                prompt: selectedItem.prompt,
                ...(selectedItem.negative_prompt !== null
                  ? { negativePrompt: selectedItem.negative_prompt }
                  : {}),
              });
            }}
          >
            Reuse prompt
          </Button>
        </div>
      </div>
    ) : imageResult ? (
      <div className="w-full max-w-3xl overflow-y-auto">
        <ImageResultPane onOpenLightbox={() => openViewer(null)} />
      </div>
    ) : (
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <ImageIcon className="h-12 w-12 opacity-20" />
        <span className="text-sm">Your generated image will appear here</span>
        <span className="text-xs">
          Write a prompt on the left and press Generate
        </span>
      </div>
    )
  ) : (
    /* ── Video canvas ── */
    <div className="flex h-full w-full min-h-0 flex-col items-center justify-center gap-3">
      {activeJob && <ActiveVideoJobCard className="w-full max-w-2xl" />}
      <div className="w-full max-w-2xl">
        <VideoPlayback ctl={videoCtl} />
      </div>
      {!videoCtl.playbackUrl && !videoCtl.playbackJobId && !activeJob && (
        <div className="flex flex-col items-center gap-3 text-muted-foreground">
          <Film className="h-12 w-12 opacity-20" />
          <span className="text-sm">Your generated video will appear here</span>
          <span className="text-xs">
            Write a prompt on the left and press Generate
          </span>
        </div>
      )}
    </div>
  );

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden">
      {/* ── LEFT: control panel ─────────────────────────────────────────── */}
      <div className="flex w-[380px] shrink-0 flex-col border-r min-h-0">
        <div className="shrink-0 space-y-3 border-b p-3">
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <ModeToggle
                mode={mode}
                onChange={setMode}
                videoBusy={videoJobActive}
              />
            </div>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8 shrink-0"
              onClick={() => setWorkflowsOpen(true)}
              aria-label="Open workflows"
              title="Workflows"
            >
              <Workflow className="h-4 w-4" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8 shrink-0"
              onClick={() => setLibraryOpen(true)}
              aria-label="Open media library"
              title="Media library"
            >
              <Layers className="h-4 w-4" />
            </Button>
          </div>
          {modelIndicator}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {!modeReady ? (
            <div className="rounded-xl border border-dashed px-4 py-8 text-center">
              <p className="text-sm font-medium">
                {isImage ? "Image" : "Video"} generation is not ready
              </p>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                {isImage
                  ? (imageStatusError ??
                    imageStatus?.unavailable_reason ??
                    "Follow the setup steps on the right to install the on-device AI packages.")
                  : (videoStatusError ??
                    videoStatus?.hardware_reason ??
                    videoStatus?.unavailable_reason ??
                    "Follow the setup steps on the right.")}
              </p>
            </div>
          ) : isImage ? (
            !imageCtl.model || !imageCtl.defaults ? (
              <div className="rounded-xl border border-dashed px-4 py-8 flex flex-col items-center gap-3 text-center">
                {imageForm.paramsLoading ? (
                  <>
                    <Loader2 className="h-6 w-6 animate-spin text-violet-500" />
                    <p className="text-xs text-muted-foreground">
                      Loading model parameters…
                    </p>
                  </>
                ) : (
                  <>
                    <ImageIcon className="h-7 w-7 text-muted-foreground/40" />
                    <p className="text-sm font-medium">No model selected</p>
                    <p className="text-xs text-muted-foreground max-w-[240px]">
                      Pick an image model — its full settings will appear here.
                    </p>
                    <Button size="sm" onClick={() => setPickerOpen(true)}>
                      Choose a model
                    </Button>
                  </>
                )}
              </div>
            ) : (
              <ImageGenerateForm ctl={imageCtl} hideActions hideNotices />
            )
          ) : !videoCtl.model || !videoCtl.defaults ? (
            <div className="rounded-xl border border-dashed px-4 py-8 flex flex-col items-center gap-3 text-center">
              {videoForm.paramsLoading ? (
                <>
                  <Loader2 className="h-6 w-6 animate-spin text-violet-500" />
                  <p className="text-xs text-muted-foreground">
                    Loading model parameters…
                  </p>
                </>
              ) : (
                <>
                  <Film className="h-7 w-7 text-muted-foreground/40" />
                  <p className="text-sm font-medium">No model selected</p>
                  <p className="text-xs text-muted-foreground max-w-[240px]">
                    Pick a video model — its full settings will appear here.
                  </p>
                  <Button size="sm" onClick={() => setPickerOpen(true)}>
                    Choose a model
                  </Button>
                </>
              )}
            </div>
          ) : (
            <VideoGenerateForm ctl={videoCtl} hideActions hideNotices />
          )}
        </div>

        {/* Sticky action bar */}
        <div className="shrink-0 space-y-2 border-t bg-background/95 p-3">
          {isImage ? (
            <>
              <ImageParamsErrorNotice ctl={imageCtl} />
              <ImageFormNotices ctl={imageCtl} />
              <ImageGenerateActions
                ctl={imageCtl}
                extraDisabled={!modeReady}
                queueLabel="Queue"
              />
            </>
          ) : (
            <>
              <VideoParamsErrorNotice ctl={videoCtl} />
              <VideoFormNotices ctl={videoCtl} />
              <VideoGenerateActions ctl={videoCtl} extraDisabled={!modeReady} />
            </>
          )}
        </div>
      </div>

      {/* ── RIGHT: canvas + queue rail + filmstrip ──────────────────────── */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4">
          {isImage ? (
            <ImageGenGate>{canvas}</ImageGenGate>
          ) : (
            <VideoGenGate>{canvas}</VideoGenGate>
          )}
        </div>

        {/* Queue rail */}
        {isImage && (
          <div className="shrink-0 border-t px-4 py-2 empty:hidden">
            <ImageQueuePanel layout="chips" heading="Queue" />
          </div>
        )}
        {!isImage && (
          <div className="shrink-0 border-t px-4 py-2 empty:hidden">
            <VideoJobsList ctl={videoCtl} layout="chips" heading="Recent videos" />
          </div>
        )}

        {/* Filmstrip */}
        <div className="shrink-0 border-t px-4 py-2.5">
          <div className="mb-1.5 flex items-center justify-between">
            <p className="text-[11px] font-medium text-muted-foreground">
              Recent generations
            </p>
            <button
              type="button"
              onClick={() => setLibraryOpen(true)}
              className="text-[11px] text-violet-500 hover:underline"
            >
              Open library →
            </button>
          </div>
          {libState.loading && filmstripItems.length === 0 ? (
            <div className="flex h-16 items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Loading library…
            </div>
          ) : filmstripItems.length === 0 ? (
            <div className="flex h-16 items-center text-xs text-muted-foreground">
              {libState.error
                ? `Library unavailable — ${libState.error}`
                : "Nothing generated yet — your images will collect here."}
            </div>
          ) : (
            <div className="flex gap-2 overflow-x-auto pb-1">
              {filmstripItems.map((item) => {
                const active = selectedItemId === item.id;
                return (
                  <div
                    key={item.id}
                    className={`h-16 w-16 shrink-0 overflow-hidden rounded-md border transition-all ${
                      active
                        ? "border-violet-500 ring-2 ring-violet-500/40"
                        : "hover:border-violet-500/50"
                    }`}
                  >
                    {/* Canonical thumb. A click puts it on the canvas (Studio's
                        model), and full size + every action stay one step away
                        via "⋯" or right-click — a filmstrip frame is not a
                        lesser image than a library card. */}
                    <MediaItemThumb
                      item={item}
                      variant="filmstrip"
                      viewingSet={viewingSet}
                      chrome="menu"
                      onActivate={() =>
                        setSelectedItemId(active ? null : item.id)
                      }
                      className="h-full w-full"
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Model picker dialog ─────────────────────────────────────────── */}
      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{isImage ? "Image models" : "Video models"}</DialogTitle>
            <DialogDescription>
              Download, load, and switch the model this studio works with.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
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
        </DialogContent>
      </Dialog>

      {/* ── Workflows dialog ────────────────────────────────────────────── */}
      <Dialog open={workflowsOpen} onOpenChange={setWorkflowsOpen}>
        <DialogContent className="flex h-[85vh] max-w-4xl flex-col overflow-hidden">
          <DialogHeader className="shrink-0">
            <DialogTitle>Workflows</DialogTitle>
            <DialogDescription>
              One-click styled generations from curated presets.
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <WorkflowSection />
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Library dialog ──────────────────────────────────────────────── */}
      <Dialog open={libraryOpen} onOpenChange={setLibraryOpen}>
        <DialogContent className="flex h-[85vh] max-w-5xl flex-col overflow-hidden">
          <DialogHeader className="shrink-0">
            <DialogTitle>Media library</DialogTitle>
            <DialogDescription>
              Everything you have generated on this device.
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <MediaLibrarySection />
          </div>
        </DialogContent>
      </Dialog>

    </div>
  );
}
