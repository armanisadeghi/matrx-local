/**
 * ImageGenSection — the "Images" experience of the media-gen tab.
 *
 * Structure: two always-visible sub-tabs — **Generate** (form + queue +
 * results) and **Models** (catalog: download / load).  ALL form state lives in
 * MediaGenContext (imageForm), so navigating anywhere and back restores the
 * exact working state (prompt, params, results, sub-tab).
 *
 * Settings doctrine: every parameter the engine accepts is visible.  Common
 * controls are rendered beautifully with the model's defaults labeled; every
 * remaining pipeline kwarg lives in the editable advanced-JSON editor.  Reset
 * affordances exist per group and as a master reset.
 */

import { useEffect, useMemo, useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  ExternalLink,
  Image as ImageIcon,
  KeyRound,
  ListPlus,
  Loader2,
  PackagePlus,
  RefreshCw,
  UserPlus,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenModelInfo, ImageGenJob } from "@/lib/api";
import { engine } from "@/lib/api";
import type { ImageGenerateInput } from "@/hooks/use-media-gen";
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
  SeedChip,
  ResetButton,
  NumberSliderField,
  DimensionPicker,
  dimensionError,
  NegativePromptField,
  AdvancedParamsEditor,
  ParamsErrorBanner,
  GeneratedImageView,
  computeAdvancedOverrides,
  parseSeedText,
  randomSeed,
  CancelableGenerateButton,
  ModelLoadingNotice,
  StillWorkingNote,
} from "./shared";
import type { SizePreset } from "./shared";

function classifyImageGenLoadError(
  message: string,
  engineConnected: boolean,
): { title: string; hint: string; kind: "engine" | "auth" | "generic" } {
  if (!engineConnected) {
    return {
      title: "Local engine not connected",
      hint: "The Matrx engine on your computer is not reachable yet. Wait for it to finish starting, or restart the app. Then use Try again below.",
      kind: "engine",
    };
  }
  const lower = message.toLowerCase();
  if (
    lower.includes("authorization") ||
    lower.includes("401") ||
    lower.includes("bearer")
  ) {
    return {
      title: "Sign in to Matrx required",
      hint: "This feature talks to the secure copy of the engine on your device. That requires an active Matrx account session (the same sign-in as the rest of the app). Your Hugging Face read token (for gated image checkpoints) is saved under Settings → API keys — not on the Configurations page.",
      kind: "auth",
    };
  }
  return {
    title: "Could not load image generation",
    hint: message,
    kind: "generic",
  };
}

// ── Model card ────────────────────────────────────────────────────────────────

function ImageModelCard({
  model,
  isLoaded,
  isLoadingThis,
  anyLoadInFlight,
  onLoad,
  onDownload,
  onGenerate,
}: {
  model: ImageGenModelInfo;
  isLoaded: boolean;
  /** True only for the card whose model is currently loading into memory. */
  isLoadingThis: boolean;
  /** True while any model load is in flight — disables sibling Load buttons. */
  anyLoadInFlight: boolean;
  onLoad: (m: ImageGenModelInfo) => void;
  onDownload: (m: ImageGenModelInfo) => void;
  onGenerate: (m: ImageGenModelInfo) => void;
}) {
  const { downloads, openModal } = useDownloadManager();
  const dl = useMemo(
    () => findModelDownload(downloads, "image_gen", model.model_id),
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

// ── Queue panel ───────────────────────────────────────────────────────────────

function ImageJobRow({
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
  const cancelling = active && !!job.cancel_requested;
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
            {job.model_id || "—"} · {cancelling ? "cancelling…" : job.status}
            {job.status === "failed" && job.error ? ` — ${job.error}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {typeof job.seed === "number" && (
            <SeedChip seed={job.seed} onReuse={onReuseSeed} />
          )}
          {cancelling ? (
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

// ── Section ───────────────────────────────────────────────────────────────────

export function ImageGenSection() {
  const navigate = useNavigate();
  const { isAuthenticated, signInWithOAuth } = useAuth();
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading,
    loadingImageModelId,
    imageGenerating,
    imageCancelling,
    imageGenStartedAt,
    imageLoadStartedAt,
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
    unloadImageModel,
    downloadImageModel,
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

  // Only the load-error banner is component-local; ALL form state is in
  // context so it survives navigating away and back.
  const [localError, setLocalError] = useState<string | null>(null);

  // When an image_gen download completes, refresh the catalog so
  // `is_downloaded` flips without a manual reload.  Gated narrowly on the
  // COUNT of completed entries in this category — not the downloads array.
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

  // The model the Generate tab works with.
  const generateModelId =
    imageForm.defaults?.modelId ??
    selectedImageModelId ??
    imageStatus?.loaded_model_id ??
    null;
  const selectedModel = useMemo(
    () => imageModels.find((m) => m.model_id === generateModelId) ?? null,
    [imageModels, generateModelId],
  );

  // If the Generate tab is open but the form defaults belong to a different
  // model (or none — e.g. a model was already loaded when the app started),
  // fetch that model's full parameter schema.  Guarded so it runs once per
  // model change, never in a loop.
  useEffect(() => {
    if (imageForm.view !== "generate") return;
    if (imageForm.paramsLoading) return;
    if (!selectedModel) return;
    if (imageForm.defaults?.modelId === selectedModel.model_id) return;
    void prepareImageGenerate(selectedModel);
  }, [
    imageForm.view,
    imageForm.paramsLoading,
    imageForm.defaults?.modelId,
    selectedModel,
    prepareImageGenerate,
  ]);

  const handleLoadModel = useCallback(
    async (model: ImageGenModelInfo) => {
      setLocalError(null);
      const result = await loadImageModel(model.model_id);
      if (result.success) {
        await prepareImageGenerate(model);
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

  const handleDownloadModel = useCallback(
    (model: ImageGenModelInfo) => {
      setLocalError(null);
      void downloadImageModel(model.model_id);
    },
    [downloadImageModel],
  );

  const handleOpenGenerate = useCallback(
    (model: ImageGenModelInfo) => {
      if (imageForm.defaults?.modelId === model.model_id) {
        // Same model — keep the user's tweaked settings, just switch tabs.
        setImageForm({ view: "generate" });
      } else {
        void prepareImageGenerate(model);
      }
    },
    [imageForm.defaults?.modelId, setImageForm, prepareImageGenerate],
  );

  const handleUnload = useCallback(async () => {
    await unloadImageModel();
  }, [unloadImageModel]);

  // ── Validation + request building ────────────────────────────────────────
  const defaults = imageForm.defaults;
  const advanced = useMemo(
    () =>
      computeAdvancedOverrides(
        imageForm.advancedText,
        defaults?.advanced ?? {},
      ),
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
    // Queue and clear NOTHING — the prompt stays editable so the user can
    // immediately write the next one.
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

  // ── Loading / error / installer states ─────────────────────────────────
  if (imageStatusLoading && !imageStatus) {
    return (
      <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="text-sm">Checking image generation status…</span>
      </div>
    );
  }

  if (imageStatusError) {
    const engineConnected = !!engine.engineUrl;
    const { title, hint, kind } = classifyImageGenLoadError(
      imageStatusError,
      engineConnected,
    );
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-4 space-y-3">
        <div className="flex items-start gap-3">
          <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
          <div className="text-sm space-y-1.5 min-w-0">
            <p className="font-medium text-foreground">{title}</p>
            <p className="text-muted-foreground text-xs leading-relaxed">
              {hint}
            </p>
            {kind !== "auth" && (
              <p className="text-[11px] font-mono text-muted-foreground/90 break-all pt-0.5">
                {imageStatusError}
              </p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void refreshImage()}
          >
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
            Try again
          </Button>
          {kind === "auth" && (
            <>
              {!isAuthenticated ? (
                <Button size="sm" onClick={() => void signInWithOAuth()}>
                  <UserPlus className="h-3.5 w-3.5 mr-1.5" />
                  Sign in to Matrx
                </Button>
              ) : null}
              <Button
                size="sm"
                variant="outline"
                onClick={() => navigate("/settings")}
              >
                <KeyRound className="h-3.5 w-3.5 mr-1.5" />
                Open Settings (account and API keys)
              </Button>
            </>
          )}
          {kind === "engine" ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate("/activity")}
            >
              View Activity / logs
            </Button>
          ) : null}
        </div>
      </div>
    );
  }

  // Not available — deps not installed → show one-click installer
  if (imageStatus && !imageStatus.available) {
    return (
      <ImageGenInstaller
        models={imageModels}
        onInstallComplete={() => void refreshImage()}
      />
    );
  }

  // Packages installed but outdated → upgrade banner + installer flow
  const outdatedBanner =
    imageStatus?.packages_outdated === true ? (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3 flex items-start gap-3">
        <PackagePlus className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="text-sm font-medium">Update AI packages</p>
            <p className="text-xs text-muted-foreground leading-relaxed">
              Your on-device AI packages
              {imageStatus.packages_version
                ? ` (diffusers ${imageStatus.packages_version})`
                : ""}{" "}
              are older than required for the latest image and video models.
              Update to unlock the new model catalog.
            </p>
          </div>
        </div>
      </div>
    ) : null;

  if (imageStatus?.packages_outdated && imageForm.view === "models") {
    return (
      <div className="space-y-4 pb-8">
        {outdatedBanner}
        <ImageGenInstaller
          models={[]}
          upgrade
          onInstallComplete={() => void refreshImage()}
        />
      </div>
    );
  }

  return (
    <div className="space-y-5 pb-8">
      {outdatedBanner}

      <SubTabBar
        tabs={[
          { id: "generate" as const, label: "Generate", badge: activeJobCount },
          { id: "models" as const, label: "Models" },
        ]}
        active={imageForm.view}
        onSelect={(view) => setImageForm({ view })}
      />

      {imageForm.view === "models" ? (
        /* ── Models sub-tab ──────────────────────────────────────────────── */
        <div className="space-y-6">
          {imageStatus?.loaded_model_id && (
            <div className="flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 px-4 py-3">
              <div className="flex items-center gap-2 text-sm">
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                <span>
                  Model loaded:{" "}
                  <span className="font-medium">
                    {imageStatus.loaded_model_id}
                  </span>
                </span>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    const m = imageModels.find(
                      (x) => x.model_id === imageStatus.loaded_model_id,
                    );
                    if (m) handleOpenGenerate(m);
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

          <div className="space-y-3">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <ImageIcon className="h-4 w-4 text-violet-500" />
              Select a model
            </h3>
            {genError && (
              <ErrorNote message={genError} onDismiss={dismissGenError} />
            )}
            <ModelLoadingNotice
              loading={imageModelLoading || !!imageStatus?.is_loading}
              startedAt={imageLoadStartedAt}
              loadError={imageStatus?.load_error}
              what={loadingImageModelId ?? "model"}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              {imageModels.map((m) => (
                <ImageModelCard
                  key={m.model_id}
                  model={m}
                  isLoaded={imageStatus?.loaded_model_id === m.model_id}
                  isLoadingThis={loadingImageModelId === m.model_id}
                  anyLoadInFlight={
                    imageModelLoading || !!imageStatus?.is_loading
                  }
                  onLoad={(model) => void handleLoadModel(model)}
                  onDownload={handleDownloadModel}
                  onGenerate={handleOpenGenerate}
                />
              ))}
            </div>
          </div>
        </div>
      ) : !selectedModel ? (
        /* ── Generate sub-tab, no model yet ─────────────────────────────── */
        <div className="rounded-xl border border-dashed px-5 py-10 flex flex-col items-center text-center gap-3">
          <ImageIcon className="h-8 w-8 text-muted-foreground/40" />
          <p className="text-sm font-medium">No model selected</p>
          <p className="text-xs text-muted-foreground max-w-sm">
            Pick a model in the Models tab — its full settings will appear here.
          </p>
          <Button size="sm" onClick={() => setImageForm({ view: "models" })}>
            Choose a model
          </Button>
        </div>
      ) : imageForm.paramsLoading || !defaults ? (
        <div className="flex items-center justify-center py-16 gap-3 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="text-sm">
            Loading {selectedModel.name}'s parameters…
          </span>
        </div>
      ) : (
        /* ── Generate sub-tab ───────────────────────────────────────────── */
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
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void handleUnload()}
              >
                Unload model
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

              {genError && (
                <ErrorNote message={genError} onDismiss={dismissGenError} />
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

          {/* ── Queue ────────────────────────────────────────────────────── */}
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
                  <ImageJobRow
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
