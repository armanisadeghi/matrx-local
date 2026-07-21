/**
 * ModelPicker — the ONE model-catalog implementation (image + video), in two
 * densities: "grid" (rich cards — classic sections, Workspace models view)
 * and "rows" (compact rows — Studio dialog, Gallery popover, Focus inline
 * list). Every rendering includes live weight-download progress via the
 * DownloadManager (this was missing from Gallery's picker — fixed by
 * unification), hardware gating, HF-token badges, per-card load spinners and
 * the loaded-model banner.
 */

import { useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Download,
  ExternalLink,
  Film,
  Image as ImageIcon,
  Loader2,
  PackagePlus,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenModelInfo, VideoGenModelInfo } from "@/lib/api";
import {
  ErrorNote,
  InlineProgressBar,
  ModelLoadingNotice,
  ResetButton,
  StarRating,
  findModelDownload,
  formatGb,
  openExternalUrl,
} from "@/components/media-gen/shared";
import type { ImageGenController } from "./imageController";
import type { VideoGenController } from "./videoController";
import { AddCustomModelDialog } from "./AddCustomModelDialog";

export type ModelPickerLayout = "grid" | "rows";

type AnyModel = ImageGenModelInfo | VideoGenModelInfo;

/** Live download entry for a model's weights, or null. */
function useModelDownload(
  category: "image_gen" | "video_gen",
  modelId: string,
) {
  const { downloads, openModal } = useDownloadManager();
  const dl = useMemo(
    () => findModelDownload(downloads, category, modelId),
    [downloads, category, modelId],
  );
  return { dl, openModal };
}

/** Shared download-progress block (both layouts). */
function DownloadProgress({
  percent,
  bytesDone,
  onDetails,
}: {
  percent: number;
  bytesDone: number;
  onDetails: () => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>Downloading weights…</span>
        <button
          type="button"
          onClick={onDetails}
          className="text-violet-500 hover:underline"
        >
          {Math.round(percent)}% · details
        </button>
      </div>
      <InlineProgressBar
        percent={percent}
        indeterminate={percent <= 0 && bytesDone <= 0}
      />
    </div>
  );
}

/**
 * Two-step delete affordance for custom models (both layouts): trash icon →
 * inline "Remove?" confirm.  Errors render loudly in place.
 */
function CustomModelDelete({
  modelName,
  onDelete,
}: {
  modelName: string;
  onDelete: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!confirming) {
    return (
      <div className="flex items-center">
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="text-muted-foreground hover:text-destructive"
          aria-label={`Delete custom model ${modelName}`}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
        {error && (
          <span className="ml-2 max-w-[16rem] truncate text-[10px] text-destructive">
            {error}
          </span>
        )}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-muted-foreground">Remove?</span>
      <Button
        size="sm"
        variant="destructive"
        className="h-6 px-2 text-[10px]"
        disabled={deleting}
        onClick={() => {
          setDeleting(true);
          setError(null);
          onDelete()
            .catch((e: unknown) => {
              setError(e instanceof Error ? e.message : String(e));
            })
            .finally(() => {
              setDeleting(false);
              setConfirming(false);
            });
        }}
      >
        {deleting ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          "Yes, delete"
        )}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        className="h-6 px-2 text-[10px]"
        disabled={deleting}
        onClick={() => setConfirming(false)}
      >
        Keep
      </Button>
    </div>
  );
}

/** One model — rich card (grid) or compact row (rows). */
function ModelEntry({
  model,
  category,
  layout,
  isLoaded,
  isLoadingThis,
  anyLoadInFlight,
  onLoad,
  onDownload,
  onGenerate,
  onDeleteCustom,
}: {
  model: AnyModel;
  category: "image_gen" | "video_gen";
  layout: ModelPickerLayout;
  isLoaded: boolean;
  isLoadingThis: boolean;
  anyLoadInFlight: boolean;
  onLoad: () => void;
  onDownload: () => void;
  /** Called for a LOADED model's primary action ("Generate →" / "Use"). */
  onGenerate: () => void;
  /** Present only for custom entries — enables the two-step delete. */
  onDeleteCustom?: () => Promise<void>;
}) {
  const { dl, openModal } = useModelDownload(category, model.model_id);
  const downloading = dl?.status === "active" || dl?.status === "queued";
  const hardwareBlocked = !model.hardware_ok;
  const imageToVideo =
    "supports_image_to_video" in model && model.supports_image_to_video;
  const img2img = "supports_img2img" in model && model.supports_img2img;
  const isCustom = "custom" in model && model.custom === true;

  const actionArea =
    downloading && dl ? (
      <DownloadProgress
        percent={dl.percent}
        bytesDone={dl.bytes_done}
        onDetails={openModal}
      />
    ) : !model.is_downloaded ? (
      <Button
        size="sm"
        className={layout === "grid" ? "w-full" : "w-full h-7 text-xs"}
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
        className={layout === "grid" ? "w-full" : "w-full h-7 text-xs"}
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
          layout === "grid" ? (
            "Generate →"
          ) : (
            <>
              Use this model
              <ChevronRight className="h-3 w-3 ml-1" />
            </>
          )
        ) : (
          "Load model"
        )}
      </Button>
    );

  const hardwareNote = hardwareBlocked && (
    <div className="rounded bg-muted/50 px-2 py-1.5 text-[11px] text-muted-foreground flex items-center gap-1.5">
      <AlertCircle className="h-3 w-3 shrink-0 text-amber-500" />
      {model.hardware_reason ??
        "Your hardware does not meet this model's requirements."}
    </div>
  );

  if (layout === "rows") {
    return (
      <div
        className={`rounded-lg border px-3 py-2.5 space-y-2 ${
          isLoaded
            ? "border-violet-500/40 bg-violet-500/5"
            : hardwareBlocked
              ? "opacity-70"
              : "hover:bg-muted/20"
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-medium truncate flex items-center gap-1.5">
              {model.name}
              {isCustom && (
                <span className="rounded bg-blue-500/20 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 text-[10px] font-normal">
                  Custom
                </span>
              )}
              {isLoaded && (
                <span className="rounded bg-green-500/20 text-green-600 dark:text-green-400 px-1.5 py-0.5 text-[10px] font-normal">
                  Loaded
                </span>
              )}
            </p>
            <p className="text-[11px] text-muted-foreground truncate">
              {model.provider} · {formatGb(model.download_size_gb)}
              {model.requires_hf_token ? " · HF token" : ""}
              {imageToVideo ? " · image→video" : ""}
              {img2img ? " · img2img" : ""}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="hidden sm:flex items-center gap-2 text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1">
                Q <StarRating value={model.quality_rating} />
              </span>
              <span className="flex items-center gap-1">
                S <StarRating value={model.speed_rating} />
              </span>
            </span>
            <button
              type="button"
              onClick={() => void openExternalUrl(model.model_card_url)}
              className="text-muted-foreground hover:text-foreground"
              aria-label={`Open model card for ${model.name}`}
            >
              <ExternalLink className="h-3.5 w-3.5" />
            </button>
            {isCustom && onDeleteCustom && (
              <CustomModelDelete
                modelName={model.name}
                onDelete={onDeleteCustom}
              />
            )}
          </div>
        </div>
        {hardwareNote}
        {actionArea}
      </div>
    );
  }

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
          <p className="font-medium text-sm flex items-center gap-1.5">
            {model.name}
            {isCustom && (
              <span className="rounded bg-blue-500/20 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 text-[10px] font-normal">
                Custom
              </span>
            )}
          </p>
          <p className="text-xs text-muted-foreground">{model.provider}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isCustom && onDeleteCustom && (
            <CustomModelDelete
              modelName={model.name}
              onDelete={onDeleteCustom}
            />
          )}
          <button
            onClick={() => void openExternalUrl(model.model_card_url)}
            className="shrink-0 text-muted-foreground hover:text-foreground"
            aria-label={`Open model card for ${model.name}`}
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </button>
        </div>
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
        {img2img && (
          <span className="rounded bg-blue-500/20 text-blue-600 dark:text-blue-400 px-1.5 py-0.5">
            Image input
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
      {hardwareNote}
      {actionArea}
    </div>
  );
}

/** Green "model loaded" banner with Generate / Unload actions. */
function LoadedBanner({
  loadedModelId,
  label,
  onGenerate,
  onUnload,
}: {
  loadedModelId: string;
  label: string;
  onGenerate: () => void;
  onUnload: () => void;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-green-500/30 bg-green-500/5 px-4 py-3">
      <div className="flex items-center gap-2 text-sm min-w-0">
        <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
        <span className="truncate">
          {label}: <span className="font-medium">{loadedModelId}</span>
        </span>
      </div>
      <div className="flex gap-2 shrink-0">
        <Button size="sm" variant="outline" onClick={onGenerate}>
          Generate
        </Button>
        <Button size="sm" variant="ghost" onClick={onUnload}>
          Unload
        </Button>
      </div>
    </div>
  );
}

/** Image model catalog. */
export function ImageModelPicker({
  ctl,
  layout = "grid",
  showLoadedBanner = layout === "grid",
  showHeading = layout === "grid",
}: {
  ctl: ImageGenController;
  layout?: ModelPickerLayout;
  showLoadedBanner?: boolean;
  showHeading?: boolean;
}) {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageModels,
    imageModelLoading,
    loadingImageModelId,
    imageLoadStartedAt,
  } = state;
  const { deleteCustomModel } = actions;
  const anyLoadInFlight = imageModelLoading || !!imageStatus?.is_loading;
  const [addCustomOpen, setAddCustomOpen] = useState(false);

  const addCustomEntry =
    layout === "grid" ? (
      <button
        type="button"
        onClick={() => setAddCustomOpen(true)}
        className="flex min-h-[7rem] flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-4 text-muted-foreground transition-colors hover:border-violet-500/50 hover:text-foreground"
      >
        <PackagePlus className="h-5 w-5" />
        <span className="text-sm font-medium">Add custom model</span>
        <span className="text-[11px]">
          Paste a Hugging Face repo or Civitai link
        </span>
      </button>
    ) : (
      <button
        type="button"
        onClick={() => setAddCustomOpen(true)}
        className="flex w-full items-center gap-2 rounded-lg border border-dashed px-3 py-2.5 text-left text-muted-foreground transition-colors hover:border-violet-500/50 hover:text-foreground"
      >
        <PackagePlus className="h-4 w-4 shrink-0" />
        <span className="min-w-0">
          <span className="block text-sm font-medium">Add custom model</span>
          <span className="block truncate text-[11px]">
            Paste a Hugging Face repo or Civitai link
          </span>
        </span>
      </button>
    );

  return (
    <div className="space-y-3">
      {showLoadedBanner && imageStatus?.loaded_model_id && (
        <LoadedBanner
          loadedModelId={imageStatus.loaded_model_id}
          label="Model loaded"
          onGenerate={() => {
            const m = imageModels.find(
              (x) => x.model_id === imageStatus.loaded_model_id,
            );
            if (m) ctl.handleOpenGenerate(m);
          }}
          onUnload={() => void ctl.handleUnload()}
        />
      )}
      {showHeading && (
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <ImageIcon className="h-4 w-4 text-violet-500" />
          Select a model
        </h3>
      )}
      {ctl.genError && (
        <ErrorNote message={ctl.genError} onDismiss={ctl.dismissGenError} />
      )}
      <ModelLoadingNotice
        loading={anyLoadInFlight}
        startedAt={imageLoadStartedAt}
        loadError={imageStatus?.load_error}
        what={loadingImageModelId ?? "model"}
      />
      <div
        className={
          layout === "grid" ? "grid gap-3 sm:grid-cols-2" : "space-y-2"
        }
      >
        {imageModels.map((m) => (
          <ModelEntry
            key={m.model_id}
            model={m}
            category="image_gen"
            layout={layout}
            isLoaded={imageStatus?.loaded_model_id === m.model_id}
            isLoadingThis={loadingImageModelId === m.model_id}
            anyLoadInFlight={anyLoadInFlight}
            onLoad={() => void ctl.handleLoadModel(m)}
            onDownload={() => void ctl.handleDownloadModel(m)}
            onGenerate={() => ctl.handleOpenGenerate(m)}
            {...(m.custom
              ? { onDeleteCustom: () => deleteCustomModel(m.model_id) }
              : {})}
          />
        ))}
        {imageModels.length === 0 && (
          <div className="col-span-full rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
            No image models available yet.
          </div>
        )}
        {addCustomEntry}
      </div>
      <AddCustomModelDialog
        open={addCustomOpen}
        onOpenChange={setAddCustomOpen}
      />
    </div>
  );
}

/** Video model catalog. */
export function VideoModelPicker({
  ctl,
  layout = "grid",
  showLoadedBanner = layout === "grid",
  showHeading = layout === "grid",
}: {
  ctl: VideoGenController;
  layout?: ModelPickerLayout;
  showLoadedBanner?: boolean;
  showHeading?: boolean;
}) {
  const [state] = useMediaGenApp();
  const {
    videoStatus,
    videoModels,
    videoModelLoading,
    loadingVideoModelId,
    videoLoadStartedAt,
  } = state;
  const anyLoadInFlight = videoModelLoading || !!videoStatus?.is_loading;

  return (
    <div className="space-y-3">
      {showLoadedBanner && videoStatus?.loaded_model_id && (
        <LoadedBanner
          loadedModelId={videoStatus.loaded_model_id}
          label="Model loaded"
          onGenerate={() => {
            const m = videoModels.find(
              (x) => x.model_id === videoStatus.loaded_model_id,
            );
            if (m) ctl.handleOpenGenerate(m);
          }}
          onUnload={() => void ctl.handleUnload()}
        />
      )}
      {showHeading && (
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Film className="h-4 w-4 text-violet-500" />
          Select a video model
        </h3>
      )}
      {ctl.genError && (
        <ErrorNote message={ctl.genError} onDismiss={ctl.dismissGenError} />
      )}
      <ModelLoadingNotice
        loading={anyLoadInFlight}
        startedAt={videoLoadStartedAt}
        loadError={videoStatus?.load_error}
        what={loadingVideoModelId ?? "model"}
      />
      <div
        className={
          layout === "grid" ? "grid gap-3 sm:grid-cols-2" : "space-y-2"
        }
      >
        {videoModels.map((m) => (
          <ModelEntry
            key={m.model_id}
            model={m}
            category="video_gen"
            layout={layout}
            isLoaded={videoStatus?.loaded_model_id === m.model_id}
            isLoadingThis={loadingVideoModelId === m.model_id}
            anyLoadInFlight={anyLoadInFlight}
            onLoad={() => void ctl.handleLoadModel(m)}
            onDownload={() => void ctl.handleDownloadModel(m)}
            onGenerate={() => ctl.handleOpenGenerate(m)}
          />
        ))}
        {videoModels.length === 0 && (
          <div className="col-span-full rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
            No video models available yet.
          </div>
        )}
      </div>
    </div>
  );
}

/** Compact one-row model selector for generate views (Classic, Workspace). */
export function ImageModelBar({ ctl }: { ctl: ImageGenController }) {
  const [state, actions] = useMediaGenApp();
  const { imageModels, imageStatus, imageModelLoading, loadingImageModelId } =
    state;
  const { resetImageAll } = actions;
  const current = ctl.model;
  const currentId = current?.model_id ?? "";
  const loadedId = imageStatus?.loaded_model_id ?? null;
  const isLoaded = !!currentId && loadedId === currentId;
  const anyLoadInFlight = imageModelLoading || !!imageStatus?.is_loading;
  const { dl, openModal } = useModelDownload("image_gen", currentId || "none");
  const downloading =
    !!currentId && (dl?.status === "active" || dl?.status === "queued");

  const handleSelect = (modelId: string) => {
    const m = imageModels.find((row) => row.model_id === modelId);
    if (m) ctl.handleOpenGenerate(m);
  };

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border bg-card px-2 py-1.5">
        <Select
          {...(currentId ? { value: currentId } : {})}
          onValueChange={handleSelect}
          disabled={imageModels.length === 0}
        >
          <SelectTrigger className="h-8 min-w-[12rem] max-w-[20rem] flex-1 text-xs">
            <SelectValue placeholder="Select image model…" />
          </SelectTrigger>
          <SelectContent>
            {imageModels.map((m) => (
              <SelectItem
                key={m.model_id}
                value={m.model_id}
                className="text-xs"
              >
                {m.name}
                {!m.is_downloaded ? " · not downloaded" : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {current && (
          <span className="hidden shrink-0 rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground sm:inline">
            {current.provider}
          </span>
        )}

        {anyLoadInFlight && (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
        )}

        {!anyLoadInFlight && isLoaded && (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-500" />
        )}

        {downloading && dl && (
          <button
            type="button"
            className="text-[10px] text-violet-500 hover:underline"
            onClick={() => openModal()}
          >
            {Math.round(dl.percent)}%
          </button>
        )}

        <div className="ml-auto flex flex-wrap items-center gap-1">
          {current && !current.is_downloaded && !downloading && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 gap-1 px-2 text-xs"
              onClick={() => ctl.handleDownloadModel(current)}
            >
              <Download className="h-3 w-3" />
              Download
            </Button>
          )}
          {current &&
            current.is_downloaded &&
            !isLoaded &&
            !anyLoadInFlight && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={() => void ctl.handleLoadModel(current)}
              >
                Load
              </Button>
            )}
          {current && (
            <>
              <ResetButton onClick={resetImageAll} label="Reset settings" />
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                disabled={!loadedId}
                onClick={() => void ctl.handleUnload()}
              >
                Unload
              </Button>
            </>
          )}
        </div>
      </div>
      {anyLoadInFlight && (
        <ModelLoadingNotice
          loading
          startedAt={state.imageLoadStartedAt}
          loadError={imageStatus?.load_error}
          what={loadingImageModelId ?? "model"}
        />
      )}
      {ctl.genError && (
        <ErrorNote message={ctl.genError} onDismiss={ctl.dismissGenError} />
      )}
    </div>
  );
}

/** Compact one-row model selector for video generate views. */
export function VideoModelBar({ ctl }: { ctl: VideoGenController }) {
  const [state, actions] = useMediaGenApp();
  const { videoModels, videoStatus, videoModelLoading, loadingVideoModelId } =
    state;
  const { resetVideoAll } = actions;
  const current = ctl.model;
  const currentId = current?.model_id ?? "";
  const loadedId = videoStatus?.loaded_model_id ?? null;
  const isLoaded = !!currentId && loadedId === currentId;
  const anyLoadInFlight = videoModelLoading || !!videoStatus?.is_loading;
  const { dl, openModal } = useModelDownload("video_gen", currentId || "none");
  const downloading =
    !!currentId && (dl?.status === "active" || dl?.status === "queued");

  const handleSelect = (modelId: string) => {
    const m = videoModels.find((row) => row.model_id === modelId);
    if (m) ctl.handleOpenGenerate(m);
  };

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5 rounded-lg border bg-card px-2 py-1.5">
        <Select
          {...(currentId ? { value: currentId } : {})}
          onValueChange={handleSelect}
          disabled={videoModels.length === 0}
        >
          <SelectTrigger className="h-8 min-w-[12rem] max-w-[20rem] flex-1 text-xs">
            <SelectValue placeholder="Select video model…" />
          </SelectTrigger>
          <SelectContent>
            {videoModels.map((m) => (
              <SelectItem
                key={m.model_id}
                value={m.model_id}
                className="text-xs"
              >
                {m.name}
                {!m.is_downloaded ? " · not downloaded" : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {current && (
          <span className="hidden shrink-0 rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground sm:inline">
            {current.provider}
          </span>
        )}

        {anyLoadInFlight && (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
        )}

        {!anyLoadInFlight && isLoaded && (
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-500" />
        )}

        {downloading && dl && (
          <button
            type="button"
            className="text-[10px] text-violet-500 hover:underline"
            onClick={() => openModal()}
          >
            {Math.round(dl.percent)}%
          </button>
        )}

        <div className="ml-auto flex flex-wrap items-center gap-1">
          {current && !current.is_downloaded && !downloading && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 gap-1 px-2 text-xs"
              onClick={() => ctl.handleDownloadModel(current)}
            >
              <Download className="h-3 w-3" />
              Download
            </Button>
          )}
          {current &&
            current.is_downloaded &&
            !isLoaded &&
            !anyLoadInFlight && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={() => void ctl.handleLoadModel(current)}
              >
                Load
              </Button>
            )}
          {current && (
            <>
              <ResetButton onClick={resetVideoAll} label="Reset settings" />
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                disabled={!loadedId}
                onClick={() => void ctl.handleUnload()}
              >
                Unload
              </Button>
            </>
          )}
        </div>
      </div>
      {anyLoadInFlight && (
        <ModelLoadingNotice
          loading
          startedAt={state.videoLoadStartedAt}
          loadError={videoStatus?.load_error}
          what={loadingVideoModelId ?? "model"}
        />
      )}
      {ctl.genError && (
        <ErrorNote message={ctl.genError} onDismiss={ctl.dismissGenError} />
      )}
    </div>
  );
}
