/**
 * BatchPreviewDialog — dry-run the real buildJobs path, then decide.
 *
 * Opens on a FROZEN snapshot of the jobs that would be enqueued. Order, seeds,
 * rendered prompts, and param sweeps are identical to Queue — we do not
 * re-expand the matrix when the user hits "Queue selected". Checkboxes only
 * filter which frozen jobs leave the building; they never re-roll the plan.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckSquare, Copy, Eye, Layers, Loader2, XSquare } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ImageGenBatchJobSpec } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface PreviewRun {
  /** Position in the batch (enqueue order). */
  index: number;
  label: string;
  prompt: string;
  negativePrompt: string;
  seed: number;
  values: Record<string, string>;
  /** Exact job payload that would be POSTed — do not rebuild. */
  job: ImageGenBatchJobSpec;
}

export function BatchPreviewDialog({
  open,
  onOpenChange,
  runs,
  truncatedTotal,
  submitting,
  onQueue,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Frozen snapshot from buildJobs — order is the enqueue order. */
  runs: readonly PreviewRun[];
  /**
   * When the plan total exceeded what was materialized, the full count.
   * Null when `runs.length` is the complete batch.
   */
  truncatedTotal: number | null;
  submitting: boolean;
  /** Queue the selected runs, in snapshot order. */
  onQueue: (selected: PreviewRun[]) => void;
}) {
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [copyOk, setCopyOk] = useState<string | null>(null);

  // New snapshot → select everything. Keyed on open+indices so reopening a
  // rebuilt preview resets cleanly without fighting user toggles mid-session.
  const selectionKey = `${open}:${runs.map((r) => r.index).join(",")}`;
  useEffect(() => {
    if (!open) return;
    setSelected(new Set(runs.map((r) => r.index)));
    setCopyOk(null);
  }, [selectionKey, open, runs]);

  const selectedRuns = useMemo(
    () => runs.filter((r) => selected.has(r.index)),
    [runs, selected],
  );

  const allSelected = runs.length > 0 && selected.size === runs.length;
  const noneSelected = selected.size === 0;

  const toggle = useCallback((index: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelected(new Set(runs.map((r) => r.index)));
  }, [runs]);

  const selectNone = useCallback(() => {
    setSelected(new Set());
  }, []);

  const copyText = useCallback(
    async (which: "selected" | "all") => {
      const list = which === "selected" ? selectedRuns : [...runs];
      const text = list
        .map((r) => {
          const header = `#${r.index + 1}  ${r.label}  seed=${r.seed}`;
          const neg =
            r.negativePrompt.trim().length > 0
              ? `\nNegative: ${r.negativePrompt}`
              : "";
          return `${header}\n${r.prompt}${neg}`;
        })
        .join("\n\n---\n\n");
      await navigator.clipboard.writeText(text);
      setCopyOk(
        which === "selected"
          ? `Copied ${list.length.toLocaleString()} selected run${list.length === 1 ? "" : "s"}.`
          : `Copied all ${list.length.toLocaleString()} runs.`,
      );
    },
    [runs, selectedRuns],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] w-[min(96vw,48rem)] max-w-3xl flex-col gap-3 overflow-hidden">
        <DialogHeader className="min-w-0 shrink-0">
          <DialogTitle className="flex min-w-0 items-center gap-2 pr-6 leading-snug">
            <Eye className="h-4 w-4 shrink-0" />
            <span className="min-w-0 break-words">
              Preview {runs.length.toLocaleString()}{" "}
              {runs.length === 1 ? "run" : "runs"}
            </span>
          </DialogTitle>
          <DialogDescription className="min-w-0 break-words leading-relaxed [overflow-wrap:anywhere]">
            A fresh randomized batch snapshot — Queue uses these exact jobs,
            order, seeds, and prompts. Nothing has been sent yet. Reopen Preview
            or start a new batch to draw a different variation set.
          </DialogDescription>
        </DialogHeader>

        {truncatedTotal !== null && (
          <p className="shrink-0 rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-800 dark:text-amber-300">
            Showing the first {runs.length.toLocaleString()} of{" "}
            {truncatedTotal.toLocaleString()} planned runs (materialization
            cap). Narrow the matrix or take a sample to preview the rest.
          </p>
        )}

        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 text-[11px]"
            onClick={selectAll}
            disabled={allSelected}
          >
            <CheckSquare className="h-3 w-3" />
            All
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 text-[11px]"
            onClick={selectNone}
            disabled={noneSelected}
          >
            <XSquare className="h-3 w-3" />
            None
          </Button>
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {selected.size.toLocaleString()} / {runs.length.toLocaleString()}{" "}
            selected
          </span>
          <div className="ml-auto flex flex-wrap gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-[11px]"
              disabled={noneSelected}
              onClick={() => void copyText("selected")}
            >
              <Copy className="h-3 w-3" />
              Copy selected
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1 text-[11px]"
              disabled={runs.length === 0}
              onClick={() => void copyText("all")}
            >
              <Copy className="h-3 w-3" />
              Copy all
            </Button>
          </div>
        </div>

        {copyOk !== null && (
          <p className="shrink-0 text-[11px] text-muted-foreground">{copyOk}</p>
        )}

        <ul className="min-h-0 min-w-0 flex-1 divide-y overflow-y-auto rounded-md border">
          {runs.map((run) => {
            const checked = selected.has(run.index);
            return (
              <li
                key={run.index}
                className={cn(
                  "flex min-w-0 gap-2 px-2.5 py-2",
                  !checked && "opacity-50",
                )}
              >
                <label className="flex cursor-pointer items-start gap-2 pt-0.5">
                  <Checkbox
                    checked={checked}
                    onCheckedChange={() => toggle(run.index)}
                    className="mt-0.5 h-3.5 w-3.5"
                    aria-label={`Include run ${run.index + 1}`}
                  />
                  <span className="w-8 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
                    {run.index + 1}
                  </span>
                </label>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex min-w-0 items-start gap-1.5">
                    <code className="min-w-0 flex-1 whitespace-normal break-words text-[11px] leading-snug text-primary [overflow-wrap:anywhere]">
                      {run.label || "—"}
                    </code>
                    <Badge
                      variant="outline"
                      className="h-4 shrink-0 px-1 text-[10px] tabular-nums"
                    >
                      seed {run.seed}
                    </Badge>
                  </div>
                  <p className="whitespace-pre-wrap break-words text-xs leading-relaxed [overflow-wrap:anywhere]">
                    {run.prompt}
                  </p>
                  {run.negativePrompt.trim().length > 0 && (
                    <p className="whitespace-pre-wrap break-words text-[11px] text-muted-foreground [overflow-wrap:anywhere]">
                      <span className="font-medium">Neg: </span>
                      {run.negativePrompt}
                    </p>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0 text-muted-foreground"
                  aria-label={`Copy run ${run.index + 1}`}
                  onClick={() => {
                    void navigator.clipboard.writeText(run.prompt).then(() => {
                      setCopyOk(`Copied run #${run.index + 1}.`);
                    });
                  }}
                >
                  <Copy className="h-3.5 w-3.5" />
                </Button>
              </li>
            );
          })}
        </ul>

        <DialogFooter className="shrink-0 sm:justify-between">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Close
          </Button>
          <Button
            className="gap-1.5"
            disabled={noneSelected || submitting}
            onClick={() => onQueue(selectedRuns)}
          >
            {submitting ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Queueing…
              </>
            ) : (
              <>
                <Layers className="h-3.5 w-3.5" />
                Queue {selected.size.toLocaleString()} selected
                {allSelected ? "" : ` of ${runs.length.toLocaleString()}`}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
