/**
 * ImageQueuePanel — THE image job-queue rendering, previously re-wired five
 * times (Images tab rows, Studio chips, Workspace rows, Gallery chips, Focus
 * feed cards). One implementation, three layouts:
 *  - "list":  full-width rows with thumbnails (classic sections, Workspace)
 *  - "chips": compact horizontal chips for rails/strips (Studio, Gallery)
 *  - "feed":  large vertical cards with the full image (Focus)
 *
 * Every layout shares the same semantics: up-next badge, optimistic
 * "Cancelling…" states, seed chips with reuse, and click-to-open of completed
 * jobs via the shared useImageJobLightbox (a click is never silently dead) —
 * which lands in the app-wide lightbox with the full action set.
 * Reads MediaGenContext directly — no prop threading of jobs/thumbs.
 */

import { AlertCircle, CheckCircle2, Loader2, X } from "lucide-react";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenJob } from "@/lib/api";
import { MediaThumb } from "@/components/media/MediaThumb";
import { descriptorFromJob, type MediaDescriptor } from "@/components/media/types";
import { CopyButton } from "@/components/media/MediaInfoDialog";
import {
  ErrorNote,
  InlineProgressBar,
  SeedChip,
  UpNextBadge,
  isUpNextJob,
  useImageJobLightbox,
} from "@/components/media-gen/shared";

export type ImageQueueLayout = "list" | "chips" | "feed";

function JobStatusIcon({
  job,
  opening,
  className = "h-4 w-4",
}: {
  job: ImageGenJob;
  opening: boolean;
  className?: string;
}) {
  if (opening) {
    return <Loader2 className={`${className} animate-spin text-violet-500`} />;
  }
  switch (job.status) {
    case "completed":
      return <CheckCircle2 className={`${className} text-green-500`} />;
    case "failed":
      return <AlertCircle className={`${className} text-destructive`} />;
    case "cancelled":
      return <X className={`${className} text-muted-foreground`} />;
    default:
      return (
        <Loader2 className={`${className} animate-spin text-violet-500`} />
      );
  }
}

/** "Cancelling…" pill or the cancel/remove X, shared by all layouts. */
function CancelControl({
  job,
  onCancel,
}: {
  job: ImageGenJob;
  onCancel: (jobId: string) => void;
}) {
  const active = job.status === "queued" || job.status === "running";
  const cancelling = active && !!job.cancel_requested;
  if (cancelling) {
    return (
      <span
        className="flex items-center gap-1 text-[10px] text-muted-foreground"
        title="Cancel requested — the current step is finishing"
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        Cancelling…
      </span>
    );
  }
  return (
    <button
      onClick={() => onCancel(job.job_id)}
      className="text-muted-foreground hover:text-foreground"
      aria-label={active ? "Cancel job" : "Remove job"}
      title={active ? "Cancel this job" : "Remove from the queue"}
    >
      <X className="h-3.5 w-3.5" />
    </button>
  );
}

/** Row/chip/card interaction wrapper: click-to-open for completed jobs. */
function viewableProps(
  job: ImageGenJob,
  onOpen: (job: ImageGenJob) => void,
): React.HTMLAttributes<HTMLDivElement> {
  const viewable = job.status === "completed" && !!job.item_id;
  if (!viewable) return {};
  return {
    onClick: () => onOpen(job),
    role: "button",
    tabIndex: 0,
    onKeyDown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onOpen(job);
      }
    },
    title: "Click to view the image",
  };
}

interface JobViewProps {
  job: ImageGenJob;
  /** Canonical descriptor — present once the job's bytes are available. */
  descriptor: MediaDescriptor | null;
  onCancel: (jobId: string) => void;
  onReuseSeed: (seed: number) => void;
  onOpen: (job: ImageGenJob) => void;
  opening: boolean;
}

function JobRowList({
  job,
  descriptor,
  onCancel,
  onReuseSeed,
  onOpen,
  opening,
}: JobViewProps) {
  const active = job.status === "queued" || job.status === "running";
  const cancelling = active && !!job.cancel_requested;
  const viewable = job.status === "completed" && !!job.item_id;
  return (
    <div
      className={`rounded-lg border bg-card px-3 py-2.5 space-y-1.5 ${
        viewable ? "cursor-pointer transition-colors hover:bg-muted/20" : ""
      }`}
      {...viewableProps(job, onOpen)}
    >
      <div className="flex items-center gap-3">
        <div className="w-5 shrink-0">
          <JobStatusIcon job={job} opening={opening} />
        </div>
        {descriptor ? (
          // Canonical thumb — a queue thumbnail opens full size, right-clicks
          // to every action, and shows its metadata, exactly like a library
          // tile. Clicks are stopped from reaching the row (which opens the
          // same viewer anyway, with the whole queue as the browse set).
          <div onClick={(e) => e.stopPropagation()} className="shrink-0">
            <MediaThumb
              item={descriptor}
              variant="icon"
              onActivate={() => onOpen(job)}
              className="h-10 w-10 rounded border"
            />
          </div>
        ) : null}
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-1.5">
            <p className="min-w-0 flex-1 truncate text-xs" title={job.prompt}>
              {job.prompt || "(no prompt)"}
            </p>
            {/* A queued/running job has no descriptor yet (no bytes), so the ⋯
                menu cannot be its escape hatch — the prompt must be copyable
                right here or it is unrecoverable. */}
            {job.prompt && (
              <CopyButton
                value={job.prompt}
                label="Copy prompt"
                className="mt-0.5"
              />
            )}
          </div>
          <p className="text-[10px] text-muted-foreground">
            {job.model_id || "—"} · {cancelling ? "cancelling…" : job.status}
            {job.status === "failed" && job.error ? ` — ${job.error}` : ""}
          </p>
        </div>
        {/* Controls: clicks here must never bubble into the row's open. */}
        <div
          className="flex items-center gap-2 shrink-0"
          onClick={(e) => e.stopPropagation()}
        >
          {isUpNextJob(job) && <UpNextBadge />}
          {typeof job.seed === "number" && (
            <SeedChip seed={job.seed} onReuse={onReuseSeed} />
          )}
          <CancelControl job={job} onCancel={onCancel} />
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

function JobChip({
  job,
  descriptor,
  onCancel,
  onReuseSeed,
  onOpen,
  opening,
}: JobViewProps) {
  const viewable = job.status === "completed" && !!job.item_id;
  return (
    <div
      className={`flex w-64 shrink-0 flex-col gap-1.5 rounded-lg border bg-card px-2.5 py-2 ${
        viewable ? "cursor-pointer transition-colors hover:bg-muted/20" : ""
      }`}
      {...viewableProps(job, onOpen)}
    >
      <div className="flex items-center gap-2">
        {descriptor ? (
          <div onClick={(e) => e.stopPropagation()} className="shrink-0">
            <MediaThumb
              item={descriptor}
              variant="icon"
              onActivate={() => onOpen(job)}
              className="h-9 w-9 rounded border"
            />
          </div>
        ) : (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded border bg-muted/30">
            <JobStatusIcon job={job} opening={opening} />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-1">
            <p className="min-w-0 flex-1 truncate text-[11px]" title={job.prompt}>
              {job.prompt || "(no prompt)"}
            </p>
            {job.prompt && (
              <CopyButton value={job.prompt} label="Copy prompt" />
            )}
          </div>
          <p className="truncate text-[10px] text-muted-foreground">
            {job.status === "queued" || job.status === "running"
              ? job.cancel_requested
                ? "cancelling…"
                : job.status
              : job.status}
            {job.status === "failed" && job.error ? ` — ${job.error}` : ""}
          </p>
        </div>
        <div
          className="flex shrink-0 items-center gap-1.5"
          onClick={(e) => e.stopPropagation()}
        >
          {isUpNextJob(job) && <UpNextBadge />}
          <CancelControl job={job} onCancel={onCancel} />
        </div>
      </div>
      {job.status === "running" && (
        <InlineProgressBar
          percent={(job.progress ?? 0) * 100}
          indeterminate={(job.progress ?? 0) <= 0}
        />
      )}
      {job.status === "completed" && typeof job.seed === "number" && (
        <div onClick={(e) => e.stopPropagation()}>
          <SeedChip seed={job.seed} onReuse={onReuseSeed} />
        </div>
      )}
    </div>
  );
}

function JobFeedCard({
  job,
  descriptor,
  onCancel,
  onReuseSeed,
  onOpen,
  opening,
}: JobViewProps) {
  const active = job.status === "queued" || job.status === "running";
  const viewable = job.status === "completed" && !!job.item_id;

  if (job.status === "completed" && descriptor) {
    return (
      <figure className="overflow-hidden rounded-xl border bg-card">
        <MediaThumb
          item={descriptor}
          variant="gallery"
          onActivate={() => onOpen(job)}
          className="w-full"
        />
        <figcaption className="space-y-1.5 px-4 py-3">
          <div className="flex items-start gap-1.5">
            <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-muted-foreground">
              {job.prompt || "(no prompt)"}
            </p>
            {job.prompt && <CopyButton value={job.prompt} label="Copy prompt" />}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            {typeof job.seed === "number" && (
              <SeedChip seed={job.seed} onReuse={onReuseSeed} />
            )}
            {typeof job.elapsed_seconds === "number" && (
              <span className="tabular-nums">
                {job.elapsed_seconds.toFixed(1)}s
              </span>
            )}
            <span className="flex-1" />
            <button
              type="button"
              onClick={() => onCancel(job.job_id)}
              className="text-muted-foreground/70 transition-colors hover:text-foreground"
              title="Remove from the queue"
            >
              Remove
            </button>
          </div>
        </figcaption>
      </figure>
    );
  }

  // Slim card: queued / running / failed / cancelled / completed-sans-thumb.
  return (
    <div
      className={`space-y-2 rounded-xl border bg-card px-4 py-3 ${
        viewable ? "cursor-pointer transition-colors hover:bg-muted/20" : ""
      }`}
      {...viewableProps(job, onOpen)}
    >
      <div className="flex items-center gap-3">
        <span className="shrink-0">
          <JobStatusIcon job={job} opening={opening} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-1.5">
            <p className="min-w-0 flex-1 truncate text-xs" title={job.prompt}>
              {job.prompt || "(no prompt)"}
            </p>
            {job.prompt && (
              <CopyButton
                value={job.prompt}
                label="Copy prompt"
                className="mt-0.5"
              />
            )}
          </div>
          <p className="text-[10px] text-muted-foreground">
            {job.status === "failed" && job.error
              ? job.error
              : active && job.cancel_requested
                ? "cancelling…"
                : job.status}
          </p>
        </div>
        <div
          className="flex shrink-0 items-center gap-2"
          onClick={(e) => e.stopPropagation()}
        >
          {isUpNextJob(job) && <UpNextBadge />}
          {typeof job.seed === "number" && !active && (
            <SeedChip seed={job.seed} onReuse={onReuseSeed} />
          )}
          <CancelControl job={job} onCancel={onCancel} />
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

/**
 * The panel. Renders nothing when the queue is empty and healthy (unless
 * `alwaysRender`). Mounts ONE shared lightbox for its jobs.
 */
export function ImageQueuePanel({
  layout = "list",
  limit,
  heading = "Generation history",
  showHeading = true,
}: {
  layout?: ImageQueueLayout;
  /** Cap the number of rendered jobs (e.g. Gallery strip shows 12). */
  limit?: number;
  heading?: string;
  showHeading?: boolean;
}) {
  const [state, actions] = useMediaGenApp();
  const { imageJobs, imageJobsError, imageJobThumbs, canLoadMoreImageHistory } = state;
  const { cancelImageJob, setImageForm, loadMoreImageHistory } = actions;

  const reuseSeed = (seed: number) => setImageForm({ seedText: String(seed) });
  const { openJob, openingJobId, descriptorOf } = useImageJobLightbox({
    jobs: imageJobs,
    thumbs: imageJobThumbs,
  });

  if (imageJobs.length === 0 && !imageJobsError) return null;

  const jobs = typeof limit === "number" ? imageJobs.slice(0, limit) : imageJobs;
  const activeCount = imageJobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  ).length;
  const onCancel = (id: string) => void cancelImageJob(id);

  const items = jobs.map((j) => {
    const thumbUrl = imageJobThumbs[j.job_id] ?? null;
    const common = {
      job: j,
      descriptor: descriptorOf(j) ?? (thumbUrl ? descriptorFromJob(j, thumbUrl) : null),
      onCancel,
      onReuseSeed: reuseSeed,
      onOpen: openJob,
      opening: openingJobId === j.job_id,
    };
    if (layout === "chips") return <JobChip key={j.job_id} {...common} />;
    if (layout === "feed") return <JobFeedCard key={j.job_id} {...common} />;
    return <JobRowList key={j.job_id} {...common} />;
  });

  return (
    <div className="space-y-2">
      {showHeading && (
        <h3 className="text-sm font-semibold">
          {heading}
          {activeCount > 0 && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {activeCount} active
            </span>
          )}
        </h3>
      )}
      {imageJobsError && <ErrorNote message={imageJobsError} />}
      {layout === "chips" ? (
        <div className="flex gap-2 overflow-x-auto pb-1">{items}</div>
      ) : (
        <div className="space-y-2">{items}</div>
      )}
      {canLoadMoreImageHistory && (
        <button
          type="button"
          onClick={() => void loadMoreImageHistory()}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          Load older history
        </button>
      )}
    </div>
  );
}
