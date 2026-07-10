/**
 * VariantFocus — "Focus flow" bake-off variant.
 *
 * A calm, centered, progressive-disclosure experience (the anti-dashboard):
 * one column, one narrative. Segmented control (Image | Video | Workflow) up
 * top, then the flow reads top-to-bottom as steps — Model → Prompt →
 * Essentials → All settings → Generate — with results appearing directly
 * beneath as a vertical session feed (newest on top). History beyond the
 * session lives behind a quiet "Open library" link (full-height dialog).
 *
 * ALL form values read/write the shared context-backed form state
 * (imageForm / videoForm via useMediaGenApp actions) so switching layout
 * variants never loses work. Local useState is used ONLY for reveals and
 * dialog-open flags, per the repo's React rules.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  Film,
  Image as ImageIcon,
  ImagePlus,
  Library,
  ListPlus,
  Loader2,
  Minus,
  MonitorX,
  Play,
  Plus,
  RefreshCw,
  Sparkles,
  Workflow as WorkflowIcon,
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type {
  ImageGenJob,
  ImageGenModelInfo,
  VideoGenJob,
  VideoGenModelInfo,
} from "@/lib/api";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";
import { ImageGenInstaller } from "@/components/media-gen/ImageGenInstaller";
import { WorkflowSection } from "@/components/media-gen/WorkflowSection";
import { MediaLibrarySection } from "@/components/media-gen/MediaLibrarySection";
import {
  AdvancedParamsEditor,
  DimensionPicker,
  ErrorNote,
  GeneratedImageView,
  InlineProgressBar,
  NumberSliderField,
  ParamsErrorBanner,
  ResetButton,
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

// ── Layout atoms ─────────────────────────────────────────────────────────────

type Segment = "image" | "video" | "workflow";

const SEGMENTS: { id: Segment; label: string; Icon: typeof ImageIcon }[] = [
  { id: "image", label: "Image", Icon: ImageIcon },
  { id: "video", label: "Video", Icon: Film },
  { id: "workflow", label: "Workflow", Icon: WorkflowIcon },
];

function SegmentedControl({
  value,
  onChange,
  videoBusy,
}: {
  value: Segment;
  onChange: (s: Segment) => void;
  videoBusy: boolean;
}) {
  return (
    <div
      role="tablist"
      aria-label="Media generation mode"
      className="inline-flex items-center gap-0.5 rounded-full border bg-muted/40 p-1"
    >
      {SEGMENTS.map(({ id, label, Icon }) => {
        const active = id === value;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(id)}
            className={`relative flex items-center gap-1.5 rounded-full px-4 py-1.5 text-xs font-medium transition-all ${
              active
                ? "bg-background text-foreground shadow-sm ring-1 ring-border"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
            {id === "video" && videoBusy && (
              <Loader2 className="h-3 w-3 animate-spin text-violet-500" />
            )}
          </button>
        );
      })}
    </div>
  );
}

/** Numbered step header — the quiet backbone of the flow. */
function StepHeading({
  step,
  title,
  aside,
}: {
  step: number;
  title: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex items-baseline gap-2.5">
        <span className="text-[11px] font-medium tabular-nums text-muted-foreground/60">
          {step}
        </span>
        <h3 className="text-[13px] font-semibold tracking-tight">{title}</h3>
      </div>
      {aside}
    </div>
  );
}

/** Calm full-column state (loading / errors / empty). */
function CalmState({
  icon,
  title,
  body,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  body?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed px-6 py-14 text-center">
      <div className="rounded-xl bg-muted/60 p-3 text-muted-foreground">
        {icon}
      </div>
      <p className="text-sm font-medium">{title}</p>
      {body && (
        <div className="max-w-sm text-xs leading-relaxed text-muted-foreground">
          {body}
        </div>
      )}
      {children}
    </div>
  );
}

// ── Model chooser (compact card + inline expandable list) ───────────────────

interface ModelRowShape {
  model_id: string;
  name: string;
  provider: string;
  description: string;
  quality_rating: number;
  speed_rating: number;
  download_size_gb: number;
  is_downloaded: boolean;
  requires_hf_token: boolean;
  hardware_ok: boolean;
  hardware_reason: string | null;
  model_card_url: string;
}

function ModelRow({
  model,
  category,
  isCurrent,
  busy,
  onSelect,
  onDownload,
}: {
  model: ModelRowShape;
  category: "image_gen" | "video_gen";
  isCurrent: boolean;
  busy: boolean;
  onSelect: () => void;
  onDownload: () => void;
}) {
  const { downloads, openModal } = useDownloadManager();
  const dl = useMemo(
    () => findModelDownload(downloads, category, model.model_id),
    [downloads, category, model.model_id],
  );
  const downloading = dl?.status === "active" || dl?.status === "queued";
  const blocked = !model.hardware_ok;

  return (
    <div
      className={`space-y-2 px-4 py-3 transition-colors ${
        isCurrent ? "bg-violet-500/[0.06]" : blocked ? "opacity-60" : "hover:bg-muted/30"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void openExternalUrl(model.model_card_url)}
              className="truncate text-left text-[13px] font-medium hover:underline"
              title={`${model.name} — open model card`}
            >
              {model.name}
            </button>
            {isCurrent && (
              <span className="rounded-full bg-violet-500/15 px-2 py-px text-[10px] font-medium text-violet-600 dark:text-violet-400">
                current
              </span>
            )}
          </div>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
            <span>{model.provider}</span>
            <span aria-hidden>·</span>
            <span>{formatGb(model.download_size_gb)}</span>
            <span className="flex items-center gap-1">
              Quality <StarRating value={model.quality_rating} />
            </span>
            <span className="flex items-center gap-1">
              Speed <StarRating value={model.speed_rating} />
            </span>
            {model.requires_hf_token && (
              <span className="rounded bg-amber-500/15 px-1.5 py-px text-[10px] text-amber-600 dark:text-amber-400">
                HF token
              </span>
            )}
          </p>
        </div>
        <div className="shrink-0 pt-0.5">
          {downloading && dl ? (
            <button
              type="button"
              onClick={openModal}
              className="text-[11px] tabular-nums text-violet-500 hover:underline"
            >
              {Math.round(dl.percent)}%
            </button>
          ) : !model.is_downloaded ? (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2.5 text-xs text-muted-foreground hover:text-foreground"
              disabled={blocked}
              onClick={onDownload}
            >
              <Download className="mr-1.5 h-3 w-3" />
              Get
            </Button>
          ) : (
            <Button
              size="sm"
              variant={isCurrent ? "secondary" : "outline"}
              className="h-7 px-3 text-xs"
              disabled={busy || blocked || isCurrent}
              onClick={onSelect}
            >
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : isCurrent ? "In use" : "Use"}
            </Button>
          )}
        </div>
      </div>
      {downloading && dl && (
        <InlineProgressBar
          percent={dl.percent}
          indeterminate={dl.percent <= 0 && dl.bytes_done <= 0}
        />
      )}
      {blocked && (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <AlertCircle className="h-3 w-3 shrink-0 text-amber-500" />
          {model.hardware_reason ?? "Not supported by this hardware."}
        </p>
      )}
    </div>
  );
}

function ModelStep<M extends ModelRowShape>({
  models,
  current,
  loadedId,
  busy,
  onSelect,
  onDownload,
  emptyLabel,
  category,
}: {
  models: M[];
  current: M | null;
  loadedId: string | null;
  busy: boolean;
  onSelect: (m: M) => void;
  onDownload: (m: M) => void;
  emptyLabel: string;
  category: "image_gen" | "video_gen";
}) {
  // Reveal-only local state (allowed): the inline model list.
  const [open, setOpen] = useState(!current);

  return (
    <section className="space-y-2.5">
      <StepHeading
        step={1}
        title="Model"
        aside={
          current ? (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              {open ? "Done" : "Change"}
            </button>
          ) : undefined
        }
      />
      <div className="overflow-hidden rounded-xl border bg-card">
        {current ? (
          <div className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <p className="truncate text-sm font-medium">{current.name}</p>
                {loadedId === current.model_id ? (
                  <span className="flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-px text-[10px] font-medium text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-2.5 w-2.5" />
                    loaded
                  </span>
                ) : (
                  <span className="rounded-full bg-muted px-2 py-px text-[10px] text-muted-foreground">
                    loads on generate
                  </span>
                )}
              </div>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
                <span>{current.provider}</span>
                <span aria-hidden>·</span>
                <span>{formatGb(current.download_size_gb)}</span>
                <span className="flex items-center gap-1">
                  Quality <StarRating value={current.quality_rating} />
                </span>
                <span className="flex items-center gap-1">
                  Speed <StarRating value={current.speed_rating} />
                </span>
              </p>
            </div>
            <button
              type="button"
              onClick={() => void openExternalUrl(current.model_card_url)}
              className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
              aria-label={`Open model card for ${current.name}`}
              title="Open model card"
            >
              <ArrowUpRight className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <div className="px-4 py-3 text-xs text-muted-foreground">
            Choose a model to begin — downloaded models are ready instantly.
          </div>
        )}
        {(open || !current) && (
          <div className="divide-y border-t">
            {models.length === 0 ? (
              <p className="px-4 py-6 text-center text-xs text-muted-foreground">
                {emptyLabel}
              </p>
            ) : (
              models.map((m) => (
                <ModelRow
                  key={m.model_id}
                  model={m}
                  category={category}
                  isCurrent={current?.model_id === m.model_id}
                  busy={busy}
                  onSelect={() => {
                    onSelect(m);
                    setOpen(false);
                  }}
                  onDownload={() => onDownload(m)}
                />
              ))
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// ── Negative-prompt reveal ───────────────────────────────────────────────────

function NegativePromptReveal({
  supported,
  value,
  onChange,
}: {
  supported: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  // Reveal-only local state (allowed). Starts open when a value already exists
  // so context-carried work is never hidden.
  const [open, setOpen] = useState(() => value.trim().length > 0);

  if (!supported) {
    return (
      <p className="text-[11px] text-muted-foreground">
        This model doesn't support negative prompts — it would ignore one, so
        the field isn't shown.
      </p>
    );
  }
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <Plus className="h-3 w-3" />
        Negative prompt
      </button>
    );
  }
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">
          Negative prompt — what to avoid
        </span>
        {value.trim().length === 0 && (
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <Minus className="h-3 w-3" />
            Hide
          </button>
        )}
      </div>
      <Textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="blurry, low quality, deformed…"
        className="min-h-[56px] resize-none bg-card text-sm"
      />
    </div>
  );
}

// ── All-settings collapsible ─────────────────────────────────────────────────

function AllSettings({
  children,
  onResetAll,
}: {
  children: React.ReactNode;
  onResetAll: () => void;
}) {
  const [open, setOpen] = useState(false); // reveal-only local state
  return (
    <section className="overflow-hidden rounded-xl border bg-card/50">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left"
      >
        <span className="flex items-center gap-1.5 text-xs font-medium">
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          )}
          All settings
          <span className="font-normal text-muted-foreground">
            — every remaining pipeline parameter
          </span>
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-t px-4 py-3">
          {children}
          <div className="flex justify-end">
            <ResetButton onClick={onResetAll} label="Reset everything to model defaults" />
          </div>
        </div>
      )}
    </section>
  );
}

// ── Session feed cards ───────────────────────────────────────────────────────

function ImageJobFeedCard({
  job,
  thumbUrl,
  onCancel,
  onReuseSeed,
}: {
  job: ImageGenJob;
  thumbUrl: string | null;
  onCancel: () => void;
  onReuseSeed: (seed: number) => void;
}) {
  const active = job.status === "queued" || job.status === "running";

  if (job.status === "completed" && thumbUrl) {
    return (
      <figure className="overflow-hidden rounded-xl border bg-card">
        <img src={thumbUrl} alt={job.prompt || "Generated image"} className="w-full object-contain" />
        <figcaption className="space-y-1.5 px-4 py-3">
          <p className="text-xs leading-relaxed text-muted-foreground" title={job.prompt}>
            {job.prompt || "(no prompt)"}
          </p>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            {typeof job.seed === "number" && (
              <SeedChip seed={job.seed} onReuse={onReuseSeed} />
            )}
            {typeof job.elapsed_seconds === "number" && (
              <span className="tabular-nums">{job.elapsed_seconds.toFixed(1)}s</span>
            )}
            <span className="flex-1" />
            <button
              type="button"
              onClick={onCancel}
              className="text-muted-foreground/70 transition-colors hover:text-foreground"
              title="Remove from the queue"
            >
              Remove
            </button>
          </div>
        </figcaption>
      </figure>
    );
  }

  // Slim card: queued / running / failed / cancelled / completed-sans-thumb.
  return (
    <div className="space-y-2 rounded-xl border bg-card px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="shrink-0">
          {active ? (
            <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
          ) : job.status === "failed" ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : job.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          ) : (
            <X className="h-4 w-4 text-muted-foreground" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs" title={job.prompt}>
            {job.prompt || "(no prompt)"}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {job.status === "failed" && job.error ? job.error : job.status}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {typeof job.seed === "number" && !active && (
            <SeedChip seed={job.seed} onReuse={onReuseSeed} />
          )}
          <button
            type="button"
            onClick={onCancel}
            className="text-muted-foreground transition-colors hover:text-foreground"
            aria-label={active ? "Cancel job" : "Remove job"}
            title={active ? "Cancel this job" : "Remove from the queue"}
          >
            <X className="h-3.5 w-3.5" />
          </button>
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

function VideoJobFeedCard({
  job,
  isActive,
  resultUrl,
  onPlay,
  onDismiss,
}: {
  job: VideoGenJob;
  isActive: boolean;
  resultUrl: string | null;
  onPlay: () => void;
  onDismiss?: () => void;
}) {
  const running = job.status === "queued" || job.status === "running";
  return (
    <div className="space-y-2 rounded-xl border bg-card px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="shrink-0">
          {running ? (
            <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
          ) : job.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          ) : job.status === "failed" ? (
            <AlertCircle className="h-4 w-4 text-destructive" />
          ) : (
            <Film className="h-4 w-4 text-muted-foreground" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs" title={job.prompt}>
            {job.prompt || "(no prompt)"}
          </p>
          <p className="text-[10px] text-muted-foreground tabular-nums">
            {running
              ? `${Math.round(job.progress * 100)}%${
                  job.total_steps > 0
                    ? ` · step ${job.current_step}/${job.total_steps}`
                    : ""
                }`
              : job.status === "failed"
                ? (job.error ?? "failed")
                : job.status === "completed"
                  ? `${job.elapsed_seconds.toFixed(0)}s`
                  : job.status}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {running && isActive && (
            <span className="flex items-center gap-1 text-[11px] tabular-nums text-muted-foreground">
              <Clock className="h-3 w-3" />
              {Math.round(job.elapsed_seconds)}s
            </span>
          )}
          {job.status === "completed" && (
            <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs" onClick={onPlay}>
              <Play className="mr-1 h-3 w-3" />
              {resultUrl ? "Show" : "Play"}
            </Button>
          )}
          {!running && isActive && onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="text-muted-foreground transition-colors hover:text-foreground"
              aria-label="Dismiss job"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
      {running && (
        <InlineProgressBar
          percent={job.progress * 100}
          indeterminate={job.status === "queued"}
        />
      )}
    </div>
  );
}

// ── Image flow ───────────────────────────────────────────────────────────────

function ImageFocusFlow() {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading,
    imageGenerating,
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
    loadImageModel,
    downloadImageModel,
    generateImage,
    clearImageResult,
    clearImageGenError,
    setImageForm,
    prepareImageGenerate,
    resetImageAdvanced,
    resetImageAll,
    enqueueImageJob,
    cancelImageJob,
  } = actions;

  const [localError, setLocalError] = useState<string | null>(null);

  // Flip `is_downloaded` when an image_gen download completes — gated narrowly
  // on the COUNT of completed entries, never the downloads array itself.
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

  const generateModelId =
    imageForm.defaults?.modelId ??
    selectedImageModelId ??
    imageStatus?.loaded_model_id ??
    null;
  const currentModel = useMemo(
    () => imageModels.find((m) => m.model_id === generateModelId) ?? null,
    [imageModels, generateModelId],
  );

  // A model is in play but the form defaults belong to another (or none) —
  // fetch its full parameter schema. Guarded to run once per model change.
  useEffect(() => {
    if (imageForm.paramsLoading) return;
    if (!currentModel) return;
    if (imageForm.defaults?.modelId === currentModel.model_id) return;
    void prepareImageGenerate(currentModel);
  }, [
    imageForm.paramsLoading,
    imageForm.defaults?.modelId,
    currentModel,
    prepareImageGenerate,
  ]);

  const handleSelectModel = useCallback(
    async (model: ImageGenModelInfo) => {
      setLocalError(null);
      const result = await loadImageModel(model.model_id);
      if (result.success) {
        await prepareImageGenerate(model);
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet — use Get to download it first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadImageModel, prepareImageGenerate],
  );

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
    await enqueueImageJob(input);
  }, [buildInput, enqueueImageJob]);

  const reuseSeed = useCallback(
    (seed: number) => setImageForm({ seedText: String(seed) }),
    [setImageForm],
  );

  const genError = imageGenError ?? localError;
  const dismissGenError = useCallback(() => {
    setLocalError(null);
    clearImageGenError();
  }, [clearImageGenError]);

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

  // ── Not-ready states, kept as calm as the happy path ────────────────────
  if (imageStatusLoading && !imageStatus) {
    return (
      <CalmState
        icon={<Loader2 className="h-5 w-5 animate-spin" />}
        title="Checking image generation…"
      />
    );
  }
  if (imageStatusError) {
    return (
      <CalmState
        icon={<AlertCircle className="h-5 w-5" />}
        title="Image generation isn't reachable"
        body={<span className="break-all">{imageStatusError}</span>}
      >
        <Button size="sm" variant="outline" onClick={() => void refreshImage()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          Try again
        </Button>
      </CalmState>
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
  if (imageStatus?.packages_outdated) {
    return (
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/40 bg-amber-500/5 px-4 py-3">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <p className="text-xs leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground">
              Update AI packages.
            </span>{" "}
            Your on-device packages
            {imageStatus.packages_version
              ? ` (diffusers ${imageStatus.packages_version})`
              : ""}{" "}
            are older than required for the latest models.
          </p>
        </div>
        <ImageGenInstaller
          models={[]}
          upgrade
          onInstallComplete={() => void refreshImage()}
        />
      </div>
    );
  }

  const paramsReady = !!defaults && !imageForm.paramsLoading;
  const feedHasContent =
    !!imageResult || imageGenerating || imageJobs.length > 0 || !!imageJobsError;

  return (
    <div className="space-y-8">
      {/* 1 · Model */}
      <ModelStep
        models={imageModels}
        current={currentModel}
        loadedId={imageStatus?.loaded_model_id ?? null}
        busy={imageModelLoading || !!imageStatus?.is_loading}
        onSelect={(m) => void handleSelectModel(m)}
        onDownload={(m) => void downloadImageModel(m.model_id)}
        emptyLabel="No image models available yet."
        category="image_gen"
      />

      {imageForm.paramsError && currentModel && (
        <ParamsErrorBanner
          error={imageForm.paramsError}
          onRetry={() => void prepareImageGenerate(currentModel)}
        />
      )}

      {/* 2 · Prompt */}
      <section className="space-y-2.5">
        <StepHeading step={2} title="Prompt" />
        <Textarea
          value={imageForm.prompt}
          onChange={(e) => setImageForm({ prompt: e.target.value })}
          placeholder="Describe the image you want to see…"
          className="min-h-[110px] resize-none rounded-xl bg-card px-4 py-3 text-[15px] leading-relaxed shadow-sm"
        />
        <NegativePromptReveal
          supported={defaults?.supportsNegativePrompt ?? true}
          value={imageForm.negativePrompt}
          onChange={(negativePrompt) => setImageForm({ negativePrompt })}
        />
      </section>

      {/* 3 · Essentials */}
      <section className="space-y-3">
        <StepHeading
          step={3}
          title="Essentials"
          aside={
            imageForm.paramsLoading ? (
              <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                loading model defaults…
              </span>
            ) : undefined
          }
        />
        <div className="space-y-4 rounded-xl border bg-card px-4 py-4">
          <DimensionPicker
            width={imageForm.width}
            height={imageForm.height}
            onChange={(w, h) => setImageForm({ width: w, height: h })}
            presets={sizePresets}
            disabled={!paramsReady}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberSliderField
              label="Steps"
              value={imageForm.steps}
              onChange={(v) => setImageForm({ steps: v })}
              min={1}
              max={currentModel?.pipeline_type.startsWith("flux") ? 50 : 100}
              step={1}
              defaultValue={defaults?.steps ?? null}
              disabled={!paramsReady}
            />
            <NumberSliderField
              label="Guidance"
              value={imageForm.guidance}
              onChange={(v) => setImageForm({ guidance: v })}
              min={0}
              max={20}
              step={0.5}
              defaultValue={defaults?.guidance ?? null}
              disabled={!paramsReady}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">
              Seed{" "}
              <span className="font-normal text-muted-foreground">
                (blank = random — the used seed is shown on every result)
              </span>
            </Label>
            <SeedInput
              value={imageForm.seedText}
              onChange={(seedText) => setImageForm({ seedText })}
              disabled={!paramsReady}
            />
          </div>
        </div>
      </section>

      {/* 4 · All settings */}
      <AllSettings onResetAll={resetImageAll}>
        {defaults ? (
          <AdvancedParamsEditor
            defaults={defaults.advanced}
            text={imageForm.advancedText}
            onChange={(advancedText) => setImageForm({ advancedText })}
            onReset={resetImageAdvanced}
          />
        ) : (
          <p className="text-xs text-muted-foreground">
            Advanced parameters appear once a model is selected.
          </p>
        )}
      </AllSettings>

      {/* 5 · Generate */}
      <section className="space-y-3">
        {genError && <ErrorNote message={genError} onDismiss={dismissGenError} />}
        {!currentModel && (
          <p className="text-center text-[11px] text-muted-foreground">
            Choose a model above to enable generation.
          </p>
        )}
        <div className="flex gap-2">
          <Button
            size="lg"
            className="h-12 flex-1 rounded-xl text-sm font-semibold"
            disabled={imageGenerating || formInvalid}
            onClick={() => void handleGenerate()}
          >
            {imageGenerating ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Generating…
              </>
            ) : (
              <>
                <Sparkles className="mr-2 h-4 w-4" />
                Generate
              </>
            )}
          </Button>
          <Button
            size="lg"
            variant="outline"
            className="h-12 rounded-xl"
            disabled={formInvalid}
            onClick={() => void handleEnqueue()}
            title="Queue this generation and keep writing the next prompt"
          >
            <ListPlus className="mr-2 h-4 w-4" />
            Queue
          </Button>
        </div>
      </section>

      {/* Session feed */}
      {feedHasContent && (
        <section className="space-y-3 border-t pt-6">
          <h3 className="text-[13px] font-semibold tracking-tight">
            This session
          </h3>
          {imageJobsError && <ErrorNote message={imageJobsError} />}
          {imageGenerating && (
            <div className="flex items-center gap-3 rounded-xl border bg-card px-4 py-3">
              <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs" title={imageForm.prompt}>
                  {imageForm.prompt.trim() || "Generating image…"}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  Generating — typically 5–60 seconds on-device
                </p>
              </div>
            </div>
          )}
          {imageResult && (
            <div className="rounded-xl border bg-card p-3">
              <GeneratedImageView
                result={imageResult}
                onClear={clearImageResult}
                onReuseSeed={reuseSeed}
              />
            </div>
          )}
          {imageJobs.map((j) => (
            <ImageJobFeedCard
              key={j.job_id}
              job={j}
              thumbUrl={imageJobThumbs[j.job_id] ?? null}
              onCancel={() => void cancelImageJob(j.job_id)}
              onReuseSeed={reuseSeed}
            />
          ))}
        </section>
      )}
    </div>
  );
}

// ── Video flow ───────────────────────────────────────────────────────────────

function VideoFocusFlow() {
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
    videoForm,
  } = state;
  const {
    refreshVideo,
    loadVideoModel,
    downloadVideoModel,
    generateVideo,
    fetchVideoResult,
    clearActiveJob,
    clearVideoGenError,
    setVideoForm,
    prepareVideoGenerate,
    resetVideoAdvanced,
    resetVideoAll,
  } = actions;

  const [localError, setLocalError] = useState<string | null>(null);
  const [playbackJobId, setPlaybackJobId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
  const currentModel = useMemo(
    () =>
      videoModels.find((m) => m.model_id === videoForm.defaults?.modelId) ??
      loadedModel,
    [videoModels, videoForm.defaults?.modelId, loadedModel],
  );

  useEffect(() => {
    if (videoForm.paramsLoading) return;
    if (!currentModel) return;
    if (videoForm.defaults?.modelId === currentModel.model_id) return;
    void prepareVideoGenerate(currentModel);
  }, [
    videoForm.paramsLoading,
    videoForm.defaults?.modelId,
    currentModel,
    prepareVideoGenerate,
  ]);

  const handleSelectModel = useCallback(
    async (model: VideoGenModelInfo) => {
      setLocalError(null);
      const result = await loadVideoModel(model.model_id);
      if (result.success) {
        await prepareVideoGenerate(model);
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet — use Get to download it first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadVideoModel, prepareVideoGenerate],
  );

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
      reader.onerror = () => setLocalError("Could not read the selected image.");
      reader.readAsDataURL(file);
    },
    [setVideoForm],
  );

  const defaults = videoForm.defaults;
  const advanced = useMemo(
    () =>
      computeAdvancedOverrides(videoForm.advancedText, defaults?.advanced ?? {}),
    [videoForm.advancedText, defaults?.advanced],
  );
  const dimError = dimensionError(videoForm.width, videoForm.height);
  const formInvalid =
    !videoForm.prompt.trim() || !defaults || !advanced.ok || dimError !== null;
  const jobIsActive =
    activeJob?.status === "queued" || activeJob?.status === "running";

  const handleGenerate = useCallback(async () => {
    const d = videoForm.defaults;
    if (!d || !videoForm.prompt.trim()) return;
    const adv = computeAdvancedOverrides(videoForm.advancedText, d.advanced);
    if (!adv.ok) return;
    setLocalError(null);
    const seed = parseSeedText(videoForm.seedText) ?? randomSeed();
    const result = await generateVideo({
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
    });
    if (result.ok) setPlaybackJobId(null);
  }, [videoForm, generateVideo]);

  const handlePlay = useCallback(
    (jobId: string) => {
      setPlaybackJobId(jobId);
      void fetchVideoResult(jobId);
    },
    [fetchVideoResult],
  );

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
    currentModel && currentModel.max_num_frames > 0
      ? currentModel.max_num_frames
      : 200;
  const approxSeconds =
    videoForm.fps > 0 ? videoForm.numFrames / videoForm.fps : 0;

  // ── Not-ready states ─────────────────────────────────────────────────────
  if (videoStatusLoading && !videoStatus) {
    return (
      <CalmState
        icon={<Loader2 className="h-5 w-5 animate-spin" />}
        title="Checking video generation…"
      />
    );
  }
  if (videoStatusError) {
    return (
      <CalmState
        icon={<AlertCircle className="h-5 w-5" />}
        title="Video generation isn't reachable"
        body={<span className="break-all">{videoStatusError}</span>}
      >
        <Button size="sm" variant="outline" onClick={() => void refreshVideo()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          Try again
        </Button>
      </CalmState>
    );
  }
  if (videoStatus && !videoStatus.hardware_supported) {
    return (
      <CalmState
        icon={<MonitorX className="h-5 w-5" />}
        title="Video generation isn't available on this computer"
        body={
          <>
            {videoStatus.hardware_reason ??
              videoStatus.unavailable_reason ??
              "Video generation requires Apple Silicon with 16GB+ memory or an NVIDIA GPU with 8GB+ VRAM."}{" "}
            Image generation may still work — try the Image tab.
          </>
        }
      />
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

  const paramsReady = !!defaults && !videoForm.paramsLoading;
  const feedJobs = jobs.filter((j) => j.job_id !== activeJob?.job_id);
  const feedHasContent = !!activeJob || !!playbackUrl || feedJobs.length > 0;

  return (
    <div className="space-y-8">
      {/* 1 · Model */}
      <ModelStep
        models={videoModels}
        current={currentModel}
        loadedId={videoStatus?.loaded_model_id ?? null}
        busy={videoModelLoading || !!videoStatus?.is_loading}
        onSelect={(m) => void handleSelectModel(m)}
        onDownload={(m) => void downloadVideoModel(m.model_id)}
        emptyLabel="No video models available yet."
        category="video_gen"
      />

      {videoForm.paramsError && currentModel && (
        <ParamsErrorBanner
          error={videoForm.paramsError}
          onRetry={() => void prepareVideoGenerate(currentModel)}
        />
      )}

      {/* 2 · Prompt */}
      <section className="space-y-2.5">
        <StepHeading step={2} title="Prompt" />
        <Textarea
          value={videoForm.prompt}
          onChange={(e) => setVideoForm({ prompt: e.target.value })}
          placeholder="Describe the video you want to see…"
          className="min-h-[110px] resize-none rounded-xl bg-card px-4 py-3 text-[15px] leading-relaxed shadow-sm"
        />
        <NegativePromptReveal
          supported={defaults?.supportsNegativePrompt ?? true}
          value={videoForm.negativePrompt}
          onChange={(negativePrompt) => setVideoForm({ negativePrompt })}
        />
        {currentModel?.supports_image_to_video && (
          <div className="pt-1">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              onChange={handlePickImage}
            />
            {videoForm.sourceImage ? (
              <div className="flex items-center gap-3 rounded-xl border bg-card px-3 py-2">
                <img
                  src={videoForm.sourceImage.previewUrl}
                  alt="Source"
                  className="h-11 w-11 rounded-lg border object-cover"
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs">{videoForm.sourceImage.name}</p>
                  <p className="text-[10px] text-muted-foreground">
                    Source image — this video animates it
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setVideoForm({ sourceImage: null })}
                  className="text-muted-foreground transition-colors hover:text-foreground"
                  aria-label="Remove source image"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
              >
                <ImagePlus className="h-3 w-3" />
                Add a source image (optional — animates the image)
              </button>
            )}
          </div>
        )}
      </section>

      {/* 3 · Essentials */}
      <section className="space-y-3">
        <StepHeading
          step={3}
          title="Essentials"
          aside={
            videoForm.paramsLoading ? (
              <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                loading model defaults…
              </span>
            ) : undefined
          }
        />
        <div className="space-y-4 rounded-xl border bg-card px-4 py-4">
          <DimensionPicker
            width={videoForm.width}
            height={videoForm.height}
            onChange={(w, h) => setVideoForm({ width: w, height: h })}
            presets={sizePresets}
            disabled={!paramsReady}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <NumberSliderField
              label="Steps"
              value={videoForm.steps}
              onChange={(v) => setVideoForm({ steps: v })}
              min={1}
              max={100}
              step={1}
              defaultValue={defaults?.steps ?? null}
              disabled={!paramsReady}
            />
            <NumberSliderField
              label="Guidance"
              value={videoForm.guidance}
              onChange={(v) => setVideoForm({ guidance: v })}
              min={0}
              max={20}
              step={0.5}
              defaultValue={defaults?.guidance ?? null}
              disabled={!paramsReady}
            />
            <NumberSliderField
              label="Frames"
              value={videoForm.numFrames}
              onChange={(v) => setVideoForm({ numFrames: v })}
              min={1}
              max={maxFrames}
              step={1}
              defaultValue={defaults?.numFrames ?? null}
              disabled={!paramsReady}
            />
            <NumberSliderField
              label="FPS"
              value={videoForm.fps}
              onChange={(v) => setVideoForm({ fps: v })}
              min={1}
              max={60}
              step={1}
              defaultValue={defaults?.fps ?? null}
              disabled={!paramsReady}
            />
          </div>
          <p className="text-[11px] tabular-nums text-muted-foreground">
            ≈ {approxSeconds.toFixed(1)}s of video ({videoForm.numFrames} frames
            at {videoForm.fps} fps)
          </p>
          <div className="space-y-1.5">
            <Label className="text-xs">
              Seed{" "}
              <span className="font-normal text-muted-foreground">
                (blank = random)
              </span>
            </Label>
            <SeedInput
              value={videoForm.seedText}
              onChange={(seedText) => setVideoForm({ seedText })}
              disabled={!paramsReady}
            />
          </div>
        </div>
      </section>

      {/* 4 · All settings */}
      <AllSettings onResetAll={resetVideoAll}>
        {defaults ? (
          <AdvancedParamsEditor
            defaults={defaults.advanced}
            text={videoForm.advancedText}
            onChange={(advancedText) => setVideoForm({ advancedText })}
            onReset={resetVideoAdvanced}
          />
        ) : (
          <p className="text-xs text-muted-foreground">
            Advanced parameters appear once a model is selected.
          </p>
        )}
      </AllSettings>

      {/* 5 · Generate */}
      <section className="space-y-3">
        {genError && <ErrorNote message={genError} onDismiss={dismissGenError} />}
        {jobIsActive && (
          <p className="text-center text-[11px] text-muted-foreground">
            One video at a time — the current job must finish before starting
            another.
          </p>
        )}
        {!currentModel && (
          <p className="text-center text-[11px] text-muted-foreground">
            Choose a model above to enable generation.
          </p>
        )}
        <Button
          size="lg"
          className="h-12 w-full rounded-xl text-sm font-semibold"
          disabled={videoGenerating || jobIsActive || formInvalid}
          onClick={() => void handleGenerate()}
        >
          {videoGenerating || jobIsActive ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {jobIsActive ? "Generating…" : "Starting…"}
            </>
          ) : (
            <>
              <Film className="mr-2 h-4 w-4" />
              Generate video
            </>
          )}
        </Button>
      </section>

      {/* Session feed */}
      {feedHasContent && (
        <section className="space-y-3 border-t pt-6">
          <h3 className="text-[13px] font-semibold tracking-tight">
            This session
          </h3>
          {playbackUrl && (
            <div className="space-y-2 rounded-xl border bg-card p-3">
              <video
                key={playbackUrl}
                controls
                autoPlay
                loop
                src={playbackUrl}
                className="max-h-[420px] w-full rounded-lg bg-black"
              />
              <div className="flex justify-end">
                <a
                  href={playbackUrl}
                  download={`matrx-video-${Date.now()}.mp4`}
                  className="inline-flex items-center text-xs text-violet-500 hover:underline"
                >
                  <Download className="mr-1 h-3.5 w-3.5" />
                  Save MP4
                </a>
              </div>
            </div>
          )}
          {playbackJobId && !playbackUrl && (
            <div className="flex items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Fetching video…
            </div>
          )}
          {activeJob && (
            <VideoJobFeedCard
              job={activeJob}
              isActive
              resultUrl={videoResults[activeJob.job_id] ?? null}
              onPlay={() => handlePlay(activeJob.job_id)}
              onDismiss={clearActiveJob}
            />
          )}
          {feedJobs.map((j) => (
            <VideoJobFeedCard
              key={j.job_id}
              job={j}
              isActive={false}
              resultUrl={videoResults[j.job_id] ?? null}
              onPlay={() => handlePlay(j.job_id)}
            />
          ))}
        </section>
      )}
    </div>
  );
}

// ── Root ─────────────────────────────────────────────────────────────────────

export function VariantFocus() {
  const [state] = useMediaGenApp();
  // Layout-only local state: which segment is shown + the library dialog.
  const [segment, setSegment] = useState<Segment>("image");
  const [libraryOpen, setLibraryOpen] = useState(false);

  const videoBusy =
    state.activeJob?.status === "queued" ||
    state.activeJob?.status === "running";

  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-6 pb-24 pt-8">
        <div className="mb-8 flex flex-col items-center gap-3">
          <SegmentedControl
            value={segment}
            onChange={setSegment}
            videoBusy={videoBusy}
          />
          <button
            type="button"
            onClick={() => setLibraryOpen(true)}
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <Library className="h-3 w-3" />
            Open library
          </button>
        </div>

        {segment === "image" && <ImageFocusFlow />}
        {segment === "video" && <VideoFocusFlow />}
        {segment === "workflow" && (
          <div className="[&>*]:mx-auto">
            <WorkflowSection />
          </div>
        )}
      </div>

      <Dialog open={libraryOpen} onOpenChange={setLibraryOpen}>
        <DialogContent className="flex h-[88vh] max-w-5xl flex-col overflow-hidden p-0">
          <DialogHeader className="border-b px-6 py-4">
            <DialogTitle className="text-sm">Media library</DialogTitle>
            <DialogDescription className="text-xs">
              Everything generated on this device, beyond the current session.
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            <MediaLibrarySection />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
