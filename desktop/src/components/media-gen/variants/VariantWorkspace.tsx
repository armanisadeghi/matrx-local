/**
 * VariantWorkspace — UI bake-off variant: "Workspace nav".
 *
 * A mini-app with its own left icon+label navigation rail (collapsible to
 * icons-only): Generate Image, Generate Video, Workflows, Library, Models.
 * The Generate views are PURE generate forms (no Generate|Models sub-tabs);
 * Models is its own first-class view listing image AND video model catalogs.
 * A persistent slim queue footer shows in-flight work across all entries.
 *
 * THIN layout shell: every logic-bearing piece (forms, pickers, queue, job
 * cards, gates) comes from media-gen/core — this file is navigation chrome.
 */

import { useCallback, useState } from "react";
import {
  AlertCircle,
  Boxes,
  CheckCircle2,
  ChevronsLeft,
  ChevronsRight,
  Film,
  Image as ImageIcon,
  Library,
  Loader2,
  Wand2,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import { WorkflowSection } from "../WorkflowSection";
import { MediaLibrarySection } from "../MediaLibrarySection";
import { ImageGenInstaller } from "../ImageGenInstaller";
import { InlineProgressBar } from "../shared";
import { useImageGenController } from "../core/imageController";
import { useVideoGenController } from "../core/videoController";
import {
  ImageGenGate,
  OutdatedPackagesBanner,
  VideoGenGate,
} from "../core/gates";
import { ImageModelPicker, VideoModelPicker } from "../core/ModelPicker";
import {
  ImageGenerateForm,
  ImageParamsLoading,
} from "../core/ImageGenerateForm";
import {
  VideoGenerateForm,
  VideoParamsLoading,
} from "../core/VideoGenerateForm";
import { ImageResultPane } from "../core/ResultView";
import { ImageQueuePanel } from "../core/ImageQueuePanel";
import {
  ActiveVideoJobCard,
  VideoJobsList,
  VideoPlayback,
} from "../core/VideoJobPanel";

// ── Navigation model ─────────────────────────────────────────────────────────

type NavId = "image" | "video" | "workflows" | "library" | "models";

const NAV_ITEMS: { id: NavId; label: string; Icon: LucideIcon }[] = [
  { id: "image", label: "Generate Image", Icon: ImageIcon },
  { id: "video", label: "Generate Video", Icon: Film },
  { id: "workflows", label: "Workflows", Icon: Wand2 },
  { id: "library", label: "Library", Icon: Library },
  { id: "models", label: "Models", Icon: Boxes },
];

function NoModelEmptyState({
  kind,
  onGoToModels,
}: {
  kind: "image" | "video";
  onGoToModels: () => void;
}) {
  const Icon = kind === "image" ? ImageIcon : Film;
  return (
    <div className="rounded-xl border border-dashed px-5 py-12 flex flex-col items-center text-center gap-3">
      <Icon className="h-8 w-8 text-muted-foreground/40" />
      <p className="text-sm font-medium">No model selected</p>
      <p className="text-xs text-muted-foreground max-w-sm">
        Pick a {kind} model in the Models view — its full settings will appear
        here.
      </p>
      <Button size="sm" onClick={onGoToModels}>
        <Boxes className="h-3.5 w-3.5 mr-1.5" />
        Open Models
      </Button>
    </div>
  );
}

// ── Models view (image AND video catalogs) ───────────────────────────────────

function ModelsView({
  onGoToImage,
  onGoToVideo,
}: {
  onGoToImage: () => void;
  onGoToVideo: () => void;
}) {
  const [state, actions] = useMediaGenApp();
  const { imageStatus } = state;
  const { refreshImage } = actions;
  const imageCtl = useImageGenController({ onAfterSelect: onGoToImage });
  const videoCtl = useVideoGenController({ onAfterSelect: onGoToVideo });

  return (
    <div className="space-y-8 pb-8">
      <section className="space-y-3">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <ImageIcon className="h-4 w-4 text-violet-500" />
          Image models
        </h3>
        <ImageGenGate>
          {imageStatus?.packages_outdated ? (
            <ImageGenInstaller
              models={[]}
              upgrade
              onInstallComplete={() => void refreshImage()}
            />
          ) : (
            <ImageModelPicker ctl={imageCtl} layout="grid" showHeading={false} />
          )}
        </ImageGenGate>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Film className="h-4 w-4 text-violet-500" />
          Video models
        </h3>
        <VideoGenGate>
          <VideoModelPicker ctl={videoCtl} layout="grid" showHeading={false} />
        </VideoGenGate>
      </section>
    </div>
  );
}

// ── Generate views (pure forms, no sub-tabs) ─────────────────────────────────

function ImageGenerateView({ onGoToModels }: { onGoToModels: () => void }) {
  const [state] = useMediaGenApp();
  const { imageForm } = state;
  const ctl = useImageGenController();

  return (
    <ImageGenGate>
      <div className="space-y-5 pb-8">
        <OutdatedPackagesBanner
          extra={
            <Button size="sm" variant="outline" onClick={onGoToModels}>
              Open Models
            </Button>
          }
        />
        {!ctl.model ? (
          <NoModelEmptyState kind="image" onGoToModels={onGoToModels} />
        ) : imageForm.paramsLoading || !ctl.defaults ? (
          <ImageParamsLoading ctl={ctl} />
        ) : (
          <div className="space-y-5">
            <div className="grid gap-6 lg:grid-cols-2">
              <ImageGenerateForm
                ctl={ctl}
                showHeader
                onSwitchModel={onGoToModels}
              />
              <div className="space-y-3">
                <ImageResultPane />
              </div>
            </div>
            <ImageQueuePanel layout="list" />
          </div>
        )}
      </div>
    </ImageGenGate>
  );
}

function VideoGenerateView({ onGoToModels }: { onGoToModels: () => void }) {
  const [state] = useMediaGenApp();
  const { videoForm } = state;
  const ctl = useVideoGenController();

  return (
    <VideoGenGate>
      <div className="space-y-5 pb-8">
        <ActiveVideoJobCard />
        <VideoPlayback ctl={ctl} />
        {!ctl.model ? (
          <NoModelEmptyState kind="video" onGoToModels={onGoToModels} />
        ) : videoForm.paramsLoading || !ctl.defaults ? (
          <VideoParamsLoading ctl={ctl} />
        ) : (
          <div className="max-w-xl">
            <VideoGenerateForm
              ctl={ctl}
              showHeader
              onSwitchModel={onGoToModels}
            />
          </div>
        )}
        <VideoJobsList ctl={ctl} layout="list" />
      </div>
    </VideoGenGate>
  );
}

// ── Persistent queue footer ──────────────────────────────────────────────────

function QueueFooter({ onJump }: { onJump: (id: NavId) => void }) {
  const [state, actions] = useMediaGenApp();
  const { imageJobs, activeJob } = state;
  const { cancelImageJob, cancelVideoJob, clearActiveJob } = actions;

  const activeImageJobs = imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  );
  const runningImageJob =
    activeImageJobs.find((j) => j.status === "running") ??
    activeImageJobs[0] ??
    null;
  const videoActive =
    activeJob?.status === "queued" || activeJob?.status === "running";

  // Hide entirely when nothing is in flight and nothing recently finished
  // (a finished video job stays until dismissed — that IS the "recent" state).
  if (activeImageJobs.length === 0 && !activeJob) return null;

  return (
    <div className="shrink-0 border-t bg-background/95 px-3 py-1.5">
      <div className="flex items-center gap-4 text-xs">
        {activeImageJobs.length > 0 && (
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={() => onJump("image")}
              className="flex items-center gap-2 min-w-0 hover:text-foreground text-muted-foreground"
              title="Go to Generate Image"
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-500 shrink-0" />
              <span className="whitespace-nowrap">
                {activeImageJobs.length} image job
                {activeImageJobs.length === 1 ? "" : "s"}
              </span>
              {runningImageJob && (
                <>
                  <span className="truncate max-w-[200px] hidden sm:inline">
                    {runningImageJob.prompt || "(no prompt)"}
                  </span>
                  <span className="w-24 shrink-0">
                    <InlineProgressBar
                      percent={(runningImageJob.progress ?? 0) * 100}
                      indeterminate={
                        runningImageJob.status === "queued" ||
                        (runningImageJob.progress ?? 0) <= 0
                      }
                    />
                  </span>
                </>
              )}
            </button>
            {runningImageJob &&
              (runningImageJob.cancel_requested ? (
                <span
                  className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0"
                  title="Cancel requested — the current step is finishing"
                >
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Cancelling…
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => void cancelImageJob(runningImageJob.job_id)}
                  className="text-muted-foreground hover:text-destructive shrink-0"
                  aria-label="Cancel image job"
                  title="Cancel this image job"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ))}
          </div>
        )}

        {activeJob && (
          <div className="flex items-center gap-2 min-w-0">
            <button
              type="button"
              onClick={() => onJump("video")}
              className="flex items-center gap-2 min-w-0 hover:text-foreground text-muted-foreground"
              title="Go to Generate Video"
            >
              {videoActive ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-500 shrink-0" />
              ) : activeJob.status === "completed" ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />
              ) : (
                <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0" />
              )}
              <span className="whitespace-nowrap">
                {videoActive
                  ? "Video generating"
                  : activeJob.status === "completed"
                    ? "Video ready"
                    : "Video failed"}
              </span>
              {activeJob.prompt && (
                <span className="truncate max-w-[200px] hidden sm:inline">
                  {activeJob.prompt}
                </span>
              )}
              {videoActive && (
                <span className="w-24 shrink-0">
                  <InlineProgressBar
                    percent={activeJob.progress * 100}
                    indeterminate={activeJob.status === "queued"}
                  />
                </span>
              )}
              {videoActive && (
                <span className="tabular-nums shrink-0">
                  {Math.round(activeJob.progress * 100)}%
                </span>
              )}
            </button>
            {videoActive &&
              (activeJob.cancel_requested ? (
                <span
                  className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0"
                  title="Cancel requested — the current step is finishing"
                >
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Cancelling…
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => void cancelVideoJob(activeJob.job_id)}
                  className="text-muted-foreground hover:text-destructive shrink-0"
                  aria-label="Cancel video job"
                  title="Cancel this video job"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ))}
            {!videoActive && (
              <button
                type="button"
                onClick={clearActiveJob}
                className="text-muted-foreground hover:text-foreground shrink-0"
                aria-label="Dismiss video job"
                title="Dismiss"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Root: rail + content + queue footer ──────────────────────────────────────

export function VariantWorkspace() {
  const [state] = useMediaGenApp();
  // Presentation-only local state — everything else lives in MediaGenContext.
  const [active, setActive] = useState<NavId>("image");
  const [collapsed, setCollapsed] = useState(false);

  const activeImageJobCount = state.imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  ).length;
  const videoJobActive =
    state.activeJob?.status === "queued" ||
    state.activeJob?.status === "running";

  const goToImage = useCallback(() => setActive("image"), []);
  const goToVideo = useCallback(() => setActive("video"), []);
  const goToModels = useCallback(() => setActive("models"), []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-1 min-h-0">
        <nav
          className={`flex shrink-0 flex-col border-r bg-muted/20 py-2 transition-[width] duration-200 ${
            collapsed ? "w-[52px]" : "w-[200px]"
          }`}
          aria-label="Media generation navigation"
        >
          <div className="flex flex-col gap-0.5 px-1.5">
            {NAV_ITEMS.map(({ id, label, Icon }) => {
              const isActive = active === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setActive(id)}
                  title={label}
                  aria-current={isActive ? "page" : undefined}
                  className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-xs font-medium transition-colors ${
                    isActive
                      ? "bg-violet-500/10 text-violet-600 dark:text-violet-400"
                      : "text-muted-foreground hover:bg-muted/40 hover:text-foreground"
                  } ${collapsed ? "justify-center" : ""}`}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed && (
                    <span className="truncate flex-1 text-left">{label}</span>
                  )}
                  {id === "image" && activeImageJobCount > 0 && (
                    <span
                      className={`rounded-full bg-violet-500/15 px-1.5 text-[10px] tabular-nums text-violet-600 dark:text-violet-400 ${
                        collapsed ? "absolute" : "shrink-0"
                      }`}
                      style={
                        collapsed
                          ? { transform: "translate(10px, -8px)" }
                          : undefined
                      }
                    >
                      {activeImageJobCount}
                    </span>
                  )}
                  {id === "video" && videoJobActive && (
                    <Loader2
                      className={`h-3 w-3 animate-spin text-violet-500 ${
                        collapsed ? "absolute" : "shrink-0"
                      }`}
                      style={
                        collapsed
                          ? { transform: "translate(10px, -8px)" }
                          : undefined
                      }
                    />
                  )}
                </button>
              );
            })}
          </div>
          <div className="mt-auto px-1.5">
            <button
              type="button"
              onClick={() => setCollapsed((c) => !c)}
              title={collapsed ? "Expand navigation" : "Collapse navigation"}
              aria-label={
                collapsed ? "Expand navigation" : "Collapse navigation"
              }
              className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-xs text-muted-foreground hover:bg-muted/40 hover:text-foreground transition-colors ${
                collapsed ? "justify-center" : ""
              }`}
            >
              {collapsed ? (
                <ChevronsRight className="h-4 w-4 shrink-0" />
              ) : (
                <>
                  <ChevronsLeft className="h-4 w-4 shrink-0" />
                  <span>Collapse</span>
                </>
              )}
            </button>
          </div>
        </nav>

        <main className="flex-1 min-w-0 overflow-y-auto">
          <div className="mx-auto w-full max-w-5xl px-5 py-5">
            {active === "image" && (
              <ImageGenerateView onGoToModels={goToModels} />
            )}
            {active === "video" && (
              <VideoGenerateView onGoToModels={goToModels} />
            )}
            {active === "workflows" && <WorkflowSection />}
            {active === "library" && <MediaLibrarySection />}
            {active === "models" && (
              <ModelsView onGoToImage={goToImage} onGoToVideo={goToVideo} />
            )}
          </div>
        </main>
      </div>

      <QueueFooter onJump={setActive} />
    </div>
  );
}
