/**
 * VariantWorkspace — UI bake-off variant: "Workspace nav".
 *
 * A mini-app with its own left icon+label navigation rail (collapsible to
 * icons-only): Generate Image, Generate Video, Workflows, Library, Models.
 * The Generate views are PURE generate forms (no Generate|Models sub-tabs);
 * Models is its own first-class view listing image AND video model cards
 * with download / load / generate actions. Workflows and Library reuse the
 * existing sections wholesale.
 *
 * A persistent slim queue footer is visible across ALL entries showing
 * in-flight work — queued/running image jobs (progress + cancel) and the
 * active video job — and clicking a segment jumps to the relevant view. The
 * bar hides entirely when nothing is active or recently finished.
 *
 * State doctrine: ALL form values read/write the SAME context-backed form
 * state (imageForm / videoForm via useMediaGenApp), so switching layout
 * variants never loses the user's work. Local useState is presentation-only
 * (active nav entry, rail collapsed, transient local errors / playback pick).
 *
 * React rules obeyed (repo CLAUDE.md → React Patterns): no `actions` in any
 * effect dependency list, effects gated on the specific values watched, no
 * init fetches here (they live in use-media-gen), no focus re-initialization.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Boxes,
  CheckCircle2,
  ChevronsLeft,
  ChevronsRight,
  Clock,
  Download,
  ExternalLink,
  Film,
  Image as ImageIcon,
  ImagePlus,
  Library,
  ListPlus,
  Loader2,
  MonitorX,
  Play,
  RefreshCw,
  Wand2,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import type {
  ImageGenModelInfo,
  VideoGenModelInfo,
  ImageGenJob,
  VideoGenRequest,
} from "@/lib/api";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";
import { ImageGenInstaller } from "../ImageGenInstaller";
import { WorkflowSection } from "../WorkflowSection";
import { MediaLibrarySection } from "../MediaLibrarySection";
import {
  StarRating,
  ErrorNote,
  InlineProgressBar,
  findModelDownload,
  formatGb,
  openExternalUrl,
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
  CancelableGenerateButton,
  StillWorkingNote,
  computeAdvancedOverrides,
  parseSeedText,
  randomSeed,
} from "../shared";
import type { SizePreset } from "../shared";

// ── Navigation model ─────────────────────────────────────────────────────────

type NavId = "image" | "video" | "workflows" | "library" | "models";

const NAV_ITEMS: { id: NavId; label: string; Icon: LucideIcon }[] = [
  { id: "image", label: "Generate Image", Icon: ImageIcon },
  { id: "video", label: "Generate Video", Icon: Film },
  { id: "workflows", label: "Workflows", Icon: Wand2 },
  { id: "library", label: "Library", Icon: Library },
  { id: "models", label: "Models", Icon: Boxes },
];

// ── Small shared pieces ──────────────────────────────────────────────────────

function CenteredSpinner({ text }: { text: string }) {
  return (
    <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
      <span className="text-sm">{text}</span>
    </div>
  );
}

function StatusErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
      <div className="flex items-start gap-3">
        <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
        <div className="text-sm space-y-1.5 min-w-0">
          <p className="font-medium text-foreground">{title}</p>
          <p className="text-muted-foreground text-xs leading-relaxed break-all">
            {message}
          </p>
        </div>
      </div>
      <Button size="sm" variant="secondary" onClick={onRetry}>
        <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
        Try again
      </Button>
    </div>
  );
}

function NoModelEmptyState({
  kind,
  onGoToModels,
}: {
  kind: "image" | "video";
  onGoToModels: () => void;
}) {
  const Icon = kind === "image" ? ImageIcon : Film;
  return (
    <div className="rounded-xl border border-dashed px-5 py-12 flex flex-col items-center text-center gap-3">
      <Icon className="h-8 w-8 text-muted-foreground/40" />
      <p className="text-sm font-medium">No model selected</p>
      <p className="text-xs text-muted-foreground max-w-sm">
        Pick a {kind} model in the Models view — its full settings will appear
        here.
      </p>
      <Button size="sm" onClick={onGoToModels}>
        <Boxes className="h-3.5 w-3.5 mr-1.5" />
        Open Models
      </Button>
    </div>
  );
}

// ── Model card (image + video, one component) ────────────────────────────────

function WorkspaceModelCard({
  model,
  category,
  isLoaded,
  isLoadingThis,
  anyLoadInFlight,
  onLoad,
  onDownload,
  onGenerate,
}: {
  model: ImageGenModelInfo | VideoGenModelInfo;
  category: "image_gen" | "video_gen";
  isLoaded: boolean;
  isLoadingThis: boolean;
  anyLoadInFlight: boolean;
  onLoad: () => void;
  onDownload: () => void;
  onGenerate: () => void;
}) {
  const { downloads, openModal } = useDownloadManager();
  const dl = useMemo(
    () => findModelDownload(downloads, category, model.model_id),
    [downloads, category, model.model_id],
  );
  const downloading = dl?.status === "active" || dl?.status === "queued";
  const hardwareBlocked = !model.hardware_ok;
  const imageToVideo =
    "supports_image_to_video" in model && model.supports_image_to_video;

  return (
    <div
      className={`rounded-lg border bg-card p-4 space-y-3 transition-colors ${
        isLoaded
          ? "border-violet-500/40 bg-violet-500/5"
          : hardwareBlocked
            ? "opacity-70"
            : "hover:bg-muted/10"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-medium text-sm">{model.name}</p>
          <p className="text-xs text-muted-foreground">{model.provider}</p>
        </div>
        <button
          onClick={() => void openExternalUrl(model.model_card_url)}
          className="shrink-0 text-muted-foreground hover:text-foreground"
          aria-label={`Open model card for ${model.name}`}
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </button>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">
        {model.description}
      </p>
      <div className="flex items-center gap-3 text-xs">
        <span className="flex items-center gap-1 text-muted-foreground">
          Quality <StarRating value={model.quality_rating} />
        </span>
        <span className="flex items-center gap-1 text-muted-foreground">
          Speed <StarRating value={model.speed_rating} />
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5 text-[10px]">
        <span className="rounded bg-muted px-1.5 py-0.5">
          {formatGb(model.download_size_gb)} download
        </span>
        <span className="rounded bg-muted px-1.5 py-0.5">
          VRAM: {model.vram_gb} GB
        </span>
        <span className="rounded bg-muted px-1.5 py-0.5">
          RAM: {model.ram_gb} GB
        </span>
        {imageToVideo && (
          <span className="rounded bg-blue-500/20 text-blue-600 dark:text-blue-400 px-1.5 py-0.5">
            Image → Video
          </span>
        )}
        {model.is_downloaded && (
          <span className="rounded bg-green-500/20 text-green-600 dark:text-green-400 px-1.5 py-0.5">
            Downloaded
          </span>
        )}
        {model.requires_hf_token && (
          <span className="rounded bg-amber-500/20 text-amber-600 dark:text-amber-400 px-1.5 py-0.5">
            HF token required
          </span>
        )}
      </div>

      {hardwareBlocked && (
        <div className="rounded bg-muted/50 px-2 py-1.5 text-[11px] text-muted-foreground flex items-center gap-1.5">
          <AlertCircle className="h-3 w-3 shrink-0 text-amber-500" />
          {model.hardware_reason ??
            "Your hardware does not meet this model's requirements."}
        </div>
      )}

      {downloading && dl ? (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span>Downloading weights…</span>
            <button
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
      ) : !model.is_downloaded ? (
        <Button
          size="sm"
          className="w-full"
          variant="outline"
          disabled={hardwareBlocked}
          onClick={onDownload}
        >
          <Download className="h-3.5 w-3.5 mr-1.5" />
          Download ({formatGb(model.download_size_gb)})
        </Button>
      ) : (
        <Button
          size="sm"
          className="w-full"
          variant={isLoaded ? "default" : "outline"}
          disabled={anyLoadInFlight || hardwareBlocked}
          onClick={() => (isLoaded ? onGenerate() : onLoad())}
        >
          {isLoadingThis ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />
              Loading…
            </>
          ) : isLoaded ? (
            "Generate →"
          ) : (
            "Load model"
          )}
        </Button>
      )}
    </div>
  );
}

// ── Models view (image AND video catalogs) ───────────────────────────────────

function ModelsView({
  onGoToImage,
  onGoToVideo,
}: {
  onGoToImage: () => void;
  onGoToVideo: () => void;
}) {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading,
    loadingImageModelId,
    imageGenError,
    imageForm,
    videoStatus,
    videoModels,
    videoStatusLoading,
    videoStatusError,
    videoModelLoading,
    loadingVideoModelId,
    videoGenError,
    videoForm,
  } = state;
  const {
    refreshImage,
    refreshVideo,
    loadImageModel,
    unloadImageModel,
    downloadImageModel,
    prepareImageGenerate,
    setImageForm,
    clearImageGenError,
    loadVideoModel,
    unloadVideoModel,
    downloadVideoModel,
    prepareVideoGenerate,
    setVideoForm,
    clearVideoGenError,
  } = actions;

  const [localError, setLocalError] = useState<string | null>(null);

  // When a media-gen weights download completes, refresh the catalog so
  // `is_downloaded` flips without a manual reload. Gated narrowly on the
  // COUNT of completed entries per category — never the downloads array.
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

  // ── Image model handlers ─────────────────────────────────────────────────
  const handleImageOpenGenerate = useCallback(
    (model: ImageGenModelInfo) => {
      if (imageForm.defaults?.modelId === model.model_id) {
        // Same model — keep the user's tweaked settings.
        setImageForm({ view: "generate" });
      } else {
        void prepareImageGenerate(model);
      }
      onGoToImage();
    },
    [imageForm.defaults?.modelId, setImageForm, prepareImageGenerate, onGoToImage],
  );

  const handleImageLoad = useCallback(
    async (model: ImageGenModelInfo) => {
      setLocalError(null);
      const result = await loadImageModel(model.model_id);
      if (result.success) {
        await prepareImageGenerate(model);
        onGoToImage();
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet. Use the Download button first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadImageModel, prepareImageGenerate, onGoToImage],
  );

  // ── Video model handlers ─────────────────────────────────────────────────
  const handleVideoOpenGenerate = useCallback(
    (model: VideoGenModelInfo) => {
      if (videoForm.defaults?.modelId === model.model_id) {
        setVideoForm({ view: "generate" });
      } else {
        void prepareVideoGenerate(model);
      }
      onGoToVideo();
    },
    [videoForm.defaults?.modelId, setVideoForm, prepareVideoGenerate, onGoToVideo],
  );

  const handleVideoLoad = useCallback(
    async (model: VideoGenModelInfo) => {
      setLocalError(null);
      const result = await loadVideoModel(model.model_id);
      if (result.success) {
        await prepareVideoGenerate(model);
        onGoToVideo();
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet. Use the Download button first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadVideoModel, prepareVideoGenerate, onGoToVideo],
  );

  const genError = imageGenError ?? videoGenError ?? localError;
  const dismissGenError = useCallback(() => {
    setLocalError(null);
    clearImageGenError();
    clearVideoGenError();
  }, [clearImageGenError, clearVideoGenError]);

  // Whether the shared package installer is already rendered for images —
  // the video packages are the same set, so we never show it twice.
  const imageNeedsInstall =
    !!imageStatus && (!imageStatus.available || imageStatus.packages_outdated);

  return (
    <div className="space-y-8 pb-8">
      {genError && <ErrorNote message={genError} onDismiss={dismissGenError} />}

      {/* ── Image models ─────────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <ImageIcon className="h-4 w-4 text-violet-500" />
          Image models
        </h3>

        {imageStatusLoading && !imageStatus ? (
          <CenteredSpinner text="Checking image generation status…" />
        ) : imageStatusError ? (
          <StatusErrorCard
            title="Could not load image generation"
            message={imageStatusError}
            onRetry={() => void refreshImage()}
          />
        ) : imageStatus && !imageStatus.available ? (
          <ImageGenInstaller
            models={imageModels}
            onInstallComplete={() => void refreshImage()}
          />
        ) : imageStatus?.packages_outdated ? (
          <ImageGenInstaller
            models={[]}
            upgrade
            onInstallComplete={() => void refreshImage()}
          />
        ) : (
          <>
            {imageStatus?.loaded_model_id && (
              <div className="flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 px-4 py-3">
                <div className="flex items-center gap-2 text-sm min-w-0">
                  <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
                  <span className="truncate">
                    Image model loaded:{" "}
                    <span className="font-medium">
                      {imageStatus.loaded_model_id}
                    </span>
                  </span>
                </div>
                <div className="flex gap-2 shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      const m = imageModels.find(
                        (x) => x.model_id === imageStatus.loaded_model_id,
                      );
                      if (m) handleImageOpenGenerate(m);
                    }}
                  >
                    Generate
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void unloadImageModel()}
                  >
                    Unload
                  </Button>
                </div>
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {imageModels.map((m) => (
                <WorkspaceModelCard
                  key={m.model_id}
                  model={m}
                  category="image_gen"
                  isLoaded={imageStatus?.loaded_model_id === m.model_id}
                  isLoadingThis={loadingImageModelId === m.model_id}
                  anyLoadInFlight={
                    imageModelLoading || !!imageStatus?.is_loading
                  }
                  onLoad={() => void handleImageLoad(m)}
                  onDownload={() => void downloadImageModel(m.model_id)}
                  onGenerate={() => handleImageOpenGenerate(m)}
                />
              ))}
              {imageModels.length === 0 && (
                <div className="col-span-full rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                  No image models available yet.
                </div>
              )}
            </div>
          </>
        )}
      </section>

      {/* ── Video models ─────────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Film className="h-4 w-4 text-violet-500" />
          Video models
        </h3>

        {videoStatusLoading && !videoStatus ? (
          <CenteredSpinner text="Checking video generation status…" />
        ) : videoStatusError ? (
          <StatusErrorCard
            title="Could not load video generation"
            message={videoStatusError}
            onRetry={() => void refreshVideo()}
          />
        ) : videoStatus && !videoStatus.hardware_supported ? (
          <div className="rounded-lg border px-4 py-4 flex items-start gap-3">
            <MonitorX className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
            <p className="text-xs text-muted-foreground leading-relaxed">
              {videoStatus.hardware_reason ??
                videoStatus.unavailable_reason ??
                "Video generation requires Apple Silicon with 16GB+ memory or an NVIDIA GPU with 8GB+ VRAM."}{" "}
              Image generation may still work above.
            </p>
          </div>
        ) : videoStatus && !videoStatus.packages_installed ? (
          imageNeedsInstall ? (
            <p className="rounded-lg border border-dashed px-4 py-3 text-xs text-muted-foreground">
              Video generation uses the same on-device AI packages as image
              generation — complete the install above to unlock video models.
            </p>
          ) : (
            <ImageGenInstaller
              models={videoModels}
              headline="Set up Video Generation"
              intro="AI Matrx can generate short videos directly on your computer. Video uses the same on-device AI packages as image generation — click Install now for the one-time setup, then download a video model."
              onInstallComplete={() => void refreshVideo()}
            />
          )
        ) : (
          <>
            {videoStatus?.loaded_model_id && (
              <div className="flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 px-4 py-3">
                <div className="flex items-center gap-2 text-sm min-w-0">
                  <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
                  <span className="truncate">
                    Video model loaded:{" "}
                    <span className="font-medium">
                      {videoStatus.loaded_model_id}
                    </span>
                  </span>
                </div>
                <div className="flex gap-2 shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      const m = videoModels.find(
                        (x) => x.model_id === videoStatus.loaded_model_id,
                      );
                      if (m) handleVideoOpenGenerate(m);
                    }}
                  >
                    Generate
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void unloadVideoModel()}
                  >
                    Unload
                  </Button>
                </div>
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {videoModels.map((m) => (
                <WorkspaceModelCard
                  key={m.model_id}
                  model={m}
                  category="video_gen"
                  isLoaded={videoStatus?.loaded_model_id === m.model_id}
                  isLoadingThis={loadingVideoModelId === m.model_id}
                  anyLoadInFlight={
                    videoModelLoading || !!videoStatus?.is_loading
                  }
                  onLoad={() => void handleVideoLoad(m)}
                  onDownload={() => void downloadVideoModel(m.model_id)}
                  onGenerate={() => handleVideoOpenGenerate(m)}
                />
              ))}
              {videoModels.length === 0 && (
                <div className="col-span-full rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                  No video models available yet.
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}

// ── Image job row (queue list inside the Generate Image view) ────────────────

function WorkspaceImageJobRow({
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
    <div className="rounded-lg border bg-card px-3 py-2.5 space-y-1.5">
      <div className="flex items-center gap-3">
        <div className="w-5 shrink-0">
          {job.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          ) : job.status === "failed" ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : job.status === "cancelled" ? (
            <X className="h-4 w-4 text-muted-foreground" />
          ) : (
            <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
          )}
        </div>
        {thumbUrl && (
          <img
            src={thumbUrl}
            alt="Generated"
            className="h-10 w-10 rounded object-cover border shrink-0"
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs" title={job.prompt}>
            {job.prompt || "(no prompt)"}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {job.model_id || "—"} ·{" "}
            {active && job.cancel_requested ? "cancelling…" : job.status}
            {job.status === "failed" && job.error ? ` — ${job.error}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {typeof job.seed === "number" && (
            <SeedChip seed={job.seed} onReuse={onReuseSeed} />
          )}
          {active && job.cancel_requested ? (
            <span
              className="flex items-center gap-1 text-[10px] text-muted-foreground"
              title="Cancel requested — the current step is finishing"
            >
              <Loader2 className="h-3 w-3 animate-spin" />
              Cancelling…
            </span>
          ) : (
            <button
              onClick={() => onCancel(job.job_id)}
              className="text-muted-foreground hover:text-foreground"
              aria-label={active ? "Cancel job" : "Remove job"}
              title={active ? "Cancel this job" : "Remove from the queue"}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
      {job.status === "running" && (
        <InlineProgressBar
          percent={(job.progress ?? 0) * 100}
          indeterminate={(job.progress ?? 0) <= 0}
        />
      )}
    </div>
  );
}

// ── Generate Image view (pure generate form, no sub-tabs) ────────────────────

function ImageGenerateView({ onGoToModels }: { onGoToModels: () => void }) {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageStatusLoading,
    imageStatusError,
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
  } = state;
  const {
    refreshImage,
    unloadImageModel,
    generateImage,
    cancelImageGeneration,
    clearImageResult,
    clearImageGenError,
    setImageForm,
    prepareImageGenerate,
    resetImageCommon,
    resetImageAdvanced,
    resetImageAll,
    enqueueImageJob,
    cancelImageJob,
  } = actions;

  // The model this form works with — same resolution as the classic section.
  const generateModelId =
    imageForm.defaults?.modelId ??
    selectedImageModelId ??
    imageStatus?.loaded_model_id ??
    null;
  const selectedModel = useMemo(
    () => imageModels.find((m) => m.model_id === generateModelId) ?? null,
    [imageModels, generateModelId],
  );

  // If the form defaults belong to a different model (or none — e.g. a model
  // was already loaded at app start), fetch that model's full parameter
  // schema. Guarded so it runs once per model change, never in a loop.
  useEffect(() => {
    if (imageForm.paramsLoading) return;
    if (!selectedModel) return;
    if (imageForm.defaults?.modelId === selectedModel.model_id) return;
    void prepareImageGenerate(selectedModel);
  }, [
    imageForm.paramsLoading,
    imageForm.defaults?.modelId,
    selectedModel,
    prepareImageGenerate,
  ]);

  // ── Validation + request building ────────────────────────────────────────
  const defaults = imageForm.defaults;
  const advanced = useMemo(
    () =>
      computeAdvancedOverrides(imageForm.advancedText, defaults?.advanced ?? {}),
    [imageForm.advancedText, defaults?.advanced],
  );
  const dimError = dimensionError(imageForm.width, imageForm.height);
  const formInvalid =
    !imageForm.prompt.trim() || !defaults || !advanced.ok || dimError !== null;

  const buildInput = useCallback((): ImageGenerateInput | null => {
    const d = imageForm.defaults;
    if (!d) return null;
    const adv = computeAdvancedOverrides(imageForm.advancedText, d.advanced);
    if (!adv.ok) return null;
    // Resolve a concrete seed even for "random" so every result is
    // reproducible — the used seed is shown on the result and in the queue.
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

  const handleGenerate = useCallback(async () => {
    const input = buildInput();
    if (!input) return;
    await generateImage(input);
  }, [buildInput, generateImage]);

  const handleEnqueue = useCallback(async () => {
    const input = buildInput();
    if (!input) return;
    // Queue and clear NOTHING — the prompt stays editable for the next one.
    await enqueueImageJob(input);
  }, [buildInput, enqueueImageJob]);

  const reuseSeed = useCallback(
    (seed: number) => setImageForm({ seedText: String(seed) }),
    [setImageForm],
  );

  const sizePresets = useMemo<SizePreset[]>(() => {
    const base: SizePreset[] = defaults
      ? [
          {
            label: `Default ${defaults.width}×${defaults.height}`,
            width: defaults.width,
            height: defaults.height,
          },
        ]
      : [];
    const fixed: SizePreset[] = [
      { label: "512", width: 512, height: 512 },
      { label: "768", width: 768, height: 768 },
      { label: "1024", width: 1024, height: 1024 },
      { label: "1536", width: 1536, height: 1536 },
      { label: "Portrait 832×1216", width: 832, height: 1216 },
      { label: "Landscape 1216×832", width: 1216, height: 832 },
    ];
    return [
      ...base,
      ...fixed.filter(
        (p) =>
          !defaults ||
          p.width !== defaults.width ||
          p.height !== defaults.height,
      ),
    ];
  }, [defaults]);

  const activeJobCount = imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  ).length;

  // ── Not-ready gates ──────────────────────────────────────────────────────
  if (imageStatusLoading && !imageStatus) {
    return <CenteredSpinner text="Checking image generation status…" />;
  }
  if (imageStatusError) {
    return (
      <StatusErrorCard
        title="Could not load image generation"
        message={imageStatusError}
        onRetry={() => void refreshImage()}
      />
    );
  }
  if (imageStatus && !imageStatus.available) {
    return (
      <ImageGenInstaller
        models={imageModels}
        onInstallComplete={() => void refreshImage()}
      />
    );
  }

  const outdatedBanner =
    imageStatus?.packages_outdated === true ? (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 flex items-start gap-3">
        <AlertCircle className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1 space-y-1">
          <p className="text-sm font-medium">Update AI packages</p>
          <p className="text-xs text-muted-foreground leading-relaxed">
            Your on-device AI packages
            {imageStatus.packages_version
              ? ` (diffusers ${imageStatus.packages_version})`
              : ""}{" "}
            are older than required for the latest models. Run the update from
            the Models view.
          </p>
          <Button size="sm" variant="outline" onClick={onGoToModels}>
            Open Models
          </Button>
        </div>
      </div>
    ) : null;

  return (
    <div className="space-y-5 pb-8">
      {outdatedBanner}

      {!selectedModel ? (
        <NoModelEmptyState kind="image" onGoToModels={onGoToModels} />
      ) : imageForm.paramsLoading || !defaults ? (
        <CenteredSpinner
          text={`Loading ${selectedModel.name}'s parameters…`}
        />
      ) : (
        <div className="space-y-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">
                {selectedModel.name}
              </span>
              <Badge variant="outline" className="text-[10px]">
                {selectedModel.provider}
              </Badge>
            </div>
            <div className="flex gap-2 items-center">
              <ResetButton onClick={resetImageAll} label="Reset all settings" />
              <Button size="sm" variant="ghost" onClick={onGoToModels}>
                Switch model
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void unloadImageModel()}
              >
                Unload
              </Button>
            </div>
          </div>

          {imageForm.paramsError && (
            <ParamsErrorBanner
              error={imageForm.paramsError}
              onRetry={() => void prepareImageGenerate(selectedModel)}
            />
          )}

          <div className="grid gap-6 lg:grid-cols-2">
            {/* Controls */}
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label className="text-xs">Prompt</Label>
                <Textarea
                  value={imageForm.prompt}
                  onChange={(e) => setImageForm({ prompt: e.target.value })}
                  placeholder="Describe the image you want to generate…"
                  className="text-sm min-h-[100px] resize-none"
                />
              </div>

              <NegativePromptField
                supported={defaults.supportsNegativePrompt}
                value={imageForm.negativePrompt}
                onChange={(v) => setImageForm({ negativePrompt: v })}
              />

              <div className="grid grid-cols-2 gap-3">
                <NumberSliderField
                  label="Steps"
                  value={imageForm.steps}
                  onChange={(v) => setImageForm({ steps: v })}
                  min={1}
                  max={
                    selectedModel.pipeline_type.startsWith("flux") ? 50 : 100
                  }
                  step={1}
                  defaultValue={defaults.steps}
                />
                <NumberSliderField
                  label="Guidance"
                  value={imageForm.guidance}
                  onChange={(v) => setImageForm({ guidance: v })}
                  min={0}
                  max={20}
                  step={0.5}
                  defaultValue={defaults.guidance}
                />
              </div>

              <DimensionPicker
                width={imageForm.width}
                height={imageForm.height}
                onChange={(w, h) => setImageForm({ width: w, height: h })}
                presets={sizePresets}
              />

              <div className="space-y-1.5">
                <Label className="text-xs">
                  Seed{" "}
                  <span className="text-muted-foreground">
                    (blank = random — the used seed is shown on the result)
                  </span>
                </Label>
                <SeedInput
                  value={imageForm.seedText}
                  onChange={(seedText) => setImageForm({ seedText })}
                />
              </div>

              <div className="flex justify-end">
                <ResetButton
                  onClick={resetImageCommon}
                  label="Reset settings to model defaults"
                />
              </div>

              <AdvancedParamsEditor
                defaults={defaults.advanced}
                text={imageForm.advancedText}
                onChange={(advancedText) => setImageForm({ advancedText })}
                onReset={resetImageAdvanced}
              />

              {imageGenError && (
                <ErrorNote
                  message={imageGenError}
                  onDismiss={clearImageGenError}
                />
              )}

              <div className="flex gap-2">
                <CancelableGenerateButton
                  generating={imageGenerating}
                  cancelling={imageCancelling}
                  startedAt={imageGenStartedAt}
                  disabled={formInvalid}
                  onGenerate={() => void handleGenerate()}
                  onCancel={() => void cancelImageGeneration()}
                  containerClassName="flex-1"
                  idleContent={
                    <>
                      <ImageIcon className="h-4 w-4 mr-2" />
                      Generate
                    </>
                  }
                />
                <Button
                  variant="outline"
                  disabled={formInvalid}
                  onClick={() => {
                    void handleEnqueue();
                  }}
                  title="Queue this generation and keep editing — write the next prompt right away"
                >
                  <ListPlus className="h-4 w-4 mr-2" />
                  Add to queue
                </Button>
              </div>
            </div>

            {/* Output */}
            <div className="space-y-3">
              {imageResult ? (
                <GeneratedImageView
                  result={imageResult}
                  onClear={clearImageResult}
                  onReuseSeed={reuseSeed}
                />
              ) : imageGenerating ? (
                <div className="flex flex-col items-center justify-center rounded-lg border border-dashed aspect-square gap-3 text-muted-foreground">
                  <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
                  <span className="text-sm">Generating image…</span>
                  <StillWorkingNote startedAt={imageGenStartedAt} />
                  <span className="text-xs">
                    Can take minutes on CPU — use Cancel to stop at any time
                  </span>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center rounded-lg border border-dashed aspect-square gap-3 text-muted-foreground">
                  <ImageIcon className="h-10 w-10 opacity-20" />
                  <span className="text-sm">
                    Your generated image will appear here
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Queue */}
          {(imageJobs.length > 0 || imageJobsError) && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold">
                Queue
                {activeJobCount > 0 && (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    {activeJobCount} active
                  </span>
                )}
              </h3>
              {imageJobsError && <ErrorNote message={imageJobsError} />}
              <div className="space-y-2">
                {imageJobs.map((j) => (
                  <WorkspaceImageJobRow
                    key={j.job_id}
                    job={j}
                    thumbUrl={imageJobThumbs[j.job_id] ?? null}
                    onCancel={(id) => void cancelImageJob(id)}
                    onReuseSeed={reuseSeed}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Generate Video view (pure generate form, no sub-tabs) ────────────────────

function VideoGenerateView({ onGoToModels }: { onGoToModels: () => void }) {
  const [state, actions] = useMediaGenApp();
  const {
    videoStatus,
    videoModels,
    videoStatusLoading,
    videoStatusError,
    videoGenerating,
    videoCancelling,
    videoGenError,
    activeJob,
    jobs,
    videoResults,
    videoForm,
  } = state;
  const {
    refreshVideo,
    unloadVideoModel,
    generateVideo,
    cancelVideoGeneration,
    cancelVideoJob,
    fetchVideoResult,
    clearActiveJob,
    clearVideoGenError,
    setVideoForm,
    prepareVideoGenerate,
    resetVideoCommon,
    resetVideoAdvanced,
    resetVideoAll,
  } = actions;

  // Presentation-only local state.
  const [localError, setLocalError] = useState<string | null>(null);
  const [playbackJobId, setPlaybackJobId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadedModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoStatus?.loaded_model_id) ??
      null,
    [videoModels, videoStatus?.loaded_model_id],
  );
  const formModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoForm.defaults?.modelId) ??
      loadedModel,
    [videoModels, videoForm.defaults?.modelId, loadedModel],
  );

  // Fetch the full parameter schema when the form's defaults belong to a
  // different model (or none). Guarded so it runs once per model change.
  useEffect(() => {
    if (videoForm.paramsLoading) return;
    if (!formModel) return;
    if (videoForm.defaults?.modelId === formModel.model_id) return;
    void prepareVideoGenerate(formModel);
  }, [
    videoForm.paramsLoading,
    videoForm.defaults?.modelId,
    formModel,
    prepareVideoGenerate,
  ]);

  const handlePickImage = useCallback(
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

  // ── Validation + request building ────────────────────────────────────────
  const defaults = videoForm.defaults;
  const advanced = useMemo(
    () =>
      computeAdvancedOverrides(videoForm.advancedText, defaults?.advanced ?? {}),
    [videoForm.advancedText, defaults?.advanced],
  );
  const dimError = dimensionError(videoForm.width, videoForm.height);
  const formInvalid =
    !videoForm.prompt.trim() || !defaults || !advanced.ok || dimError !== null;

  const handleGenerate = useCallback(async () => {
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

  const handlePlay = useCallback(
    (jobId: string) => {
      setPlaybackJobId(jobId);
      void fetchVideoResult(jobId);
    },
    [fetchVideoResult],
  );

  const jobIsActive =
    activeJob?.status === "queued" || activeJob?.status === "running";
  const playbackUrl = playbackJobId
    ? (videoResults[playbackJobId] ?? null)
    : activeJob?.status === "completed"
      ? (videoResults[activeJob.job_id] ?? null)
      : null;

  const genError = videoGenError ?? localError;
  const dismissGenError = useCallback(() => {
    setLocalError(null);
    clearVideoGenError();
  }, [clearVideoGenError]);

  const sizePresets = useMemo<SizePreset[]>(() => {
    const base: SizePreset[] = defaults
      ? [
          {
            label: `Default ${defaults.width}×${defaults.height}`,
            width: defaults.width,
            height: defaults.height,
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
          !defaults ||
          p.width !== defaults.width ||
          p.height !== defaults.height,
      ),
    ];
  }, [defaults]);

  const maxFrames =
    formModel && formModel.max_num_frames > 0 ? formModel.max_num_frames : 200;
  const approxSeconds =
    videoForm.fps > 0 ? videoForm.numFrames / videoForm.fps : 0;

  // ── Not-ready gates ──────────────────────────────────────────────────────
  if (videoStatusLoading && !videoStatus) {
    return <CenteredSpinner text="Checking video generation status…" />;
  }
  if (videoStatusError) {
    return (
      <StatusErrorCard
        title="Could not load video generation"
        message={videoStatusError}
        onRetry={() => void refreshVideo()}
      />
    );
  }
  if (videoStatus && !videoStatus.hardware_supported) {
    return (
      <div className="rounded-xl border px-5 py-8 flex flex-col items-center text-center gap-3">
        <div className="rounded-lg bg-muted p-3">
          <MonitorX className="h-6 w-6 text-muted-foreground" />
        </div>
        <p className="font-semibold text-sm">
          Video generation is not available on this computer
        </p>
        <p className="text-xs text-muted-foreground leading-relaxed max-w-md">
          {videoStatus.hardware_reason ??
            videoStatus.unavailable_reason ??
            "Video generation requires Apple Silicon with 16GB+ memory or an NVIDIA GPU with 8GB+ VRAM."}
        </p>
        <p className="text-[11px] text-muted-foreground">
          Image generation may still work — check Generate Image.
        </p>
      </div>
    );
  }
  if (videoStatus && !videoStatus.packages_installed) {
    return (
      <ImageGenInstaller
        models={videoModels}
        headline="Set up Video Generation"
        intro="AI Matrx can generate short videos directly on your computer. Video uses the same on-device AI packages as image generation — click Install now for the one-time setup, then download a video model."
        onInstallComplete={() => void refreshVideo()}
      />
    );
  }

  return (
    <div className="space-y-5 pb-8">
      {/* ── Active job progress (always visible in this view) ────────────── */}
      {activeJob && (
        <div
          className={`rounded-lg border px-4 py-3 space-y-2 ${
            activeJob.status === "failed"
              ? "border-destructive/30 bg-destructive/5"
              : activeJob.status === "completed"
                ? "border-green-500/30 bg-green-500/5"
                : jobIsActive
                  ? "border-violet-500/30 bg-violet-500/5"
                  : "border-border bg-muted/20"
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0 text-sm">
              {jobIsActive ? (
                <Loader2 className="h-4 w-4 animate-spin text-violet-500 shrink-0" />
              ) : activeJob.status === "completed" ? (
                <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
              ) : activeJob.status === "failed" ? (
                <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
              ) : (
                <Film className="h-4 w-4 text-muted-foreground shrink-0" />
              )}
              <span className="truncate" title={activeJob.prompt}>
                {jobIsActive
                  ? "Generating video…"
                  : activeJob.status === "completed"
                    ? "Video ready"
                    : activeJob.status === "failed"
                      ? "Video generation failed"
                      : `Video job — ${activeJob.status}`}
                {activeJob.prompt ? ` — ${activeJob.prompt}` : ""}
              </span>
            </div>
            <div className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
              {jobIsActive && activeJob.total_steps > 0 && (
                <span className="tabular-nums">
                  step {activeJob.current_step}/{activeJob.total_steps}
                </span>
              )}
              {jobIsActive && (
                <span className="flex items-center gap-1 tabular-nums">
                  <Clock className="h-3 w-3" />
                  {Math.round(activeJob.elapsed_seconds)}s
                </span>
              )}
              {!jobIsActive && (
                <button
                  onClick={clearActiveJob}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label="Dismiss job"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
          {jobIsActive && (
            <InlineProgressBar
              percent={activeJob.progress * 100}
              indeterminate={activeJob.status === "queued"}
            />
          )}
          {activeJob.status === "failed" && activeJob.error && (
            <p className="text-xs text-destructive break-all">
              {activeJob.error}
            </p>
          )}
        </div>
      )}

      {/* ── Playback ─────────────────────────────────────────────────────── */}
      {playbackUrl && (
        <div className="space-y-2">
          <video
            key={playbackUrl}
            controls
            autoPlay
            loop
            src={playbackUrl}
            className="w-full max-h-[420px] rounded-lg border bg-black"
          />
          <div className="flex items-center justify-end">
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
      )}
      {playbackJobId && !playbackUrl && (
        <div className="flex items-center gap-2 rounded-lg border border-dashed px-4 py-6 justify-center text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          Fetching video…
        </div>
      )}

      {!formModel ? (
        <NoModelEmptyState kind="video" onGoToModels={onGoToModels} />
      ) : videoForm.paramsLoading || !defaults ? (
        <CenteredSpinner text={`Loading ${formModel.name}'s parameters…`} />
      ) : (
        <div className="space-y-4 max-w-xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">{formModel.name}</span>
              <Badge variant="outline" className="text-[10px]">
                {formModel.provider}
              </Badge>
            </div>
            <div className="flex gap-2 items-center">
              <ResetButton onClick={resetVideoAll} label="Reset all settings" />
              <Button size="sm" variant="ghost" onClick={onGoToModels}>
                Switch model
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void unloadVideoModel()}
              >
                Unload
              </Button>
            </div>
          </div>

          {videoForm.paramsError && (
            <ParamsErrorBanner
              error={videoForm.paramsError}
              onRetry={() => void prepareVideoGenerate(formModel)}
            />
          )}

          <div className="space-y-1.5">
            <Label className="text-xs">Prompt</Label>
            <Textarea
              value={videoForm.prompt}
              onChange={(e) => setVideoForm({ prompt: e.target.value })}
              placeholder="Describe the video you want to generate…"
              className="text-sm min-h-[80px] resize-none"
            />
          </div>

          <NegativePromptField
            supported={defaults.supportsNegativePrompt}
            value={videoForm.negativePrompt}
            onChange={(v) => setVideoForm({ negativePrompt: v })}
          />

          <div className="grid grid-cols-2 gap-3">
            <NumberSliderField
              label="Steps"
              value={videoForm.steps}
              onChange={(v) => setVideoForm({ steps: v })}
              min={1}
              max={100}
              step={1}
              defaultValue={defaults.steps}
            />
            <NumberSliderField
              label="Guidance"
              value={videoForm.guidance}
              onChange={(v) => setVideoForm({ guidance: v })}
              min={0}
              max={20}
              step={0.5}
              defaultValue={defaults.guidance}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <NumberSliderField
              label="Frames"
              value={videoForm.numFrames}
              onChange={(v) => setVideoForm({ numFrames: v })}
              min={1}
              max={maxFrames}
              step={1}
              defaultValue={defaults.numFrames}
            />
            <NumberSliderField
              label="FPS"
              value={videoForm.fps}
              onChange={(v) => setVideoForm({ fps: v })}
              min={1}
              max={60}
              step={1}
              defaultValue={defaults.fps}
            />
          </div>
          <p className="text-[11px] text-muted-foreground tabular-nums">
            ≈ {approxSeconds.toFixed(1)}s of video ({videoForm.numFrames}{" "}
            frames at {videoForm.fps} fps)
          </p>

          <DimensionPicker
            width={videoForm.width}
            height={videoForm.height}
            onChange={(w, h) => setVideoForm({ width: w, height: h })}
            presets={sizePresets}
          />

          <div className="space-y-1.5">
            <Label className="text-xs">
              Seed{" "}
              <span className="text-muted-foreground">(blank = random)</span>
            </Label>
            <SeedInput
              value={videoForm.seedText}
              onChange={(seedText) => setVideoForm({ seedText })}
            />
          </div>

          <div className="flex justify-end">
            <ResetButton
              onClick={resetVideoCommon}
              label="Reset settings to model defaults"
            />
          </div>

          {formModel.supports_image_to_video && (
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
                onChange={handlePickImage}
              />
              {videoForm.sourceImage ? (
                <div className="flex items-center gap-3 rounded-lg border px-3 py-2">
                  <img
                    src={videoForm.sourceImage.previewUrl}
                    alt="Source"
                    className="h-12 w-12 rounded object-cover border"
                  />
                  <span className="text-xs truncate flex-1">
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
            defaults={defaults.advanced}
            text={videoForm.advancedText}
            onChange={(advancedText) => setVideoForm({ advancedText })}
            onReset={resetVideoAdvanced}
          />

          {genError && (
            <ErrorNote message={genError} onDismiss={dismissGenError} />
          )}
          {jobIsActive && (
            <p className="text-[11px] text-muted-foreground">
              One video at a time — the current job must finish before starting
              another.
            </p>
          )}

          <CancelableGenerateButton
            generating={videoGenerating || jobIsActive}
            cancelling={videoCancelling || !!activeJob?.cancel_requested}
            elapsedSeconds={
              jobIsActive ? (activeJob?.elapsed_seconds ?? null) : null
            }
            disabled={formInvalid}
            onGenerate={() => void handleGenerate()}
            onCancel={() => void cancelVideoGeneration()}
            workingLabel="Generating video"
            idleContent={
              <>
                <Film className="h-4 w-4 mr-2" />
                Generate Video
              </>
            }
          />
        </div>
      )}

      {/* ── Recent jobs ──────────────────────────────────────────────────── */}
      {jobs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Recent videos</h3>
          <div className="space-y-2">
            {jobs.map((j) => (
              <div
                key={j.job_id}
                className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5"
              >
                <div className="w-5 shrink-0">
                  {j.status === "completed" ? (
                    <CheckCircle2 className="h-4 w-4 text-green-500" />
                  ) : j.status === "failed" ? (
                    <AlertCircle className="h-4 w-4 text-destructive" />
                  ) : j.status === "queued" || j.status === "running" ? (
                    <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
                  ) : (
                    <Film className="h-4 w-4 text-muted-foreground" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs" title={j.prompt}>
                    {j.prompt || "(no prompt)"}
                  </p>
                  <p className="text-[10px] text-muted-foreground">
                    {j.model_id || "—"} ·{" "}
                    {j.status === "failed"
                      ? (j.error ?? "failed")
                      : j.status === "completed"
                        ? `${j.elapsed_seconds.toFixed(0)}s`
                        : j.status === "queued" || j.status === "running"
                          ? j.cancel_requested
                            ? "cancelling…"
                            : `${Math.round(j.progress * 100)}%`
                          : j.status}
                  </p>
                </div>
                {(j.status === "queued" || j.status === "running") &&
                  (j.cancel_requested ? (
                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Cancelling…
                    </span>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive hover:text-destructive shrink-0"
                      onClick={() => void cancelVideoJob(j.job_id)}
                      title="Cancel this video job"
                    >
                      <X className="h-3.5 w-3.5 mr-1" />
                      Cancel
                    </Button>
                  ))}
                {j.status === "completed" && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handlePlay(j.job_id)}
                  >
                    <Play className="h-3.5 w-3.5 mr-1" />
                    {videoResults[j.job_id] ? "Show" : "Play"}
                  </Button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Persistent queue footer ──────────────────────────────────────────────────

function QueueFooter({ onJump }: { onJump: (id: NavId) => void }) {
  const [state, actions] = useMediaGenApp();
  const { imageJobs, activeJob } = state;
  const { cancelImageJob, cancelVideoJob, clearActiveJob } = actions;

  const activeImageJobs = imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  );
  const runningImageJob =
    activeImageJobs.find((j) => j.status === "running") ??
    activeImageJobs[0] ??
    null;
  const videoActive =
    activeJob?.status === "queued" || activeJob?.status === "running";

  // Hide entirely when nothing is in flight and nothing recently finished
  // (a finished video job stays until dismissed — that IS the "recent" state).
  if (activeImageJobs.length === 0 && !activeJob) return null;

  return (
    <div className="shrink-0 border-t bg-background/95 px-3 py-1.5">
      <div className="flex items-center gap-4 text-xs">
        {/* Image jobs segment */}
        {activeImageJobs.length > 0 && (
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={() => onJump("image")}
              className="flex items-center gap-2 min-w-0 hover:text-foreground text-muted-foreground"
              title="Go to Generate Image"
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-500 shrink-0" />
              <span className="whitespace-nowrap">
                {activeImageJobs.length} image job
                {activeImageJobs.length === 1 ? "" : "s"}
              </span>
              {runningImageJob && (
                <>
                  <span className="truncate max-w-[200px] hidden sm:inline">
                    {runningImageJob.prompt || "(no prompt)"}
                  </span>
                  <span className="w-24 shrink-0">
                    <InlineProgressBar
                      percent={(runningImageJob.progress ?? 0) * 100}
                      indeterminate={
                        runningImageJob.status === "queued" ||
                        (runningImageJob.progress ?? 0) <= 0
                      }
                    />
                  </span>
                </>
              )}
            </button>
            {runningImageJob &&
              (runningImageJob.cancel_requested ? (
                <span
                  className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0"
                  title="Cancel requested — the current step is finishing"
                >
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Cancelling…
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => void cancelImageJob(runningImageJob.job_id)}
                  className="text-muted-foreground hover:text-destructive shrink-0"
                  aria-label="Cancel image job"
                  title="Cancel this image job"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ))}
          </div>
        )}

        {/* Video job segment */}
        {activeJob && (
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={() => onJump("video")}
              className="flex items-center gap-2 min-w-0 hover:text-foreground text-muted-foreground"
              title="Go to Generate Video"
            >
              {videoActive ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-500 shrink-0" />
              ) : activeJob.status === "completed" ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
              ) : (
                <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0" />
              )}
              <span className="whitespace-nowrap">
                {videoActive
                  ? "Video generating"
                  : activeJob.status === "completed"
                    ? "Video ready"
                    : "Video failed"}
              </span>
              {activeJob.prompt && (
                <span className="truncate max-w-[200px] hidden sm:inline">
                  {activeJob.prompt}
                </span>
              )}
              {videoActive && (
                <span className="w-24 shrink-0">
                  <InlineProgressBar
                    percent={activeJob.progress * 100}
                    indeterminate={activeJob.status === "queued"}
                  />
                </span>
              )}
              {videoActive && (
                <span className="tabular-nums shrink-0">
                  {Math.round(activeJob.progress * 100)}%
                </span>
              )}
            </button>
            {videoActive &&
              (activeJob.cancel_requested ? (
                <span
                  className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0"
                  title="Cancel requested — the current step is finishing"
                >
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Cancelling…
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => void cancelVideoJob(activeJob.job_id)}
                  className="text-muted-foreground hover:text-destructive shrink-0"
                  aria-label="Cancel video job"
                  title="Cancel this video job"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ))}
            {!videoActive && (
              <button
                type="button"
                onClick={clearActiveJob}
                className="text-muted-foreground hover:text-foreground shrink-0"
                aria-label="Dismiss video job"
                title="Dismiss"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Root: rail + content + queue footer ──────────────────────────────────────

export function VariantWorkspace() {
  const [state] = useMediaGenApp();
  // Presentation-only local state — everything else lives in MediaGenContext.
  const [active, setActive] = useState<NavId>("image");
  const [collapsed, setCollapsed] = useState(false);

  const activeImageJobCount = state.imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  ).length;
  const videoJobActive =
    state.activeJob?.status === "queued" ||
    state.activeJob?.status === "running";

  const goToImage = useCallback(() => setActive("image"), []);
  const goToVideo = useCallback(() => setActive("video"), []);
  const goToModels = useCallback(() => setActive("models"), []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-1 min-h-0">
        {/* ── Left nav rail ───────────────────────────────────────────────── */}
        <nav
          className={`flex shrink-0 flex-col border-r bg-muted/20 py-2 transition-[width] duration-200 ${
            collapsed ? "w-[52px]" : "w-[200px]"
          }`}
          aria-label="Media generation navigation"
        >
          <div className="flex flex-col gap-0.5 px-1.5">
            {NAV_ITEMS.map(({ id, label, Icon }) => {
              const isActive = active === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setActive(id)}
                  title={label}
                  aria-current={isActive ? "page" : undefined}
                  className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-xs font-medium transition-colors ${
                    isActive
                      ? "bg-violet-500/10 text-violet-600 dark:text-violet-400"
                      : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                  } ${collapsed ? "justify-center" : ""}`}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed && (
                    <span className="truncate flex-1 text-left">{label}</span>
                  )}
                  {/* Badges: active image jobs / running video job */}
                  {id === "image" && activeImageJobCount > 0 && (
                    <span
                      className={`rounded-full bg-violet-500/15 px-1.5 text-[10px] tabular-nums text-violet-600 dark:text-violet-400 ${
                        collapsed ? "absolute" : "shrink-0"
                      }`}
                      style={
                        collapsed
                          ? { transform: "translate(10px, -8px)" }
                          : undefined
                      }
                    >
                      {activeImageJobCount}
                    </span>
                  )}
                  {id === "video" && videoJobActive && (
                    <Loader2
                      className={`h-3 w-3 animate-spin text-violet-500 ${
                        collapsed ? "absolute" : "shrink-0"
                      }`}
                      style={
                        collapsed
                          ? { transform: "translate(10px, -8px)" }
                          : undefined
                      }
                    />
                  )}
                </button>
              );
            })}
          </div>
          <div className="mt-auto px-1.5">
            <button
              type="button"
              onClick={() => setCollapsed((c) => !c)}
              title={collapsed ? "Expand navigation" : "Collapse navigation"}
              aria-label={
                collapsed ? "Expand navigation" : "Collapse navigation"
              }
              className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-xs text-muted-foreground hover:bg-muted/40 hover:text-foreground transition-colors ${
                collapsed ? "justify-center" : ""
              }`}
            >
              {collapsed ? (
                <ChevronsRight className="h-4 w-4 shrink-0" />
              ) : (
                <>
                  <ChevronsLeft className="h-4 w-4 shrink-0" />
                  <span>Collapse</span>
                </>
              )}
            </button>
          </div>
        </nav>

        {/* ── Content area ────────────────────────────────────────────────── */}
        <main className="flex-1 min-w-0 overflow-y-auto">
          <div className="mx-auto w-full max-w-5xl px-5 py-5">
            {active === "image" && (
              <ImageGenerateView onGoToModels={goToModels} />
            )}
            {active === "video" && (
              <VideoGenerateView onGoToModels={goToModels} />
            )}
            {active === "workflows" && <WorkflowSection />}
            {active === "library" && <MediaLibrarySection />}
            {active === "models" && (
              <ModelsView onGoToImage={goToImage} onGoToVideo={goToVideo} />
            )}
          </div>
        </main>
      </div>

      {/* ── Persistent queue footer (all entries) ────────────────────────── */}
      <QueueFooter onJump={setActive} />
    </div>
  );
}
