/**
 * VideoJobPanel — the canonical video-job surfaces every layout previously
 * re-implemented:
 *  - ActiveVideoJobCard  the watched job's progress / cancel / dismiss card
 *  - VideoPlayback       playback <video> + Save-MP4 (+ optional expand)
 *  - VideoJobsList       recent jobs in "list", "chips" or "feed" density
 *                        with cancel (queued/running) and Play/Show actions
 */

import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Download,
  Film,
  Loader2,
  Maximize2,
  Play,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { VideoGenJob } from "@/lib/api";
import { InlineProgressBar } from "@/components/media-gen/shared";
import type { VideoGenController } from "./videoController";

/** The watched job's status card (progress, step counter, cancel, dismiss). */
export function ActiveVideoJobCard({ className = "" }: { className?: string }) {
  const [state, actions] = useMediaGenApp();
  const { activeJob, videoCancelling } = state;
  const { cancelVideoGeneration, clearActiveJob } = actions;
  if (!activeJob) return null;
  const jobIsActive =
    activeJob.status === "queued" || activeJob.status === "running";
  return (
    <div
      className={`rounded-lg border px-4 py-3 space-y-2 ${
        activeJob.status === "failed"
          ? "border-destructive/30 bg-destructive/5"
          : activeJob.status === "completed"
            ? "border-green-500/30 bg-green-500/5"
            : jobIsActive
              ? "border-violet-500/30 bg-violet-500/5"
              : "border-border bg-muted/20"
      } ${className}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0 text-sm">
          {jobIsActive ? (
            <Loader2 className="h-4 w-4 animate-spin text-violet-500 shrink-0" />
          ) : activeJob.status === "completed" ? (
            <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
          ) : activeJob.status === "failed" ? (
            <AlertCircle className="h-4 w-4 text-destructive shrink-0" />
          ) : (
            <Film className="h-4 w-4 text-muted-foreground shrink-0" />
          )}
          <span className="truncate" title={activeJob.prompt}>
            {jobIsActive
              ? "Generating video…"
              : activeJob.status === "completed"
                ? "Video ready"
                : activeJob.status === "failed"
                  ? "Video generation failed"
                  : `Video job — ${activeJob.status}`}
            {activeJob.prompt ? ` — ${activeJob.prompt}` : ""}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
          {jobIsActive && activeJob.total_steps > 0 && (
            <span className="tabular-nums">
              step {activeJob.current_step}/{activeJob.total_steps}
            </span>
          )}
          {jobIsActive && (
            <span className="flex items-center gap-1 tabular-nums">
              <Clock className="h-3 w-3" />
              {Math.round(activeJob.elapsed_seconds)}s
            </span>
          )}
          {jobIsActive &&
            (videoCancelling || activeJob.cancel_requested ? (
              <span className="flex items-center gap-1 text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                Cancelling…
              </span>
            ) : (
              <button
                onClick={() => void cancelVideoGeneration()}
                className="text-destructive hover:underline"
                title="Stop this video generation"
              >
                Cancel
              </button>
            ))}
          {!jobIsActive && (
            <button
              onClick={clearActiveJob}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Dismiss job"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>
      {jobIsActive && (
        <InlineProgressBar
          percent={activeJob.progress * 100}
          indeterminate={activeJob.status === "queued"}
        />
      )}
      {activeJob.status === "failed" && activeJob.error && (
        <p className="text-xs text-destructive break-all">{activeJob.error}</p>
      )}
    </div>
  );
}

/** Playback player + Save MP4, with a "Fetching video…" placeholder. */
export function VideoPlayback({
  ctl,
  onExpand,
  videoClassName = "w-full max-h-[420px] rounded-lg border bg-black",
}: {
  ctl: VideoGenController;
  /** Optional expand-to-lightbox affordance over the player. */
  onExpand?: () => void;
  videoClassName?: string;
}) {
  const url = ctl.playbackUrl;
  if (url) {
    return (
      <div className="space-y-2">
        <div className="relative">
          <video
            key={url}
            controls
            autoPlay
            loop
            src={url}
            className={videoClassName}
          />
          {onExpand && (
            <button
              type="button"
              onClick={onExpand}
              className="absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/55 px-1.5 py-1 text-[10px] text-white transition-colors hover:bg-black/75"
              aria-label="Open video in the viewer"
              title="Open in the full-screen viewer"
            >
              <Maximize2 className="h-3 w-3" />
              Expand
            </button>
          )}
        </div>
        <div className="flex items-center justify-end">
          <a
            href={url}
            download={`matrx-video-${Date.now()}.mp4`}
            className="inline-flex items-center text-xs text-violet-500 hover:underline"
          >
            <Download className="h-3.5 w-3.5 mr-1" />
            Save MP4
          </a>
        </div>
      </div>
    );
  }
  if (ctl.playbackJobId) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed px-4 py-6 justify-center text-muted-foreground text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        Fetching video…
      </div>
    );
  }
  return null;
}

export type VideoJobsLayout = "list" | "chips";

function VideoJobEntry({
  job,
  layout,
  resultUrl,
  onPlay,
  onCancel,
}: {
  job: VideoGenJob;
  layout: VideoJobsLayout;
  resultUrl: string | null;
  onPlay: (jobId: string) => void;
  onCancel: (jobId: string) => void;
}) {
  const active = job.status === "queued" || job.status === "running";
  const cancelling = active && !!job.cancel_requested;

  if (layout === "chips") {
    return (
      <div className="flex w-64 shrink-0 flex-col gap-1 rounded-lg border bg-card px-2.5 py-2">
        <div className="flex items-center gap-2">
          {job.status === "completed" ? (
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-500" />
          ) : job.status === "failed" ? (
            <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
          ) : active ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-violet-500" />
          ) : (
            <Film className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <span
            className="min-w-0 flex-1 truncate text-[11px]"
            title={job.prompt}
          >
            {job.prompt || "(no prompt)"}
          </span>
          {job.status === "completed" && (
            <button
              type="button"
              onClick={() => onPlay(job.job_id)}
              className="shrink-0 text-violet-500 hover:underline text-[11px] inline-flex items-center gap-0.5"
            >
              <Play className="h-3 w-3" />
              {resultUrl ? "Show" : "Play"}
            </button>
          )}
          {active &&
            (cancelling ? (
              <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
            ) : (
              <button
                type="button"
                onClick={() => onCancel(job.job_id)}
                className="shrink-0 text-muted-foreground hover:text-destructive"
                aria-label="Cancel video job"
                title="Cancel this video job"
              >
                <X className="h-3 w-3" />
              </button>
            ))}
        </div>
        {active ? (
          <InlineProgressBar
            percent={job.progress * 100}
            indeterminate={job.status === "queued"}
          />
        ) : (
          <p className="truncate text-[10px] text-muted-foreground">
            {job.status === "failed"
              ? (job.error ?? "failed")
              : `${job.elapsed_seconds.toFixed(0)}s · ${job.model_id || "—"}`}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5">
      <div className="w-5 shrink-0">
        {job.status === "completed" ? (
          <CheckCircle2 className="h-4 w-4 text-green-500" />
        ) : job.status === "failed" ? (
          <AlertCircle className="h-4 w-4 text-destructive" />
        ) : active ? (
          <Loader2 className="h-4 w-4 animate-spin text-violet-500" />
        ) : (
          <Film className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs" title={job.prompt}>
          {job.prompt || "(no prompt)"}
        </p>
        <p className="text-[10px] text-muted-foreground">
          {job.model_id || "—"} ·{" "}
          {job.status === "failed"
            ? (job.error ?? "failed")
            : job.status === "completed"
              ? `${job.elapsed_seconds.toFixed(0)}s`
              : cancelling
                ? "cancelling…"
                : active
                  ? `${Math.round(job.progress * 100)}%`
                  : job.status}
        </p>
      </div>
      {active &&
        (cancelling ? (
          <span
            className="flex items-center gap-1 text-[10px] text-muted-foreground shrink-0"
            title="Cancel requested — the current step is finishing (can take tens of seconds)"
          >
            <Loader2 className="h-3 w-3 animate-spin" />
            Cancelling…
          </span>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive shrink-0"
            onClick={() => onCancel(job.job_id)}
            title="Cancel this video job"
          >
            <X className="h-3.5 w-3.5 mr-1" />
            Cancel
          </Button>
        ))}
      {job.status === "completed" && (
        <Button size="sm" variant="outline" onClick={() => onPlay(job.job_id)}>
          <Play className="h-3.5 w-3.5 mr-1" />
          {resultUrl ? "Show" : "Play"}
        </Button>
      )}
    </div>
  );
}

/** Recent video jobs. Renders nothing when there are none. */
export function VideoJobsList({
  ctl,
  layout = "list",
  heading = "Recent videos",
  showHeading = true,
  excludeActive = false,
}: {
  ctl: VideoGenController;
  layout?: VideoJobsLayout;
  heading?: string;
  showHeading?: boolean;
  /** Skip the watched job (when ActiveVideoJobCard already shows it). */
  excludeActive?: boolean;
}) {
  const [state, actions] = useMediaGenApp();
  const { jobs, activeJob, videoResults } = state;
  const { cancelVideoJob } = actions;
  const list = excludeActive
    ? jobs.filter((j) => j.job_id !== activeJob?.job_id)
    : jobs;
  if (list.length === 0) return null;
  const entries = list.map((j) => (
    <VideoJobEntry
      key={j.job_id}
      job={j}
      layout={layout}
      resultUrl={videoResults[j.job_id] ?? null}
      onPlay={ctl.handlePlay}
      onCancel={(id) => void cancelVideoJob(id)}
    />
  ));
  return (
    <div className="space-y-2">
      {showHeading && <h3 className="text-sm font-semibold">{heading}</h3>}
      {layout === "chips" ? (
        <div className="flex gap-2 overflow-x-auto pb-1">{entries}</div>
      ) : (
        <div className="space-y-2">{entries}</div>
      )}
    </div>
  );
}
