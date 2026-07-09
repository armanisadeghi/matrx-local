/**
 * ImageGenSection — the "Images" experience of the media-gen tab.
 *
 * Extracted from LocalModels.tsx (formerly MediaModelsTab).  All persistent
 * state (status, models, generated result, selected model) lives in
 * MediaGenContext so it survives tab switches; only transient form fields are
 * local to this component.
 */

import { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  ExternalLink,
  Image as ImageIcon,
  KeyRound,
  Loader2,
  PackagePlus,
  RefreshCw,
  UserPlus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenModelInfo } from "@/lib/api";
import { engine } from "@/lib/api";
import { ImageGenInstaller } from "./ImageGenInstaller";
import {
  StarRating,
  ErrorNote,
  InlineProgressBar,
  findModelDownload,
  formatGb,
  openExternalUrl,
} from "./shared";

type ImageGenView = "picker" | "generate" | "workflow";

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
  anyLoading,
  onLoad,
  onDownload,
  onGenerate,
}: {
  model: ImageGenModelInfo;
  isLoaded: boolean;
  anyLoading: boolean;
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

// ── Section ───────────────────────────────────────────────────────────────────

export function ImageGenSection() {
  const navigate = useNavigate();
  const { isAuthenticated, signInWithOAuth } = useAuth();
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imagePresets,
    imageStatusLoading,
    imageStatusError,
    imageModelLoading,
    imageGenerating,
    imageGenError,
    imageResult,
  } = state;
  const {
    refreshImage,
    loadImageModel,
    unloadImageModel,
    downloadImageModel,
    generateImage,
    generateImageWorkflow,
    setSelectedImageModelId,
    clearImageResult,
    clearImageGenError,
  } = actions;

  // Transient UI state — intentionally local (form fields / current view)
  const [view, setView] = useState<ImageGenView>("picker");
  const [selectedModel, setSelectedModel] = useState<ImageGenModelInfo | null>(
    null,
  );
  const [prompt, setPrompt] = useState("");
  const [negPrompt, setNegPrompt] = useState("");
  const [steps, setSteps] = useState<number | null>(null);
  const [guidance, setGuidance] = useState<number | null>(null);
  const [seedText, setSeedText] = useState("");
  const [workflowSubject, setWorkflowSubject] = useState("");
  const [workflowPresetId, setWorkflowPresetId] = useState<string>("");
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

  const parseSeed = useCallback((): number | undefined => {
    const trimmed = seedText.trim();
    if (!trimmed) return undefined;
    const n = Number(trimmed);
    return Number.isFinite(n) ? Math.floor(n) : undefined;
  }, [seedText]);

  const handleLoadModel = useCallback(
    async (model: ImageGenModelInfo) => {
      setLocalError(null);
      const result = await loadImageModel(model.model_id);
      if (result.success) {
        setSelectedModel(model);
        setSteps(model.recommended_steps);
        setGuidance(model.recommended_guidance);
        setView("generate");
      } else if (result.needs_download) {
        setLocalError(
          `${model.name} is not downloaded yet. Use the Download button first.`,
        );
      } else if (result.error) {
        setLocalError(result.error);
      }
    },
    [loadImageModel],
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
      setSelectedModel(model);
      setSelectedImageModelId(model.model_id);
      setSteps(model.recommended_steps);
      setGuidance(model.recommended_guidance);
      setView("generate");
    },
    [setSelectedImageModelId],
  );

  const handleUnload = useCallback(async () => {
    await unloadImageModel();
    setSelectedModel(null);
    setView("picker");
  }, [unloadImageModel]);

  const handleGenerate = useCallback(async () => {
    if (!selectedModel || !prompt.trim()) return;
    await generateImage({
      prompt: prompt.trim(),
      model_id: selectedModel.model_id,
      negative_prompt: negPrompt.trim() || undefined,
      steps: steps ?? undefined,
      guidance: guidance ?? undefined,
      seed: parseSeed(),
    });
  }, [
    selectedModel,
    prompt,
    negPrompt,
    steps,
    guidance,
    parseSeed,
    generateImage,
  ]);

  const handleWorkflowGenerate = useCallback(async () => {
    if (!workflowPresetId || !workflowSubject.trim()) return;
    const ok = await generateImageWorkflow({
      preset_id: workflowPresetId,
      subject: workflowSubject.trim(),
      model_id: selectedModel?.model_id,
      seed: parseSeed(),
    });
    // Only switch to the generate view when a result was actually produced.
    // On failure, stay put so the error banner (rendered here) stays visible —
    // never navigate to an empty "generating forever" screen.
    if (ok) setView("generate");
  }, [
    workflowPresetId,
    workflowSubject,
    selectedModel?.model_id,
    parseSeed,
    generateImageWorkflow,
  ]);

  const handleSavePng = useCallback(() => {
    if (!imageResult) return;
    const a = document.createElement("a");
    a.href = `data:image/png;base64,${imageResult.b64}`;
    a.download = `matrx-image-${Date.now()}.png`;
    a.click();
  }, [imageResult]);

  const genError = imageGenError ?? localError;
  const dismissGenError = useCallback(() => {
    setLocalError(null);
    clearImageGenError();
  }, [clearImageGenError]);

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

  if (imageStatus?.packages_outdated && view === "picker") {
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

  // ── Model picker ────────────────────────────────────────────────────────
  if (view === "picker") {
    return (
      <div className="space-y-6 pb-8">
        {/* Quick workflow strip */}
        {imagePresets.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Quick workflows</h3>
              <button
                onClick={() => setView("workflow")}
                className="text-xs text-violet-500 hover:underline"
              >
                Run a workflow →
              </button>
            </div>
            <div className="flex gap-2 flex-wrap">
              {imagePresets.map((p) => (
                <button
                  key={p.preset_id}
                  onClick={() => {
                    setWorkflowPresetId(p.preset_id);
                    setView("workflow");
                  }}
                  className="rounded-full border px-3 py-1 text-xs hover:bg-muted/30 transition-colors"
                >
                  {p.name}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Currently loaded indicator */}
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
              <Button size="sm" variant="ghost" onClick={() => void handleUnload()}>
                Unload
              </Button>
            </div>
          </div>
        )}

        {/* Model grid */}
        <div className="space-y-3">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <ImageIcon className="h-4 w-4 text-violet-500" />
            Select a model
          </h3>
          {genError && <ErrorNote message={genError} onDismiss={dismissGenError} />}
          <div className="grid gap-3 sm:grid-cols-2">
            {imageModels.map((m) => (
              <ImageModelCard
                key={m.model_id}
                model={m}
                isLoaded={imageStatus?.loaded_model_id === m.model_id}
                anyLoading={imageModelLoading || !!imageStatus?.is_loading}
                onLoad={(model) => void handleLoadModel(model)}
                onDownload={handleDownloadModel}
                onGenerate={handleOpenGenerate}
              />
            ))}
          </div>
        </div>
      </div>
    );
  }

  // ── Workflow view ────────────────────────────────────────────────────────
  if (view === "workflow") {
    const preset = imagePresets.find((p) => p.preset_id === workflowPresetId);
    const resolvedPrompt = preset
      ? preset.prompt_template.replace("{subject}", workflowSubject || "…")
      : "";

    return (
      <div className="space-y-5 pb-8 max-w-xl">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setView("picker")}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← Back
          </button>
          <span className="text-sm font-semibold">Quick Workflow</span>
        </div>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Workflow</Label>
            <div className="flex gap-2 flex-wrap">
              {imagePresets.map((p) => (
                <button
                  key={p.preset_id}
                  onClick={() => setWorkflowPresetId(p.preset_id)}
                  className={`rounded-full border px-3 py-1 text-xs transition-colors ${workflowPresetId === p.preset_id ? "border-violet-500 bg-violet-500/10 text-violet-600 dark:text-violet-400" : "hover:bg-muted/30"}`}
                >
                  {p.name}
                </button>
              ))}
            </div>
            {preset && (
              <p className="text-xs text-muted-foreground">
                {preset.description}
              </p>
            )}
          </div>

          {workflowPresetId && (
            <div className="space-y-1.5">
              <Label className="text-xs">Subject / Topic</Label>
              <Input
                value={workflowSubject}
                onChange={(e) => setWorkflowSubject(e.target.value)}
                placeholder={
                  preset?.name === "Photorealistic Portrait"
                    ? "a smiling woman in business attire"
                    : "describe your subject…"
                }
                className="text-sm"
              />
            </div>
          )}

          {workflowSubject && preset && (
            <div className="rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">
                Generated prompt:{" "}
              </span>
              {resolvedPrompt}
            </div>
          )}

          {imageStatus?.loaded_model_id && (
            <div className="text-xs text-muted-foreground">
              Model:{" "}
              <span className="font-medium text-foreground">
                {imageStatus.loaded_model_id}
              </span>
              <button
                onClick={() => setView("picker")}
                className="ml-2 text-violet-500 hover:underline"
              >
                change
              </button>
            </div>
          )}

          {genError && <ErrorNote message={genError} onDismiss={dismissGenError} />}

          <Button
            className="w-full"
            disabled={
              imageGenerating || !workflowPresetId || !workflowSubject.trim()
            }
            onClick={() => {
              void handleWorkflowGenerate();
            }}
          >
            {imageGenerating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                Generating…
              </>
            ) : (
              "Generate Image"
            )}
          </Button>
        </div>

        {imageResult && (
          <div className="space-y-2">
            <img
              src={`data:image/png;base64,${imageResult.b64}`}
              alt="Generated"
              className="w-full rounded-lg border"
            />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {imageResult.width}×{imageResult.height} ·{" "}
                {imageResult.elapsed.toFixed(1)}s
              </span>
              <Button size="sm" variant="outline" onClick={handleSavePng}>
                <Download className="h-3.5 w-3.5 mr-1.5" />
                Download
              </Button>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── Generate view ────────────────────────────────────────────────────────
  return (
    <div className="space-y-5 pb-8">
      {outdatedBanner}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setView("picker")}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← Models
          </button>
          <span className="text-sm font-semibold">
            {selectedModel?.name ?? "Image Generation"}
          </span>
          {selectedModel && (
            <Badge variant="outline" className="text-[10px]">
              {selectedModel.provider}
            </Badge>
          )}
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => setView("workflow")}>
            Workflows
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void handleUnload()}>
            Unload model
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Controls */}
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-xs">Prompt</Label>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe the image you want to generate…"
              className="text-sm min-h-[100px] resize-none"
            />
          </div>

          {selectedModel?.supports_negative_prompt && (
            <div className="space-y-1.5">
              <Label className="text-xs">
                Negative prompt{" "}
                <span className="text-muted-foreground">(what to avoid)</span>
              </Label>
              <Textarea
                value={negPrompt}
                onChange={(e) => setNegPrompt(e.target.value)}
                placeholder="blurry, low quality, deformed…"
                className="text-sm min-h-[60px] resize-none"
              />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">
                Steps{" "}
                <span className="text-muted-foreground">
                  ({steps ?? selectedModel?.recommended_steps})
                </span>
              </Label>
              <Slider
                min={1}
                max={selectedModel?.pipeline_type.startsWith("flux") ? 50 : 100}
                step={1}
                value={[steps ?? selectedModel?.recommended_steps ?? 20]}
                onValueChange={([v]) => setSteps(v)}
              />
            </div>
            {selectedModel && selectedModel.recommended_guidance > 0 && (
              <div className="space-y-1.5">
                <Label className="text-xs">
                  Guidance{" "}
                  <span className="text-muted-foreground">
                    (
                    {(guidance ?? selectedModel.recommended_guidance).toFixed(
                      1,
                    )}
                    )
                  </span>
                </Label>
                <Slider
                  min={0}
                  max={20}
                  step={0.5}
                  value={[guidance ?? selectedModel.recommended_guidance]}
                  onValueChange={([v]) => setGuidance(v)}
                />
              </div>
            )}
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">
              Seed{" "}
              <span className="text-muted-foreground">
                (optional — same seed reproduces an image)
              </span>
            </Label>
            <Input
              value={seedText}
              onChange={(e) => setSeedText(e.target.value)}
              inputMode="numeric"
              placeholder="random"
              className="text-sm"
            />
          </div>

          {genError && <ErrorNote message={genError} onDismiss={dismissGenError} />}

          <Button
            className="w-full"
            disabled={imageGenerating || !prompt.trim()}
            onClick={() => {
              void handleGenerate();
            }}
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

          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              className="flex-1 text-xs"
              onClick={() => setView("workflow")}
            >
              Use a workflow preset
            </Button>
          </div>
        </div>

        {/* Output */}
        <div className="space-y-3">
          {imageResult ? (
            <>
              <img
                src={`data:image/png;base64,${imageResult.b64}`}
                alt="Generated image"
                className="w-full rounded-lg border object-contain"
              />
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {imageResult.width}×{imageResult.height} ·{" "}
                  {imageResult.elapsed.toFixed(1)}s
                </span>
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" onClick={clearImageResult}>
                    Clear
                  </Button>
                  <Button size="sm" variant="outline" onClick={handleSavePng}>
                    <Download className="h-3.5 w-3.5 mr-1.5" />
                    Download PNG
                  </Button>
                </div>
              </div>
            </>
          ) : imageGenerating ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed aspect-square gap-3 text-muted-foreground">
              <Loader2 className="h-8 w-8 animate-spin text-violet-500" />
              <span className="text-sm">Generating image…</span>
              <span className="text-xs">
                This may take 5–60 seconds depending on your hardware
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
    </div>
  );
}
