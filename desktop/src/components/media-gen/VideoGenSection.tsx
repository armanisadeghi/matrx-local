/**
 * VideoGenSection — the "Video" experience of the media-gen tab.
 *
 * Structure mirrors ImageGenSection: two always-visible sub-tabs —
 * **Generate** (full-settings form + active job + results) and **Models**
 * (catalog).  ALL form state lives in MediaGenContext (videoForm), so
 * navigating away and back restores exactly where you were — critical while a
 * 10-minute job runs.
 *
 * Hardware-gated: the engine's /video-gen/status decides availability.  The
 * package install is SHARED with image generation (same torch/diffusers set),
 * so when packages are missing we render the same one-click installer.
 *
 * Settings doctrine: every parameter the engine accepts is visible — prompt,
 * negative prompt (or an explicit "not supported" note), steps, guidance,
 * width/height, frames, fps, seed, and the editable advanced-JSON with every
 * remaining pipeline kwarg.  Reset affordances per group + master reset.
 */

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Download,
  ExternalLink,
  Film,
  ImagePlus,
  Loader2,
  MonitorX,
  Play,
  RefreshCw,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type {
  VideoGenModelInfo,
  VideoGenJob,
  VideoGenRequest,
} from "@/lib/api";
import { ImageGenInstaller } from "./ImageGenInstaller";
import {
  StarRating,
  ErrorNote,
  InlineProgressBar,
  findModelDownload,
  formatGb,
  openExternalUrl,
  SubTabBar,
  SeedInput,
  ResetButton,
  NumberSliderField,
  DimensionPicker,
  dimensionError,
  NegativePromptField,
  AdvancedParamsEditor,
  ParamsErrorBanner,
  computeAdvancedOverrides,
  parseSeedText,
  randomSeed,
  CancelableGenerateButton,
  ModelLoadingNotice,
} from "./shared";
import type { SizePreset } from "./shared";

// ── Model card ────────────────────────────────────────────────────────────────

function VideoModelCard({
  model,
  isLoaded,
  isLoadingThis,
  anyLoadInFlight,
  onLoad,
  onDownload,
  onGenerate,
}: {
  model: VideoGenModelInfo;
  isLoaded: boolean;
  isLoadingThis: boolean;
  anyLoadInFlight: boolean;
  onLoad: (m: VideoGenModelInfo) => void;
  onDownload: (m: VideoGenModelInfo) => void;
  onGenerate: (m: VideoGenModelInfo) => void;
}) {
  const { downloads, openModal } = useDownloadManager();
  const dl = useMemo(
    () => findModelDownload(downloads, "video_gen", model.model_id),
    [downloads, model.model_id],
  );
  const downloading = dl?.status === "active" || dl?.status === "queued";
  const hardwareBlocked = !model.hardware_ok;

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
        {model.supports_image_to_video && (
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
          onClick={() => onDownload(model)}
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
          onClick={() => (isLoaded ? onGenerate(model) : onLoad(model))}
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

// ── Job row ───────────────────────────────────────────────────────────────────

function JobRow({
  job,
  resultUrl,
  onPlay,
  onCancel,
}: {
  job: VideoGenJob;
  resultUrl: string | null;
  onPlay: (jobId: string) => void;
  /** Cancel a queued/running job (now allowed by the engine). */
  onCancel: (jobId: string) => void;
}) {
  const active = job.status === "queued" || job.status === "running";
  const cancelling = active && !!job.cancel_requested;
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5">
      <div className="w-5 shrink-0">
        {job.status === "completed" ? (
          <CheckCircle2 className="h-4 w-4 text-green-500" />
        ) : job.status === "failed" ? (
          <AlertCircle className="h-4 w-4 text-destructive" />
        ) : active ? (
          <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
        ) : (
          <Film className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs" title={job.prompt}>
          {job.prompt || "(no prompt)"}
        </p>
        <p className="text-[10px] text-muted-foreground">
          {job.model_id || "—"} ·{" "}
          {job.status === "failed"
            ? (job.error ?? "failed")
            : job.status === "completed"
              ? `${job.elapsed_seconds.toFixed(0)}s`
              : cancelling
                ? "cancelling…"
                : active
                  ? `${Math.round(job.progress * 100)}%`
                  : job.status}
        </p>
      </div>
      {active &&
        (cancelling ? (
          <span
            className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0"
            title="Cancel requested — the current step is finishing (can take tens of seconds)"
          >
            <Loader2 className="h-3 w-3 animate-spin" />
            Cancelling…
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive shrink-0"
            onClick={() => onCancel(job.job_id)}
            title="Cancel this video job"
          >
            <X className="h-3.5 w-3.5 mr-1" />
            Cancel
          </Button>
        ))}
      {job.status === "completed" && (
        <Button size="sm" variant="outline" onClick={() => onPlay(job.job_id)}>
          <Play className="h-3.5 w-3.5 mr-1" />
          {resultUrl ? "Show" : "Play"}
        </Button>
      )}
    </div>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────

export function VideoGenSection() {
  const [state, actions] = useMediaGenApp();
  const {
    videoStatus,
    videoModels,
    videoStatusLoading,
    videoStatusError,
    videoModelLoading,
    loadingVideoModelId,
    videoGenerating,
    videoCancelling,
    videoLoadStartedAt,
    videoGenError,
    activeJob,
    jobs,
    videoResults,
    videoForm,
  } = state;
  const {
    refreshVideo,
    loadVideoModel,
    unloadVideoModel,
    downloadVideoModel,
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

  // Only transient bits stay local: load-error banner + playback selection.
  const [localError, setLocalError] = useState<string | null>(null);
  const [playbackJobId, setPlaybackJobId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // When a video_gen download completes, refresh the catalog so
  // `is_downloaded` flips.  Gated narrowly on the COUNT of completed entries.
  const { downloads } = useDownloadManager();
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

  const loadedModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoStatus?.loaded_model_id) ??
      null,
    [videoModels, videoStatus?.loaded_model_id],
  );
  // The model the Generate tab works with: the one the form defaults belong
  // to, else the currently loaded model.
  const formModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoForm.defaults?.modelId) ??
      loadedModel,
    [videoModels, videoForm.defaults?.modelId, loadedModel],
  );

  // If the Generate tab is open but the form defaults belong to a different
  // model (or none), fetch that model's full parameter schema.  Guarded so it
  // runs once per model change, never in a loop.
  useEffect(() => {
    if (videoForm.view !== "generate") return;
    if (videoForm.paramsLoading) return;
    if (!formModel) return;
    if (videoForm.defaults?.modelId === formModel.model_id) return;
    void prepareVideoGenerate(formModel);
  }, [
    videoForm.view,
    videoForm.paramsLoading,
    videoForm.defaults?.modelId,
    formModel,
    prepareVideoGenerate,
  ]);

  const handleLoadModel = useCallback(
    async (model: VideoGenModelInfo) => {
      setLocalError(null);
      const result = await loadVideoModel(model.model_id);
      if (result.success) {
        await prepareVideoGenerate(model);
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

  const handleOpenGenerate = useCallback(
    (model: VideoGenModelInfo) => {
      if (videoForm.defaults?.modelId === model.model_id) {
        // Same model — keep the user's tweaked settings, just switch tabs.
        setVideoForm({ view: "generate" });
      } else {
        void prepareVideoGenerate(model);
      }
    },
    [videoForm.defaults?.modelId, setVideoForm, prepareVideoGenerate],
  );

  const handleUnload = useCallback(async () => {
    await unloadVideoModel();
  }, [unloadVideoModel]);

  const handlePickImage = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      // allow re-selecting the same file later
      e.target.value = "";
      if (!file) return;
      // Guard before base64-encoding: an oversized image balloons the request
      // body (base64 is ~1.33x) and the memory used to hold it.
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
      computeAdvancedOverrides(
        videoForm.advancedText,
        defaults?.advanced ?? {},
      ),
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

  // ── Loading / error / gates ─────────────────────────────────────────────
  if (videoStatusLoading && !videoStatus) {
    return (
      <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="text-sm">Checking video generation status…</span>
      </div>
    );
  }

  if (videoStatusError) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
        <div className="flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
          <div className="text-sm space-y-1.5 min-w-0">
            <p className="font-medium text-foreground">
              Could not load video generation
            </p>
            <p className="text-muted-foreground text-xs leading-relaxed break-all">
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

  // Hardware not supported → hard gate, no install prompt.
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
          Image generation may still work — check the Images tab.
        </p>
      </div>
    );
  }

  // Hardware OK but packages missing → same shared installer flow as images.
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
      <SubTabBar
        tabs={[
          {
            id: "generate" as const,
            label: "Generate",
            badge: jobIsActive ? 1 : null,
          },
          { id: "models" as const, label: "Models" },
        ]}
        active={videoForm.view}
        onSelect={(view) => setVideoForm({ view })}
      />

      {/* ── Active job progress (always visible, both sub-tabs) ─────────── */}
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
              {jobIsActive &&
                (videoCancelling || activeJob.cancel_requested ? (
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Cancelling…
                  </span>
                ) : (
                  <button
                    onClick={() => void cancelVideoGeneration()}
                    className="text-destructive hover:underline"
                    title="Stop this video generation"
                  >
                    Cancel
                  </button>
                ))}
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

      {/* ── Playback ────────────────────────────────────────────────────── */}
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

      {videoForm.view === "models" ? (
        /* ── Models sub-tab ──────────────────────────────────────────────── */
        <div className="space-y-3">
          {videoStatus?.loaded_model_id && (
            <div className="flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 px-4 py-3">
              <div className="flex items-center gap-2 text-sm">
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                <span>
                  Model loaded:{" "}
                  <span className="font-medium">
                    {videoStatus.loaded_model_id}
                  </span>
                </span>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if (loadedModel) handleOpenGenerate(loadedModel);
                  }}
                >
                  Generate
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void handleUnload()}
                >
                  Unload
                </Button>
              </div>
            </div>
          )}

          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Film className="h-4 w-4 text-violet-500" />
            Select a video model
          </h3>
          {genError && (
            <ErrorNote message={genError} onDismiss={dismissGenError} />
          )}
          <ModelLoadingNotice
            loading={videoModelLoading || !!videoStatus?.is_loading}
            startedAt={videoLoadStartedAt}
            loadError={videoStatus?.load_error}
            what={loadingVideoModelId ?? "model"}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {videoModels.map((m) => (
              <VideoModelCard
                key={m.model_id}
                model={m}
                isLoaded={videoStatus?.loaded_model_id === m.model_id}
                isLoadingThis={loadingVideoModelId === m.model_id}
                anyLoadInFlight={videoModelLoading || !!videoStatus?.is_loading}
                onLoad={(model) => void handleLoadModel(model)}
                onDownload={(model) => void downloadVideoModel(model.model_id)}
                onGenerate={handleOpenGenerate}
              />
            ))}
            {videoModels.length === 0 && (
              <div className="col-span-full rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                No video models available yet.
              </div>
            )}
          </div>
        </div>
      ) : !formModel ? (
        /* ── Generate sub-tab, no model yet ─────────────────────────────── */
        <div className="rounded-xl border border-dashed px-5 py-10 flex flex-col items-center text-center gap-3">
          <Film className="h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm font-medium">No model selected</p>
          <p className="text-xs text-muted-foreground max-w-sm">
            Pick a model in the Models tab — its full settings will appear here.
          </p>
          <Button size="sm" onClick={() => setVideoForm({ view: "models" })}>
            Choose a model
          </Button>
        </div>
      ) : videoForm.paramsLoading || !defaults ? (
        <div className="flex items-center justify-center py-16 gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="text-sm">
            Loading {formModel.name}'s parameters…
          </span>
        </div>
      ) : (
        /* ── Generate sub-tab ───────────────────────────────────────────── */
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
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void handleUnload()}
              >
                Unload model
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
              className="text-sm min-h-[80px] max-h-[320px] resize-y"
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
            ≈ {approxSeconds.toFixed(1)}s of video ({videoForm.numFrames} frames
            at {videoForm.fps} fps)
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

      {/* ── Recent jobs ─────────────────────────────────────────────────── */}
      {jobs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Recent videos</h3>
          <div className="space-y-2">
            {jobs.map((j) => (
              <JobRow
                key={j.job_id}
                job={j}
                resultUrl={videoResults[j.job_id] ?? null}
                onPlay={handlePlay}
                onCancel={(id) => void cancelVideoJob(id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
