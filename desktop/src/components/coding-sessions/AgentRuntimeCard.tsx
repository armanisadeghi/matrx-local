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
  FolderPlus,
  Loader2,
  Play,
  RefreshCw,
  Square,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
  const [folderInput, setFolderInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

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

  const approve = async () => {
    if (!folderInput.trim()) return;
    setBusy(true);
    try {
      await engine.approveRuntimeFolder(folderInput.trim());
      setFolderInput("");
      await refresh();
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (folder: string) => {
    setBusy(true);
    try {
      await engine.revokeRuntimeFolder(folder);
      await refresh();
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setBusy(false);
    }
  };

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
            Agent Runtime — Claude Code on this Mac
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            Start Claude Code sessions here or from AI Matrx on the web. Runs
            use your own installed Claude Code and its login, work only inside
            folders you approve below, and mirror into AI Matrx as they run.
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

        {capabilities && !capabilities.available && (
          <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            <div>
              <div className="font-medium">The runtime is not available yet:</div>
              <ul className="mt-1 list-disc pl-5">
                {capabilities.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {capabilities?.available && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            Ready — {capabilities.claude_cli}
            {capabilities.claude_account_label
              ? ` · ${capabilities.claude_account_label}`
              : null}
            {" · using your Claude subscription login"}
          </div>
        )}

        <div>
          <div className="text-sm font-medium">Approved folders</div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Agent runs may only touch these folders (all must be under{" "}
            {capabilities?.workspace_roots.join(", ") ?? "~/code"}). Approval is
            one click, once, and only from this app.
          </p>
          <div className="mt-2 space-y-1">
            {(capabilities?.approved_folders ?? []).map((folder) => (
              <div
                key={folder}
                className="flex items-center justify-between rounded-md border px-3 py-1.5 text-sm"
              >
                <span className="truncate font-mono text-xs">{folder}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void revoke(folder)}
                  disabled={busy}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
            {capabilities && capabilities.approved_folders.length === 0 && (
              <div className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
                No folders approved yet — nothing can run until you approve one.
              </div>
            )}
          </div>
          <div className="mt-2 flex gap-2">
            <Input
              placeholder="~/code/my-repo"
              value={folderInput}
              onChange={(event) => setFolderInput(event.target.value)}
            />
            <Button onClick={() => void approve()} disabled={busy || !folderInput.trim()}>
              <FolderPlus className="mr-2 h-4 w-4" />
              Approve
            </Button>
          </div>
        </div>

        <div>
          <div className="text-sm font-medium">Runs</div>
          <div className="mt-2 space-y-1">
            {runs.length === 0 && (
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
