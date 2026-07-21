/**
 * ImageGenerateForm — the canonical image generate-form building blocks.
 * Every layout composes these; none re-implements a control:
 *
 *  - ImagePromptField        prompt label + inline toolbar + capacity info icon
 *  - ImageCommonSettings     negative → steps/guidance → size → seed
 *  - InputImageControl       img2img: drop/pick/paste + preview + strength
 *  - AlternativeTextEncodersSection model-scoped Standard/alternative choice
 *  - LoraStylesSection       compact active summary + searchable manager
 *  - ImageAdvancedSection    editable advanced-JSON (every pipeline kwarg)
 *  - ImageFormNotices        params-error banner + gen error + queue notice
 *  - ImageGenerateActions    Generate + Add-to-queue + Reset
 *  - ImageFormHeader         model name/provider + reset-all + switch/unload
 *  - ImageGenerateForm       the full vertical composition of all the above
 *
 * All state lives in MediaGenContext via the ImageGenController — these are
 * pure views over it, configurable by layout-level props only.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  Download,
  GitBranch,
  Image as ImageIcon,
  ImagePlus,
  Layers,
  ListPlus,
  Loader2,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { IMG2IMG_DEFAULT_STRENGTH } from "@/hooks/use-media-gen";
import {
  AdvancedParamsEditor,
  CancelableGenerateButton,
  DimensionPicker,
  ErrorNote,
  NegativePromptField,
  NumberSliderField,
  ParamsErrorBanner,
  PromptCapacityInfo,
  QueueNotice,
  ResetButton,
  SeedInput,
  formatGb,
} from "@/components/media-gen/shared";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  ImagePromptToolbar,
  type ImageFormPanelToggles,
} from "./ImagePromptToolbar";
import { IMAGE_GEN_PANEL_KEYS, usePersistedToggle } from "./usePersistedToggle";
import type { ImageGenController } from "./imageController";
import { ImageRevisionVersionPicker } from "./ImageRevisionVersionPicker";
import { LoraStylesSection } from "./LoraManager";
export { LoraStylesSection };
import { MediaThumb } from "@/components/media/MediaThumb";
import { descriptorFromInputImage } from "@/components/media/types";

// ── Prompt ───────────────────────────────────────────────────────────────────

export function ImageRevisionBanner({ ctl }: { ctl: ImageGenController }) {
  const [, actions] = useMediaGenApp();
  if (!ctl.form.revision) return null;
  const isInstructionEdit = ctl.model?.pipeline_type === "flux2-klein";
  return (
    <div className="flex items-start gap-3 rounded-lg border border-violet-500/40 bg-violet-500/5 px-3 py-2.5">
      <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" />
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium">Revision branch</p>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {isInstructionEdit
            ? "Describe the change. Apply keeps this result as the next parent."
            : "Adjust the image description. Apply keeps this result as the next parent."}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <ImageRevisionVersionPicker ctl={ctl} />
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          onClick={actions.endImageRevision}
        >
          Exit
        </Button>
      </div>
    </div>
  );
}

export function ImagePromptField({
  ctl,
  placeholder = "Describe the image you want to generate…",
  textareaClassName = "min-h-[100px] resize-y text-sm",
  showLabel = true,
  showToolbar = true,
  showActions = false,
  renderActions,
  panels,
}: {
  ctl: ImageGenController;
  placeholder?: string;
  textareaClassName?: string;
  showLabel?: boolean;
  showToolbar?: boolean;
  showActions?: boolean;
  /** Shared action row factory — call in every slot so rows stay identical. */
  renderActions?: () => ReactNode;
  panels?: ImageFormPanelToggles;
}) {
  const [, actions] = useMediaGenApp();
  const d = ctl.defaults;
  const negativeSupported =
    d?.supportsNegativePrompt ?? ctl.model?.supports_negative_prompt ?? true;
  const label =
    ctl.isRevision && ctl.model?.pipeline_type === "flux2-klein"
      ? "Change"
      : "Prompt";
  return (
    <div className="space-y-1.5">
      <ImageRevisionBanner ctl={ctl} />
      {showLabel && (
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1">
            <Label className="text-xs">{label}</Label>
            <PromptCapacityInfo pipelineType={ctl.model?.pipeline_type} />
          </div>
          {showToolbar && (
            <ImagePromptToolbar
              ctl={ctl}
              compact
              {...(panels !== undefined ? { panels } : {})}
            />
          )}
        </div>
      )}
      <Textarea
        value={ctl.form.prompt}
        onChange={(e) => actions.setImageForm({ prompt: e.target.value })}
        placeholder={
          ctl.isRevision && ctl.model?.pipeline_type === "flux2-klein"
            ? "Describe what to change while preserving everything else…"
            : placeholder
        }
        className={textareaClassName}
      />
      {panels?.showNegative && (
        <NegativePromptField
          supported={negativeSupported}
          value={ctl.form.negativePrompt}
          onChange={(v) => actions.setImageForm({ negativePrompt: v })}
          disabled={ctl.form.paramsLoading}
          hideLabel
        />
      )}
      {showActions && renderActions?.()}
    </div>
  );
}

// ── Common settings ──────────────────────────────────────────────────────────

export function ImageCommonSettings({
  ctl,
  showNegative = true,
  showSeed = true,
  showReset = false,
  sliderColumns = true,
  layout = "stack",
}: {
  ctl: ImageGenController;
  showNegative?: boolean;
  showSeed?: boolean;
  showReset?: boolean;
  /** Steps/Guidance side by side (true) or stacked (false). */
  sliderColumns?: boolean;
  layout?: "stack" | "row";
}) {
  const [, actions] = useMediaGenApp();
  const { setImageForm, resetImageCommon } = actions;
  const d = ctl.defaults;
  if (!d) return null;
  const disabled = ctl.form.paramsLoading;
  const row = layout === "row";
  const sliders = (
    <>
      <NumberSliderField
        label="Steps"
        value={ctl.form.steps}
        onChange={(v) => setImageForm({ steps: v })}
        min={1}
        max={ctl.model?.pipeline_type.startsWith("flux") ? 50 : 100}
        step={1}
        defaultValue={d.steps}
        disabled={disabled}
        layout={layout}
      />
      <NumberSliderField
        label="Guidance"
        value={ctl.form.guidance}
        onChange={(v) => setImageForm({ guidance: v })}
        min={0}
        max={20}
        step={0.5}
        defaultValue={d.guidance}
        disabled={disabled}
        layout={layout}
      />
    </>
  );
  return (
    <div className={row ? "space-y-2" : "space-y-4"}>
      {showNegative && (
        <NegativePromptField
          supported={d.supportsNegativePrompt}
          value={ctl.form.negativePrompt}
          onChange={(v) => setImageForm({ negativePrompt: v })}
          disabled={disabled}
        />
      )}
      {row ? (
        sliders
      ) : sliderColumns ? (
        <div className="grid grid-cols-2 gap-3">{sliders}</div>
      ) : (
        sliders
      )}
      <DimensionPicker
        width={ctl.form.width}
        height={ctl.form.height}
        onChange={(w, h) => setImageForm({ width: w, height: h })}
        presets={ctl.sizePresets}
        disabled={disabled}
        layout={layout}
      />
      {showSeed &&
        (row ? (
          <SeedInput
            value={ctl.form.seedText}
            onChange={(seedText) => setImageForm({ seedText })}
            disabled={disabled}
            layout="row"
          />
        ) : (
          <div className="space-y-1.5">
            <Label className="text-xs">Seed</Label>
            <SeedInput
              value={ctl.form.seedText}
              onChange={(seedText) => setImageForm({ seedText })}
              disabled={disabled}
            />
          </div>
        ))}
      {showReset && (
        <div className="flex justify-end">
          <ResetButton
            onClick={resetImageCommon}
            label="Reset settings to model defaults"
          />
        </div>
      )}
    </div>
  );
}

// ── img2img input image ──────────────────────────────────────────────────────

/**
 * Input-image control: shown ONLY when the selected model reports
 * supports_img2img. Drop-zone + file pick + paste; preview thumb with remove;
 * strength slider ("How much to change the input") with the model default
 * labeled.
 */
export function InputImageControl({
  ctl,
  compact = false,
}: {
  ctl: ImageGenController;
  compact?: boolean;
}) {
  const [, actions] = useMediaGenApp();
  const { setImageForm } = actions;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const d = ctl.defaults;
  if (!d?.supportsImg2Img) return null;
  const img = ctl.form.initImage;

  return (
    <div className="space-y-2">
      {!compact && <Label className="text-xs">Input image</Label>}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) ctl.handlePickInitImage(file);
        }}
      />
      {img ? (
        <div className="flex items-center gap-3 rounded-lg border px-3 py-2">
          <MediaThumb
            item={descriptorFromInputImage(img.previewUrl, img.name)}
            variant="icon"
            className="h-12 w-12 rounded object-cover border"
          />
          <span className="text-xs truncate flex-1">{img.name}</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={ctl.clearInitImage}
            aria-label="Remove input image"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : (
        <div
          role="button"
          tabIndex={0}
          onClick={() => fileInputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              fileInputRef.current?.click();
            }
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const file = e.dataTransfer.files?.[0];
            if (file) ctl.handlePickInitImage(file);
          }}
          onPaste={(e) => {
            const file = Array.from(e.clipboardData.files)[0];
            if (file) {
              e.preventDefault();
              ctl.handlePickInitImage(file);
            }
          }}
          className={`flex cursor-pointer flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 py-5 text-center transition-colors ${
            dragOver ? "border-violet-500 bg-violet-500/5" : "hover:bg-muted/20"
          }`}
          title="Drop, paste, or choose an image"
        >
          <ImagePlus className="h-5 w-5 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">
            Drop, paste, or choose
          </p>
        </div>
      )}
      {img && d.strength !== null && (
        <NumberSliderField
          label="Strength"
          value={ctl.form.strength}
          onChange={(strength) => setImageForm({ strength })}
          min={0}
          max={1}
          step={0.05}
          defaultValue={d.strength ?? IMG2IMG_DEFAULT_STRENGTH}
          layout="row"
        />
      )}
    </div>
  );
}

// ── Alternative text encoders ───────────────────────────────────────────────

/**
 * Explicit model-scoped encoder choice. The stock encoder remains available;
 * selecting an uninstalled alternative immediately starts its persistent
 * DownloadManager install and generation stays disabled until it is complete.
 */
export function AlternativeTextEncodersSection({
  ctl,
}: {
  ctl: ImageGenController;
}) {
  const [, actions] = useMediaGenApp();
  const { downloads } = useDownloadManager();
  const encoders = ctl.model?.text_encoders ?? [];
  const selectedId = ctl.form.textEncoderId;
  const selected = selectedId
    ? encoders.find((encoder) => encoder.encoder_id === selectedId)
    : null;
  const selectedDownload = selected
    ? downloads.find(
        (entry) =>
          entry.category === "image_gen_text_encoder" &&
          (entry.filename === selected.encoder_id ||
            entry.metadata?.["text_encoder_id"] === selected.encoder_id),
      )
    : undefined;
  const { downloadTextEncoder, setImageForm } = actions;

  // Selection is the first-use boundary. This also covers encoders restored
  // by Remix rather than selected with a click. A failed entry remains present
  // so retries stay explicit instead of looping.
  useEffect(() => {
    if (
      !ctl.model?.model_id ||
      !selectedId ||
      selected?.installed ||
      (selectedDownload && selectedDownload.status !== "completed")
    ) {
      return;
    }
    void downloadTextEncoder(ctl.model.model_id, selectedId);
  }, [
    ctl.model?.model_id,
    selectedId,
    selected?.installed,
    selectedDownload,
    downloadTextEncoder,
  ]);

  if (encoders.length === 0 || !ctl.model) return null;

  const selectEncoder = (encoderId: string | null) => {
    setImageForm({ textEncoderId: encoderId });
  };

  return (
    <div
      className="space-y-2 rounded-lg border p-3"
      role="radiogroup"
      aria-label="Alternative text encoders"
    >
      <div>
        <Label className="text-xs">Alternative text encoders</Label>
        <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
          Choose how prompts are encoded. Alternatives download once when first
          selected and remain installed on this device.
        </p>
      </div>

      <button
        type="button"
        role="radio"
        aria-checked={selectedId === null}
        onClick={() => selectEncoder(null)}
        className={`w-full rounded-md border px-3 py-2 text-left transition-colors ${
          selectedId === null
            ? "border-violet-500 bg-violet-500/5"
            : "hover:bg-muted/30"
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium">Standard encoder</span>
          <Badge variant="secondary" className="text-[10px]">
            Included
          </Badge>
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">
          The encoder published with {ctl.model.name}.
        </p>
      </button>

      {encoders.map((encoder) => {
        const download = downloads.find(
          (entry) =>
            entry.category === "image_gen_text_encoder" &&
            (entry.filename === encoder.encoder_id ||
              entry.metadata?.["text_encoder_id"] === encoder.encoder_id),
        );
        const downloading =
          download?.status === "queued" || download?.status === "active";
        const retryable =
          !encoder.installed &&
          (download?.status === "failed" || download?.status === "cancelled");
        const isSelected = selectedId === encoder.encoder_id;
        return (
          <div
            key={encoder.encoder_id}
            className={`rounded-md border transition-colors ${
              isSelected
                ? "border-violet-500 bg-violet-500/5"
                : "hover:bg-muted/30"
            }`}
          >
            <button
              type="button"
              role="radio"
              aria-checked={isSelected}
              onClick={() => selectEncoder(encoder.encoder_id)}
              className="w-full px-3 py-2 text-left"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs font-medium">{encoder.name}</span>
                <span className="flex shrink-0 flex-wrap justify-end gap-1">
                  {encoder.unverified && (
                    <Badge variant="outline" className="text-[9px]">
                      Unverified
                    </Badge>
                  )}
                  <Badge variant="outline" className="text-[9px] uppercase">
                    {encoder.format}
                  </Badge>
                  {encoder.installed ? (
                    <Badge variant="secondary" className="text-[9px]">
                      Installed
                    </Badge>
                  ) : downloading ? (
                    <Badge variant="secondary" className="text-[9px]">
                      <Loader2 className="mr-1 h-2.5 w-2.5 animate-spin" />
                      {Math.round(download?.percent ?? 0)}%
                    </Badge>
                  ) : (
                    <Badge variant="secondary" className="text-[9px]">
                      <Download className="mr-1 h-2.5 w-2.5" />
                      {encoder.download_size_gb > 0
                        ? formatGb(encoder.download_size_gb)
                        : "On demand"}
                    </Badge>
                  )}
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                {encoder.description}
              </p>
              <p className="mt-1 text-[10px] text-muted-foreground/80">
                License: {encoder.license}
                {encoder.requires_hf_token
                  ? " · Hugging Face access approval and token required"
                  : ""}
              </p>
            </button>
            {retryable && (
              <div className="flex items-start gap-2 border-t px-3 py-2 text-[11px] text-destructive">
                <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                <span className="min-w-0 flex-1 break-words">
                  {download?.error_msg ??
                    (download?.status === "cancelled"
                      ? "Download cancelled"
                      : "Download failed")}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[10px]"
                  onClick={() =>
                    void downloadTextEncoder(
                      ctl.model!.model_id,
                      encoder.encoder_id,
                    )
                  }
                >
                  Retry
                </Button>
              </div>
            )}
          </div>
        );
      })}

      {selectedId !== null && selected === undefined && (
        <ErrorNote
          message={`The recorded text encoder '${selectedId}' is no longer offered for this model. Choose Standard or another alternative.`}
        />
      )}
    </div>
  );
}

// ── Advanced / notices / actions / header ────────────────────────────────────

export function ImageAdvancedSection({ ctl }: { ctl: ImageGenController }) {
  const [, actions] = useMediaGenApp();
  const { setImageForm, resetImageAdvanced } = actions;
  if (!ctl.defaults) return null;
  return (
    <AdvancedParamsEditor
      defaults={ctl.defaults.advanced}
      text={ctl.form.advancedText}
      onChange={(advancedText) => setImageForm({ advancedText })}
      onReset={resetImageAdvanced}
    />
  );
}

/** LOUD params-endpoint failure banner with retry (null when healthy). */
export function ImageParamsErrorNotice({ ctl }: { ctl: ImageGenController }) {
  const [, actions] = useMediaGenApp();
  const { prepareImageGenerate } = actions;
  if (!ctl.form.paramsError || !ctl.model) return null;
  const model = ctl.model;
  return (
    <ParamsErrorBanner
      error={ctl.form.paramsError}
      onRetry={() => void prepareImageGenerate(model)}
    />
  );
}

/** Generation error + queue-first notice, in canonical order. */
export function ImageFormNotices({ ctl }: { ctl: ImageGenController }) {
  const [state, actions] = useMediaGenApp();
  const { imageQueueNotice } = state;
  const { clearImageQueueNotice } = actions;
  return (
    <>
      {ctl.genError && (
        <ErrorNote message={ctl.genError} onDismiss={ctl.dismissGenError} />
      )}
      {imageQueueNotice && (
        <QueueNotice
          message={imageQueueNotice}
          onDismiss={clearImageQueueNotice}
        />
      )}
    </>
  );
}

/** Generate (cancelable) + Add-to-queue + Reset — one implementation, every slot. */
export function ImageGenerateActions({
  ctl,
  size = "default",
  buttonClassName = "w-full",
  queueLabel = "Add to queue",
  extraDisabled = false,
}: {
  ctl: ImageGenController;
  size?: "default" | "sm" | "lg" | "icon";
  buttonClassName?: string;
  queueLabel?: string;
  /** Extra gating from the layout (e.g. mode not ready). */
  extraDisabled?: boolean;
  /** @deprecated Ignored — all action rows share one layout. */
  compact?: boolean;
}) {
  const [state, actions] = useMediaGenApp();
  const { imageGenerating, imageCancelling, imageGenStartedAt } = state;
  const { cancelImageGeneration, resetImageCommon } = actions;
  const disabled = ctl.formInvalid || extraDisabled;
  return (
    <div className="flex gap-2">
      <CancelableGenerateButton
        generating={imageGenerating}
        cancelling={imageCancelling}
        startedAt={imageGenStartedAt}
        disabled={disabled}
        onGenerate={() => void ctl.handleGenerate()}
        onCancel={() => void cancelImageGeneration()}
        containerClassName="flex-1 min-w-0"
        size={size}
        buttonClassName={buttonClassName}
        showWorkingNote={false}
        idleContent={
          <>
            <ImageIcon className="mr-2 h-4 w-4" />
            {ctl.isRevision ? "Apply change" : "Generate"}
          </>
        }
      />
      <Button
        variant="outline"
        size={size}
        disabled={disabled}
        onClick={() => void ctl.handleEnqueue()}
        title="Add to queue"
        className="flex-1"
      >
        <ListPlus className="mr-2 h-4 w-4" />
        {ctl.isRevision ? "Queue revision" : queueLabel}
      </Button>
      <ResetButton
        onClick={resetImageCommon}
        label="Reset settings"
        disabled={ctl.form.paramsLoading}
      />
    </div>
  );
}

/** Model name + provider badge + reset-all / switch / unload header row. */
export function ImageFormHeader({
  ctl,
  onSwitchModel,
}: {
  ctl: ImageGenController;
  /** When set, renders a "Switch model" button (Workspace nav). */
  onSwitchModel?: () => void;
}) {
  const [, actions] = useMediaGenApp();
  const { resetImageAll } = actions;
  if (!ctl.model) return null;
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold">{ctl.model.name}</span>
        <Badge variant="outline" className="text-[10px]">
          {ctl.model.provider}
        </Badge>
      </div>
      <div className="flex gap-2 items-center">
        <ResetButton onClick={resetImageAll} label="Reset all settings" />
        {onSwitchModel && (
          <Button size="sm" variant="ghost" onClick={onSwitchModel}>
            Switch model
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void ctl.handleUnload()}
        >
          Unload model
        </Button>
      </div>
    </div>
  );
}

/** Loading placeholder while a model's parameter schema is being fetched. */
export function ImageParamsLoading({ ctl }: { ctl: ImageGenController }) {
  return (
    <div className="flex items-center justify-center py-16 gap-3 text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
      <span className="text-sm">
        Loading {ctl.model?.name ?? "model"}'s parameters…
      </span>
    </div>
  );
}

// ── Full composition ─────────────────────────────────────────────────────────

/**
 * The complete vertical image generate form. Layouts that need a different
 * arrangement (Gallery popovers, Focus steps) compose the blocks directly.
 */
// ── Single ⇄ Batch mode ──────────────────────────────────────────────────────

/**
 * Which mode the generate form is in. Persisted so it survives a reload — a
 * user who works in batches should not have to re-pick it every session.
 * localStorage (not context) because it is a lone boolean-ish preference, and
 * the matrix STATE it reveals already lives in PromptMatrixContext.
 */
export type ImageGenMode = "single" | "batch";

const MODE_KEY = "matrx-image-gen-mode";

function readMode(): ImageGenMode {
  return localStorage.getItem(MODE_KEY) === "batch" ? "batch" : "single";
}

export function useImageGenMode(): [ImageGenMode, (m: ImageGenMode) => void] {
  const [mode, setModeState] = useState<ImageGenMode>(readMode);
  const setMode = useCallback((m: ImageGenMode) => {
    setModeState(m);
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch (err) {
      // Non-fatal (the mode still applies this session) but never silent.
      console.error("[media-gen] Could not persist the generate mode:", err);
    }
  }, []);
  return [mode, setMode];
}

export function ImageGenModeToggle({
  mode,
  onChange,
  queuedCount = 0,
}: {
  mode: ImageGenMode;
  onChange: (mode: ImageGenMode) => void;
  queuedCount?: number;
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="inline-flex rounded-md border p-0.5">
        <Button
          variant={mode === "single" ? "secondary" : "ghost"}
          size="sm"
          className="h-6 gap-1.5 px-2 text-xs"
          onClick={() => onChange("single")}
        >
          <ImageIcon className="h-3.5 w-3.5" />
          Single
        </Button>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant={mode === "batch" ? "secondary" : "ghost"}
              size="sm"
              className="h-6 gap-1.5 px-2 text-xs"
              onClick={() => onChange("batch")}
            >
              <Layers className="h-3.5 w-3.5" />
              Batch
            </Button>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            Write one prompt with {"{{variables}}"}, give each variable a list
            of options, and queue every combination in one go.
          </TooltipContent>
        </Tooltip>
      </div>
      {queuedCount > 0 && (
        <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
          {queuedCount} in queue
        </Badge>
      )}
    </div>
  );
}

export function ImageGenerateForm({
  ctl,
  hideActions = false,
  hideNotices = false,
  showHeader = false,
  onSwitchModel,
}: {
  ctl: ImageGenController;
  hideActions?: boolean;
  hideNotices?: boolean;
  showHeader?: boolean;
  onSwitchModel?: () => void;
}) {
  const negativePanel = usePersistedToggle(IMAGE_GEN_PANEL_KEYS.negative);
  const inputImagePanel = usePersistedToggle(IMAGE_GEN_PANEL_KEYS.inputImage);
  const lorasPanel = usePersistedToggle(IMAGE_GEN_PANEL_KEYS.loras);
  const advancedPanel = usePersistedToggle(IMAGE_GEN_PANEL_KEYS.advanced);
  const activeLoraCount = ctl.form.loras.filter((lora) => lora.enabled).length;
  const supportsImg2Img = ctl.defaults?.supportsImg2Img ?? false;

  const panels: ImageFormPanelToggles = {
    showNegative: negativePanel.open,
    onToggleNegative: negativePanel.toggle,
    onRevealNegative: negativePanel.reveal,
    showInputImage: inputImagePanel.open,
    onToggleInputImage: inputImagePanel.toggle,
    showInputImageButton: supportsImg2Img,
    showLoras: lorasPanel.open,
    onToggleLoras: lorasPanel.toggle,
    activeLoraCount,
    showAdvanced: advancedPanel.open,
    onToggleAdvanced: advancedPanel.toggle,
  };

  const renderActions = useCallback(
    () => <ImageGenerateActions ctl={ctl} />,
    [ctl],
  );

  return (
    <div className="space-y-4">
      {showHeader && (
        <ImageFormHeader
          ctl={ctl}
          {...(onSwitchModel !== undefined ? { onSwitchModel } : {})}
        />
      )}
      {!hideNotices && <ImageParamsErrorNotice ctl={ctl} />}

      <ImagePromptField
        ctl={ctl}
        showActions
        renderActions={renderActions}
        panels={panels}
      />

      <ImageCommonSettings ctl={ctl} showNegative={false} layout="row" />
      {inputImagePanel.open && supportsImg2Img && (
        <InputImageControl ctl={ctl} compact />
      )}
      <AlternativeTextEncodersSection ctl={ctl} />
      {lorasPanel.open && <LoraStylesSection ctl={ctl} />}
      {advancedPanel.open && <ImageAdvancedSection ctl={ctl} />}
      {!hideNotices && <ImageFormNotices ctl={ctl} />}

      {!hideActions && renderActions()}
    </div>
  );
}
