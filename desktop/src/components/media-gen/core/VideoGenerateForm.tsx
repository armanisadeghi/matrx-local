/**
 * VideoGenerateForm — canonical video generate-form building blocks (twin of
 * ImageGenerateForm): prompt, common settings (steps/guidance/frames/fps/
 * size/seed), source-image control (image→video), advanced JSON, notices,
 * cancelable generate action, header, and the full vertical composition.
 */

import { useRef } from "react";
import { Film, ImagePlus, Loader2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import {
  AdvancedParamsEditor,
  CancelableGenerateButton,
  DimensionPicker,
  ErrorNote,
  NegativePromptField,
  NumberSliderField,
  ParamsErrorBanner,
  ResetButton,
  SeedInput,
} from "@/components/media-gen/shared";
import type { VideoGenController } from "./videoController";

export function VideoPromptField({
  ctl,
  placeholder = "Describe the video you want to generate…",
  textareaClassName = "text-sm min-h-[80px] max-h-[320px] resize-y",
  showLabel = true,
}: {
  ctl: VideoGenController;
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
        onChange={(e) => actions.setVideoForm({ prompt: e.target.value })}
        placeholder={placeholder}
        className={textareaClassName}
      />
    </div>
  );
}

export function VideoCommonSettings({
  ctl,
  showNegative = true,
  showSeed = true,
  showReset = true,
}: {
  ctl: VideoGenController;
  showNegative?: boolean;
  showSeed?: boolean;
  showReset?: boolean;
}) {
  const [, actions] = useMediaGenApp();
  const { setVideoForm, resetVideoCommon } = actions;
  const d = ctl.defaults;
  if (!d) return null;
  const disabled = ctl.form.paramsLoading;
  return (
    <div className="space-y-4">
      {showNegative && (
        <NegativePromptField
          supported={d.supportsNegativePrompt}
          value={ctl.form.negativePrompt}
          onChange={(v) => setVideoForm({ negativePrompt: v })}
          disabled={disabled}
        />
      )}
      <div className="grid grid-cols-2 gap-3">
        <NumberSliderField
          label="Steps"
          value={ctl.form.steps}
          onChange={(v) => setVideoForm({ steps: v })}
          min={1}
          max={100}
          step={1}
          defaultValue={d.steps}
          disabled={disabled}
        />
        <NumberSliderField
          label="Guidance"
          value={ctl.form.guidance}
          onChange={(v) => setVideoForm({ guidance: v })}
          min={0}
          max={20}
          step={0.5}
          defaultValue={d.guidance}
          disabled={disabled}
        />
        <NumberSliderField
          label="Frames"
          value={ctl.form.numFrames}
          onChange={(v) => setVideoForm({ numFrames: v })}
          min={1}
          max={ctl.maxFrames}
          step={1}
          defaultValue={d.numFrames}
          disabled={disabled}
        />
        <NumberSliderField
          label="FPS"
          value={ctl.form.fps}
          onChange={(v) => setVideoForm({ fps: v })}
          min={1}
          max={60}
          step={1}
          defaultValue={d.fps}
          disabled={disabled}
        />
      </div>
      <p className="text-[11px] text-muted-foreground tabular-nums">
        ≈ {ctl.approxSeconds.toFixed(1)}s of video ({ctl.form.numFrames} frames
        at {ctl.form.fps} fps)
      </p>
      <DimensionPicker
        width={ctl.form.width}
        height={ctl.form.height}
        onChange={(w, h) => setVideoForm({ width: w, height: h })}
        presets={ctl.sizePresets}
        disabled={disabled}
      />
      {showSeed && (
        <div className="space-y-1.5">
          <Label className="text-xs">
            Seed{" "}
            <span className="text-muted-foreground">(blank = random)</span>
          </Label>
          <SeedInput
            value={ctl.form.seedText}
            onChange={(seedText) => setVideoForm({ seedText })}
            disabled={disabled}
          />
        </div>
      )}
      {showReset && (
        <div className="flex justify-end">
          <ResetButton
            onClick={resetVideoCommon}
            label="Reset settings to model defaults"
          />
        </div>
      )}
    </div>
  );
}

/** Source image (image→video). Shown only when the model supports it. */
export function SourceImageControl({ ctl }: { ctl: VideoGenController }) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  if (!ctl.model?.supports_image_to_video) return null;
  const img = ctl.form.sourceImage;
  return (
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
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) ctl.handlePickSourceImage(file);
        }}
      />
      {img ? (
        <div className="flex items-center gap-3 rounded-lg border px-3 py-2">
          <img
            src={img.previewUrl}
            alt="Source"
            className="h-12 w-12 rounded object-cover border"
          />
          <span className="text-xs truncate flex-1">{img.name}</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={ctl.clearSourceImage}
            aria-label="Remove source image"
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
  );
}

export function VideoAdvancedSection({ ctl }: { ctl: VideoGenController }) {
  const [, actions] = useMediaGenApp();
  const { setVideoForm, resetVideoAdvanced } = actions;
  if (!ctl.defaults) return null;
  return (
    <AdvancedParamsEditor
      defaults={ctl.defaults.advanced}
      text={ctl.form.advancedText}
      onChange={(advancedText) => setVideoForm({ advancedText })}
      onReset={resetVideoAdvanced}
    />
  );
}

export function VideoParamsErrorNotice({ ctl }: { ctl: VideoGenController }) {
  const [, actions] = useMediaGenApp();
  const { prepareVideoGenerate } = actions;
  if (!ctl.form.paramsError || !ctl.model) return null;
  const model = ctl.model;
  return (
    <ParamsErrorBanner
      error={ctl.form.paramsError}
      onRetry={() => void prepareVideoGenerate(model)}
    />
  );
}

export function VideoFormNotices({ ctl }: { ctl: VideoGenController }) {
  return (
    <>
      {ctl.genError && (
        <ErrorNote message={ctl.genError} onDismiss={ctl.dismissGenError} />
      )}
      {ctl.jobIsActive && (
        <p className="text-[11px] text-muted-foreground">
          One video at a time — the current job must finish before starting
          another.
        </p>
      )}
    </>
  );
}

/** The cancelable Generate Video button (job-aware). */
export function VideoGenerateActions({
  ctl,
  size,
  buttonClassName = "w-full",
  extraDisabled = false,
}: {
  ctl: VideoGenController;
  size?: "default" | "sm" | "lg" | "icon";
  buttonClassName?: string;
  extraDisabled?: boolean;
}) {
  const [state, actions] = useMediaGenApp();
  const { videoGenerating, videoCancelling, activeJob } = state;
  const { cancelVideoGeneration } = actions;
  return (
    <CancelableGenerateButton
      generating={videoGenerating || ctl.jobIsActive}
      cancelling={videoCancelling || !!activeJob?.cancel_requested}
      elapsedSeconds={
        ctl.jobIsActive ? (activeJob?.elapsed_seconds ?? null) : null
      }
      disabled={ctl.formInvalid || extraDisabled}
      onGenerate={() => void ctl.handleGenerate()}
      onCancel={() => void cancelVideoGeneration()}
      size={size}
      buttonClassName={buttonClassName}
      workingLabel="Generating video"
      idleContent={
        <>
          <Film className="h-4 w-4 mr-2" />
          Generate Video
        </>
      }
    />
  );
}

export function VideoFormHeader({
  ctl,
  onSwitchModel,
}: {
  ctl: VideoGenController;
  onSwitchModel?: () => void;
}) {
  const [, actions] = useMediaGenApp();
  const { resetVideoAll } = actions;
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
        <ResetButton onClick={resetVideoAll} label="Reset all settings" />
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

export function VideoParamsLoading({ ctl }: { ctl: VideoGenController }) {
  return (
    <div className="flex items-center justify-center py-16 gap-3 text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
      <span className="text-sm">
        Loading {ctl.model?.name ?? "model"}'s parameters…
      </span>
    </div>
  );
}

/** Full vertical video generate form. */
export function VideoGenerateForm({
  ctl,
  hideActions = false,
  hideNotices = false,
  showHeader = false,
  onSwitchModel,
}: {
  ctl: VideoGenController;
  hideActions?: boolean;
  hideNotices?: boolean;
  showHeader?: boolean;
  onSwitchModel?: () => void;
}) {
  return (
    <div className="space-y-4">
      {showHeader && (
        <VideoFormHeader ctl={ctl} onSwitchModel={onSwitchModel} />
      )}
      {!hideNotices && <VideoParamsErrorNotice ctl={ctl} />}
      <VideoPromptField ctl={ctl} />
      <VideoCommonSettings ctl={ctl} />
      <SourceImageControl ctl={ctl} />
      <VideoAdvancedSection ctl={ctl} />
      {!hideNotices && <VideoFormNotices ctl={ctl} />}
      {!hideActions && <VideoGenerateActions ctl={ctl} />}
    </div>
  );
}
