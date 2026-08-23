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

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CloudUpload,
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
import { getAppRuntimeConfig } from "@/lib/app-config";
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

function mirrorBadge(run: LocalRuntimeRun) {
  const status = run.mirror?.status ?? (run.mirror_error ? "failed" : run.mirror_passes > 0 ? "enqueued" : "not_started");
  switch (status) {
    case "failed":
      return <Badge variant="destructive">AI Matrx delivery failed</Badge>;
    case "enqueued":
      return <Badge variant="secondary">Stored for AI Matrx delivery</Badge>;
    case "pending":
      return <Badge variant="outline">AI Matrx delivery pending</Badge>;
    default:
      return <Badge variant="outline">No AI Matrx delivery recorded</Badge>;
  }
}

export function AgentRuntimeCard() {
  const newWorkUrl = `${getAppRuntimeConfig().webAppOrigin}/work/new`;
  const [capabilities, setCapabilities] =
    useState<LocalRuntimeCapabilities | null>(null);
  const [runs, setRuns] = useState<LocalRuntimeRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [initialLoad, setInitialLoad] = useState(true);
  const [eventsByRun, setEventsByRun] = useState<Record<string, Array<Record<string, unknown>>>>({});

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
  const activeRunIds = useMemo(() => runs.filter((run) => ACTIVE_STATUSES.has(run.status)).map((run) => run.runtime_id), [runs]);
  const activeRunKey = activeRunIds.join(",");
  useEffect(() => {
    if (!hasActiveRuns) return;
    const id = setInterval(() => void refresh(), 3000);
    return () => clearInterval(id);
  }, [hasActiveRuns, refresh]);

  useEffect(() => {
    const baseUrl = engine.engineUrl;
    if (!baseUrl || activeRunIds.length === 0) return;
    const sources = activeRunIds.map((runtimeId) => {
      const source = new EventSource(`${baseUrl}/coding-session/runtime/${encodeURIComponent(runtimeId)}/events`);
      source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as Record<string, unknown>;
          setEventsByRun((current) => ({ ...current, [runtimeId]: [...(current[runtimeId] ?? []), payload].slice(-20) }));
        } catch {
          setEventsByRun((current) => ({ ...current, [runtimeId]: [...(current[runtimeId] ?? []), { event: "unreadable_event", raw: event.data }].slice(-20) }));
        }
      };
      source.addEventListener("done", () => { source.close(); void refresh(); });
      source.onerror = () => source.close();
      return source;
    });
    return () => sources.forEach((source) => source.close());
  }, [activeRunKey, refresh]); // activeRunKey intentionally represents the set

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
            {capabilities.claude_account_display_identity ?? capabilities.claude_account_label
              ? ` · ${capabilities.claude_account_display_identity ?? capabilities.claude_account_label}`
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
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><div className="text-sm font-medium">Runs</div><p className="text-xs text-muted-foreground">Provider execution and AI Matrx delivery settle independently. Both are shown for every run.</p></div>
            {capabilities?.capabilities.start && <Badge variant="outline"><CloudUpload className="mr-1 h-3 w-3" />Start supported through AI Matrx New work</Badge>}
          </div>
          <div className="mt-2 space-y-2">
            {!initialLoad && runs.length === 0 && (
              <div className="rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground">
                <p>No runs yet. Start through the canonical AI Matrx work composer, then choose Claude Code on this Mac.</p>
                <Button asChild variant="outline" size="sm" className="mt-2"><a href={newWorkUrl} target="_blank" rel="noreferrer">Open AI Matrx New work</a></Button>
              </div>
            )}
            {runs.map((run) => (
              <article
                key={run.runtime_id}
                className="rounded-md border px-3 py-3 text-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-muted-foreground">Claude execution</span>
                      {statusBadge(run.execution?.status ?? run.status)}
                      <span className="text-xs text-muted-foreground">{run.action === "resume" ? "resumed" : "started"} · {run.turns_completed} turn{run.turns_completed === 1 ? "" : "s"}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-muted-foreground">AI Matrx delivery</span>{mirrorBadge(run)}<span className="text-xs text-muted-foreground">{run.mirror?.passes ?? run.mirror_passes} mirror pass{(run.mirror?.passes ?? run.mirror_passes) === 1 ? "" : "es"}</span></div>
                    <p className="mt-2 truncate text-xs">{run.prompt_preview || "No prompt preview reported"}</p>
                    <dl className="mt-2 grid gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2 xl:grid-cols-3">
                      <div><dt className="inline font-medium text-foreground">Workspace: </dt><dd className="inline" title={run.workspace}>{run.workspace}</dd></div>
                      <div><dt className="inline font-medium text-foreground">Runtime: </dt><dd className="inline font-mono">{run.runtime_id}</dd></div>
                      <div><dt className="inline font-medium text-foreground">Provider session: </dt><dd className="inline font-mono">{run.provider_session_id ?? "not reported"}</dd></div>
                      <div><dt className="inline font-medium text-foreground">AI Matrx conversation: </dt><dd className="inline font-mono">{run.conversation_id ?? "not reported"}</dd></div>
                      <div><dt className="inline font-medium text-foreground">Events retained: </dt><dd className="inline">{run.event_count}{run.first_event_sequence != null && run.last_event_sequence != null ? ` · sequence ${run.first_event_sequence}–${run.last_event_sequence}` : ""}</dd></div>
                      <div><dt className="inline font-medium text-foreground">Started: </dt><dd className="inline">{new Date(run.started_at).toLocaleString()}</dd></div>
                    </dl>
                    {(run.execution?.error ?? run.error) && <div className="mt-2 text-xs text-destructive"><span className="font-medium">Claude execution error:</span> {run.execution?.error ?? run.error}</div>}
                    {(run.mirror?.error ?? run.mirror_error) && <div className="mt-2 text-xs text-destructive"><span className="font-medium">AI Matrx delivery error:</span> {run.mirror?.error ?? run.mirror_error}</div>}
                    {(eventsByRun[run.runtime_id]?.length ?? 0) > 0 && <details className="mt-2 rounded-md bg-muted/30 p-2 text-xs"><summary className="cursor-pointer font-medium">Live runtime events ({eventsByRun[run.runtime_id]?.length} retained in this view)</summary><div className="mt-2 max-h-40 space-y-1 overflow-auto font-mono text-muted-foreground">{eventsByRun[run.runtime_id]?.map((event, index) => <div key={`${String(event.sequence ?? "event")}-${index}`}>{String(event.sequence ?? "—")} · {String(event.event ?? event.type ?? "runtime event")}</div>)}</div></details>}
                  </div>
                  {ACTIVE_STATUSES.has(run.status) && <Button variant="outline" size="sm" onClick={() => void cancel(run.runtime_id)} disabled={busy}><Square className="mr-2 h-3.5 w-3.5" />Stop Claude run</Button>}
                </div>
              </article>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
