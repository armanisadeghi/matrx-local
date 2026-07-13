/**
 * BatchConfirmDialog — the last thing between the user and 400 GPU-minutes.
 *
 * Queueing a batch is cheap to click and expensive to run, so this dialog does
 * the three things a confirmation must do to be worth showing at all:
 *
 *  1. States the COUNT in plain words, before anything is scheduled.
 *  2. Estimates the TIME from the machine's own recent generations, because
 *     "180 images" means nothing until it means "about 2 hours".
 *  3. Shows the actual first and last runs — the cheapest possible way to catch
 *     a template mistake that would otherwise repeat itself 180 times.
 *
 * A big batch additionally requires typing nothing — the count is the warning —
 * but the primary button is deliberately not focused, so Enter cannot start it.
 */

import { AlertTriangle, Clock, Layers, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { LARGE_BATCH_THRESHOLD, type MatrixPlan } from "@/lib/prompt-matrix";
import { cn } from "@/lib/utils";

/** Human-readable duration from seconds ("about 2h 10m"). */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "unknown";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  if (m >= 1) return `${m}m`;
  return `${Math.round(seconds)}s`;
}

export function BatchConfirmDialog({
  open,
  onOpenChange,
  plan,
  /** Median seconds per image from this machine's recent jobs; null if unknown. */
  secondsPerRun,
  queuedAhead,
  submitting,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  plan: MatrixPlan;
  secondsPerRun: number | null;
  queuedAhead: number;
  submitting: boolean;
  onConfirm: () => void;
}) {
  const total = plan.total;
  const large = total >= LARGE_BATCH_THRESHOLD;
  const first = plan.combinations[0];
  const last = plan.combinations[plan.combinations.length - 1];
  const estimate =
    secondsPerRun !== null ? formatDuration(secondsPerRun * total) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-lg overflow-x-hidden overflow-y-auto">
        <DialogHeader className="min-w-0">
          <DialogTitle className="flex items-center gap-2">
            <Layers className="h-4 w-4 shrink-0" />
            Queue {total.toLocaleString()}{" "}
            {total === 1 ? "generation" : "generations"}?
          </DialogTitle>
          <DialogDescription className="break-words leading-relaxed">
            They run one at a time, in the order shown. You can pause, reorder,
            or cancel the batch at any point — including after it starts.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {/* ── the numbers ────────────────────────────────────────────── */}
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Runs" value={total.toLocaleString()} emphasize={large} />
            <Stat
              label="Est. time"
              value={estimate ?? "—"}
              hint={
                estimate === null
                  ? "No timing history on this machine yet"
                  : `≈ ${Math.round(secondsPerRun ?? 0)}s each`
              }
            />
            <Stat
              label="Queued ahead"
              value={queuedAhead.toLocaleString()}
              hint={queuedAhead > 0 ? "These run first" : undefined}
            />
          </div>

          {large && (
            <div className="flex gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2.5 text-xs text-amber-800 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                That is a lot of generations
                {estimate !== null ? ` — roughly ${estimate} of GPU time` : ""}.
                Consider <strong>One at a time</strong> or a{" "}
                <strong>Random sample</strong> to probe the space first.
              </span>
            </div>
          )}

          {plan.warnings.length > 0 && (
            <ul className="space-y-1 rounded-md border bg-muted/40 p-2.5 text-xs text-muted-foreground">
              {plan.warnings.map((w) => (
                <li key={w} className="flex gap-1.5">
                  <span aria-hidden>•</span>
                  <span>{w}</span>
                </li>
              ))}
            </ul>
          )}

          {/* ── what actually gets generated ───────────────────────────── */}
          {first !== undefined && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">
                First run
              </p>
              <RunPreview
                label={first.label}
                prompt={first.rendered["prompt"] ?? ""}
                seed={first.seed}
              />
              {last !== undefined && total > 1 && (
                <>
                  <p className="flex items-center gap-1.5 pt-1 text-xs font-medium text-muted-foreground">
                    <span>Last run</span>
                    {plan.truncated && (
                      <Badge variant="outline" className="h-4 px-1 text-[10px]">
                        of the first {plan.combinations.length.toLocaleString()}{" "}
                        shown
                      </Badge>
                    )}
                  </p>
                  <RunPreview
                    label={last.label}
                    prompt={last.rendered["prompt"] ?? ""}
                    seed={last.seed}
                  />
                </>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button onClick={onConfirm} disabled={submitting} className="gap-1.5">
            {submitting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Queueing…
              </>
            ) : (
              <>
                <Layers className="h-3.5 w-3.5" />
                Queue {total.toLocaleString()}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({
  label,
  value,
  hint,
  emphasize = false,
}: {
  label: string;
  value: string;
  hint?: string | undefined;
  emphasize?: boolean;
}) {
  return (
    <div className="min-w-0 rounded-md border bg-muted/30 p-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          "text-lg font-semibold tabular-nums",
          emphasize && "text-amber-600 dark:text-amber-400",
        )}
      >
        {value}
      </p>
      {hint !== undefined && (
        <p className="flex min-w-0 items-start gap-1 break-words text-[10px] leading-snug text-muted-foreground">
          {label === "Est. time" && (
            <Clock className="mt-0.5 h-2.5 w-2.5 shrink-0" />
          )}
          <span>{hint}</span>
        </p>
      )}
    </div>
  );
}

function RunPreview({
  label,
  prompt,
  seed,
}: {
  label: string;
  prompt: string;
  seed: number;
}) {
  return (
    <div className="rounded-md border bg-muted/30 p-2">
      <div className="flex items-center gap-1.5 pb-1">
        <code className="truncate text-[11px] text-primary">{label || "—"}</code>
        <span className="ml-auto shrink-0 text-[10px] tabular-nums text-muted-foreground">
          seed {seed}
        </span>
      </div>
      <ScrollArea className="max-h-16">
        <p className="whitespace-pre-wrap break-words text-xs">{prompt}</p>
      </ScrollArea>
    </div>
  );
}
