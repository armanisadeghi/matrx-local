/**
 * VariantFocus — "Focus flow" bake-off variant.
 *
 * A calm, centered, progressive-disclosure experience (the anti-dashboard):
 * one column, one narrative. Segmented control (Image | Video | Workflow) up
 * top, then the flow reads top-to-bottom as steps — Model → Prompt →
 * Essentials → All settings → Generate — with results appearing directly
 * beneath as a vertical session feed. History lives behind a quiet
 * "Open library" link (full-height dialog).
 *
 * THIN layout shell: model catalog, form controls (incl. img2img + LoRA),
 * queue feed, video job cards and gates all come from media-gen/core; this
 * file only owns the step chrome and reveal toggles (presentation-only).
 */

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Film,
  Image as ImageIcon,
  Library,
  ListPlus,
  Layers,
  Loader2,
  Minus,
  Plus,
  Sparkles,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { ImageGenInstaller } from "@/components/media-gen/ImageGenInstaller";
import { WorkflowSection } from "@/components/media-gen/WorkflowSection";
import { MediaLibrarySection } from "@/components/media-gen/MediaLibrarySection";
import {
  CancelableGenerateButton,
  NegativePromptField,
  ResetButton,
  StillWorkingNote,
} from "@/components/media-gen/shared";
import { useImageGenController } from "@/components/media-gen/core/imageController";
import { useVideoGenController } from "@/components/media-gen/core/videoController";
import {
  ImageGenGate,
  OutdatedPackagesBanner,
  VideoGenGate,
} from "@/components/media-gen/core/gates";
import {
  ImageModelPicker,
  VideoModelPicker,
} from "@/components/media-gen/core/ModelPicker";
import {
  ImageAdvancedSection,
  ImageCommonSettings,
  ImageFormNotices,
  ImageGenerateForm,
  ImageParamsErrorNotice,
  ImagePromptField,
  InputImageControl,
  LoraStylesSection,
  useImageGenMode,
} from "@/components/media-gen/core/ImageGenerateForm";
import {
  SourceImageControl,
  VideoAdvancedSection,
  VideoCommonSettings,
  VideoFormNotices,
  VideoGenerateActions,
  VideoParamsErrorNotice,
  VideoPromptField,
} from "@/components/media-gen/core/VideoGenerateForm";
import { ImageResultPane } from "@/components/media-gen/core/ResultView";
import { ImageQueuePanel } from "@/components/media-gen/core/ImageQueuePanel";
import {
  ActiveVideoJobCard,
  VideoJobsList,
  VideoPlayback,
} from "@/components/media-gen/core/VideoJobPanel";

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

/** Model step: current-model summary + reveal containing the shared picker. */
function ModelStep({
  hasModel,
  children,
}: {
  hasModel: boolean;
  children: React.ReactNode;
}) {
  // Reveal-only local state (allowed): the inline model list.
  const [open, setOpen] = useState(!hasModel);
  const showList = open || !hasModel;
  return (
    <section className="space-y-2.5">
      <StepHeading
        step={1}
        title="Model"
        aside={
          hasModel ? (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              {showList ? "Done" : "Change"}
            </button>
          ) : undefined
        }
      />
      {showList ? (
        children
      ) : (
        <p className="rounded-xl border bg-card px-4 py-3 text-xs text-muted-foreground">
          Model selected — use Change to pick another.
        </p>
      )}
    </section>
  );
}

/** Negative-prompt reveal wrapping the canonical field. */
function NegativePromptReveal({
  supported,
  value,
  onChange,
}: {
  supported: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  // Starts open when a value already exists so context work is never hidden.
  const [open, setOpen] = useState(() => value.trim().length > 0);
  if (!open && supported) {
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
      {supported && value.trim().length === 0 && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <Minus className="h-3 w-3" />
            Hide
          </button>
        </div>
      )}
      <NegativePromptField
        supported={supported}
        value={value}
        onChange={onChange}
      />
    </div>
  );
}

/** All-settings collapsible. */
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
            <ResetButton
              onClick={onResetAll}
              label="Reset everything to model defaults"
            />
          </div>
        </div>
      )}
    </section>
  );
}

// ── Image flow ───────────────────────────────────────────────────────────────

function ImageFocusFlow() {
  const [state, actions] = useMediaGenApp();
  const {
    imageStatus,
    imageGenerating,
    imageCancelling,
    imageGenStartedAt,
    imageResult,
    imageForm,
    imageJobs,
  } = state;
  const { setImageForm, resetImageAll, cancelImageGeneration, refreshImage } =
    actions;
  const ctl = useImageGenController();
  const [batchBuilderOpen, setBatchBuilderOpen] = useState(false);
  const [, setImageGenMode] = useImageGenMode();

  const feedHasContent =
    !!imageResult || imageGenerating || imageJobs.length > 0;

  // Packages installed but outdated → the calm flow makes no sense until the
  // one-time update runs; show the upgrade installer instead.
  if (imageStatus?.packages_outdated) {
    return (
      <ImageGenGate>
        <div className="space-y-4">
          <OutdatedPackagesBanner />
          <ImageGenInstaller
            models={[]}
            upgrade
            onInstallComplete={() => void refreshImage()}
          />
        </div>
      </ImageGenGate>
    );
  }

  return (
    <ImageGenGate>
      <div className="space-y-8">

        {/* 1 · Model */}
        <ModelStep hasModel={!!ctl.model}>
          <ImageModelPicker
            ctl={ctl}
            layout="rows"
            showHeading={false}
            showLoadedBanner={false}
          />
        </ModelStep>

        <ImageParamsErrorNotice ctl={ctl} />

        {/* 2 · Prompt */}
        <section className="space-y-2.5">
          <StepHeading step={2} title="Prompt" />
          <ImagePromptField
            ctl={ctl}
            showLabel={false}
            placeholder="Describe the image you want to see…"
            textareaClassName="min-h-[110px] max-h-[400px] resize-y rounded-xl bg-card px-4 py-3 text-[15px] leading-relaxed shadow-sm"
          />
          <NegativePromptReveal
            supported={ctl.defaults?.supportsNegativePrompt ?? true}
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
            {ctl.defaults ? (
              <>
                <ImageCommonSettings
                  ctl={ctl}
                  showNegative={false}
                  showReset={false}
                />
                <InputImageControl ctl={ctl} />
                <LoraStylesSection ctl={ctl} />
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                Settings appear once a model is selected.
              </p>
            )}
          </div>
        </section>

        {/* 4 · All settings */}
        <AllSettings onResetAll={resetImageAll}>
          {ctl.defaults ? (
            <ImageAdvancedSection ctl={ctl} />
          ) : (
            <p className="text-xs text-muted-foreground">
              Advanced parameters appear once a model is selected.
            </p>
          )}
        </AllSettings>

        {/* 5 · Generate */}
        <section className="space-y-3">
          <ImageFormNotices ctl={ctl} />
          {!ctl.model && (
            <p className="text-center text-[11px] text-muted-foreground">
              Choose a model above to enable generation.
            </p>
          )}
          <div className="flex gap-2">
            <CancelableGenerateButton
              generating={imageGenerating}
              cancelling={imageCancelling}
              startedAt={imageGenStartedAt}
              disabled={ctl.formInvalid}
              onGenerate={() => void ctl.handleGenerate()}
              onCancel={() => void cancelImageGeneration()}
              containerClassName="flex-1"
              size="lg"
              buttonClassName="h-12 w-full rounded-xl text-sm font-semibold"
              idleContent={
                <>
                  <Sparkles className="mr-2 h-4 w-4" />
                  Generate
                </>
              }
            />
            <Button
              size="lg"
              variant="outline"
              className="h-12 rounded-xl"
              disabled={ctl.formInvalid}
              onClick={() => void ctl.handleEnqueue()}
              title="Queue this generation and keep writing the next prompt"
            >
              <ListPlus className="mr-2 h-4 w-4" />
              Queue
            </Button>
            <Button
              size="lg"
              variant="outline"
              className="h-12 rounded-xl"
              disabled={!ctl.defaults}
              onClick={() => {
                setImageGenMode("batch");
                setBatchBuilderOpen(true);
              }}
              title="Build a randomized image batch"
            >
              <Layers className="mr-2 h-4 w-4" />
              Batch
            </Button>
          </div>
        </section>

        {/* Session feed */}
        {feedHasContent && (
          <section className="space-y-3 border-t pt-6">
            <h3 className="text-[13px] font-semibold tracking-tight">
              This session
            </h3>
            {imageGenerating && (
              <div className="flex items-center gap-3 rounded-xl border bg-card px-4 py-3">
                <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs" title={imageForm.prompt}>
                    {imageForm.prompt.trim() || "Generating image…"}
                  </p>
                  <StillWorkingNote
                    startedAt={imageGenStartedAt}
                    label="Generating on-device — still working"
                    className="justify-start"
                  />
                </div>
              </div>
            )}
            {imageResult && (
              <div className="rounded-xl border bg-card p-3">
                <ImageResultPane hideIdle />
              </div>
            )}
            <ImageQueuePanel layout="feed" showHeading={false} />
          </section>
        )}

        <Dialog open={batchBuilderOpen} onOpenChange={setBatchBuilderOpen}>
          <DialogContent className="max-h-[92vh] max-w-4xl overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Randomized image batch</DialogTitle>
              <DialogDescription>
                Every new preview or queue action draws a fresh randomized batch.
              </DialogDescription>
            </DialogHeader>
            <ImageGenerateForm ctl={ctl} hideNotices />
          </DialogContent>
        </Dialog>
      </div>
    </ImageGenGate>
  );
}

// ── Video flow ───────────────────────────────────────────────────────────────

function VideoFocusFlow() {
  const [state, actions] = useMediaGenApp();
  const { activeJob, videoForm } = state;
  const { setVideoForm, resetVideoAll } = actions;
  const ctl = useVideoGenController();

  const feedHasContent =
    !!activeJob || !!ctl.playbackUrl || !!ctl.playbackJobId;

  return (
    <VideoGenGate>
      <div className="space-y-8">
        {/* 1 · Model */}
        <ModelStep hasModel={!!ctl.model}>
          <VideoModelPicker
            ctl={ctl}
            layout="rows"
            showHeading={false}
            showLoadedBanner={false}
          />
        </ModelStep>

        <VideoParamsErrorNotice ctl={ctl} />

        {/* 2 · Prompt */}
        <section className="space-y-2.5">
          <StepHeading step={2} title="Prompt" />
          <VideoPromptField
            ctl={ctl}
            showLabel={false}
            placeholder="Describe the video you want to see…"
            textareaClassName="min-h-[110px] max-h-[400px] resize-y rounded-xl bg-card px-4 py-3 text-[15px] leading-relaxed shadow-sm"
          />
          <NegativePromptReveal
            supported={ctl.defaults?.supportsNegativePrompt ?? true}
            value={videoForm.negativePrompt}
            onChange={(negativePrompt) => setVideoForm({ negativePrompt })}
          />
          <SourceImageControl ctl={ctl} />
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
            {ctl.defaults ? (
              <VideoCommonSettings
                ctl={ctl}
                showNegative={false}
                showReset={false}
              />
            ) : (
              <p className="text-xs text-muted-foreground">
                Settings appear once a model is selected.
              </p>
            )}
          </div>
        </section>

        {/* 4 · All settings */}
        <AllSettings onResetAll={resetVideoAll}>
          {ctl.defaults ? (
            <VideoAdvancedSection ctl={ctl} />
          ) : (
            <p className="text-xs text-muted-foreground">
              Advanced parameters appear once a model is selected.
            </p>
          )}
        </AllSettings>

        {/* 5 · Generate */}
        <section className="space-y-3">
          <VideoFormNotices ctl={ctl} />
          {!ctl.model && (
            <p className="text-center text-[11px] text-muted-foreground">
              Choose a model above to enable generation.
            </p>
          )}
          <VideoGenerateActions
            ctl={ctl}
            size="lg"
            buttonClassName="h-12 w-full rounded-xl text-sm font-semibold"
          />
        </section>

        {/* Session feed */}
        {feedHasContent && (
          <section className="space-y-3 border-t pt-6">
            <h3 className="text-[13px] font-semibold tracking-tight">
              This session
            </h3>
            <VideoPlayback ctl={ctl} />
            <ActiveVideoJobCard />
          </section>
        )}
        <VideoJobsList
          ctl={ctl}
          layout="list"
          heading="Recent videos"
          excludeActive
        />
      </div>
    </VideoGenGate>
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
