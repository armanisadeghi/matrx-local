/**
 * Agent Runtime — Claude Code sessions this app can start on this machine.
 *
 * The desktop face of the LOCAL Claude Code runtime
 * (app/services/coding_sessions/local_runtime.py): truthful availability,
 * the one-click persisted folder-approval gate (loopback-only by design —
 * approving a folder for agent execution is a physical-presence decision),
 * and the live run list with cancel.
 *
 * Launching from the web (/work/new → "Claude Code on my Mac") arrives over
 * the per-user Supabase Broadcast bridge channel; runs started there show up
 * here exactly like locally-started ones.
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Play,
  RefreshCw,
  Square,
} from "lucide-react";

import { WorkspaceApprovalTree } from "@/components/coding-sessions/WorkspaceApprovalTree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { engine } from "@/lib/api";
import type { LocalRuntimeCapabilities, LocalRuntimeRun } from "@/lib/api";

const ACTIVE_STATUSES = new Set(["starting", "running"]);

function statusBadge(status: LocalRuntimeRun["status"]) {
  switch (status) {
    case "completed":
      return <Badge variant="secondary">completed</Badge>;
    case "failed":
      return <Badge variant="destructive">failed</Badge>;
    case "cancelled":
      return <Badge variant="outline">cancelled</Badge>;
    default:
      return <Badge>{status}</Badge>;
  }
}

export function AgentRuntimeCard() {
  const [capabilities, setCapabilities] =
    useState<LocalRuntimeCapabilities | null>(null);
  const [runs, setRuns] = useState<LocalRuntimeRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [initialLoad, setInitialLoad] = useState(true);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [nextCapabilities, nextStatus] = await Promise.all([
        engine.getRuntimeCapabilities(),
        engine.getRuntimeStatus(),
      ]);
      setCapabilities(nextCapabilities);
      setRuns(nextStatus.runs);
      setError(null);
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setRefreshing(false);
      setInitialLoad(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const hasActiveRuns = runs.some((run) => ACTIVE_STATUSES.has(run.status));
  useEffect(() => {
    if (!hasActiveRuns) return;
    const id = setInterval(() => void refresh(), 3000);
    return () => clearInterval(id);
  }, [hasActiveRuns, refresh]);

  const cancel = async (runtimeId: string) => {
    setBusy(true);
    try {
      await engine.cancelRuntimeSession(runtimeId);
      await refresh();
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Play className="h-4 w-4" />
            Local runtime — Claude Code on this Mac
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Runs use your installed Claude Code and its subscription login.
            They can work only in folders you explicitly approve, and their
            turn events are queued for delivery to your private AI Matrx history.
          </p>
        </div>
        <Button variant="outline" onClick={() => void refresh()} disabled={refreshing}>
          {refreshing ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" />
          )}
          Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <span>{error}</span>
          </div>
        )}

        {initialLoad && (
          <div className="flex items-center gap-2 rounded-md border border-dashed p-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Checking Claude Code,
            account access, and local runtime support…
          </div>
        )}

        {capabilities && !capabilities.available && (
          <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            <div>
              <div className="font-medium">Runtime setup needs attention</div>
              <ul className="mt-1 list-disc pl-5">
                {[...new Set(capabilities.reasons)].map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {capabilities?.available && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            Claude Code is ready — {capabilities.claude_cli}
            {capabilities.claude_account_label
              ? ` · ${capabilities.claude_account_label}`
              : null}
            {" · using your Claude subscription login"}
          </div>
        )}

        {capabilities && (
          <WorkspaceApprovalTree
            workspaceRoots={capabilities.workspace_roots}
            approvedFolders={capabilities.approved_folders}
            disabled={busy}
            onChanged={refresh}
            onError={setError}
          />
        )}

        <div>
          <div className="text-sm font-medium">Runs</div>
          <div className="mt-2 space-y-1">
            {!initialLoad && runs.length === 0 && (
              <div className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
                No runs yet. Start one from AI Matrx → New work → Claude Code.
              </div>
            )}
            {runs.map((run) => (
              <div
                key={run.runtime_id}
                className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    {statusBadge(run.status)}
                    <span className="text-xs text-muted-foreground">
                      {run.action === "resume" ? "resumed" : "started"} ·{" "}
                      {run.workspace.split("/").slice(-1)[0]} · turns{" "}
                      {run.turns_completed}
                    </span>
                  </div>
                  <div className="mt-0.5 truncate text-xs text-muted-foreground">
                    {run.prompt_preview}
                  </div>
                  {run.error && (
                    <div className="mt-0.5 truncate text-xs text-destructive">
                      {run.error}
                    </div>
                  )}
                </div>
                {ACTIVE_STATUSES.has(run.status) && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void cancel(run.runtime_id)}
                    disabled={busy}
                  >
                    <Square className="mr-2 h-3.5 w-3.5" />
                    Stop
                  </Button>
                )}
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
