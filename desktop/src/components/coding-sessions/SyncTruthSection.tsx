/**
 * "Does this conversation match AI Matrx?" — answered, with a button that fixes it.
 *
 * Arman, 2026-09-17: "I have a chat in Claude Code that simply doesn't match
 * what I see in AI Matrx. I cannot figure out what is wrong. A normal DB
 * system would show me that it can't sync, or when it was synced — this thing
 * is a dead fish."
 *
 * So this section leads with the verdict sentence, then the four counts side
 * by side, then the four "when" stamps, then one Reconcile button. Every
 * number is the engine's; every word is the engine's. This file only lays them
 * out.
 *
 * THE TWO RULES IT ENFORCES ON SCREEN:
 *  1. A layer that could not be read shows "Not readable" — never 0, never
 *     blank. A zero is a claim.
 *  2. The button is offered only when reconciling can actually help. When it
 *     cannot (AI Matrx holds the session under a different Claude account, and
 *     will refuse the delivery every time) the remedy sentence stands alone
 *     instead of a control that is guaranteed to fail.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, Wand2 } from "lucide-react";

import { Button } from "@ai-matrx/design-system";
import { engine } from "@/lib/api";
import type {
  ClaudeSessionReconcileReport,
  ClaudeSessionSyncTruth,
} from "@/lib/api";
import {
  OUTCOME_LABEL,
  STEP_LABEL,
  UNREADABLE_COUNT,
  VERDICT_LABEL,
  VERDICT_TONE,
  VERDICT_TONE_CLASS,
  canReconcile,
  countCells,
  syncStamps,
} from "@/lib/coding-sessions/sync-truth";

function stamp(value: string | null): string {
  if (!value) return "Never";
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return value;
  return new Date(ms).toLocaleString();
}

export function SyncTruthSection({
  sessionId,
  onChanged,
}: {
  sessionId: string;
  onChanged?: () => Promise<void> | void;
}) {
  const [truth, setTruth] = useState<ClaudeSessionSyncTruth | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [report, setReport] = useState<ClaudeSessionReconcileReport | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTruth(await engine.getClaudeSessionSyncTruth(sessionId));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    setTruth(null);
    setReport(null);
    void load();
  }, [load]);

  const reconcile = async () => {
    setReconciling(true);
    setError(null);
    try {
      const next = await engine.reconcileClaudeSession(sessionId);
      setReport(next);
      setTruth(next.truth);
      await onChanged?.();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setReconciling(false);
    }
  };

  return (
    <section className="rounded-lg border" data-testid="sync-truth">
      <h3 className="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <span>Sync</span>
        <Button
          variant="ghost"
          size="sm"
          className="h-6"
          onClick={() => void load()}
          disabled={loading || reconciling}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          <span className="sr-only">Check sync again</span>
        </Button>
      </h3>

      <div className="flex flex-col gap-3 px-3 py-3">
        {error && (
          <div
            className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2.5 text-sm text-destructive"
            role="alert"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {!truth && !error && (
          <p className="text-sm text-muted-foreground">
            {loading ? "Comparing this conversation with AI Matrx…" : "Not checked yet."}
          </p>
        )}

        {truth && (
          <>
            <div
              className={`rounded-lg border p-3 text-sm ${
                VERDICT_TONE_CLASS[VERDICT_TONE[truth.verdict.code]]
              }`}
              data-testid="sync-verdict"
            >
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {VERDICT_LABEL[truth.verdict.code]}
              </p>
              <p className="mt-1 font-medium" data-testid="sync-sentence">
                {truth.verdict.sentence}
              </p>
              {truth.verdict.remedy && (
                <p className="mt-1 text-muted-foreground" data-testid="sync-remedy">
                  {truth.verdict.remedy}
                </p>
              )}
            </div>

            <dl className="grid grid-cols-4 gap-2" data-testid="sync-counts">
              {countCells(truth).map((cell) => (
                <div key={cell.label} className="rounded-md border px-2 py-1.5" title={cell.hint}>
                  <dt className="truncate text-[11px] uppercase tracking-wide text-muted-foreground">
                    {cell.label}
                  </dt>
                  <dd
                    className={
                      cell.unreadable
                        ? "text-xs italic text-muted-foreground"
                        : "text-lg font-semibold tabular-nums"
                    }
                  >
                    {cell.value}
                  </dd>
                </div>
              ))}
            </dl>

            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm" data-testid="sync-stamps">
              {syncStamps(truth).map((entry) => (
                <div key={entry.label} className="flex justify-between gap-2 border-b py-1">
                  <dt className="text-muted-foreground">{entry.label}</dt>
                  <dd className="text-right">{stamp(entry.value)}</dd>
                </div>
              ))}
            </dl>

            {truth.cloud.fidelity && (
              <p className="text-xs text-muted-foreground">
                AI Matrx is mirroring this conversation at{" "}
                <span className="font-medium">{truth.cloud.fidelity}</span> fidelity.
              </p>
            )}

            {canReconcile(truth) ? (
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={() => void reconcile()} disabled={reconciling}>
                  {reconciling ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Wand2 className="mr-2 h-4 w-4" />
                  )}
                  {reconciling ? "Reconciling…" : "Reconcile"}
                </Button>
                <span className="text-xs text-muted-foreground">
                  Delivers what is missing, asks AI Matrx to finish its side, and refreshes
                  this Mac's copy.
                </span>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground" data-testid="sync-no-action">
                There is nothing for Reconcile to do here.
              </p>
            )}

            {report && (
              <ol className="flex flex-col gap-1 rounded-md border bg-muted/30 p-2" data-testid="sync-report">
                {report.actions.map((action) => (
                  <li key={action.step} className="text-sm">
                    <span className="font-medium">{STEP_LABEL[action.step] ?? action.step}</span>
                    <span className="text-muted-foreground">
                      {" — "}
                      {OUTCOME_LABEL[action.outcome] ?? action.outcome}. {action.detail}
                    </span>
                  </li>
                ))}
              </ol>
            )}

            {truth.delivered.quarantine_reasons.length > 0 && (
              <ul className="flex flex-col gap-1" data-testid="sync-quarantine">
                {truth.delivered.quarantine_reasons.map((reason) => (
                  <li key={reason.code} className="text-xs text-muted-foreground">
                    <span className="font-medium">{reason.count}</span> held back — {reason.message}
                  </li>
                ))}
              </ul>
            )}

            {truth.cloud.projection_errors.length > 0 && (
              <ul className="flex flex-col gap-1" data-testid="sync-projection-errors">
                {truth.cloud.projection_errors.map((entry) => (
                  <li key={`${entry.code}-${entry.detail}`} className="text-xs text-muted-foreground">
                    <span className="font-medium">{entry.count}</span> not projected —{" "}
                    {entry.detail ?? entry.code ?? "reason not recorded"}
                  </li>
                ))}
              </ul>
            )}

            {truth.cloud.cloud_sentence &&
              truth.cloud.cloud_sentence !== truth.verdict.sentence && (
                <p className="text-xs text-muted-foreground" data-testid="sync-server-claim">
                  AI Matrx's own read of its half: {truth.cloud.cloud_sentence}
                </p>
              )}

            {truth.transcript.unreadable_lines > 0 && (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                {truth.transcript.unreadable_lines} line
                {truth.transcript.unreadable_lines === 1 ? "" : "s"} of the transcript could not be
                read and cannot be delivered.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

export { UNREADABLE_COUNT };
