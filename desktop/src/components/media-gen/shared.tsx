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

import { useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Dices,
  Download,
  RotateCcw,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import type { DownloadEntry } from "@/lib/downloads/types";
import type { GeneratedImageResult } from "@/hooks/use-media-gen";

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
 * filename is the real join key. We match the sanitized id against the entry's
 * filename (and, as a courtesy, the display_name), case-insensitively and
 * tolerating both `--` and `_` sanitizing variants.
 */
export function findModelDownload(
  downloads: DownloadEntry[],
  category: "image_gen" | "video_gen",
  modelId: string,
): DownloadEntry | null {
  const idLower = modelId.toLowerCase();
  const sanitized = idLower.replace(/\//g, "--");
  const sanitizedUnderscore = idLower.replace(/[/]/g, "_");
  const candidates = downloads.filter((d) => {
    if (d.category !== category) return false;
    const fn = (d.filename ?? "").toLowerCase();
    const dn = (d.display_name ?? "").toLowerCase();
    return (
      fn.includes(idLower) ||
      fn.includes(sanitized) ||
      fn.includes(sanitizedUnderscore) ||
      dn.includes(idLower)
    );
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
  return activeFirst[0];
}

// ── Small presentational pieces ──────────────────────────────────────────────

export function StarRating({ value, max = 5 }: { value: number; max?: number }) {
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
}: {
  value: string;
  onChange: (text: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex gap-2">
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputMode="numeric"
        placeholder="random"
        disabled={disabled}
        className="text-sm"
        aria-label="Seed"
      />
      <Button
        type="button"
        size="icon"
        variant="outline"
        disabled={disabled}
        onClick={() => onChange(String(randomSeed()))}
        aria-label="Randomize seed"
        title="Pick a random seed"
        className="shrink-0"
      >
        <Dices className="h-4 w-4" />
      </Button>
    </div>
  );
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
}) {
  const isDecimal = step < 1;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="text-xs">
          {label}
          {defaultValue !== null && (
            <span className="text-muted-foreground font-normal">
              {" "}
              (model default: {isDecimal ? defaultValue.toFixed(1) : defaultValue})
            </span>
          )}
        </Label>
        <Input
          value={String(value)}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (Number.isFinite(n)) onChange(n);
          }}
          inputMode={isDecimal ? "decimal" : "numeric"}
          disabled={disabled}
          className="h-6 w-16 px-1.5 text-xs text-right tabular-nums"
          aria-label={`${label} value`}
        />
      </div>
      <Slider
        min={min}
        max={max}
        step={step}
        value={[Math.min(max, Math.max(min, value))]}
        onValueChange={([v]) => onChange(v)}
        disabled={disabled}
      />
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
export function dimensionError(
  width: number,
  height: number,
): string | null {
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

/** Preset chips + free width/height inputs with multiple-of-8 validation. */
export function DimensionPicker({
  width,
  height,
  onChange,
  presets,
  disabled = false,
}: {
  width: number;
  height: number;
  onChange: (w: number, h: number) => void;
  presets: SizePreset[];
  disabled?: boolean;
}) {
  const error = dimensionError(width, height);
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">Size (width × height)</Label>
      <div className="flex gap-1.5 flex-wrap">
        {presets.map((p) => {
          const active = p.width === width && p.height === height;
          return (
            <button
              key={`${p.label}-${p.width}x${p.height}`}
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
      <div className="flex items-center gap-2">
        <Input
          value={String(width)}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (Number.isFinite(n)) onChange(Math.floor(n), height);
          }}
          inputMode="numeric"
          disabled={disabled}
          className="text-sm w-24 tabular-nums"
          aria-label="Width"
        />
        <span className="text-xs text-muted-foreground">×</span>
        <Input
          value={String(height)}
          onChange={(e) => {
            const n = Number(e.target.value);
            if (Number.isFinite(n)) onChange(width, Math.floor(n));
          }}
          inputMode="numeric"
          disabled={disabled}
          className="text-sm w-24 tabular-nums"
          aria-label="Height"
        />
        <span className="text-[11px] text-muted-foreground">px</span>
      </div>
      {error && (
        <p className="text-[11px] text-destructive flex items-center gap-1">
          <AlertCircle className="h-3 w-3 shrink-0" />
          {error}
        </p>
      )}
    </div>
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
}: {
  supported: boolean;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">
        Negative prompt{" "}
        <span className="text-muted-foreground">(what to avoid)</span>
      </Label>
      {supported ? (
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="blurry, low quality, deformed…"
          disabled={disabled}
          className="text-sm min-h-[60px] resize-none"
        />
      ) : (
        <p className="rounded-md border border-dashed px-3 py-2 text-[11px] text-muted-foreground">
          Not supported by this model — it ignores negative prompts, so the
          field is disabled rather than silently dropped.
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
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    Array.isArray(parsed)
  ) {
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
        Showing basic defaults from the model catalog instead; advanced
        settings are unavailable until this loads.
      </p>
      <Button size="sm" variant="outline" className="h-6 text-xs" onClick={onRetry}>
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
 */
export function GeneratedImageView({
  result,
  onClear,
  onReuseSeed,
}: {
  result: GeneratedImageResult;
  onClear?: () => void;
  /** Puts the result's seed back into the form's seed input. */
  onReuseSeed?: (seed: number) => void;
}) {
  return (
    <div className="space-y-2">
      <img
        src={`data:image/png;base64,${result.b64}`}
        alt="Generated image"
        className="w-full rounded-lg border object-contain"
      />
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground flex-wrap">
        <span className="flex items-center gap-2 flex-wrap">
          <span className="tabular-nums">
            {result.width}×{result.height} · {result.elapsed.toFixed(1)}s
          </span>
          {result.seed !== null && (
            <SeedChip seed={result.seed} onReuse={onReuseSeed} />
          )}
        </span>
        <div className="flex gap-2 shrink-0">
          {onClear && (
            <Button size="sm" variant="ghost" onClick={onClear}>
              Clear
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              const a = document.createElement("a");
              a.href = `data:image/png;base64,${result.b64}`;
              a.download = `matrx-image-${Date.now()}.png`;
              a.click();
            }}
          >
            <Download className="h-3.5 w-3.5 mr-1.5" />
            Download PNG
          </Button>
        </div>
      </div>
      {result.filePath && (
        <p
          className="text-[10px] text-muted-foreground/70 font-mono break-all"
          title="Saved to your media library"
        >
          Saved: {result.filePath}
        </p>
      )}
    </div>
  );
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
