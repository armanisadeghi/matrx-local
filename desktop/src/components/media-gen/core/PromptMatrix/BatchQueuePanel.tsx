/**
 * BatchQueuePanel — control over the queue AFTER it has started running.
 *
 * An unattended batch is only trustworthy if you can still steer it, so this
 * panel exists for exactly the things a user wants at 11pm with 140 images left:
 *
 *  • PAUSE — stop starting new work; the running job finishes (aborting a
 *    90%-denoised generation to honour a pause would throw away real GPU time).
 *  • REORDER — drag the pending jobs. The one you care about runs next, not in
 *    two hours. The running job never moves.
 *  • CANCEL a whole batch — without touching the images it already produced.
 *  • RETRY what failed — one click, fresh attempt budget.
 *
 * Reordering is optimistic: the dragged row lands where it was dropped and
 * stays there, then reconciles against the engine's authoritative order.
 */

import { useCallback, useMemo, useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  restrictToParentElement,
  restrictToVerticalAxis,
} from "@dnd-kit/modifiers";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  AlertTriangle,
  Ban,
  GripVertical,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Timer,
  Trash2,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMediaGenApp } from "@/contexts/MediaGenContext";
import type { ImageGenBatch, ImageGenJob } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatDuration } from "./BatchConfirmDialog";

export function BatchQueuePanel() {
  const [state, actions] = useMediaGenApp();
  const { imageJobs, imageBatches, imageQueueState, imageJobsError } = state;
  const {
    setImageQueuePaused,
    reorderImageQueue,
    cancelImageJob,
    cancelImageBatch,
    retryImageJob,
    clearFinishedImageJobs,
  } = actions;

  const running = useMemo(
    () => imageJobs.filter((j) => j.status === "running"),
    [imageJobs],
  );
  // The engine returns newest-first; the QUEUE reads oldest-first (run order).
  const queued = useMemo(
    () => imageJobs.filter((j) => j.status === "queued").slice().reverse(),
    [imageJobs],
  );
  const failed = useMemo(
    () => imageJobs.filter((j) => j.status === "failed"),
    [imageJobs],
  );
  const finishedCount = useMemo(
    () =>
      imageJobs.filter(
        (j) =>
          j.status === "completed" ||
          j.status === "failed" ||
          j.status === "cancelled",
      ).length,
    [imageJobs],
  );

  const paused = imageQueueState?.paused ?? false;
  const activeBatches = imageBatches.filter((b) => !b.finished);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  // Optimistic order so the dragged row doesn't snap back while the request
  // is in flight. Cleared once the engine's order agrees.
  const [dragOrder, setDragOrder] = useState<string[] | null>(null);
  const orderedQueued = useMemo(() => {
    if (dragOrder === null) return queued;
    const byId = new Map(queued.map((j) => [j.job_id, j]));
    const out = dragOrder
      .map((id) => byId.get(id))
      .filter((j): j is ImageGenJob => j !== undefined);
    // Anything the engine added since the drag (a new enqueue) goes to the end.
    for (const j of queued) if (!dragOrder.includes(j.job_id)) out.push(j);
    return out;
  }, [queued, dragOrder]);

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (over === null || active.id === over.id) return;
      const ids = orderedQueued.map((j) => j.job_id);
      const from = ids.indexOf(String(active.id));
      const to = ids.indexOf(String(over.id));
      if (from < 0 || to < 0) return;
      const next = [...ids];
      next.splice(to, 0, ...next.splice(from, 1));
      setDragOrder(next);
      void reorderImageQueue(next).finally(() => setDragOrder(null));
    },
    [orderedQueued, reorderImageQueue],
  );

  // Estimated time left, from this machine's own median generation time.
  // Every hook must run before ANY early return — hooks are positional.
  const secondsPerRun = useMemo(() => {
    const times = imageJobs
      .filter((j) => j.status === "completed" && (j.elapsed_seconds ?? 0) > 0)
      .map((j) => j.elapsed_seconds as number)
      .sort((a, b) => a - b);
    return times.length > 0 ? (times[Math.floor(times.length / 2)] ?? null) : null;
  }, [imageJobs]);

  const totalPending = running.length + queued.length;
  if (totalPending === 0 && finishedCount === 0) return null;

  return (
    <div className="space-y-3">
      {/* ── header: the master controls ──────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-medium">Queue</h3>
        {totalPending > 0 && (
          <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
            {totalPending} pending
          </Badge>
        )}
        {paused && (
          <Badge className="h-5 gap-1 bg-amber-500/15 px-1.5 text-[10px] text-amber-700 hover:bg-amber-500/15 dark:text-amber-400">
            <Pause className="h-2.5 w-2.5" />
            Paused
          </Badge>
        )}
        {secondsPerRun !== null && totalPending > 0 && !paused && (
          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Timer className="h-3 w-3" />
            ~{formatDuration(secondsPerRun * totalPending)} left
          </span>
        )}

        <div className="ml-auto flex items-center gap-1">
          {totalPending > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant={paused ? "default" : "outline"}
                  size="sm"
                  className="h-7 gap-1.5 text-xs"
                  onClick={() => void setImageQueuePaused(!paused)}
                >
                  {paused ? (
                    <>
                      <Play className="h-3.5 w-3.5" />
                      Resume
                    </>
                  ) : (
                    <>
                      <Pause className="h-3.5 w-3.5" />
                      Pause
                    </>
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs">
                {paused
                  ? "Start running queued jobs again."
                  : "Stop starting new jobs. The one already running finishes."}
              </TooltipContent>
            </Tooltip>
          )}
          {finishedCount > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 gap-1.5 text-xs text-muted-foreground"
                  onClick={() => void clearFinishedImageJobs()}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Clear finished
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                Removes the records only — your images are kept.
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>

      {imageJobsError !== null && (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
          {imageJobsError}
        </p>
      )}

      {/* ── batches: one row per sweep ───────────────────────────────────── */}
      {activeBatches.map((batch) => (
        <BatchRow
          key={batch.batch_id}
          batch={batch}
          onCancel={() => void cancelImageBatch(batch.batch_id)}
        />
      ))}

      {/* ── running ──────────────────────────────────────────────────────── */}
      {running.map((job) => (
        <RunningRow
          key={job.job_id}
          job={job}
          onCancel={() => void cancelImageJob(job.job_id)}
        />
      ))}

      {/* ── pending: drag to reorder ─────────────────────────────────────── */}
      {orderedQueued.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] text-muted-foreground">
            Up next — drag to reorder
          </p>
          <ScrollArea className={orderedQueued.length > 8 ? "h-72" : ""}>
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
              modifiers={[restrictToVerticalAxis, restrictToParentElement]}
            >
              <SortableContext
                items={orderedQueued.map((j) => j.job_id)}
                strategy={verticalListSortingStrategy}
              >
                <div className="space-y-1 pr-2">
                  {orderedQueued.map((job, i) => (
                    <QueuedRow
                      key={job.job_id}
                      job={job}
                      position={i + 1}
                      onCancel={() => void cancelImageJob(job.job_id)}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          </ScrollArea>
        </div>
      )}

      {/* ── failed ───────────────────────────────────────────────────────── */}
      {failed.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] text-muted-foreground">
            Failed — {failed.length}
          </p>
          {failed.slice(0, 5).map((job) => (
            <FailedRow
              key={job.job_id}
              job={job}
              onRetry={() => void retryImageJob(job.job_id)}
              onRemove={() => void cancelImageJob(job.job_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── rows ─────────────────────────────────────────────────────────────────────

function BatchRow({
  batch,
  onCancel,
}: {
  batch: ImageGenBatch;
  onCancel: () => void;
}) {
  const pct = batch.total > 0 ? (batch.done / batch.total) * 100 : 0;
  return (
    <div className="rounded-lg border bg-card p-2.5">
      <div className="flex items-center gap-2">
        <span className="truncate text-xs font-medium">
          {batch.label ?? "Batch"}
        </span>
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {batch.done} / {batch.total}
        </span>
        {batch.failed > 0 && (
          <Badge
            variant="outline"
            className="h-4 shrink-0 gap-0.5 border-destructive/40 px-1 text-[10px] text-destructive"
          >
            <AlertTriangle className="h-2.5 w-2.5" />
            {batch.failed}
          </Badge>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto h-6 shrink-0 gap-1 px-1.5 text-[11px] text-muted-foreground hover:text-destructive"
              onClick={onCancel}
            >
              <Ban className="h-3 w-3" />
              Cancel batch
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            Cancels the {batch.queued + batch.running} unfinished job
            {batch.queued + batch.running === 1 ? "" : "s"}. The{" "}
            {batch.completed} image{batch.completed === 1 ? "" : "s"} it already
            made are kept.
          </TooltipContent>
        </Tooltip>
      </div>
      <Progress value={pct} className="mt-1.5 h-1" />
    </div>
  );
}

function RunningRow({
  job,
  onCancel,
}: {
  job: ImageGenJob;
  onCancel: () => void;
}) {
  const cancelling = job.cancel_requested === true;
  const pct = (job.progress ?? 0) * 100;
  return (
    <div className="rounded-lg border border-primary/40 bg-primary/5 p-2.5">
      <div className="flex items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        <JobLabel job={job} />
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
          {Math.round(pct)}%
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
          onClick={onCancel}
          disabled={cancelling}
          aria-label="Cancel this generation"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <Progress value={pct} className="mt-1.5 h-1" />
      {cancelling && (
        <p className="pt-1 text-[10px] text-muted-foreground">
          Cancelling… stops at the next step.
        </p>
      )}
      {(job.attempts ?? 0) > 1 && (
        <p className="pt-1 text-[10px] text-amber-600 dark:text-amber-400">
          Attempt {job.attempts} of {job.max_attempts}
        </p>
      )}
    </div>
  );
}

function QueuedRow({
  job,
  position,
  onCancel,
}: {
  job: ImageGenJob;
  position: number;
  onCancel: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: job.job_id });

  const retrying = job.retrying === true;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "flex items-center gap-1.5 rounded-md border bg-card px-2 py-1.5",
        isDragging && "z-10 opacity-80 shadow-lg",
      )}
    >
      <button
        type="button"
        className="cursor-grab touch-none text-muted-foreground hover:text-foreground active:cursor-grabbing"
        aria-label="Reorder this job"
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-3.5 w-3.5" />
      </button>
      <span className="w-5 shrink-0 text-[10px] tabular-nums text-muted-foreground">
        {position}
      </span>
      <JobLabel job={job} />

      {retrying && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="outline"
              className="h-4 shrink-0 gap-0.5 border-amber-500/40 px-1 text-[10px] text-amber-600 dark:text-amber-400"
            >
              <RefreshCw className="h-2.5 w-2.5" />
              retry {job.attempts}/{job.max_attempts}
            </Badge>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            Failed and will try again
            {(job.retry_in_seconds ?? 0) > 0
              ? ` in ${Math.ceil(job.retry_in_seconds ?? 0)}s`
              : ""}
            {job.last_error != null ? `: ${job.last_error}` : "."}
          </TooltipContent>
        </Tooltip>
      )}

      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
        onClick={onCancel}
        aria-label="Remove from the queue"
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

function FailedRow({
  job,
  onRetry,
  onRemove,
}: {
  job: ImageGenJob;
  onRetry: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1.5">
      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <JobLabel job={job} />
        {job.error != null && (
          <p className="truncate text-[10px] text-destructive">{job.error}</p>
        )}
      </div>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 shrink-0 text-muted-foreground hover:text-foreground"
            onClick={onRetry}
            aria-label="Retry"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>
          Try again{" "}
          {(job.attempts ?? 0) > 1
            ? `(gave up after ${job.attempts} attempts)`
            : ""}
        </TooltipContent>
      </Tooltip>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
        onClick={onRemove}
        aria-label="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

/**
 * The combination label is what makes a queued row legible — `style=noir ·
 * subject=cat` tells you which run this is at a glance. Falls back to the
 * prompt for one-off jobs that came from the normal form.
 */
function JobLabel({ job }: { job: ImageGenJob }) {
  const label = job.combo_label;
  return (
    <div className="min-w-0 flex-1">
      {label != null && label.length > 0 ? (
        <code className="block truncate text-[11px] text-primary">{label}</code>
      ) : (
        <span className="block truncate text-[11px]">{job.prompt}</span>
      )}
      {job.batch_size != null && job.batch_size > 0 && (
        <span className="text-[10px] text-muted-foreground">
          {(job.batch_index ?? 0) + 1} of {job.batch_size}
          {job.batch_label != null ? ` · ${job.batch_label}` : ""}
        </span>
      )}
    </div>
  );
}
