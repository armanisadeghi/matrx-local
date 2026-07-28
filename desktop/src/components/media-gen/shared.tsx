/**
 * Shared building blocks for the media-gen UI (Images + Video + Workflow
 * sections): presentational atoms, download matching, and the full-settings
 * form controls (seed, dimensions, sliders, advanced-JSON editor).
 *
 * Doctrine for the form controls: EVERY setting the engine accepts is visible
 * and editable.  Common ones get beautiful controls; everything else lives in
 * the editable advanced-JSON editor.  Nothing is hidden, nothing silently
 * defaults, and every control has an obvious way back to the model default.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Dices,
  Download,
  ImagePlus,
  Info,
  ListPlus,
  Loader2,
  Maximize2,
  RotateCcw,
  Square,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";
import { NumberInput } from "@/components/ui/number-input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import type { DownloadEntry } from "@/lib/downloads/types";
import type { GeneratedImageResult } from "@/hooks/use-media-gen";
import type { ImageGenJob } from "@/lib/api";
import { engine, fetchMediaLibraryFile } from "@/lib/api";
import { emitClientLog } from "@/hooks/use-unified-log";
import { recovery } from "@/lib/recovery";
import { useMediaActions } from "@/components/media/MediaActionsProvider";
import { MediaOverflowMenu } from "@/components/media/MediaOverflowMenu";
import { CopyButton } from "@/components/media/MediaInfoDialog";
import { MediaThumb } from "@/components/media/MediaThumb";
import {
  descriptorFromJob,
  descriptorFromResult,
  type MediaDescriptor,
} from "@/components/media/types";
import {
  NEGATIVE_PROMPT_DEFAULT_ROWS,
  PROMPT_TEXTAREA_KEYS,
  ResizablePromptTextarea,
} from "@/components/media-gen/prompts/ResizablePromptTextarea";

// ── Formatting helpers ───────────────────────────────────────────────────────

export function formatGb(gb: number): string {
  if (gb <= 0) return "—";
  if (gb < 1) return `${Math.round(gb * 1000)} MB`;
  return `${gb.toFixed(1)} GB`;
}

// ── Download matching ────────────────────────────────────────────────────────

/**
 * Find the live DownloadManager entry for a model's weights download.
 *
 * Contract: the engine enqueues weight downloads in category
 * "image_gen" / "video_gen" with the download `filename` set to the SANITIZED
 * model id (`/` → `--`, done by the engine's `sanitize_model_id`). The SSE
 * progress payload (ProgressEvent) does NOT carry a metadata field, so the
 * filename is the real join key.
 *
 * Matching is EXACT on the sanitized filename (and the underscore variant),
 * never substring — a loose `includes()` would let one active download light
 * up every card that shared an org prefix (e.g. both Wan-AI models).
 * Metadata.model_id is preferred when present (persisted on enqueue).
 */
export function findModelDownload(
  downloads: DownloadEntry[],
  category: "image_gen" | "video_gen",
  modelId: string,
): DownloadEntry | null {
  const idLower = modelId.toLowerCase();
  const sanitized = idLower.replace(/\//g, "--");
  const sanitizedUnderscore = idLower.replace(/[/.]/g, "_");
  const candidates = downloads.filter((d) => {
    if (d.category !== category) return false;
    const metaId = d.metadata?.model_id;
    if (typeof metaId === "string" && metaId.toLowerCase() === idLower) {
      return true;
    }
    const fn = (d.filename ?? "").toLowerCase();
    return fn === sanitized || fn === sanitizedUnderscore || fn === idLower;
  });
  if (candidates.length === 0) return null;
  // Prefer the most recently updated active/queued entry, else the newest.
  const activeFirst = candidates.sort((a, b) => {
    const rank = (s: string) => (s === "active" ? 0 : s === "queued" ? 1 : 2);
    return (
      rank(a.status) - rank(b.status) ||
      (b.updated_at ?? "").localeCompare(a.updated_at ?? "")
    );
  });
  return activeFirst[0] ?? null;
}

// ── Small presentational pieces ──────────────────────────────────────────────

export function StarRating({
  value,
  max = 5,
}: {
  value: number;
  max?: number;
}) {
  return (
    <span className="flex gap-0.5">
      {Array.from({ length: max }).map((_, i) => (
        <span
          key={i}
          className={`h-2 w-2 rounded-full ${i < value ? "bg-violet-500" : "bg-muted-foreground/20"}`}
        />
      ))}
    </span>
  );
}

export function ErrorNote({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive flex items-center gap-2">
      <AlertCircle className="h-3.5 w-3.5 shrink-0" />
      <span className="break-words min-w-0 flex-1">{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="shrink-0 text-destructive/70 hover:text-destructive"
          aria-label="Dismiss error"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

/**
 * Informational (NON-error) banner for the queue-first Generate flow:
 * "A generation is already running — added to the queue as next."
 * Violet styling so it never reads as a failure.
 */
export function QueueNotice({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="rounded-lg border border-violet-500/40 bg-violet-500/5 px-3 py-2 text-xs text-violet-700 dark:text-violet-300 flex items-center gap-2">
      <ListPlus className="h-3.5 w-3.5 shrink-0" />
      <span className="break-words min-w-0 flex-1">{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="shrink-0 opacity-70 hover:opacity-100"
          aria-label="Dismiss notice"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

/** "Up next" badge for a queued job enqueued with priority="next". */
export function UpNextBadge() {
  return (
    <span className="rounded-full bg-violet-500/15 text-violet-600 dark:text-violet-400 px-1.5 py-0.5 text-[10px] font-medium shrink-0">
      Up next
    </span>
  );
}

/** True when this queued job should carry the "Up next" label. */
export function isUpNextJob(job: ImageGenJob): boolean {
  return job.status === "queued" && job.priority === "next";
}

export function InlineProgressBar({
  percent,
  indeterminate = false,
}: {
  percent: number;
  indeterminate?: boolean;
}) {
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
      {indeterminate ? (
        <div className="absolute inset-y-0 left-0 w-1/3 animate-pulse rounded-full bg-violet-500" />
      ) : (
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-violet-500 transition-[width] duration-300"
          style={{ width: `${clamped}%` }}
        />
      )}
    </div>
  );
}

// ── Elapsed time (never-looks-frozen readouts) ──────────────────────────────

/** "M:SS" for elapsed readouts. */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Ticking elapsed-seconds counter for a busy operation.  Pass the epoch-ms
 * start time; returns whole seconds (re-rendering once per second), or null
 * when not running.
 */
export function useElapsedSeconds(startedAt: number | null): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (startedAt === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [startedAt]);
  if (startedAt === null) return null;
  return Math.max(0, Math.floor((now - startedAt) / 1000));
}

/**
 * "Still working — N:SS elapsed" line shown beside every long-running spinner
 * so an in-flight generation/model-load never LOOKS frozen.  Pass either a
 * `startedAt` (ticks locally) or an engine-reported `elapsedSeconds`.
 */
export function StillWorkingNote({
  startedAt = null,
  elapsedSeconds = null,
  label = "Still working",
  className = "",
}: {
  startedAt?: number | null;
  elapsedSeconds?: number | null;
  label?: string;
  className?: string;
}) {
  const ticking = useElapsedSeconds(startedAt);
  const seconds = elapsedSeconds ?? ticking;
  return (
    <p
      className={`flex items-center justify-center gap-1.5 whitespace-nowrap text-[11px] text-muted-foreground tabular-nums ${className}`}
    >
      <Loader2 className="h-3 w-3 shrink-0 animate-spin text-violet-500" />
      <span className="truncate">
        {label}
        {seconds !== null ? ` — ${formatElapsed(seconds)} elapsed` : "…"}
      </span>
    </p>
  );
}

// ── Cancelable generate button (the one escape hatch, shared everywhere) ────

/**
 * THE generate-button pattern for every media-gen surface.  Idle: a normal
 * Generate button.  While a generation is in flight it BECOMES a prominent,
 * destructive Cancel button (plus a ticking "still working" readout), so
 * there is always a way out — no generation can ever only be stopped by
 * force-killing the app.
 *
 * `cancelling` shows "Cancelling…" until the cancel request settles / the
 * job status flips (video steps can take tens of seconds to actually stop).
 */
export function CancelableGenerateButton({
  generating,
  cancelling,
  startedAt = null,
  elapsedSeconds = null,
  disabled = false,
  onGenerate,
  onCancel,
  idleContent,
  workingLabel = "Generating",
  cancelLabel = "Cancel generation",
  buttonClassName = "w-full",
  containerClassName = "",
  size,
  showWorkingNote = true,
}: {
  /** True while the generation this button controls is in flight. */
  generating: boolean;
  /** True while a cancel request is pending / being honored. */
  cancelling: boolean;
  /** Epoch ms when the generation started (local ticking readout). */
  startedAt?: number | null;
  /** Engine-reported elapsed seconds (wins over startedAt when set). */
  elapsedSeconds?: number | null;
  /** Disables the GENERATE action only — cancel is always available. */
  disabled?: boolean;
  onGenerate: () => void;
  onCancel: () => void;
  /** Idle button content (icon + label). */
  idleContent: ReactNode;
  workingLabel?: string;
  cancelLabel?: string;
  /** Applied to the button itself (both states). */
  buttonClassName?: string;
  containerClassName?: string;
  size?: "default" | "sm" | "lg" | "icon";
  /** When false, omit the sub-button elapsed readout (e.g. action rows — preview shows it). */
  showWorkingNote?: boolean;
}) {
  if (!generating) {
    return (
      <div className={containerClassName || undefined}>
        <Button
          size={size}
          className={buttonClassName}
          disabled={disabled}
          onClick={onGenerate}
        >
          {idleContent}
        </Button>
      </div>
    );
  }
  return (
    <div className={`space-y-1.5 ${containerClassName}`}>
      <Button
        size={size}
        variant="destructive"
        className={buttonClassName}
        disabled={cancelling}
        onClick={onCancel}
        title="Stop this generation"
      >
        {cancelling ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
            Cancelling…
          </>
        ) : (
          <>
            <Square className="h-4 w-4 mr-2" />
            {cancelLabel}
          </>
        )}
      </Button>
      {showWorkingNote && (
        <StillWorkingNote
          startedAt={startedAt}
          elapsedSeconds={elapsedSeconds}
          label={
            cancelling
              ? `Cancelling ${workingLabel.toLowerCase()}`
              : `${workingLabel} — still working`
          }
        />
      )}
    </div>
  );
}

/**
 * Model-load banner: elapsed readout while `is_loading`, and the LOUD
 * resolution of the spinner into `load_error` when the engine reports one.
 */
export function ModelLoadingNotice({
  loading,
  startedAt,
  loadError,
  what = "model",
}: {
  loading: boolean;
  startedAt: number | null;
  loadError: string | null | undefined;
  what?: string;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!loading) return;
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [loading]);
  const stale = loading && startedAt !== null && now - startedAt >= 60_000;
  if (loading) {
    return (
      <div className="rounded-lg border bg-muted/20 px-3 py-2">
        <StillWorkingNote
          startedAt={startedAt}
          label={`Loading ${what} — still working`}
          className="justify-start"
        />
        {stale && (
          <div className="mt-2 flex items-center justify-between gap-3 border-t pt-2 text-xs">
            <span className="text-amber-600 dark:text-amber-400">
              This load is taking unusually long. Refresh its status or reset
              this view; your queues are preserved.
            </span>
            <div className="flex shrink-0 gap-1">
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  void recovery.refreshSurface("/media-generation")
                }
              >
                Refresh status
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void recovery.resetSurface("/media-generation")}
              >
                Reset view
              </Button>
            </div>
          </div>
        )}
      </div>
    );
  }
  if (loadError) {
    return <ErrorNote message={`Model load failed: ${loadError}`} />;
  }
  return null;
}

// ── Seed ─────────────────────────────────────────────────────────────────────

/** A random 32-bit seed — used so a "random" run is still reproducible. */
export function randomSeed(): number {
  return Math.floor(Math.random() * 4294967296);
}

/**
 * Parse the seed text field.  Blank → undefined (caller decides whether to
 * randomize client-side for reproducibility).  Non-numeric → undefined.
 */
export function parseSeedText(text: string): number | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  const n = Number(trimmed);
  return Number.isFinite(n) ? Math.floor(n) : undefined;
}

/** Seed field with a dice button that fills in a fresh random seed. */
export function SeedInput({
  value,
  onChange,
  disabled = false,
  layout = "stack",
}: {
  value: string;
  onChange: (text: string) => void;
  disabled?: boolean;
  layout?: "stack" | "row";
}) {
  const field = (
    <>
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode="numeric"
        placeholder="Random"
        disabled={disabled}
        className={`text-xs ${layout === "row" ? "h-7 flex-1" : "text-sm"}`}
        aria-label="Seed"
      />
      <Button
        type="button"
        size="icon"
        variant="outline"
        disabled={disabled}
        onClick={() => onChange(String(randomSeed()))}
        aria-label="Randomize seed"
        title="Random seed"
        className={`shrink-0 ${layout === "row" ? "h-7 w-7" : ""}`}
      >
        <Dices className={layout === "row" ? "h-3.5 w-3.5" : "h-4 w-4"} />
      </Button>
    </>
  );

  if (layout === "row") {
    return (
      <div className="flex items-center gap-2">
        <Label className="w-16 shrink-0 text-xs">Seed</Label>
        <div className="flex min-w-0 flex-1 items-center gap-1.5">{field}</div>
      </div>
    );
  }

  return <div className="flex gap-2">{field}</div>;
}

// ── Reset affordance ─────────────────────────────────────────────────────────

/** Small, consistent "back to defaults" button used across the forms. */
export function ResetButton({
  onClick,
  label = "Reset to defaults",
  disabled = false,
}: {
  onClick: () => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      onClick={onClick}
      disabled={disabled}
      className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
    >
      <RotateCcw className="h-3 w-3 mr-1" />
      {label}
    </Button>
  );
}

// ── Slider + number field ────────────────────────────────────────────────────

/**
 * Slider paired with a free number input; the model's recommended/default
 * value is always labeled so the user knows where "normal" is.
 */
export function NumberSliderField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  defaultValue,
  disabled = false,
  layout = "stack",
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  /** The model's default/recommended value (labeled next to the control). */
  defaultValue: number | null;
  disabled?: boolean;
  layout?: "stack" | "row";
}) {
  const isDecimal = step < 1;
  const clamped = Math.min(max, Math.max(min, value));
  const numberInput = (
    <NumberInput
      value={value}
      onChange={onChange}
      min={min}
      max={max}
      step={step}
      integer={!isDecimal}
      disabled={disabled}
      className="h-7 w-14 shrink-0 px-1.5 text-xs text-right tabular-nums"
      aria-label={`${label} value`}
    />
  );
  const slider = (
    <Slider
      min={min}
      max={max}
      step={step}
      value={[clamped]}
      onValueChange={([v]) => v !== undefined && onChange(v)}
      disabled={disabled}
      className={layout === "row" ? "flex-1" : undefined}
    />
  );

  if (layout === "row") {
    return (
      <div className="flex items-center gap-2">
        <Label className="w-16 shrink-0 text-xs">{label}</Label>
        {slider}
        {numberInput}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="text-xs">
          {label}
          {defaultValue !== null && (
            <span className="font-normal text-muted-foreground">
              {" "}
              (model default:{" "}
              {isDecimal ? defaultValue.toFixed(1) : defaultValue})
            </span>
          )}
        </Label>
        <NumberInput
          value={value}
          onChange={onChange}
          min={min}
          max={max}
          step={step}
          integer={!isDecimal}
          disabled={disabled}
          className="h-6 w-16 px-1.5 text-right text-xs tabular-nums"
          aria-label={`${label} value`}
        />
      </div>
      {slider}
    </div>
  );
}

// ── Dimensions (width / height) ──────────────────────────────────────────────

export interface SizePreset {
  label: string;
  width: number;
  height: number;
}

/** Multiple-of-8 validation per the generation API. Null when valid. */
export function dimensionError(width: number, height: number): string | null {
  for (const [name, v] of [
    ["Width", width],
    ["Height", height],
  ] as const) {
    if (!Number.isInteger(v) || v <= 0) {
      return `${name} must be a positive whole number.`;
    }
    if (v % 8 !== 0) {
      return `${name} must be a multiple of 8 (got ${v}).`;
    }
  }
  return null;
}

function sizePresetKey(p: SizePreset): string {
  return `${p.label}|${p.width}x${p.height}`;
}

/** Preset chips + free width/height inputs with multiple-of-8 validation. */
export function DimensionPicker({
  width,
  height,
  onChange,
  presets,
  disabled = false,
  layout = "stack",
}: {
  width: number;
  height: number;
  onChange: (w: number, h: number) => void;
  presets: SizePreset[];
  disabled?: boolean;
  layout?: "stack" | "row";
}) {
  const error = dimensionError(width, height);
  const matchedPreset = presets.find(
    (p) => p.width === width && p.height === height,
  );
  const selectValue = matchedPreset ? sizePresetKey(matchedPreset) : "custom";

  const dimensionInputs = (
    <div className="flex items-center gap-1.5">
      <NumberInput
        value={width}
        onChange={(n) => onChange(n, height)}
        min={8}
        integer
        emptyValue={width}
        disabled={disabled}
        className="h-7 w-[4.5rem] px-2 text-xs tabular-nums"
        aria-label="Width"
      />
      <span className="text-xs text-muted-foreground">×</span>
      <NumberInput
        value={height}
        onChange={(n) => onChange(width, n)}
        min={8}
        integer
        emptyValue={height}
        disabled={disabled}
        className="h-7 w-[4.5rem] px-2 text-xs tabular-nums"
        aria-label="Height"
      />
      <span className="text-[11px] text-muted-foreground">px</span>
    </div>
  );

  if (layout === "row") {
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Label className="w-16 shrink-0 text-xs">Size</Label>
          <Select
            value={selectValue}
            onValueChange={(value) => {
              if (value === "custom") return;
              const preset = presets.find((p) => sizePresetKey(p) === value);
              if (preset) onChange(preset.width, preset.height);
            }}
            disabled={disabled}
          >
            <SelectTrigger className="h-7 w-[9.5rem] shrink-0 px-2 text-xs">
              <SelectValue placeholder="Size" />
            </SelectTrigger>
            <SelectContent>
              {presets.map((p) => (
                <SelectItem
                  key={sizePresetKey(p)}
                  value={sizePresetKey(p)}
                  className="text-xs"
                >
                  {p.label}
                </SelectItem>
              ))}
              <SelectItem value="custom" className="text-xs">
                Custom
              </SelectItem>
            </SelectContent>
          </Select>
          {dimensionInputs}
        </div>
        {error && (
          <p className="flex items-center gap-1 pl-[4.5rem] text-[11px] text-destructive">
            <AlertCircle className="h-3 w-3 shrink-0" />
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <Label className="text-xs">Size (width × height)</Label>
      <div className="flex flex-wrap gap-1.5">
        {presets.map((p) => {
          const active = p.width === width && p.height === height;
          return (
            <button
              key={sizePresetKey(p)}
              type="button"
              disabled={disabled}
              onClick={() => onChange(p.width, p.height)}
              className={`rounded-full border px-2.5 py-0.5 text-[11px] transition-colors ${
                active
                  ? "border-violet-500 bg-violet-500/10 text-violet-600 dark:text-violet-400"
                  : "hover:bg-muted/30"
              }`}
              title={`${p.width}×${p.height}`}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      {dimensionInputs}
      {error && (
        <p className="flex items-center gap-1 text-[11px] text-destructive">
          <AlertCircle className="h-3 w-3 shrink-0" />
          {error}
        </p>
      )}
    </div>
  );
}

// ── Prompt capacity (honest, per model family) ──────────────────────────────

/**
 * The user-facing truth about how much prompt a model family actually reads.
 * The API accepts long prompts (10k chars); what the MODEL does with them
 * differs per family — SD/SDXL's CLIP encoder truncates at 77 tokens, FLUX's
 * T5 reads ~512 tokens, Qwen/Z-Image accept long prompts. Informational
 * only: never block typing, never truncate client-side.
 */
export function promptCapacityHint(
  pipelineType: string | null | undefined,
): string | null {
  switch (pipelineType) {
    case "stable-diffusion":
    case "stable-diffusion-xl":
      return "This model reads ~77 tokens (~300 characters) — longer prompts are truncated by the model itself.";
    case "flux":
    case "flux2-klein":
      return "This model reads up to ~512 tokens (~2,000 characters) of prompt.";
    case "z-image":
    case "qwen-image":
      return "This model accepts long, detailed prompts (hundreds of tokens).";
    default:
      return null;
  }
}

/** Info icon for prompt token limits — hover for details. */
export function PromptCapacityInfo({
  pipelineType,
  className = "",
}: {
  pipelineType: string | null | undefined;
  className?: string;
}) {
  const hint = promptCapacityHint(pipelineType);
  if (!hint) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={`inline-flex shrink-0 rounded-sm text-muted-foreground/70 hover:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${className}`}
          aria-label="Prompt length limits"
        >
          <Info className="h-3 w-3" />
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs text-xs leading-relaxed">
        {hint}
      </TooltipContent>
    </Tooltip>
  );
}

/** @deprecated Use PromptCapacityInfo inline with the label. */
export function PromptCapacityHint({
  pipelineType,
  className = "",
}: {
  pipelineType: string | null | undefined;
  className?: string;
}) {
  return (
    <PromptCapacityInfo pipelineType={pipelineType} className={className} />
  );
}

// ── Negative prompt (never silently hidden) ──────────────────────────────────

/**
 * Negative-prompt field.  When the model does not support one, we say so
 * explicitly instead of silently hiding the control — the user must always
 * know which settings exist and why one is unavailable.
 */
export function NegativePromptField({
  supported,
  value,
  onChange,
  disabled = false,
  hideLabel = false,
  resizeStorageKey = PROMPT_TEXTAREA_KEYS.imageNegative,
}: {
  supported: boolean;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  hideLabel?: boolean;
  resizeStorageKey?: string;
}) {
  return (
    <div className="space-y-1.5">
      {!hideLabel && <Label className="text-xs">Negative prompt</Label>}
      {supported ? (
        <ResizablePromptTextarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          resizeStorageKey={resizeStorageKey}
          defaultRows={NEGATIVE_PROMPT_DEFAULT_ROWS}
          placeholder="blurry, low quality, deformed…"
          disabled={disabled}
          className="text-sm"
        />
      ) : (
        <p className="rounded-md border border-dashed px-3 py-2 text-[11px] text-muted-foreground">
          Not supported by this model.
        </p>
      )}
    </div>
  );
}

// ── Advanced params (editable JSON) ──────────────────────────────────────────

export type AdvancedOverrides =
  | {
      ok: true;
      /** Only the keys whose values differ from the model defaults. */
      overrides: Record<string, unknown>;
      count: number;
    }
  | { ok: false; error: string };

/**
 * Parse the advanced-JSON textarea and diff it against the model defaults.
 * Only CHANGED keys are sent to the engine as `extra_params` (user wins);
 * keys the user added that aren't in the defaults count as overrides too.
 */
export function computeAdvancedOverrides(
  text: string,
  defaults: Record<string, unknown>,
): AdvancedOverrides {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, overrides: {}, count: 0 };
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (e) {
    return {
      ok: false,
      error: `Invalid JSON: ${e instanceof Error ? e.message : String(e)}`,
    };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, error: "Advanced settings must be a JSON object." };
  }
  const obj = parsed as Record<string, unknown>;
  const overrides: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj)) {
    const isDefault =
      key in defaults &&
      JSON.stringify(value) === JSON.stringify(defaults[key]);
    if (!isDefault) overrides[key] = value;
  }
  return { ok: true, overrides, count: Object.keys(overrides).length };
}

/**
 * Collapsible "Advanced settings" editor: an editable JSON textarea prefilled
 * with EVERY remaining pipeline kwarg and its default.  Inline JSON errors;
 * an "N advanced overrides active" badge when the user diverges from the
 * defaults; one-click reset back to the model's advanced defaults.
 */
export function AdvancedParamsEditor({
  defaults,
  text,
  onChange,
  onReset,
  disabled = false,
}: {
  defaults: Record<string, unknown>;
  text: string;
  onChange: (text: string) => void;
  onReset: () => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const result = useMemo(
    () => computeAdvancedOverrides(text, defaults),
    [text, defaults],
  );
  const overrideCount = result.ok ? result.count : 0;

  return (
    <div className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
        aria-expanded={open}
      >
        <span className="flex items-center gap-1.5 text-xs font-medium">
          {open ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          Advanced settings
          <span className="text-muted-foreground font-normal">
            (every remaining pipeline parameter — editable JSON)
          </span>
        </span>
        <span className="flex items-center gap-2 shrink-0">
          {!result.ok && (
            <span className="rounded bg-destructive/15 text-destructive px-1.5 py-0.5 text-[10px] font-medium">
              invalid JSON
            </span>
          )}
          {result.ok && overrideCount > 0 && (
            <span className="rounded bg-violet-500/15 text-violet-600 dark:text-violet-400 px-1.5 py-0.5 text-[10px] font-medium">
              {overrideCount} advanced override{overrideCount === 1 ? "" : "s"}{" "}
              active
            </span>
          )}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t px-3 py-2.5">
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            These are all of this model's remaining pipeline parameters with
            their defaults. Edit any value — only the keys you change are sent
            to the engine. Unknown or invalid parameters are rejected by the
            engine with a clear error.
          </p>
          <Textarea
            value={text}
            onChange={(e) => onChange(e.target.value)}
            spellCheck={false}
            disabled={disabled}
            className="min-h-[160px] font-mono text-xs resize-y"
            aria-label="Advanced settings JSON"
          />
          {!result.ok && (
            <p className="text-[11px] text-destructive flex items-center gap-1">
              <AlertCircle className="h-3 w-3 shrink-0" />
              {result.error}
            </p>
          )}
          <div className="flex justify-end">
            <ResetButton
              onClick={onReset}
              label="Reset advanced to defaults"
              disabled={disabled}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Params-endpoint failure banner (loud, never silent) ─────────────────────

/**
 * Shown when GET /…-gen/params/{model} fails.  We fall back to the basic
 * defaults from the model catalog, but we SAY so — the user must know the
 * full parameter set could not be loaded.
 */
export function ParamsErrorBanner({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2.5 space-y-1.5">
      <p className="text-xs font-medium flex items-center gap-1.5">
        <AlertCircle className="h-3.5 w-3.5 text-amber-500 shrink-0" />
        Could not load this model's full parameter set
      </p>
      <p className="text-[11px] text-muted-foreground break-all">{error}</p>
      <p className="text-[11px] text-muted-foreground">
        Showing basic defaults from the model catalog instead; advanced settings
        are unavailable until this loads.
      </p>
      <Button
        size="sm"
        variant="outline"
        className="h-6 text-xs"
        onClick={onRetry}
      >
        Retry
      </Button>
    </div>
  );
}

// ── Sub-tab bar (Generate | Models) ──────────────────────────────────────────

/**
 * Always-visible sub-navigation inside a media-gen section.  Replaces the old
 * hidden view-state flow — the user can always see where their work lives and
 * get back to it.
 */
export function SubTabBar<T extends string>({
  tabs,
  active,
  onSelect,
}: {
  tabs: { id: T; label: string; badge?: string | number | null }[];
  active: T;
  onSelect: (id: T) => void;
}) {
  return (
    <div className="flex gap-1 border-b" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={t.id === active}
          onClick={() => onSelect(t.id)}
          className={`-mb-px flex items-center gap-1.5 rounded-t-md border-b-2 px-3 py-1.5 text-xs font-medium transition-colors ${
            t.id === active
              ? "border-violet-500 text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          {t.label}
          {t.badge !== undefined && t.badge !== null && t.badge !== 0 && (
            <span className="rounded-full bg-violet-500/15 text-violet-600 dark:text-violet-400 px-1.5 text-[10px] tabular-nums">
              {t.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

// ── Seed display (prominent, copyable, reusable) ─────────────────────────────

/** Prominent seed chip: value, copy button, and optional "reuse" action. */
export function SeedChip({
  seed,
  onReuse,
}: {
  seed: number;
  onReuse?: (seed: number) => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border bg-muted/40 px-2 py-1 text-xs">
      <span className="text-muted-foreground">Seed</span>
      <span className="font-mono font-medium tabular-nums">{seed}</span>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard.writeText(String(seed)).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          });
        }}
        className="text-muted-foreground hover:text-foreground"
        aria-label="Copy seed"
        title="Copy seed"
      >
        {copied ? (
          <Check className="h-3 w-3 text-green-500" />
        ) : (
          <Copy className="h-3 w-3" />
        )}
      </button>
      {onReuse && (
        <button
          type="button"
          onClick={() => onReuse(seed)}
          className="text-violet-500 hover:underline"
          title="Put this seed into the seed input to reproduce this result"
        >
          Reuse
        </button>
      )}
    </span>
  );
}

// ── Generated image result ───────────────────────────────────────────────────

/**
 * The one result-display pattern for generated images (generate view and
 * workflow view).  Prominently shows the seed actually used (copy + reuse for
 * reproducibility) and, subtly, the on-disk path where the engine saved it.
 *
 * The image itself is always clickable: by default it opens the MediaLightbox
 * with this single result; callers with a richer viewing set (e.g. the Studio
 * variant's filmstrip) pass `onOpenLightbox` to take over the click.
 */
export function GeneratedImageView({
  result,
  onClear,
  onReuseSeed,
  prompt,
  modelId,
  meta,
  onOpenLightbox,
  onUseAsInput,
}: {
  result: GeneratedImageResult;
  onClear?: () => void;
  /** Puts the result's seed back into the form's seed input. */
  onReuseSeed?: (seed: number) => void;
  /** The prompt used for this result. */
  prompt?: string;
  /** The model used — makes the result remixable. */
  modelId?: string;
  /** Extra generation params — shown in the info panel. */
  meta?: Record<string, unknown>;
  /** Overrides the click-to-expand behavior (e.g. multi-item viewing set). */
  onOpenLightbox?: () => void;
  /** Routes this result into the img2img input slot. */
  onUseAsInput?: () => void;
}) {
  const actions = useMediaActions();
  const resolvedPrompt = prompt ?? result.request.prompt;
  const descriptor = useMemo<MediaDescriptor>(
    () =>
      descriptorFromResult(result, {
        ...(prompt !== undefined ? { prompt } : {}),
        ...(modelId !== undefined ? { modelId } : {}),
        ...(meta !== undefined ? { params: meta } : {}),
      }),
    [result, prompt, modelId, meta],
  );

  return (
    <div className="space-y-2">
      <MediaThumb
        item={descriptor}
        variant="card"
        onActivate={onOpenLightbox ?? (() => actions.openOne(descriptor))}
        className="w-full rounded-lg border"
      >
        <span className="pointer-events-none absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/55 px-1.5 py-1 text-[10px] text-white opacity-0 transition-opacity group-hover:opacity-100">
          <Maximize2 className="h-3 w-3" />
          Expand
        </span>
      </MediaThumb>

      <div className="flex min-w-0 items-center gap-2 overflow-x-auto">
        <span className="shrink-0 whitespace-nowrap text-[11px] tabular-nums text-muted-foreground">
          {result.width}×{result.height} · {result.elapsed.toFixed(1)}s
        </span>
        {result.seed !== null && (
          <SeedChip
            seed={result.seed}
            {...(onReuseSeed !== undefined ? { onReuse: onReuseSeed } : {})}
          />
        )}
        {resolvedPrompt.trim() && (
          <>
            <span
              className="min-w-0 flex-1 truncate text-xs text-muted-foreground"
              title={resolvedPrompt}
            >
              {resolvedPrompt}
            </span>
            <CopyButton value={resolvedPrompt} label="Copy prompt" />
          </>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-0.5">
          {onUseAsInput && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={onUseAsInput}
              title="Use as input"
            >
              <ImagePlus className="h-3.5 w-3.5" />
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={() => void actions.download(descriptor)}
            title="Download"
          >
            <Download className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={() => actions.openOne(descriptor)}
            title="Expand"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </Button>
          {onClear && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={onClear}
              title="Clear"
            >
              Clear
            </Button>
          )}
          <MediaOverflowMenu item={descriptor} />
        </span>
      </div>
    </div>
  );
}

// ── Completed queue-job viewer (shared across every queue panel) ────────────

/**
 * THE click-to-open behavior for completed image-queue jobs, shared by every
 * queue panel (Images tab, Studio rail, Focus feed, Workspace list, Gallery
 * strip). Clicking a completed job opens it in the app-wide lightbox with the
 * panel's OTHER completed jobs as the prev/next set — and from there the user
 * has the full action set (remix, info, copy, delete, vault, …), exactly as if
 * they had opened it from the library grid.
 *
 * URL resolution: thumbnails are JPEG posters only. Opening a completed job
 * always resolves `/media-library/file/{itemId}` so the lightbox receives the
 * exact full engine media file.
 *
 * Call `openJob(job)` from the row click handler (completed jobs only). There
 * is nothing to mount: the lightbox lives in MediaActionsProvider.
 */
export function useImageJobLightbox({
  jobs,
  thumbs,
}: {
  /** The panel's job list (any statuses — only completed ones are viewable). */
  jobs: ImageGenJob[];
  /** jobId → object URL cache (imageJobThumbs from the media-gen context). */
  thumbs: Record<string, string>;
}): {
  /** Open this (completed) job in the lightbox. No-op for other statuses. */
  openJob: (job: ImageGenJob) => void;
  /** Job id whose bytes are being fetched before opening, else null. */
  openingJobId: string | null;
  /** Descriptor for a job, when its bytes are available (for menus/thumbs). */
  descriptorOf: (job: ImageGenJob) => MediaDescriptor | null;
} {
  const actions = useMediaActions();
  const [fetchedUrls, setFetchedUrls] = useState<Record<string, string>>({});
  const [openingJobId, setOpeningJobId] = useState<string | null>(null);
  const openSeqRef = useRef(0);

  // Revoke only the object URLs THIS hook created (thumbs belong to the
  // media-gen context, which revokes its own).
  const fetchedUrlsRef = useRef(fetchedUrls);
  useEffect(() => {
    fetchedUrlsRef.current = fetchedUrls;
  }, [fetchedUrls]);
  useEffect(() => {
    return () => {
      for (const url of Object.values(fetchedUrlsRef.current)) {
        URL.revokeObjectURL(url);
      }
    };
  }, []);

  const thumbUrlOf = useCallback(
    (j: ImageGenJob): string | null =>
      thumbs[j.job_id] ?? fetchedUrls[j.job_id] ?? null,
    [thumbs, fetchedUrls],
  );
  const fullUrlOf = useCallback(
    (j: ImageGenJob): string | null => fetchedUrls[j.job_id] ?? null,
    [fetchedUrls],
  );

  const descriptorOf = useCallback(
    (job: ImageGenJob): MediaDescriptor | null => {
      if (job.status !== "completed" || !job.item_id) return null;
      const url = thumbUrlOf(job);
      return url ? descriptorFromJob(job, url) : null;
    },
    [thumbUrlOf],
  );

  const openJob = useCallback(
    (job: ImageGenJob) => {
      if (job.status !== "completed" || !job.item_id) return;
      const requestId = ++openSeqRef.current;

      const openWith = (targetUrl: string) => {
        const items: MediaDescriptor[] = [];
        for (const j of jobs) {
          if (j.status !== "completed" || !j.item_id) continue;
          const u = j.job_id === job.job_id ? targetUrl : fullUrlOf(j);
          if (u) items.push(descriptorFromJob(j, u));
        }
        const index = Math.max(
          0,
          items.findIndex((d) => d.id === job.job_id),
        );
        actions.open(items, index, job.item_id ?? undefined);
      };

      const known = fullUrlOf(job);
      if (known) {
        openWith(known);
        return;
      }
      // Bytes not cached yet — fetch, then open. Never a dead click.
      const base = engine.engineUrl;
      if (!base) {
        emitClientLog(
          "error",
          `[media-gen] cannot open job ${job.job_id} — engine not connected`,
          "engine",
        );
        return;
      }
      setOpeningJobId(job.job_id);
      void fetchMediaLibraryFile(base, job.item_id)
        .then((url) => {
          if (requestId !== openSeqRef.current) {
            URL.revokeObjectURL(url);
            return;
          }
          setFetchedUrls((prev) =>
            prev[job.job_id] ? prev : { ...prev, [job.job_id]: url },
          );
          openWith(url);
        })
        .catch((e) => {
          emitClientLog(
            "error",
            `[media-gen] could not load the image for job ${job.job_id}: ${String(e)}`,
            "engine",
          );
        })
        .finally(() => setOpeningJobId(null));
    },
    [jobs, fullUrlOf, actions],
  );

  return { openJob, openingJobId, descriptorOf };
}

/** Open an external URL via the Tauri shell when available, else a new tab. */
export async function openExternalUrl(url: string) {
  if (
    typeof window !== "undefined" &&
    (window as unknown as Record<string, unknown>).__TAURI__
  ) {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
