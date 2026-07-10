/**
 * VariantStudio — "Studio split-pane" UI bake-off variant.
 *
 * Pro image-gen tool layout (A1111 / Fooocus / Midjourney-web style):
 *  - LEFT: fixed-width scrollable control panel — Image|Video mode toggle,
 *    loaded-model indicator + model-picker dialog, prompt, negative prompt,
 *    common params, collapsible advanced JSON, seed, and a sticky bottom
 *    action bar (Generate / Add to queue).
 *  - RIGHT: large centered canvas showing the current result, a slim queue
 *    rail (active/queued image jobs with progress + cancel; video jobs in
 *    video mode), and a horizontal filmstrip of recent library images along
 *    the bottom. Clicking a filmstrip item shows it on the canvas with its
 *    metadata (prompt / seed / params) in a side strip.
 *  - Workflows and the full Library open in full-height dialogs, reusing
 *    WorkflowSection / MediaLibrarySection wholesale.
 *
 * State doctrine: ALL form values read/write the SAME context-backed form
 * state (imageForm / videoForm via useMediaGenApp actions) so switching
 * layout variants preserves the user's work. Local useState is used ONLY for
 * pure presentation (mode, open dialogs, filmstrip selection, playback pick).
 *
 * React rules obeyed (repo CLAUDE.md → React Patterns): no `actions` in any
 * effect dependency list — only specific stable callbacks; every interval-free
 * effect is narrowly gated on primitives; init fetches live in the hooks.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Download,
  ExternalLink,
  Film,
  Image as ImageIcon,
  ImagePlus,
  Layers,
  ListPlus,
  Loader2,
  Maximize2,
  MonitorX,
  Play,
  RefreshCw,
  Workflow,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { useMediaLibrary } from "@/hooks/use-media-library";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import type {
  ImageGenModelInfo,
  ImageGenJob,
  MediaLibraryItem,
  VideoGenJob,
  VideoGenModelInfo,
  VideoGenRequest,
} from "@/lib/api";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";
import { ImageGenInstaller } from "@/components/media-gen/ImageGenInstaller";
import { MediaLightbox } from "@/components/media-gen/MediaLightbox";
import type { LightboxItem } from "@/components/media-gen/MediaLightbox";
import { WorkflowSection } from "@/components/media-gen/WorkflowSection";
import { MediaLibrarySection } from "@/components/media-gen/MediaLibrarySection";
import {
  AdvancedParamsEditor,
  DimensionPicker,
  ErrorNote,
  GeneratedImageView,
  InlineProgressBar,
  NegativePromptField,
  NumberSliderField,
  ParamsErrorBanner,
  SeedChip,
  SeedInput,
  StarRating,
  computeAdvancedOverrides,
  dimensionError,
  findModelDownload,
  formatGb,
  openExternalUrl,
  parseSeedText,
  randomSeed,
} from "@/components/media-gen/shared";
import type { SizePreset } from "@/components/media-gen/shared";

type StudioMode = "image" | "video";

// ── Mode toggle ───────────────────────────────────────────────────────────────

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

// ── Model picker rows ─────────────────────────────────────────────────────────

function PickerModelRow({
  name,
  provider,
  modelId,
  category,
  sizeGb,
  quality,
  speed,
  isDownloaded,
  isLoaded,
  hardwareOk,
  hardwareReason,
  requiresHfToken,
  modelCardUrl,
  isLoadingThis,
  anyLoadInFlight,
  onLoad,
  onUse,
  onDownload,
}: {
  name: string;
  provider: string;
  modelId: string;
  category: "image_gen" | "video_gen";
  sizeGb: number;
  quality: number;
  speed: number;
  isDownloaded: boolean;
  isLoaded: boolean;
  hardwareOk: boolean;
  hardwareReason: string | null;
  requiresHfToken: boolean;
  modelCardUrl: string;
  isLoadingThis: boolean;
  anyLoadInFlight: boolean;
  onLoad: () => void;
  onUse: () => void;
  onDownload: () => void;
}) {
  const { downloads, openModal } = useDownloadManager();
  const dl = useMemo(
    () => findModelDownload(downloads, category, modelId),
    [downloads, category, modelId],
  );
  const downloading = dl?.status === "active" || dl?.status === "queued";

  return (
    <div
      className={`rounded-lg border px-3 py-2.5 space-y-2 ${
        isLoaded
          ? "border-violet-500/40 bg-violet-500/5"
          : !hardwareOk
            ? "opacity-70"
            : "hover:bg-muted/20"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium truncate flex items-center gap-1.5">
            {name}
            {isLoaded && (
              <span className="rounded bg-green-500/20 text-green-600 dark:text-green-400 px-1.5 py-0.5 text-[10px] font-normal">
                Loaded
              </span>
            )}
          </p>
          <p className="text-[11px] text-muted-foreground truncate">
            {provider} · {formatGb(sizeGb)}
            {requiresHfToken ? " · HF token" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="hidden sm:flex items-center gap-2 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              Q <StarRating value={quality} />
            </span>
            <span className="flex items-center gap-1">
              S <StarRating value={speed} />
            </span>
          </span>
          <button
            type="button"
            onClick={() => void openExternalUrl(modelCardUrl)}
            className="text-muted-foreground hover:text-foreground"
            aria-label={`Open model card for ${name}`}
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {!hardwareOk && (
        <p className="text-[11px] text-muted-foreground flex items-center gap-1.5">
          <AlertCircle className="h-3 w-3 shrink-0 text-amber-500" />
          {hardwareReason ?? "Your hardware cannot run this model."}
        </p>
      )}

      {downloading && dl ? (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span>Downloading weights…</span>
            <button
              type="button"
              onClick={openModal}
              className="text-violet-500 hover:underline"
            >
              {Math.round(dl.percent)}% · details
            </button>
          </div>
          <InlineProgressBar
            percent={dl.percent}
            indeterminate={dl.percent <= 0 && dl.bytes_done <= 0}
          />
        </div>
      ) : !isDownloaded ? (
        <Button
          size="sm"
          variant="outline"
          className="w-full h-7 text-xs"
          disabled={!hardwareOk}
          onClick={onDownload}
        >
          <Download className="h-3 w-3 mr-1.5" />
          Download ({formatGb(sizeGb)})
        </Button>
      ) : isLoaded ? (
        <Button size="sm" className="w-full h-7 text-xs" onClick={onUse}>
          Use this model
          <ChevronRight className="h-3 w-3 ml-1" />
        </Button>
      ) : (
        <Button
          size="sm"
          variant="outline"
          className="w-full h-7 text-xs"
          disabled={anyLoadInFlight || !hardwareOk}
          onClick={onLoad}
        >
          {isLoadingThis ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin mr-1.5" />
              Loading…
            </>
          ) : (
            "Load model"
          )}
        </Button>
      )}
    </div>
  );
}

// ── Queue chips ───────────────────────────────────────────────────────────────

function ImageJobChip({
  job,
  onCancel,
  onReuseSeed,
  onExpand,
}: {
  job: ImageGenJob;
  onCancel: (jobId: string) => void;
  onReuseSeed: (seed: number) => void;
  /** Opens the completed job's image in the lightbox. */
  onExpand?: (job: ImageGenJob) => void;
}) {
  const active = job.status === "queued" || job.status === "running";
  return (
    <div className="flex w-56 shrink-0 flex-col gap-1 rounded-lg border bg-card px-2.5 py-2">
      <div className="flex items-center gap-2">
        {job.status === "completed" ? (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-500" />
        ) : job.status === "failed" ? (
          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
        ) : job.status === "cancelled" ? (
          <X className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-violet-500" />
        )}
        <span className="min-w-0 flex-1 truncate text-[11px]" title={job.prompt}>
          {job.prompt || "(no prompt)"}
        </span>
        <button
          type="button"
          onClick={() => onCancel(job.job_id)}
          className="shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={active ? "Cancel job" : "Remove job"}
          title={active ? "Cancel this job" : "Remove from the queue"}
        >
          <X className="h-3 w-3" />
        </button>
      </div>
      {job.status === "running" ? (
        <InlineProgressBar
          percent={(job.progress ?? 0) * 100}
          indeterminate={(job.progress ?? 0) <= 0}
        />
      ) : (
        <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
          <span className="truncate">{job.status}</span>
          {job.status === "failed" && job.error && (
            <span className="truncate text-destructive" title={job.error}>
              — {job.error}
            </span>
          )}
          {typeof job.seed === "number" && job.status === "completed" && (
            <button
              type="button"
              onClick={() => onReuseSeed(job.seed as number)}
              className="ml-auto shrink-0 text-violet-500 hover:underline"
              title="Reuse this seed"
            >
              seed {job.seed}
            </button>
          )}
          {job.status === "completed" && job.item_id && onExpand && (
            <button
              type="button"
              onClick={() => onExpand(job)}
              className={`shrink-0 text-muted-foreground hover:text-foreground ${
                typeof job.seed === "number" ? "" : "ml-auto"
              }`}
              aria-label="View image"
              title="View this job's image"
            >
              <Maximize2 className="h-3 w-3" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function VideoJobChip({
  job,
  onPlay,
}: {
  job: VideoGenJob;
  onPlay: (jobId: string) => void;
}) {
  const active = job.status === "queued" || job.status === "running";
  return (
    <div className="flex w-64 shrink-0 flex-col gap-1 rounded-lg border bg-card px-2.5 py-2">
      <div className="flex items-center gap-2">
        {job.status === "completed" ? (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-500" />
        ) : job.status === "failed" ? (
          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
        ) : (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-violet-500" />
        )}
        <span className="min-w-0 flex-1 truncate text-[11px]" title={job.prompt}>
          {job.prompt || "(no prompt)"}
        </span>
        {job.status === "completed" && (
          <button
            type="button"
            onClick={() => onPlay(job.job_id)}
            className="shrink-0 text-violet-500 hover:underline text-[11px] inline-flex items-center gap-0.5"
          >
            <Play className="h-3 w-3" />
            Play
          </button>
        )}
      </div>
      {active ? (
        <InlineProgressBar
          percent={job.progress * 100}
          indeterminate={job.status === "queued"}
        />
      ) : (
        <p className="truncate text-[10px] text-muted-foreground">
          {job.status === "failed"
            ? (job.error ?? "failed")
            : `${job.elapsed_seconds.toFixed(0)}s · ${job.model_id || "—"}`}
        </p>
      )}
    </div>
  );
}

// ── Main variant ──────────────────────────────────────────────────────────────

export function VariantStudio() {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading,
    loadingImageModelId,
    imageGenerating,
    imageGenError,
    imageResult,
    selectedImageModelId,
    imageForm,
    imageJobs,
    imageJobsError,
    videoStatus,
    videoModels,
    videoStatusLoading,
    videoStatusError,
    videoModelLoading,
    loadingVideoModelId,
    videoGenerating,
    videoGenError,
    activeJob,
    jobs,
    videoResults,
    videoForm,
  } = state;
  const {
    refreshImage,
    loadImageModel,
    downloadImageModel,
    generateImage,
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
    fetchVideoResult,
    clearActiveJob,
    clearVideoGenError,
    setVideoForm,
    prepareVideoGenerate,
    resetVideoCommon,
    resetVideoAdvanced,
  } = actions;

  // ── Pure-presentation local state ────────────────────────────────────────
  const [mode, setMode] = useState<StudioMode>("image");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [workflowsOpen, setWorkflowsOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [playbackJobId, setPlaybackJobId] = useState<string | null>(null);
  // Lightbox viewing set, snapshotted at open time (URLs are cached blob/data
  // URLs, so a snapshot stays valid; live-mutating the list under the viewer
  // would shift indices mid-browse).
  const [lightbox, setLightbox] = useState<{
    items: LightboxItem[];
    index: number;
    forVideo: boolean;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // ── Library filmstrip (own hook instance, images only, first 20) ─────────
  const [libState, libActions] = useMediaLibrary();
  const { getFileUrl, refresh: refreshLibrary } = libActions;
  const filmstripItems = useMemo(
    () =>
      libState.items.filter((i) => i.media_type === "image").slice(0, 20),
    [libState.items],
  );
  const filmstripIds = useMemo(
    () => filmstripItems.map((i) => i.id).join("\n"),
    [filmstripItems],
  );
  // Resolve auth'd blob URLs for filmstrip thumbnails. getFileUrl caches and
  // dedupes in-flight fetches, so re-running on the id list is cheap.
  useEffect(() => {
    if (!filmstripIds) return;
    for (const id of filmstripIds.split("\n")) void getFileUrl(id);
  }, [filmstripIds, getFileUrl]);

  // A fresh direct generation was persisted → the library has a new item.
  useEffect(() => {
    if (!imageResult?.itemId) return;
    void refreshLibrary();
  }, [imageResult?.itemId, refreshLibrary]);

  // Queue jobs completing also add library items. Gated on the COUNT of
  // completed jobs — a primitive — so this never loops on unrelated renders.
  const completedImageJobCount = imageJobs.filter(
    (j) => j.status === "completed",
  ).length;
  useEffect(() => {
    if (completedImageJobCount === 0) return;
    void refreshLibrary();
  }, [completedImageJobCount, refreshLibrary]);

  // When weight downloads finish, refresh the catalogs so `is_downloaded`
  // flips without a manual reload (same narrow gating as the sections).
  const { downloads } = useDownloadManager();
  const completedImageDownloads = useMemo(
    () =>
      downloads.filter(
        (d) => d.category === "image_gen" && d.status === "completed",
      ).length,
    [downloads],
  );
  useEffect(() => {
    if (completedImageDownloads === 0) return;
    void refreshImage();
  }, [completedImageDownloads, refreshImage]);
  const completedVideoDownloads = useMemo(
    () =>
      downloads.filter(
        (d) => d.category === "video_gen" && d.status === "completed",
      ).length,
    [downloads],
  );
  useEffect(() => {
    if (completedVideoDownloads === 0) return;
    void refreshVideo();
  }, [completedVideoDownloads, refreshVideo]);

  // ── Image model resolution + auto-prepare (mirrors ImageGenSection) ──────
  const imageModelId =
    imageForm.defaults?.modelId ??
    selectedImageModelId ??
    imageStatus?.loaded_model_id ??
    null;
  const imageModel = useMemo(
    () => imageModels.find((m) => m.model_id === imageModelId) ?? null,
    [imageModels, imageModelId],
  );
  useEffect(() => {
    if (imageForm.paramsLoading) return;
    if (!imageModel) return;
    if (imageForm.defaults?.modelId === imageModel.model_id) return;
    void prepareImageGenerate(imageModel);
  }, [
    imageForm.paramsLoading,
    imageForm.defaults?.modelId,
    imageModel,
    prepareImageGenerate,
  ]);

  // ── Video model resolution + auto-prepare (mirrors VideoGenSection) ──────
  const loadedVideoModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoStatus?.loaded_model_id) ??
      null,
    [videoModels, videoStatus?.loaded_model_id],
  );
  const videoModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoForm.defaults?.modelId) ??
      loadedVideoModel,
    [videoModels, videoForm.defaults?.modelId, loadedVideoModel],
  );
  useEffect(() => {
    if (videoForm.paramsLoading) return;
    if (!videoModel) return;
    if (videoForm.defaults?.modelId === videoModel.model_id) return;
    void prepareVideoGenerate(videoModel);
  }, [
    videoForm.paramsLoading,
    videoForm.defaults?.modelId,
    videoModel,
    prepareVideoGenerate,
  ]);

  // ── Model picker handlers ────────────────────────────────────────────────
  const handleLoadImageModel = useCallback(
    async (model: ImageGenModelInfo) => {
      setLocalError(null);
      const result = await loadImageModel(model.model_id);
      if (result.success) {
        await prepareImageGenerate(model);
        setPickerOpen(false);
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet. Use the Download button first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadImageModel, prepareImageGenerate],
  );

  const handleUseImageModel = useCallback(
    (model: ImageGenModelInfo) => {
      if (imageForm.defaults?.modelId !== model.model_id) {
        void prepareImageGenerate(model);
      }
      setPickerOpen(false);
    },
    [imageForm.defaults?.modelId, prepareImageGenerate],
  );

  const handleLoadVideoModel = useCallback(
    async (model: VideoGenModelInfo) => {
      setLocalError(null);
      const result = await loadVideoModel(model.model_id);
      if (result.success) {
        await prepareVideoGenerate(model);
        setPickerOpen(false);
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet. Use the Download button first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadVideoModel, prepareVideoGenerate],
  );

  const handleUseVideoModel = useCallback(
    (model: VideoGenModelInfo) => {
      if (videoForm.defaults?.modelId !== model.model_id) {
        void prepareVideoGenerate(model);
      }
      setPickerOpen(false);
    },
    [videoForm.defaults?.modelId, prepareVideoGenerate],
  );

  // ── Image request building (identical semantics to ImageGenSection) ──────
  const imageDefaults = imageForm.defaults;
  const imageAdvanced = useMemo(
    () =>
      computeAdvancedOverrides(
        imageForm.advancedText,
        imageDefaults?.advanced ?? {},
      ),
    [imageForm.advancedText, imageDefaults?.advanced],
  );
  const imageDimError = dimensionError(imageForm.width, imageForm.height);
  const imageFormInvalid =
    !imageForm.prompt.trim() ||
    !imageDefaults ||
    !imageAdvanced.ok ||
    imageDimError !== null;

  const buildImageInput = useCallback((): ImageGenerateInput | null => {
    const d = imageForm.defaults;
    if (!d) return null;
    const adv = computeAdvancedOverrides(imageForm.advancedText, d.advanced);
    if (!adv.ok) return null;
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

  const handleGenerateImage = useCallback(async () => {
    const input = buildImageInput();
    if (!input) return;
    // Show the incoming result on the canvas, not a stale filmstrip pick.
    setSelectedItemId(null);
    await generateImage(input);
  }, [buildImageInput, generateImage]);

  const handleEnqueueImage = useCallback(async () => {
    const input = buildImageInput();
    if (!input) return;
    await enqueueImageJob(input);
  }, [buildImageInput, enqueueImageJob]);

  const reuseImageSeed = useCallback(
    (seed: number) => setImageForm({ seedText: String(seed) }),
    [setImageForm],
  );

  // ── Lightbox plumbing ────────────────────────────────────────────────────
  const libraryItemToLightbox = useCallback(
    (item: MediaLibraryItem, url: string): LightboxItem => ({
      id: item.id,
      kind: item.media_type,
      url,
      prompt: item.prompt,
      seed: item.seed,
      meta: {
        model_id: item.model_id,
        width: item.width,
        height: item.height,
        elapsed_seconds: item.elapsed_seconds,
        created_at: item.created_at,
        ...(item.negative_prompt
          ? { negative_prompt: item.negative_prompt }
          : {}),
        ...(Object.keys(item.params ?? {}).length > 0
          ? { params: item.params }
          : {}),
      },
    }),
    [],
  );

  /**
   * The full image viewing set: the fresh result (when shown) followed by the
   * filmstrip's recent generations, so prev/next flows through everything.
   */
  const buildImageLightboxItems = useCallback((): LightboxItem[] => {
    const arr: LightboxItem[] = [];
    if (imageResult) {
      arr.push({
        id: imageResult.itemId ?? "fresh-result",
        kind: "image",
        url: `data:image/png;base64,${imageResult.b64}`,
        prompt: imageForm.prompt.trim() || undefined,
        seed: imageResult.seed,
        meta: {
          width: imageResult.width,
          height: imageResult.height,
          elapsed_seconds: imageResult.elapsed,
          ...(imageResult.filePath ? { file_path: imageResult.filePath } : {}),
        },
        title: "Latest result",
      });
    }
    for (const item of filmstripItems) {
      // The fresh result may already be persisted as a library item — don't
      // show it twice in the browse order.
      if (imageResult?.itemId && item.id === imageResult.itemId) continue;
      const url = libState.fileUrls[item.id];
      if (!url) continue;
      arr.push(libraryItemToLightbox(item, url));
    }
    return arr;
  }, [
    imageResult,
    imageForm.prompt,
    filmstripItems,
    libState.fileUrls,
    libraryItemToLightbox,
  ]);

  /** Open the image lightbox at the given item id (null → first item). */
  const openImageLightboxAt = useCallback(
    (id: string | null) => {
      const arr = buildImageLightboxItems();
      if (arr.length === 0) return;
      const idx = id ? arr.findIndex((x) => x.id === id) : 0;
      setLightbox({ items: arr, index: idx >= 0 ? idx : 0, forVideo: false });
    },
    [buildImageLightboxItems],
  );

  /** Open a completed queue job's image (fetches its file URL if needed). */
  const openJobLightbox = useCallback(
    async (job: ImageGenJob) => {
      const itemId = job.item_id;
      if (!itemId) return;
      const url = libState.fileUrls[itemId] ?? (await getFileUrl(itemId));
      if (!url) return;
      const arr = buildImageLightboxItems();
      let idx = arr.findIndex((x) => x.id === itemId);
      if (idx < 0) {
        // Not in the filmstrip snapshot yet (URL just resolved) — lead with it.
        arr.unshift({
          id: itemId,
          kind: "image",
          url,
          prompt: job.prompt,
          seed: job.seed ?? null,
          meta: {
            model_id: job.model_id,
            ...(Object.keys(job.params ?? {}).length > 0
              ? { params: job.params }
              : {}),
          },
        });
        idx = 0;
      }
      setLightbox({ items: arr, index: idx, forVideo: false });
    },
    [libState.fileUrls, getFileUrl, buildImageLightboxItems],
  );

  const closeLightbox = useCallback(() => setLightbox(null), []);

  // ── Video request building (identical semantics to VideoGenSection) ──────
  const videoDefaults = videoForm.defaults;
  const videoAdvanced = useMemo(
    () =>
      computeAdvancedOverrides(
        videoForm.advancedText,
        videoDefaults?.advanced ?? {},
      ),
    [videoForm.advancedText, videoDefaults?.advanced],
  );
  const videoDimError = dimensionError(videoForm.width, videoForm.height);
  const videoFormInvalid =
    !videoForm.prompt.trim() ||
    !videoDefaults ||
    !videoAdvanced.ok ||
    videoDimError !== null;
  const videoJobActive =
    activeJob?.status === "queued" || activeJob?.status === "running";

  const handleGenerateVideo = useCallback(async () => {
    const d = videoForm.defaults;
    if (!d || !videoForm.prompt.trim()) return;
    const adv = computeAdvancedOverrides(videoForm.advancedText, d.advanced);
    if (!adv.ok) return;
    setLocalError(null);
    const seed = parseSeedText(videoForm.seedText) ?? randomSeed();
    const req: VideoGenRequest = {
      prompt: videoForm.prompt.trim(),
      negative_prompt: d.supportsNegativePrompt
        ? videoForm.negativePrompt.trim() || undefined
        : undefined,
      model_id: d.modelId,
      width: videoForm.width,
      height: videoForm.height,
      num_frames: videoForm.numFrames,
      fps: videoForm.fps,
      steps: videoForm.steps,
      guidance: videoForm.guidance,
      seed,
      image_base64: videoForm.sourceImage?.base64,
      extra_params: adv.count > 0 ? adv.overrides : undefined,
    };
    const result = await generateVideo(req);
    if (result.ok) setPlaybackJobId(null);
  }, [videoForm, generateVideo]);

  const handlePickSourceImage = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
      if (file.size > MAX_IMAGE_BYTES) {
        setLocalError(
          `That image is ${(file.size / (1024 * 1024)).toFixed(1)} MB — please choose one under 20 MB.`,
        );
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = typeof reader.result === "string" ? reader.result : "";
        const comma = dataUrl.indexOf(",");
        if (comma < 0) {
          setLocalError("Could not read the selected image.");
          return;
        }
        setVideoForm({
          sourceImage: {
            name: file.name,
            base64: dataUrl.slice(comma + 1),
            previewUrl: dataUrl,
          },
        });
      };
      reader.onerror = () =>
        setLocalError("Could not read the selected image.");
      reader.readAsDataURL(file);
    },
    [setVideoForm],
  );

  const handlePlayVideo = useCallback(
    (jobId: string) => {
      setPlaybackJobId(jobId);
      void fetchVideoResult(jobId);
    },
    [fetchVideoResult],
  );

  // ── Derived presentation values ──────────────────────────────────────────
  const isImage = mode === "image";
  const genError = (isImage ? imageGenError : videoGenError) ?? localError;
  const dismissGenError = useCallback(() => {
    setLocalError(null);
    clearImageGenError();
    clearVideoGenError();
  }, [clearImageGenError, clearVideoGenError]);

  const imageSizePresets = useMemo<SizePreset[]>(() => {
    const base: SizePreset[] = imageDefaults
      ? [
          {
            label: `Default ${imageDefaults.width}×${imageDefaults.height}`,
            width: imageDefaults.width,
            height: imageDefaults.height,
          },
        ]
      : [];
    const fixed: SizePreset[] = [
      { label: "512", width: 512, height: 512 },
      { label: "768", width: 768, height: 768 },
      { label: "1024", width: 1024, height: 1024 },
      { label: "Portrait 832×1216", width: 832, height: 1216 },
      { label: "Landscape 1216×832", width: 1216, height: 832 },
    ];
    return [
      ...base,
      ...fixed.filter(
        (p) =>
          !imageDefaults ||
          p.width !== imageDefaults.width ||
          p.height !== imageDefaults.height,
      ),
    ];
  }, [imageDefaults]);

  const videoSizePresets = useMemo<SizePreset[]>(() => {
    const base: SizePreset[] = videoDefaults
      ? [
          {
            label: `Default ${videoDefaults.width}×${videoDefaults.height}`,
            width: videoDefaults.width,
            height: videoDefaults.height,
          },
        ]
      : [];
    const fixed: SizePreset[] = [
      { label: "Landscape 832×480", width: 832, height: 480 },
      { label: "Portrait 480×832", width: 480, height: 832 },
      { label: "Wide 1280×704", width: 1280, height: 704 },
    ];
    return [
      ...base,
      ...fixed.filter(
        (p) =>
          !videoDefaults ||
          p.width !== videoDefaults.width ||
          p.height !== videoDefaults.height,
      ),
    ];
  }, [videoDefaults]);

  const maxFrames =
    videoModel && videoModel.max_num_frames > 0
      ? videoModel.max_num_frames
      : 200;

  const selectedItem: MediaLibraryItem | null = useMemo(
    () =>
      selectedItemId
        ? (libState.items.find((i) => i.id === selectedItemId) ?? null)
        : null,
    [selectedItemId, libState.items],
  );

  const playbackUrl = playbackJobId
    ? (videoResults[playbackJobId] ?? null)
    : activeJob?.status === "completed"
      ? (videoResults[activeJob.job_id] ?? null)
      : null;

  const reuseVideoSeed = useCallback(
    (seed: number) => setVideoForm({ seedText: String(seed) }),
    [setVideoForm],
  );

  /** Open the currently playing video in the lightbox. */
  const openVideoLightbox = useCallback(() => {
    if (!playbackUrl) return;
    const jobId =
      playbackJobId ??
      (activeJob?.status === "completed" ? activeJob.job_id : null);
    const job =
      jobs.find((j) => j.job_id === jobId) ??
      (activeJob && activeJob.job_id === jobId ? activeJob : null);
    setLightbox({
      items: [
        {
          id: jobId ?? "video-playback",
          kind: "video",
          url: playbackUrl,
          prompt: job?.prompt || videoForm.prompt.trim() || undefined,
          meta: job
            ? {
                model_id: job.model_id,
                elapsed_seconds: job.elapsed_seconds,
                status: job.status,
              }
            : undefined,
          title: "Generated video",
        },
      ],
      index: 0,
      forVideo: true,
    });
  }, [playbackUrl, playbackJobId, activeJob, jobs, videoForm.prompt]);

  // Readiness gates per mode.
  const imageReady = !!imageStatus?.available;
  const videoReady =
    !!videoStatus?.hardware_supported && !!videoStatus?.packages_installed;
  const modeReady = isImage ? imageReady : videoReady;
  const activeImageQueue = imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  ).length;

  // ── Left-panel form (per mode) ───────────────────────────────────────────
  const modelIndicator = (() => {
    const name = isImage ? imageModel?.name : videoModel?.name;
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

  const imageFormBody =
    !imageModel || !imageDefaults ? (
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
      <div className="space-y-4">
        {imageForm.paramsError && (
          <ParamsErrorBanner
            error={imageForm.paramsError}
            onRetry={() => void prepareImageGenerate(imageModel)}
          />
        )}

        <div className="space-y-1.5">
          <Label className="text-xs">Prompt</Label>
          <Textarea
            value={imageForm.prompt}
            onChange={(e) => setImageForm({ prompt: e.target.value })}
            placeholder="Describe the image you want to generate…"
            className="text-sm min-h-[96px] resize-none"
          />
        </div>

        <NegativePromptField
          supported={imageDefaults.supportsNegativePrompt}
          value={imageForm.negativePrompt}
          onChange={(v) => setImageForm({ negativePrompt: v })}
        />

        <NumberSliderField
          label="Steps"
          value={imageForm.steps}
          onChange={(v) => setImageForm({ steps: v })}
          min={1}
          max={imageModel.pipeline_type.startsWith("flux") ? 50 : 100}
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

        <DimensionPicker
          width={imageForm.width}
          height={imageForm.height}
          onChange={(w, h) => setImageForm({ width: w, height: h })}
          presets={imageSizePresets}
        />

        <div className="space-y-1.5">
          <Label className="text-xs">
            Seed <span className="text-muted-foreground">(blank = random)</span>
          </Label>
          <SeedInput
            value={imageForm.seedText}
            onChange={(seedText) => setImageForm({ seedText })}
          />
        </div>

        <AdvancedParamsEditor
          defaults={imageDefaults.advanced}
          text={imageForm.advancedText}
          onChange={(advancedText) => setImageForm({ advancedText })}
          onReset={resetImageAdvanced}
        />

        <div className="flex justify-end">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={resetImageCommon}
          >
            Reset to model defaults
          </Button>
        </div>
      </div>
    );

  const videoFormBody =
    !videoModel || !videoDefaults ? (
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
      <div className="space-y-4">
        {videoForm.paramsError && (
          <ParamsErrorBanner
            error={videoForm.paramsError}
            onRetry={() => void prepareVideoGenerate(videoModel)}
          />
        )}

        <div className="space-y-1.5">
          <Label className="text-xs">Prompt</Label>
          <Textarea
            value={videoForm.prompt}
            onChange={(e) => setVideoForm({ prompt: e.target.value })}
            placeholder="Describe the video you want to generate…"
            className="text-sm min-h-[96px] resize-none"
          />
        </div>

        <NegativePromptField
          supported={videoDefaults.supportsNegativePrompt}
          value={videoForm.negativePrompt}
          onChange={(v) => setVideoForm({ negativePrompt: v })}
        />

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
          min={1}
          max={maxFrames}
          step={1}
          defaultValue={videoDefaults.numFrames}
        />
        <NumberSliderField
          label="FPS"
          value={videoForm.fps}
          onChange={(v) => setVideoForm({ fps: v })}
          min={1}
          max={60}
          step={1}
          defaultValue={videoDefaults.fps}
        />
        <p className="text-[11px] text-muted-foreground tabular-nums">
          ≈{" "}
          {(videoForm.fps > 0 ? videoForm.numFrames / videoForm.fps : 0).toFixed(
            1,
          )}
          s of video ({videoForm.numFrames} frames at {videoForm.fps} fps)
        </p>

        <DimensionPicker
          width={videoForm.width}
          height={videoForm.height}
          onChange={(w, h) => setVideoForm({ width: w, height: h })}
          presets={videoSizePresets}
        />

        <div className="space-y-1.5">
          <Label className="text-xs">
            Seed <span className="text-muted-foreground">(blank = random)</span>
          </Label>
          <SeedInput
            value={videoForm.seedText}
            onChange={(seedText) => setVideoForm({ seedText })}
          />
        </div>

        {videoModel.supports_image_to_video && (
          <div className="space-y-1.5">
            <Label className="text-xs">
              Source image{" "}
              <span className="text-muted-foreground">
                (optional — animates the image)
              </span>
            </Label>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              onChange={handlePickSourceImage}
            />
            {videoForm.sourceImage ? (
              <div className="flex items-center gap-3 rounded-lg border px-3 py-2">
                <img
                  src={videoForm.sourceImage.previewUrl}
                  alt="Source"
                  className="h-10 w-10 rounded border object-cover"
                />
                <span className="min-w-0 flex-1 truncate text-xs">
                  {videoForm.sourceImage.name}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setVideoForm({ sourceImage: null })}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            ) : (
              <Button
                size="sm"
                variant="outline"
                className="w-full"
                onClick={() => fileInputRef.current?.click()}
              >
                <ImagePlus className="h-3.5 w-3.5 mr-1.5" />
                Choose image
              </Button>
            )}
          </div>
        )}

        <AdvancedParamsEditor
          defaults={videoDefaults.advanced}
          text={videoForm.advancedText}
          onChange={(advancedText) => setVideoForm({ advancedText })}
          onReset={resetVideoAdvanced}
        />

        <div className="flex justify-end">
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={resetVideoCommon}
          >
            Reset to model defaults
          </Button>
        </div>
      </div>
    );

  // ── Right-pane gate content (loading / error / installer) ────────────────
  const gateContent = (() => {
    if (isImage) {
      if (imageStatusLoading && !imageStatus) {
        return (
          <div className="flex items-center gap-3 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-sm">Checking image generation status…</span>
          </div>
        );
      }
      if (imageStatusError) {
        return (
          <div className="w-full max-w-md rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-4 w-4 shrink-0 text-destructive mt-0.5" />
              <div className="min-w-0 space-y-1.5 text-sm">
                <p className="font-medium">Could not load image generation</p>
                <p className="break-all text-xs text-muted-foreground">
                  {imageStatusError}
                </p>
              </div>
            </div>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void refreshImage()}
            >
              <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
              Try again
            </Button>
          </div>
        );
      }
      if (imageStatus && !imageStatus.available) {
        return (
          <div className="h-full w-full overflow-y-auto p-6">
            <ImageGenInstaller
              models={imageModels}
              onInstallComplete={() => void refreshImage()}
            />
          </div>
        );
      }
      return null;
    }
    if (videoStatusLoading && !videoStatus) {
      return (
        <div className="flex items-center gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="text-sm">Checking video generation status…</span>
        </div>
      );
    }
    if (videoStatusError) {
      return (
        <div className="w-full max-w-md rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-4 w-4 shrink-0 text-destructive mt-0.5" />
            <div className="min-w-0 space-y-1.5 text-sm">
              <p className="font-medium">Could not load video generation</p>
              <p className="break-all text-xs text-muted-foreground">
                {videoStatusError}
              </p>
            </div>
          </div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void refreshVideo()}
          >
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            Try again
          </Button>
        </div>
      );
    }
    if (videoStatus && !videoStatus.hardware_supported) {
      return (
        <div className="flex max-w-md flex-col items-center gap-3 text-center">
          <div className="rounded-lg bg-muted p-3">
            <MonitorX className="h-6 w-6 text-muted-foreground" />
          </div>
          <p className="text-sm font-semibold">
            Video generation is not available on this computer
          </p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {videoStatus.hardware_reason ??
              videoStatus.unavailable_reason ??
              "Video generation requires Apple Silicon with 16GB+ memory or an NVIDIA GPU with 8GB+ VRAM."}
          </p>
          <p className="text-[11px] text-muted-foreground">
            Image generation may still work — switch to Image mode.
          </p>
        </div>
      );
    }
    if (videoStatus && !videoStatus.packages_installed) {
      return (
        <div className="h-full w-full overflow-y-auto p-6">
          <ImageGenInstaller
            models={videoModels}
            headline="Set up Video Generation"
            intro="AI Matrx can generate short videos directly on your computer. Video uses the same on-device AI packages as image generation — click Install now for the one-time setup, then download a video model."
            onInstallComplete={() => void refreshVideo()}
          />
        </div>
      );
    }
    return null;
  })();

  // ── Canvas content ───────────────────────────────────────────────────────
  const selectedItemUrl = selectedItem
    ? (libState.fileUrls[selectedItem.id] ?? null)
    : null;

  const canvas = isImage ? (
    imageGenerating ? (
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <Loader2 className="h-9 w-9 animate-spin text-violet-500" />
        <span className="text-sm">Generating image…</span>
        <span className="text-xs">
          This may take 5–60 seconds depending on your hardware
        </span>
      </div>
    ) : selectedItem ? (
      <div className="flex h-full w-full min-h-0 flex-col items-center gap-3 lg:flex-row lg:items-stretch">
        <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center">
          {selectedItemUrl ? (
            <button
              type="button"
              onClick={() => openImageLightboxAt(selectedItem.id)}
              className="group relative flex max-h-full max-w-full cursor-zoom-in items-center justify-center"
              aria-label="Expand image"
              title="Click to expand"
            >
              <img
                src={selectedItemUrl}
                alt={selectedItem.prompt || "Library image"}
                className="max-h-full max-w-full rounded-lg border object-contain"
              />
              <span className="pointer-events-none absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/55 px-1.5 py-1 text-[10px] text-white opacity-0 transition-opacity group-hover:opacity-100">
                <Maximize2 className="h-3 w-3" />
                Expand
              </span>
            </button>
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading image…
            </div>
          )}
        </div>
        {/* Metadata side strip */}
        <div className="w-full shrink-0 space-y-2 rounded-lg border bg-card/60 p-3 lg:w-60 lg:overflow-y-auto">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-semibold">Library image</p>
            <button
              type="button"
              onClick={() => setSelectedItemId(null)}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Back to latest result"
              title="Back to latest result"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <p
            className="text-[11px] leading-relaxed text-muted-foreground break-words"
            title={selectedItem.prompt}
          >
            {selectedItem.prompt || "(no prompt)"}
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            {selectedItem.seed !== null && (
              <SeedChip seed={selectedItem.seed} onReuse={reuseImageSeed} />
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
          {Object.keys(selectedItem.params ?? {}).length > 0 && (
            <pre className="max-h-32 overflow-auto rounded border bg-muted/30 p-2 font-mono text-[10px] leading-snug">
              {JSON.stringify(selectedItem.params, null, 2)}
            </pre>
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
        <GeneratedImageView
          result={imageResult}
          onClear={clearImageResult}
          onReuseSeed={reuseImageSeed}
          prompt={imageForm.prompt.trim() || undefined}
          onOpenLightbox={() => openImageLightboxAt(null)}
        />
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
      {activeJob && (
        <div
          className={`w-full max-w-2xl rounded-lg border px-4 py-3 space-y-2 ${
            activeJob.status === "failed"
              ? "border-destructive/30 bg-destructive/5"
              : activeJob.status === "completed"
                ? "border-green-500/30 bg-green-500/5"
                : videoJobActive
                  ? "border-violet-500/30 bg-violet-500/5"
                  : "border-border bg-muted/20"
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              {videoJobActive ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-violet-500" />
              ) : activeJob.status === "completed" ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
              ) : (
                <AlertCircle className="h-4 w-4 shrink-0 text-destructive" />
              )}
              <span className="truncate" title={activeJob.prompt}>
                {videoJobActive
                  ? "Generating video…"
                  : activeJob.status === "completed"
                    ? "Video ready"
                    : "Video generation failed"}
                {activeJob.prompt ? ` — ${activeJob.prompt}` : ""}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              {videoJobActive && activeJob.total_steps > 0 && (
                <span className="tabular-nums">
                  step {activeJob.current_step}/{activeJob.total_steps}
                </span>
              )}
              {videoJobActive && (
                <span className="flex items-center gap-1 tabular-nums">
                  <Clock className="h-3 w-3" />
                  {Math.round(activeJob.elapsed_seconds)}s
                </span>
              )}
              {!videoJobActive && (
                <button
                  type="button"
                  onClick={clearActiveJob}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label="Dismiss job"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
          {videoJobActive && (
            <InlineProgressBar
              percent={activeJob.progress * 100}
              indeterminate={activeJob.status === "queued"}
            />
          )}
          {activeJob.status === "failed" && activeJob.error && (
            <p className="break-all text-xs text-destructive">
              {activeJob.error}
            </p>
          )}
        </div>
      )}

      {playbackUrl ? (
        <div className="flex min-h-0 w-full max-w-2xl flex-1 flex-col gap-2">
          <div className="relative flex min-h-0 w-full flex-1">
            <video
              key={playbackUrl}
              controls
              autoPlay
              loop
              src={playbackUrl}
              className="min-h-0 w-full flex-1 rounded-lg border bg-black object-contain"
            />
            <button
              type="button"
              onClick={openVideoLightbox}
              className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/55 px-1.5 py-1 text-[10px] text-white transition-colors hover:bg-black/75"
              aria-label="Open video in the viewer"
              title="Open in the full-screen viewer"
            >
              <Maximize2 className="h-3 w-3" />
              Expand
            </button>
          </div>
          <div className="flex justify-end">
            <a
              href={playbackUrl}
              download={`matrx-video-${Date.now()}.mp4`}
              className="inline-flex items-center text-xs text-violet-500 hover:underline"
            >
              <Download className="h-3.5 w-3.5 mr-1" />
              Save MP4
            </a>
          </div>
        </div>
      ) : playbackJobId ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Fetching video…
        </div>
      ) : !activeJob ? (
        <div className="flex flex-col items-center gap-3 text-muted-foreground">
          <Film className="h-12 w-12 opacity-20" />
          <span className="text-sm">Your generated video will appear here</span>
          <span className="text-xs">
            Write a prompt on the left and press Generate
          </span>
        </div>
      ) : null}
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
            imageFormBody
          ) : (
            videoFormBody
          )}
        </div>

        {/* Sticky action bar */}
        <div className="shrink-0 space-y-2 border-t bg-background/95 p-3">
          {genError && (
            <ErrorNote message={genError} onDismiss={dismissGenError} />
          )}
          {isImage ? (
            <div className="flex gap-2">
              <Button
                className="flex-1"
                disabled={!modeReady || imageGenerating || imageFormInvalid}
                onClick={() => void handleGenerateImage()}
              >
                {imageGenerating ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    Generating…
                  </>
                ) : (
                  <>
                    <ImageIcon className="h-4 w-4 mr-2" />
                    Generate
                  </>
                )}
              </Button>
              <Button
                variant="outline"
                disabled={!modeReady || imageFormInvalid}
                onClick={() => void handleEnqueueImage()}
                title="Queue this generation and keep editing — write the next prompt right away"
              >
                <ListPlus className="h-4 w-4 mr-2" />
                Queue
              </Button>
            </div>
          ) : (
            <>
              {videoJobActive && (
                <p className="text-[11px] text-muted-foreground">
                  One video at a time — the current job must finish before
                  starting another.
                </p>
              )}
              <Button
                className="w-full"
                disabled={
                  !modeReady ||
                  videoGenerating ||
                  videoJobActive ||
                  videoFormInvalid
                }
                onClick={() => void handleGenerateVideo()}
              >
                {videoGenerating || videoJobActive ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    {videoJobActive ? "Generating…" : "Starting…"}
                  </>
                ) : (
                  <>
                    <Film className="h-4 w-4 mr-2" />
                    Generate Video
                  </>
                )}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* ── RIGHT: canvas + queue rail + filmstrip ──────────────────────── */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4">
          {gateContent ?? canvas}
        </div>

        {/* Queue rail */}
        {isImage && (imageJobs.length > 0 || imageJobsError) && (
          <div className="shrink-0 border-t px-4 py-2">
            <div className="mb-1.5 flex items-center gap-2">
              <p className="text-[11px] font-medium text-muted-foreground">
                Queue
              </p>
              {activeImageQueue > 0 && (
                <Badge className="h-4 border-violet-500/30 bg-violet-500/15 px-1.5 text-[10px] text-violet-600 dark:text-violet-400">
                  {activeImageQueue} active
                </Badge>
              )}
            </div>
            {imageJobsError && <ErrorNote message={imageJobsError} />}
            <div className="flex gap-2 overflow-x-auto pb-1">
              {imageJobs.map((j) => (
                <ImageJobChip
                  key={j.job_id}
                  job={j}
                  onCancel={(id) => void cancelImageJob(id)}
                  onReuseSeed={reuseImageSeed}
                  onExpand={(job) => void openJobLightbox(job)}
                />
              ))}
            </div>
          </div>
        )}
        {!isImage && jobs.length > 0 && (
          <div className="shrink-0 border-t px-4 py-2">
            <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">
              Recent videos
            </p>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {jobs.map((j) => (
                <VideoJobChip key={j.job_id} job={j} onPlay={handlePlayVideo} />
              ))}
            </div>
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
                const url = libState.fileUrls[item.id] ?? null;
                const active = selectedItemId === item.id;
                return (
                  <div
                    key={item.id}
                    className="group relative h-16 w-16 shrink-0"
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedItemId(active ? null : item.id);
                        void getFileUrl(item.id);
                      }}
                      className={`h-16 w-16 overflow-hidden rounded-md border transition-all ${
                        active
                          ? "border-violet-500 ring-2 ring-violet-500/40"
                          : "hover:border-violet-500/50"
                      }`}
                      title={item.prompt || "(no prompt)"}
                      aria-label={`Show ${item.prompt || "generated image"} on the canvas`}
                    >
                      {url ? (
                        <img
                          src={url}
                          alt={item.prompt || "Generated image"}
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <span className="flex h-full w-full items-center justify-center bg-muted/40">
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                        </span>
                      )}
                    </button>
                    {url && (
                      <button
                        type="button"
                        onClick={() => openImageLightboxAt(item.id)}
                        className="absolute right-0.5 top-0.5 rounded bg-black/60 p-0.5 text-white opacity-0 transition-opacity hover:bg-black/80 focus-visible:opacity-100 group-hover:opacity-100"
                        aria-label={`Open ${item.prompt || "generated image"} in the viewer`}
                        title="Open in the full-screen viewer"
                      >
                        <Maximize2 className="h-3 w-3" />
                      </button>
                    )}
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
            <DialogTitle>
              {isImage ? "Image models" : "Video models"}
            </DialogTitle>
            <DialogDescription>
              Download, load, and switch the model this studio works with.
            </DialogDescription>
          </DialogHeader>
          {localError && (
            <ErrorNote
              message={localError}
              onDismiss={() => setLocalError(null)}
            />
          )}
          <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
            {isImage
              ? imageModels.map((m) => (
                  <PickerModelRow
                    key={m.model_id}
                    name={m.name}
                    provider={m.provider}
                    modelId={m.model_id}
                    category="image_gen"
                    sizeGb={m.download_size_gb}
                    quality={m.quality_rating}
                    speed={m.speed_rating}
                    isDownloaded={m.is_downloaded}
                    isLoaded={imageStatus?.loaded_model_id === m.model_id}
                    hardwareOk={m.hardware_ok}
                    hardwareReason={m.hardware_reason}
                    requiresHfToken={m.requires_hf_token}
                    modelCardUrl={m.model_card_url}
                    isLoadingThis={loadingImageModelId === m.model_id}
                    anyLoadInFlight={
                      imageModelLoading || !!imageStatus?.is_loading
                    }
                    onLoad={() => void handleLoadImageModel(m)}
                    onUse={() => handleUseImageModel(m)}
                    onDownload={() => void downloadImageModel(m.model_id)}
                  />
                ))
              : videoModels.map((m) => (
                  <PickerModelRow
                    key={m.model_id}
                    name={m.name}
                    provider={m.provider}
                    modelId={m.model_id}
                    category="video_gen"
                    sizeGb={m.download_size_gb}
                    quality={m.quality_rating}
                    speed={m.speed_rating}
                    isDownloaded={m.is_downloaded}
                    isLoaded={videoStatus?.loaded_model_id === m.model_id}
                    hardwareOk={m.hardware_ok}
                    hardwareReason={m.hardware_reason}
                    requiresHfToken={m.requires_hf_token}
                    modelCardUrl={m.model_card_url}
                    isLoadingThis={loadingVideoModelId === m.model_id}
                    anyLoadInFlight={
                      videoModelLoading || !!videoStatus?.is_loading
                    }
                    onLoad={() => void handleLoadVideoModel(m)}
                    onUse={() => handleUseVideoModel(m)}
                    onDownload={() => void downloadVideoModel(m.model_id)}
                  />
                ))}
            {(isImage ? imageModels : videoModels).length === 0 && (
              <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                No models available yet.
              </p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Workflows dialog (reuses WorkflowSection wholesale) ─────────── */}
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

      {/* ── Library dialog (reuses MediaLibrarySection wholesale) ───────── */}
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

      {/* ── Full-power media viewer ─────────────────────────────────────── */}
      <MediaLightbox
        open={lightbox !== null}
        items={lightbox?.items ?? []}
        startIndex={lightbox?.index ?? 0}
        onClose={closeLightbox}
        onReuseSeed={lightbox?.forVideo ? reuseVideoSeed : reuseImageSeed}
      />
    </div>
  );
}
