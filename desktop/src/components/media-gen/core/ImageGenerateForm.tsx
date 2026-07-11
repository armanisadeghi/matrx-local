/**
 * ImageGenerateForm — the canonical image generate-form building blocks.
 * Every layout composes these; none re-implements a control:
 *
 *  - ImagePromptField        prompt + per-family capacity hint
 *  - ImageCommonSettings     negative → steps/guidance → size → seed (+reset)
 *  - InputImageControl       img2img: drop/pick/paste + preview + strength
 *  - LoraStylesSection       "Styles (LoRA)": installed toggles/scales,
 *                            base-family mismatch warnings, Get-more dialog
 *                            with DownloadManager progress + HF repo paste
 *  - ImageAdvancedSection    editable advanced-JSON (every pipeline kwarg)
 *  - ImageFormNotices        params-error banner + gen error + queue notice
 *  - ImageGenerateActions    CancelableGenerateButton + Add-to-queue
 *  - ImageFormHeader         model name/provider + reset-all + switch/unload
 *  - ImageGenerateForm       the full vertical composition of all the above
 *
 * All state lives in MediaGenContext via the ImageGenController — these are
 * pure views over it, configurable by layout-level props only.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Download,
  Image as ImageIcon,
  ImagePlus,
  KeyRound,
  ListPlus,
  Loader2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenLoraInfo } from "@/lib/api";
import { IMG2IMG_DEFAULT_STRENGTH } from "@/hooks/use-media-gen";
import type { SelectedLora } from "@/hooks/use-media-gen";
import {
  AdvancedParamsEditor,
  CancelableGenerateButton,
  DimensionPicker,
  ErrorNote,
  NegativePromptField,
  NumberSliderField,
  ParamsErrorBanner,
  PromptCapacityHint,
  QueueNotice,
  ResetButton,
  SeedInput,
  formatGb,
} from "@/components/media-gen/shared";
import type { ImageGenController } from "./imageController";

// ── Prompt ───────────────────────────────────────────────────────────────────

export function ImagePromptField({
  ctl,
  placeholder = "Describe the image you want to generate…",
  textareaClassName = "text-sm min-h-[100px] max-h-[320px] resize-y",
  showLabel = true,
}: {
  ctl: ImageGenController;
  placeholder?: string;
  textareaClassName?: string;
  showLabel?: boolean;
}) {
  const [, actions] = useMediaGenApp();
  return (
    <div className="space-y-1.5">
      {showLabel && <Label className="text-xs">Prompt</Label>}
      <Textarea
        value={ctl.form.prompt}
        onChange={(e) => actions.setImageForm({ prompt: e.target.value })}
        placeholder={placeholder}
        className={textareaClassName}
      />
      <PromptCapacityHint pipelineType={ctl.model?.pipeline_type} />
    </div>
  );
}

// ── Common settings ──────────────────────────────────────────────────────────

export function ImageCommonSettings({
  ctl,
  showNegative = true,
  showSeed = true,
  showReset = true,
  sliderColumns = true,
}: {
  ctl: ImageGenController;
  showNegative?: boolean;
  showSeed?: boolean;
  showReset?: boolean;
  /** Steps/Guidance side by side (true) or stacked (false). */
  sliderColumns?: boolean;
}) {
  const [, actions] = useMediaGenApp();
  const { setImageForm, resetImageCommon } = actions;
  const d = ctl.defaults;
  if (!d) return null;
  const disabled = ctl.form.paramsLoading;
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
      />
    </>
  );
  return (
    <div className="space-y-4">
      {showNegative && (
        <NegativePromptField
          supported={d.supportsNegativePrompt}
          value={ctl.form.negativePrompt}
          onChange={(v) => setImageForm({ negativePrompt: v })}
          disabled={disabled}
        />
      )}
      {sliderColumns ? (
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
      />
      {showSeed && (
        <div className="space-y-1.5">
          <Label className="text-xs">
            Seed{" "}
            <span className="text-muted-foreground">
              (blank = random — the used seed is shown on the result)
            </span>
          </Label>
          <SeedInput
            value={ctl.form.seedText}
            onChange={(seedText) => setImageForm({ seedText })}
            disabled={disabled}
          />
        </div>
      )}
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
export function InputImageControl({ ctl }: { ctl: ImageGenController }) {
  const [, actions] = useMediaGenApp();
  const { setImageForm } = actions;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const d = ctl.defaults;
  if (!d?.supportsImg2Img) return null;
  const img = ctl.form.initImage;

  return (
    <div className="space-y-2">
      <Label className="text-xs">
        Input image{" "}
        <span className="text-muted-foreground">
          (optional — generate from this image instead of noise)
        </span>
      </Label>
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
          <img
            src={img.previewUrl}
            alt="Input"
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
            dragOver
              ? "border-violet-500 bg-violet-500/5"
              : "hover:bg-muted/20"
          }`}
          title="Click to choose an image, or drop / paste one here"
        >
          <ImagePlus className="h-5 w-5 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">
            Drop an image here, click to choose, or paste
          </p>
          <p className="text-[10px] text-muted-foreground/70">
            PNG / JPEG / WebP, up to 20 MB
          </p>
        </div>
      )}
      {img && d.strength !== null && (
        <NumberSliderField
          label="How much to change the input"
          value={ctl.form.strength}
          onChange={(strength) => setImageForm({ strength })}
          min={0}
          max={1}
          step={0.05}
          defaultValue={d.strength ?? IMG2IMG_DEFAULT_STRENGTH}
        />
      )}
      {img && d.strength === null && (
        <p className="text-[11px] text-muted-foreground">
          This model edits from the reference image and your prompt directly —
          it has no strength control.
        </p>
      )}
    </div>
  );
}

// ── LoRA styles ──────────────────────────────────────────────────────────────

/**
 * Loose base-family ↔ pipeline-type match; unknowns never warn (the engine
 * is the authority and errors loudly on a real mismatch).
 */
export function loraFamilyMatches(
  baseFamily: string,
  pipelineType: string | null | undefined,
): boolean {
  if (!baseFamily || !pipelineType) return true;
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  const aliases: Record<string, string> = {
    sdxl: "stablediffusionxl",
    sd: "stablediffusion",
    sd15: "stablediffusion",
  };
  const bfRaw = norm(baseFamily);
  const bf = aliases[bfRaw] ?? bfRaw;
  const pt = norm(pipelineType);
  return pt.includes(bf) || bf.includes(pt);
}

function selectedLoraOf(
  loras: SelectedLora[],
  id: string,
): SelectedLora | null {
  return loras.find((l) => l.id === id) ?? null;
}

function InstalledLoraRow({
  lora,
  ctl,
}: {
  lora: ImageGenLoraInfo;
  ctl: ImageGenController;
}) {
  const [, actions] = useMediaGenApp();
  const { setImageForm, deleteLora } = actions;
  const selected = selectedLoraOf(ctl.form.loras, lora.id);
  const enabled = selected?.enabled ?? false;
  const scale = selected?.scale ?? 1;
  const mismatch =
    enabled && !loraFamilyMatches(lora.base_family, ctl.model?.pipeline_type);

  const setSelection = (patch: Partial<SelectedLora>) => {
    const others = ctl.form.loras.filter((l) => l.id !== lora.id);
    setImageForm({
      loras: [
        ...others,
        { id: lora.id, scale, enabled, ...patch },
      ],
    });
  };

  return (
    <div className="space-y-2 rounded-lg border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setSelection({ enabled: e.target.checked })}
          aria-label={`Enable ${lora.id}`}
          className="h-3.5 w-3.5 accent-violet-500"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium" title={lora.repo_id}>
            {lora.id}
          </p>
          <p className="truncate text-[10px] text-muted-foreground">
            {lora.repo_id}
            {lora.size_bytes > 0
              ? ` · ${formatGb(lora.size_bytes / 1024 ** 3)}`
              : ""}
            {lora.source ? ` · ${lora.source}` : ""}
          </p>
        </div>
        {lora.base_family && (
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] shrink-0 ${
              mismatch
                ? "bg-amber-500/20 text-amber-600 dark:text-amber-400"
                : "bg-muted text-muted-foreground"
            }`}
            title={
              mismatch
                ? `This LoRA targets ${lora.base_family}; the selected model is ${ctl.model?.pipeline_type ?? "unknown"}`
                : `Base family: ${lora.base_family}`
            }
          >
            {lora.base_family}
          </span>
        )}
        <button
          type="button"
          onClick={() => void deleteLora(lora.id)}
          className="shrink-0 text-muted-foreground hover:text-destructive"
          aria-label={`Remove ${lora.id}`}
          title="Delete this LoRA from disk"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
      {mismatch && (
        <p className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
          <AlertCircle className="h-3 w-3 shrink-0" />
          Family mismatch — trained for {lora.base_family}, the selected model
          is {ctl.model?.pipeline_type}. The engine will refuse a real
          incompatibility.
        </p>
      )}
      {enabled && (
        <div className="flex items-center gap-2 pl-5">
          <span className="text-[10px] text-muted-foreground shrink-0 w-16">
            Strength
          </span>
          <Slider
            min={0}
            max={1.5}
            step={0.05}
            value={[scale]}
            onValueChange={([v]) => setSelection({ scale: v })}
            className="flex-1"
          />
          <span className="w-9 text-right text-[10px] tabular-nums text-muted-foreground">
            {scale.toFixed(2)}
          </span>
        </div>
      )}
    </div>
  );
}

function LoraCatalogDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [state, actions] = useMediaGenApp();
  const { loraList, loraError, loraDownloads, loraNeedsCivitaiKey } = state;
  const { downloadLora, refreshLoras } = actions;
  const { downloads } = useDownloadManager();
  const navigate = useNavigate();
  const [repoInput, setRepoInput] = useState("");

  // Live progress: repo_id → DownloadEntry, joined by the returned id.
  const entryByRepo = useMemo(() => {
    const map: Record<string, (typeof downloads)[number]> = {};
    for (const [repoId, dlId] of Object.entries(loraDownloads)) {
      const entry = downloads.find((d) => d.id === dlId);
      if (entry) map[repoId] = entry;
    }
    return map;
  }, [downloads, loraDownloads]);

  // When a tracked LoRA download completes, refresh the installed list.
  const completedTracked = useMemo(
    () =>
      Object.values(entryByRepo).filter((d) => d.status === "completed")
        .length,
    [entryByRepo],
  );
  useEffect(() => {
    if (completedTracked === 0) return;
    void refreshLoras();
  }, [completedTracked, refreshLoras]);

  const installedIds = new Set(
    (loraList?.installed ?? []).map((l) => l.repo_id),
  );
  const catalog = (loraList?.catalog ?? []).filter(
    (c) => !installedIds.has(c.repo_id),
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Get more styles (LoRA)</DialogTitle>
          <DialogDescription>
            Download style adapters from the curated catalog, or paste any
            Hugging Face LoRA repo or Civitai link.
          </DialogDescription>
        </DialogHeader>
        {loraError && <ErrorNote message={loraError} />}
        {loraNeedsCivitaiKey && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2.5">
            <p className="flex items-start gap-2 text-[11px] leading-relaxed text-amber-600 dark:text-amber-400">
              <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              Civitai downloads need your Civitai API key.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="h-7 shrink-0 text-xs"
              onClick={() => navigate("/settings?tab=api-keys")}
            >
              Set your Civitai API key
            </Button>
          </div>
        )}
        <div className="max-h-[45vh] space-y-2 overflow-y-auto pr-1">
          {catalog.map((c) => {
            const dl = entryByRepo[c.repo_id];
            const downloading =
              dl && (dl.status === "active" || dl.status === "queued");
            return (
              <div
                key={c.repo_id}
                className="space-y-1.5 rounded-lg border px-3 py-2.5"
              >
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{c.id}</p>
                    <p className="truncate text-[10px] text-muted-foreground">
                      {c.repo_id}
                      {c.base_family ? ` · ${c.base_family}` : ""}
                      {c.size_bytes > 0
                        ? ` · ${formatGb(c.size_bytes / 1024 ** 3)}`
                        : ""}
                      {c.source ? ` · ${c.source}` : ""}
                      {c.unverified ? " · unverified" : ""}
                    </p>
                  </div>
                  {downloading ? (
                    <span className="text-[11px] tabular-nums text-violet-500">
                      {Math.round(dl.percent)}%
                    </span>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 px-2.5 text-xs"
                      onClick={() =>
                        void downloadLora(
                          c.repo_id,
                          c.weight_name ?? undefined,
                        )
                      }
                    >
                      <Download className="mr-1 h-3 w-3" />
                      Get
                    </Button>
                  )}
                </div>
                {downloading && (
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
                    <div
                      className="h-full rounded-full bg-violet-500 transition-[width] duration-300"
                      style={{
                        width: `${Math.min(100, Math.max(0, dl.percent))}%`,
                      }}
                    />
                  </div>
                )}
              </div>
            );
          })}
          {catalog.length === 0 && !loraError && (
            <p className="py-4 text-center text-xs text-muted-foreground">
              No catalog entries — paste a Hugging Face repo or Civitai link
              below.
            </p>
          )}
        </div>
        <div className="space-y-1.5 border-t pt-3">
          <Label className="text-xs">
            Hugging Face repo or Civitai link
          </Label>
          <div className="flex gap-2">
            <Input
              value={repoInput}
              onChange={(e) => setRepoInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && repoInput.trim()) {
                  void downloadLora(repoInput.trim());
                  setRepoInput("");
                }
              }}
              placeholder="e.g. ostris/super-cereal-sdxl-lora or https://civitai.com/models/…"
              className="text-sm"
            />
            <Button
              size="sm"
              disabled={!repoInput.trim()}
              onClick={() => {
                void downloadLora(repoInput.trim());
                setRepoInput("");
              }}
            >
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Download
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Collapsible "Styles (LoRA)" section. Always visible on the image form —
 * when the engine's LoRA endpoints are missing (404) the failure is shown
 * LOUDLY here rather than the section silently disappearing.
 */
export function LoraStylesSection({ ctl }: { ctl: ImageGenController }) {
  const [state] = useMediaGenApp();
  const { loraList, loraError } = state;
  const [open, setOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);

  const enabledCount = ctl.form.loras.filter((l) => l.enabled).length;
  const installed = loraList?.installed ?? [];
  // Enabled selections whose LoRA is no longer installed — warn, never hide.
  const missingEnabled = loraList
    ? ctl.form.loras.filter(
        (l) => l.enabled && !installed.some((i) => i.id === l.id),
      )
    : [];

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
          <Sparkles className="h-3.5 w-3.5 text-violet-500" />
          Styles (LoRA)
          <span className="text-muted-foreground font-normal">
            (optional style adapters)
          </span>
        </span>
        {enabledCount > 0 && (
          <span className="rounded bg-violet-500/15 text-violet-600 dark:text-violet-400 px-1.5 py-0.5 text-[10px] font-medium shrink-0">
            {enabledCount} active
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-2 border-t px-3 py-2.5">
          {loraError && (
            <ErrorNote
              message={`LoRA styles unavailable — ${loraError}`}
            />
          )}
          {missingEnabled.length > 0 && (
            <ErrorNote
              message={`Enabled style${missingEnabled.length === 1 ? "" : "s"} no longer installed: ${missingEnabled.map((l) => l.id).join(", ")} — the engine will reject them by name.`}
            />
          )}
          {installed.map((l) => (
            <InstalledLoraRow key={l.id} lora={l} ctl={ctl} />
          ))}
          {!loraError && installed.length === 0 && (
            <p className="rounded-md border border-dashed px-3 py-3 text-center text-[11px] text-muted-foreground">
              No styles installed yet — use Get more to download one.
            </p>
          )}
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => setCatalogOpen(true)}
            >
              <Download className="mr-1.5 h-3 w-3" />
              Get more
            </Button>
          </div>
        </div>
      )}
      <LoraCatalogDialog open={catalogOpen} onOpenChange={setCatalogOpen} />
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

/** Generate (cancelable) + Add-to-queue action pair. */
export function ImageGenerateActions({
  ctl,
  size,
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
}) {
  const [state, actions] = useMediaGenApp();
  const { imageGenerating, imageCancelling, imageGenStartedAt } = state;
  const { cancelImageGeneration } = actions;
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
        containerClassName="flex-1"
        {...(size !== undefined ? { size } : {})}
        buttonClassName={buttonClassName}
        idleContent={
          <>
            <ImageIcon className="h-4 w-4 mr-2" />
            Generate
          </>
        }
      />
      <Button
        variant="outline"
        size={size}
        disabled={disabled}
        onClick={() => void ctl.handleEnqueue()}
        title="Queue this generation and keep editing — write the next prompt right away"
      >
        <ListPlus className="h-4 w-4 mr-2" />
        {queueLabel}
      </Button>
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
export function ImageGenerateForm({
  ctl,
  hideActions = false,
  hideNotices = false,
  showHeader = false,
  onSwitchModel,
}: {
  ctl: ImageGenController;
  /** Layout renders the actions elsewhere (e.g. Studio's sticky bar). */
  hideActions?: boolean;
  /** Layout renders errors/notices elsewhere. */
  hideNotices?: boolean;
  showHeader?: boolean;
  onSwitchModel?: () => void;
}) {
  return (
    <div className="space-y-4">
      {showHeader && (
        <ImageFormHeader ctl={ctl} {...(onSwitchModel !== undefined ? { onSwitchModel } : {})} />
      )}
      {!hideNotices && <ImageParamsErrorNotice ctl={ctl} />}
      <ImagePromptField ctl={ctl} />
      <ImageCommonSettings ctl={ctl} />
      <InputImageControl ctl={ctl} />
      <LoraStylesSection ctl={ctl} />
      <ImageAdvancedSection ctl={ctl} />
      {!hideNotices && <ImageFormNotices ctl={ctl} />}
      {!hideActions && <ImageGenerateActions ctl={ctl} />}
    </div>
  );
}
