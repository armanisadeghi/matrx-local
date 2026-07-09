/**
 * VideoGenSection — the "Video" experience of the media-gen tab (all new).
 *
 * Hardware-gated: the engine's /video-gen/status decides availability.  The
 * package install is SHARED with image generation (same torch/diffusers set),
 * so when packages are missing we render the same one-click installer.
 *
 * Job state (activeJob, results, history) lives in MediaGenContext so a
 * long-running generation survives tab switches; polling is owned by the
 * useMediaGen hook (2s, only while a job is queued/running).
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
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { VideoGenModelInfo, VideoGenJob } from "@/lib/api";
import { ImageGenInstaller } from "./ImageGenInstaller";
import {
  StarRating,
  ErrorNote,
  InlineProgressBar,
  findModelDownload,
  formatGb,
  openExternalUrl,
} from "./shared";

// ── Resolution presets ────────────────────────────────────────────────────────

interface ResolutionPreset {
  id: string;
  label: string;
  width: number;
  height: number;
}

function resolutionPresetsFor(model: VideoGenModelInfo): ResolutionPreset[] {
  const presets: ResolutionPreset[] = [
    {
      id: "default",
      label: `Model default (${model.default_width}×${model.default_height})`,
      width: model.default_width,
      height: model.default_height,
    },
    { id: "landscape", label: "Landscape 832×480", width: 832, height: 480 },
    { id: "portrait", label: "Portrait 480×832", width: 480, height: 832 },
    { id: "wide", label: "Wide 1280×704", width: 1280, height: 704 },
  ];
  // De-dupe if the model default equals one of the fixed presets
  return presets.filter(
    (p, i) =>
      i === 0 ||
      p.width !== model.default_width ||
      p.height !== model.default_height,
  );
}

// ── Model card ────────────────────────────────────────────────────────────────

function VideoModelCard({
  model,
  isLoaded,
  anyLoading,
  onLoad,
  onDownload,
  onGenerate,
}: {
  model: VideoGenModelInfo;
  isLoaded: boolean;
  anyLoading: boolean;
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
          disabled={anyLoading || hardwareBlocked}
          onClick={() => (isLoaded ? onGenerate(model) : onLoad(model))}
        >
          {anyLoading ? (
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
}: {
  job: VideoGenJob;
  resultUrl: string | null;
  onPlay: (jobId: string) => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5">
      <div className="w-5 shrink-0">
        {job.status === "completed" ? (
          <CheckCircle2 className="h-4 w-4 text-green-500" />
        ) : job.status === "failed" ? (
          <AlertCircle className="h-4 w-4 text-destructive" />
        ) : job.status === "queued" || job.status === "running" ? (
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
              : job.status === "queued" || job.status === "running"
                ? `${Math.round(job.progress * 100)}%`
                : job.status}
        </p>
      </div>
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
    videoGenerating,
    videoGenError,
    activeJob,
    jobs,
    videoResults,
  } = state;
  const {
    refreshVideo,
    loadVideoModel,
    unloadVideoModel,
    downloadVideoModel,
    generateVideo,
    fetchVideoResult,
    clearActiveJob,
  } = actions;

  // Transient form state
  const [selectedModel, setSelectedModel] = useState<VideoGenModelInfo | null>(
    null,
  );
  const [showGenerateForm, setShowGenerateForm] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [negPrompt, setNegPrompt] = useState("");
  const [resolutionId, setResolutionId] = useState("default");
  const [durationSec, setDurationSec] = useState(3);
  const [fps, setFps] = useState(16);
  const [seedText, setSeedText] = useState("");
  const [sourceImage, setSourceImage] = useState<{
    name: string;
    base64: string;
    previewUrl: string;
  } | null>(null);
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
  const formModel = selectedModel ?? loadedModel;

  const presets = useMemo(
    () => (formModel ? resolutionPresetsFor(formModel) : []),
    [formModel],
  );

  const handleLoadModel = useCallback(
    async (model: VideoGenModelInfo) => {
      setLocalError(null);
      const result = await loadVideoModel(model.model_id);
      if (result.success) {
        setSelectedModel(model);
        setShowGenerateForm(true);
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet. Use the Download button first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadVideoModel],
  );

  const handleOpenGenerate = useCallback((model: VideoGenModelInfo) => {
    setSelectedModel(model);
    setShowGenerateForm(true);
  }, []);

  const handleUnload = useCallback(async () => {
    await unloadVideoModel();
    setSelectedModel(null);
    setShowGenerateForm(false);
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
        setSourceImage({
          name: file.name,
          base64: dataUrl.slice(comma + 1),
          previewUrl: dataUrl,
        });
      };
      reader.onerror = () =>
        setLocalError("Could not read the selected image.");
      reader.readAsDataURL(file);
    },
    [],
  );

  const handleGenerate = useCallback(async () => {
    if (!formModel || !prompt.trim()) return;
    setLocalError(null);
    const preset =
      presets.find((p) => p.id === resolutionId) ?? presets[0] ?? null;
    const numFrames = Math.min(
      Math.max(Math.round(durationSec * fps), fps),
      formModel.max_num_frames > 0 ? formModel.max_num_frames : Infinity,
    );
    const trimmedSeed = seedText.trim();
    const seed =
      trimmedSeed && Number.isFinite(Number(trimmedSeed))
        ? Math.floor(Number(trimmedSeed))
        : undefined;
    const result = await generateVideo({
      prompt: prompt.trim(),
      negative_prompt: negPrompt.trim() || undefined,
      model_id: formModel.model_id,
      width: preset?.width,
      height: preset?.height,
      num_frames: numFrames,
      fps,
      seed,
      image_base64: sourceImage?.base64,
    });
    if (result.ok) setPlaybackJobId(null);
  }, [
    formModel,
    prompt,
    negPrompt,
    presets,
    resolutionId,
    durationSec,
    fps,
    seedText,
    sourceImage?.base64,
    generateVideo,
  ]);

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
    <div className="space-y-6 pb-8">
      {/* ── Active job progress ─────────────────────────────────────────── */}
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

      {/* ── Generate form ───────────────────────────────────────────────── */}
      {showGenerateForm && formModel ? (
        <div className="space-y-4 max-w-xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowGenerateForm(false)}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                ← Models
              </button>
              <span className="text-sm font-semibold">{formModel.name}</span>
              <Badge variant="outline" className="text-[10px]">
                {formModel.provider}
              </Badge>
            </div>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void handleUnload()}
            >
              Unload model
            </Button>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Prompt</Label>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe the video you want to generate…"
              className="text-sm min-h-[80px] resize-none"
            />
          </div>

          {formModel.supports_negative_prompt && (
            <div className="space-y-1.5">
              <Label className="text-xs">
                Negative prompt{" "}
                <span className="text-muted-foreground">(what to avoid)</span>
              </Label>
              <Textarea
                value={negPrompt}
                onChange={(e) => setNegPrompt(e.target.value)}
                placeholder="blurry, low quality, distorted…"
                className="text-sm min-h-[52px] resize-none"
              />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Resolution</Label>
              <Select value={resolutionId} onValueChange={setResolutionId}>
                <SelectTrigger className="text-sm">
                  <SelectValue placeholder="Resolution" />
                </SelectTrigger>
                <SelectContent>
                  {presets.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Frame rate</Label>
              <Select
                value={String(fps)}
                onValueChange={(v) => setFps(Number(v))}
              >
                <SelectTrigger className="text-sm">
                  <SelectValue placeholder="FPS" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="16">16 fps</SelectItem>
                  <SelectItem value="24">24 fps</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">
                Duration{" "}
                <span className="text-muted-foreground">
                  ({durationSec}s ≈{" "}
                  {Math.min(
                    Math.round(durationSec * fps),
                    formModel.max_num_frames > 0
                      ? formModel.max_num_frames
                      : Number.MAX_SAFE_INTEGER,
                  )}{" "}
                  frames)
                </span>
              </Label>
              <Slider
                min={1}
                max={
                  formModel.max_num_frames > 0
                    ? Math.max(1, Math.floor(formModel.max_num_frames / fps))
                    : 10
                }
                step={1}
                value={[durationSec]}
                onValueChange={([v]) => setDurationSec(v)}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">
                Seed <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                value={seedText}
                onChange={(e) => setSeedText(e.target.value)}
                inputMode="numeric"
                placeholder="random"
                className="text-sm"
              />
            </div>
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
              {sourceImage ? (
                <div className="flex items-center gap-3 rounded-lg border px-3 py-2">
                  <img
                    src={sourceImage.previewUrl}
                    alt="Source"
                    className="h-12 w-12 rounded object-cover border"
                  />
                  <span className="text-xs truncate flex-1">
                    {sourceImage.name}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setSourceImage(null)}
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

          {genError && <ErrorNote message={genError} />}
          {jobIsActive && (
            <p className="text-[11px] text-muted-foreground">
              One video at a time — the current job must finish before starting
              another.
            </p>
          )}

          <Button
            className="w-full"
            disabled={videoGenerating || jobIsActive || !prompt.trim()}
            onClick={() => void handleGenerate()}
          >
            {videoGenerating || jobIsActive ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                {jobIsActive ? "Generating…" : "Starting…"}
              </>
            ) : (
              <>
                <Film className="h-4 w-4 mr-2" />
                Generate Video
              </>
            )}
          </Button>
        </div>
      ) : (
        /* ── Model picker ──────────────────────────────────────────────── */
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
          {genError && <ErrorNote message={genError} />}
          <div className="grid gap-3 sm:grid-cols-2">
            {videoModels.map((m) => (
              <VideoModelCard
                key={m.model_id}
                model={m}
                isLoaded={videoStatus?.loaded_model_id === m.model_id}
                anyLoading={videoModelLoading || !!videoStatus?.is_loading}
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
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
