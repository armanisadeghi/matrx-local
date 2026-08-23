import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  CloudUpload,
  Database,
  History,
  Loader2,
  MonitorCheck,
  RefreshCw,
  RotateCw,
  Tags,
  Trash2,
} from "lucide-react";

import { AgentRuntimeCard } from "@/components/coding-sessions/AgentRuntimeCard";
import {
  DeliveryEvidenceDialog,
  type DeliveryEvidenceFilter,
} from "@/components/coding-sessions/DeliveryEvidenceDialog";
import {
  HistoryInventoryTable,
  historyReviewCounts,
} from "@/components/coding-sessions/HistoryInventoryTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { engine } from "@/lib/api";
import type {
  ClaudeCaptureStatus,
  ClaudeCaptureReconcileResult,
  ClaudeHistoryImportResult,
  ClaudeHistoryInventoryPage,
  ClaudeHistoryReview,
  ClaudeHistoryStatus,
  ClaudeLabelSyncResult,
  ClaudeLabelSyncStatus,
  CodingSessionBridgeStatus,
  CodingSessionProvider,
} from "@/lib/api";
import {
  codingSessionActionLabel,
  codingSessionSourceLabel,
  formatRetryDuration,
  runSingleFlight,
} from "@/lib/coding-session-ui";

type CodingSessionsTab = "overview" | "history" | "titles" | "runtime";

const PROVIDER_LABELS: Record<CodingSessionProvider, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
  vscode: "VS Code",
};

const PROVIDER_SCOPE: Record<CodingSessionProvider, string> = {
  claude_code: "Live events, local history import, titles, and local runtime",
  codex: "Live command-hook events",
  cursor: "Live editor events exposed by Cursor",
  vscode: "Conversations created through the @matrx participant",
};

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function blockedMessage(reason: string | null): string {
  switch (reason) {
    case "claude_not_installed":
      return "AI Matrx could not locate an executable Claude Code installation. Open Local runtime for diagnostics.";
    case "claude_not_signed_in":
      return "Sign in to Claude Code, then review again.";
    case "claude_status_timeout":
      return "Claude Code was found, but its account check timed out. Close any blocked Claude process and review again.";
    case "claude_status_execution_failed":
      return "Claude Code was found, but its account status command failed. Open Local runtime for the exact recovery step.";
    case "claude_account_identity_unavailable":
      return "Claude is signed in, but this login does not expose a stable account identity. Sync is paused so histories from different accounts cannot be mixed.";
    default:
      return "Claude account status is unavailable. Open Claude Code and confirm its login, then review again.";
  }
}

export function ClaudeHistorySync() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const activeTab: CodingSessionsTab =
    requestedTab === "history" || requestedTab === "titles" || requestedTab === "runtime"
      ? requestedTab
      : "overview";
  const setActiveTab = useCallback((tab: CodingSessionsTab) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (tab === "overview") next.delete("tab");
      else next.set("tab", tab);
      return next;
    });
  }, [setSearchParams]);
  const [preview, setPreview] = useState<ClaudeHistoryReview | null>(null);
  const [historyPage, setHistoryPage] = useState<ClaudeHistoryInventoryPage | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ClaudeHistoryImportResult | null>(null);
  const [historyStatus, setHistoryStatus] = useState<ClaudeHistoryStatus | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [labelStatus, setLabelStatus] = useState<ClaudeLabelSyncStatus | null>(null);
  const [labelResult, setLabelResult] = useState<ClaudeLabelSyncResult | null>(null);
  const [labelSyncing, setLabelSyncing] = useState(false);
  const [labelError, setLabelError] = useState<string | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<CodingSessionBridgeStatus | null>(null);
  const [captureStatus, setCaptureStatus] = useState<ClaudeCaptureStatus | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [overviewRefreshing, setOverviewRefreshing] = useState(false);
  const [captureReconciling, setCaptureReconciling] = useState(false);
  const [bridgeUpdatedAt, setBridgeUpdatedAt] = useState<Date | null>(null);
  const [captureUpdatedAt, setCaptureUpdatedAt] = useState<Date | null>(null);
  const [bridgeStale, setBridgeStale] = useState(false);
  const [captureStale, setCaptureStale] = useState(false);
  const [captureResult, setCaptureResult] =
    useState<ClaudeCaptureReconcileResult | null>(null);
  const overviewFlight = useRef<Promise<void> | null>(null);

  const refreshStatus = useCallback(async () => {
    const next = await engine.getClaudeHistoryStatus();
    setHistoryStatus(next);
  }, []);

  const refreshLabelStatus = useCallback(async () => {
    setLabelStatus(await engine.getClaudeLabelStatus());
  }, []);

  const refreshOverview = useCallback(() => {
    return runSingleFlight(overviewFlight, async () => {
      setOverviewRefreshing(true);
      try {
        const [bridgeResult, captureStatusResult] = await Promise.allSettled([
          engine.getCodingSessionStatus(),
          engine.getClaudeCaptureStatus(),
        ]);
        const failures: string[] = [];
        const refreshedAt = new Date();
        if (bridgeResult.status === "fulfilled") {
          setBridgeStatus(bridgeResult.value);
          setBridgeUpdatedAt(refreshedAt);
          setBridgeStale(false);
        } else {
          setBridgeStale(true);
          failures.push(
            `Delivery status: ${
              bridgeResult.reason instanceof Error
                ? bridgeResult.reason.message
                : String(bridgeResult.reason)
            }`,
          );
        }
        if (captureStatusResult.status === "fulfilled") {
          setCaptureStatus(captureStatusResult.value);
          setCaptureUpdatedAt(refreshedAt);
          setCaptureStale(false);
        } else {
          setCaptureStale(true);
          failures.push(
            `Recovery status: ${
              captureStatusResult.reason instanceof Error
                ? captureStatusResult.reason.message
                : String(captureStatusResult.reason)
            }`,
          );
        }
        setOverviewError(failures.length > 0 ? failures.join(" · ") : null);
      } finally {
        setOverviewRefreshing(false);
      }
    });
  }, []);

  useEffect(() => {
    void refreshStatus().catch(() => {
      // The main review action reports engine/auth errors with full context.
    });
  }, [refreshStatus]);

  useEffect(() => {
    void refreshLabelStatus().catch(() => {
      // Label sync reports its own blocked reason when the user presses sync.
    });
  }, [refreshLabelStatus]);

  useEffect(() => {
    void refreshOverview();
  }, [refreshOverview]);

  useEffect(() => {
    if (!bridgeStatus || bridgeStatus.pending.total === 0) return;
    let cancelled = false;
    let timeoutId: number | undefined;
    const poll = async () => {
      try {
        await refreshOverview();
      } catch (nextError) {
        setOverviewError(
          nextError instanceof Error ? nextError.message : String(nextError),
        );
      } finally {
        if (!cancelled) timeoutId = window.setTimeout(() => void poll(), 3000);
      }
    };
    timeoutId = window.setTimeout(() => void poll(), 3000);
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [bridgeStatus?.pending.total, refreshOverview]);

  const reconcileCapture = async () => {
    setCaptureReconciling(true);
    setOverviewError(null);
    setCaptureResult(null);
    try {
      setCaptureResult(await engine.reconcileClaudeCapture(false));
      await refreshOverview();
    } catch (nextError) {
      setOverviewError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setCaptureReconciling(false);
    }
  };

  const syncLabels = async (dryRun: boolean) => {
    setLabelSyncing(true);
    setLabelError(null);
    try {
      setLabelResult(await engine.syncClaudeLabels(dryRun));
      await refreshLabelStatus();
      await refreshStatus();
    } catch (nextError) {
      setLabelError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setLabelSyncing(false);
    }
  };

  const selectedSessions = useMemo(
    () => historyPage?.items.filter((session) =>
      selected.has(`${session.project_key}:${session.session_id}`),
    ) ?? [],
    [historyPage, selected],
  );
  const selectedBytes = useMemo(
    () => selectedSessions.reduce((total, session) => total + session.bytes, 0),
    [selectedSessions],
  );

  const review = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const next = await engine.reviewClaudeHistory(100);
      setPreview(next);
      setHistoryPage(next);
      setSelected(new Set());
      await refreshStatus();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  };

  const reviewChanges = preview ? historyReviewCounts(preview) : null;

  const sync = async () => {
    if (!preview?.provider_account_key || selectedSessions.length === 0) return;
    setSyncing(true);
    setError(null);
    setResult(null);
    try {
      const prepared = await engine.prepareClaudeHistorySelection(
        preview.scan.scan_id,
        preview.provider_account_key,
        selectedSessions.map((session) => ({
          session_id: session.session_id,
          provider_project_key: session.project_key,
          source_state: session.source_state!,
        })),
      );
      const next = await engine.importClaudeHistory(
        preview.provider_account_key,
        prepared.prepared.map((session) => ({
          session_id: session.session_id,
          provider_project_key: session.project_key,
          source_revision: session.source_revision,
        })),
      );
      setResult(next);
      await refreshStatus();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setSyncing(false);
    }
  };

  const discardPending = async () => {
    setDiscarding(true);
    setError(null);
    try {
      await engine.discardPendingClaudeHistory();
      await refreshStatus();
      setResult(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setDiscarding(false);
    }
  };

  const retryPending = async () => {
    setRetrying(true);
    setError(null);
    try {
      await engine.retryPendingClaudeHistory();
      await refreshStatus();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setRetrying(false);
    }
  };

  const selectionOverLimit = Boolean(
    preview &&
      (selectedSessions.length > preview.limits.selected_sessions ||
        selectedBytes > preview.limits.import_bytes),
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Coding sessions"
        description="See what each coding provider supports, control local access, and follow every sync from local capture through cloud acknowledgement."
      />
      <Tabs
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as CodingSessionsTab)}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="border-b px-6 py-3">
          <TabsList className="h-auto flex-wrap justify-start">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="history">History import</TabsTrigger>
            <TabsTrigger value="titles">Session details sync</TabsTrigger>
            <TabsTrigger value="runtime">Local runtime</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="overview" className="m-0 flex-1 space-y-4 overflow-y-auto p-6">
          <OverviewPanel
            bridgeStatus={bridgeStatus}
            captureStatus={captureStatus}
            bridgeUpdatedAt={bridgeUpdatedAt}
            captureUpdatedAt={captureUpdatedAt}
            bridgeStale={bridgeStale}
            captureStale={captureStale}
            captureResult={captureResult}
            error={overviewError}
            refreshing={overviewRefreshing}
            reconciling={captureReconciling}
            onRefresh={refreshOverview}
            onReconcile={reconcileCapture}
            onNavigate={setActiveTab}
            onOpenAccount={() => navigate("/settings")}
          />
        </TabsContent>

        <TabsContent value="history" className="m-0 flex-1 space-y-4 overflow-y-auto p-6">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-4">
            <div>
              <CardTitle className="text-base">Review local Claude history</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Review scans local Claude transcript metadata and never uploads.
                You choose sessions in a separate step before any copies enter the delivery queue.
              </p>
            </div>
            <Button onClick={() => void review()} disabled={loading || syncing}>
              {loading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : preview ? (
                <RefreshCw className="mr-2 h-4 w-4" />
              ) : (
                <History className="mr-2 h-4 w-4" />
              )}
              {preview ? "Review again" : "Review local history"}
            </Button>
          </CardHeader>
          {preview && (
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Summary label="Sessions present" value={preview.scan.present_count.toLocaleString()} />
                <Summary label="Files examined" value={preview.scan.file_count.toLocaleString()} />
                <Summary label="Stored locally" value={formatBytes(preview.scan.total_bytes)} />
                <Summary label="Projects" value={preview.scan.project_count.toLocaleString()} />
              </div>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {preview.account_identity_available ? (
                  <Badge variant="secondary">
                    Claude account{" "}
                    {preview.provider_account_display_identity ?? preview.provider_account_label ?? preview.account_fingerprint}
                  </Badge>
                ) : (
                  <Badge variant="destructive">Claude identity unavailable</Badge>
                )}
                {preview.claude_client_version && (
                  <Badge variant="outline">{preview.claude_client_version}</Badge>
                )}
                {preview.provider_account_display_identity && <span className="text-xs text-muted-foreground">Observed locally during this review{preview.account_identity_observed_at ? ` at ${formatDate(preview.account_identity_observed_at)}` : ""}.</span>}
                {!preview.matrx_user_available && (
                  <Badge variant="destructive">AI Matrx sign-in required</Badge>
                )}
              </div>
              {!preview.account_identity_available && (
                <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                  <span>{blockedMessage(preview.account_blocked_reason)}</span>
                </div>
              )}
              {reviewChanges && (
                <div className="rounded-md border border-blue-500/30 bg-blue-500/5 p-3" role="status">
                  <p className="font-medium">What this review found</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {reviewChanges.new.toLocaleString()} new · {reviewChanges.contentChanged.toLocaleString()} transcript changes · {reviewChanges.metadataChanged.toLocaleString()} detail changes · {reviewChanges.missing.toLocaleString()} missing locally · {reviewChanges.unchanged.toLocaleString()} unchanged · {reviewChanges.blocked.toLocaleString()} blocked
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Durable review {preview.scan.scan_id} completed {formatDate(preview.scan.completed_at)} against {preview.scan.previous_scan_id ? `review ${preview.scan.previous_scan_id}` : "the first recorded baseline"}.
                  </p>
                </div>
              )}
              <div className="rounded-md border p-3 text-sm text-muted-foreground">
                The complete inventory contains {preview.scan.session_count.toLocaleString()} rows. The table queries that durable inventory on demand; search and filters are not limited to the first page.
              </div>
            </CardContent>
          )}
        </Card>
        {preview && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Session inventory</CardTitle>
              <p className="text-sm text-muted-foreground">
                Search, sort, filter, and select the rows returned by this review. Full transcripts can contain code, commands, file contents, and secrets.
              </p>
            </CardHeader>
            <CardContent className="space-y-4">
              <HistoryInventoryTable
                review={preview}
                selected={selected}
                onSelectedChange={setSelected}
                onPageRowsChange={setHistoryPage}
                disabled={syncing}
              />
              <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 rounded-md border bg-background/95 p-3 backdrop-blur">
                <span className="text-sm text-muted-foreground">
                  {selectedSessions.length} selected · {formatBytes(selectedBytes)} · limits: {preview.limits.selected_sessions} sessions and {formatBytes(preview.limits.import_bytes)} per copy operation
                </span>
                <Button onClick={() => void sync()} disabled={syncing || !preview.import_ready || selectedSessions.length === 0 || selectionOverLimit}>
                  {syncing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CloudUpload className="mr-2 h-4 w-4" />}
                  Copy selected to local delivery queue
                </Button>
              </div>
              {selectionOverLimit && <p className="text-sm text-destructive" role="alert">This selection exceeds the operation limits shown above. Remove sessions before copying.</p>}
            </CardContent>
          </Card>
        )}
        {result && (
          <div className="flex gap-3 rounded-md border border-blue-500/30 bg-blue-500/5 p-4 text-sm" role="status">
            <CloudUpload className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" />
            <div><p className="font-medium">Copies stored locally; cloud delivery is not yet proven</p><p className="mt-1 text-muted-foreground">{result.entries.toLocaleString()} entries from {result.selected_sessions} sessions produced {result.queued_batches} local delivery envelopes. {result.pending_outbox} are waiting for AI Matrx acknowledgement.</p></div>
          </div>
        )}
        {historyStatus && historyStatus.pending_history_imports > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
            <div><p className="font-medium">{historyStatus.pending_history_imports.toLocaleString()} history envelopes await cloud acknowledgement</p><p className="mt-1 text-muted-foreground">{historyStatus.oldest_history_import?.last_error ? `Blocked after ${historyStatus.oldest_history_import.attempts} attempts: ${historyStatus.oldest_history_import.last_error}` : "Stored safely on this Mac and waiting for delivery."}</p></div>
            <div className="flex gap-2"><Button type="button" variant="outline" disabled={discarding || retrying || syncing} onClick={() => void retryPending()}>{retrying ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}Retry now</Button><Button type="button" variant="outline" disabled={discarding || retrying || syncing} onClick={() => { if (window.confirm(`Discard ${historyStatus.pending_history_imports} queued history envelopes from this Mac? Claude files are unchanged.`)) void discardPending(); }}>{discarding ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}Discard queued copies</Button></div>
          </div>
        )}
        {historyStatus && historyStatus.quarantined_history_imports > 0 && <div className="flex gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" /><div><p className="font-medium">{historyStatus.quarantined_history_imports.toLocaleString()} preserved history envelopes were not accepted</p><p className="mt-1 text-muted-foreground">They are not counted as synchronized. Open Overview to inspect the reported blocker before retrying.</p></div></div>}
        {error && <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>{error}</span></div>}
        </TabsContent>

        <TabsContent value="titles" className="m-0 flex-1 space-y-4 overflow-y-auto p-6">
        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle className="text-base">Compare session details before syncing</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Preview the differences first. Nothing changes until you review
                the plan and choose Apply. The current engine compares titles and
                also carries available project, branch, worktree, archive, pin,
                rank, and category metadata toward AI Matrx.
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Claude Code reads its session list when it starts, so a title
                sent back appears there the next time Claude Code reloads.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={() => void syncLabels(labelResult?.dry_run !== true)}
              disabled={labelSyncing || labelStatus?.index_available === false}
            >
              {labelSyncing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Tags className="mr-2 h-4 w-4" />
              )}
              {labelResult?.dry_run ? `Apply ${labelResult.queued.toLocaleString()} proposed updates` : "Preview session detail changes"}
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {labelStatus && !labelStatus.index_available && (
              <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <span>
                  The Claude Code desktop app has not written a session index on
                  this machine, so there are no Claude titles to read yet.
                </span>
              </div>
            )}
            {labelStatus && labelStatus.index_available && (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Summary
                  label="Claude sessions on this Mac"
                  value={labelStatus.index_records.toLocaleString()}
                />
                <Summary
                  label="Sessions with a recorded cloud settlement"
                  value={labelStatus.synced_sessions.toLocaleString()}
                />
                <Summary
                  label="Claude indexes previously written"
                  value={labelStatus.pushed_sessions.toLocaleString()}
                />
                <Summary
                  label="Index files read"
                  value={labelStatus.index_files.toLocaleString()}
                />
              </div>
            )}
            {labelError && (
              <div className="flex gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                <span>{labelError}</span>
              </div>
            )}
            {labelResult && (
              <div className={`space-y-3 rounded-md border p-3 text-sm ${labelResult.dry_run ? "border-blue-500/30 bg-blue-500/5" : ""}`}>
                <div className="flex items-center gap-2">
                  {labelResult.dry_run ? <Tags className="h-4 w-4 text-blue-600" /> : <CloudUpload className="h-4 w-4 text-blue-600" />}
                  <span>
                    {labelResult.dry_run ? "Preview: " : "Applied locally: "}
                    {labelResult.queued.toLocaleString()} metadata envelope
                    {labelResult.queued === 1 ? "" : "s"} {labelResult.dry_run ? "would be added to the local delivery queue" : "added to the local delivery queue; cloud acknowledgement is still pending"} ·{" "}
                    {labelResult.already_queued > 0 && (
                      <>
                        {labelResult.already_queued.toLocaleString()} already waiting ·{" "}
                      </>
                    )}
                    {labelResult.matched.toLocaleString()} of{" "}
                    {labelResult.bound_sessions.toLocaleString()} cloud-bound sessions
                    matched a Claude session · {labelResult.unchanged.toLocaleString()}{" "}
                    already identical
                  </span>
                </div>
                {(labelResult.push_down.written > 0 ||
                  labelResult.push_down.refused > 0 ||
                  labelResult.push_down.deferred_to_claude > 0) && (
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                    <span>
                      {labelResult.push_down.written.toLocaleString()} AI Matrx
                      rename
                      {labelResult.push_down.written === 1 ? "" : "s"} written
                      back into Claude Code
                      {labelResult.push_down.deferred_to_claude > 0 && (
                        <>
                          {" "}
                          ·{" "}
                          {labelResult.push_down.deferred_to_claude.toLocaleString()}{" "}
                          left to Claude Code, which was renamed more recently
                        </>
                      )}
                      {labelResult.push_down.refused > 0 && (
                        <>
                          {" "}
                          · {labelResult.push_down.refused.toLocaleString()}{" "}
                          skipped to avoid overwriting Claude Code (
                          {Object.keys(
                            labelResult.push_down.refusal_reasons,
                          ).join(", ")}
                          )
                        </>
                      )}
                    </span>
                  </div>
                )}
                {labelResult.unmatched > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {labelResult.unmatched.toLocaleString()} cloud-bound session
                    {labelResult.unmatched === 1 ? " has" : "s have"} no Claude
                    record on this machine — they were most likely created on
                    another computer.
                  </p>
                )}
                {labelResult.sample_titles.length > 0 && <div className="overflow-x-auto rounded-md border bg-background"><table className="w-full min-w-[680px] text-xs"><thead className="border-b bg-muted/40 text-left"><tr><th className="px-3 py-2">Session</th><th className="px-3 py-2">Claude Code value</th><th className="px-3 py-2">AI Matrx value before sync</th><th className="px-3 py-2">Planned result</th></tr></thead><tbody className="divide-y">{labelResult.sample_titles.map((item) => <tr key={item.provider_session_id}><td className="px-3 py-2 font-mono">{item.provider_session_id}</td><td className="px-3 py-2">{item.title}</td><td className="px-3 py-2 text-muted-foreground">Not returned by the current engine contract</td><td className="px-3 py-2">{item.title}</td></tr>)}</tbody></table></div>}
                {labelResult.sample_titles.length < labelResult.queued && <p className="text-xs text-muted-foreground">The engine returned only {labelResult.sample_titles.length} sample rows for {labelResult.queued.toLocaleString()} proposed updates. The remaining exact comparisons are not available to this UI yet, so this is not a complete row-level proof.</p>}
              </div>
            )}
          </CardContent>
        </Card>
        </TabsContent>

        <TabsContent value="runtime" className="m-0 flex-1 space-y-4 overflow-y-auto p-6">
          <AgentRuntimeCard />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function OverviewPanel({
  bridgeStatus,
  captureStatus,
  bridgeUpdatedAt,
  captureUpdatedAt,
  bridgeStale,
  captureStale,
  captureResult,
  error,
  refreshing,
  reconciling,
  onRefresh,
  onReconcile,
  onNavigate,
  onOpenAccount,
}: {
  bridgeStatus: CodingSessionBridgeStatus | null;
  captureStatus: ClaudeCaptureStatus | null;
  bridgeUpdatedAt: Date | null;
  captureUpdatedAt: Date | null;
  bridgeStale: boolean;
  captureStale: boolean;
  captureResult: ClaudeCaptureReconcileResult | null;
  error: string | null;
  refreshing: boolean;
  reconciling: boolean;
  onRefresh: () => Promise<void>;
  onReconcile: () => Promise<void>;
  onNavigate: (tab: CodingSessionsTab) => void;
  onOpenAccount: () => void;
}) {
  const [deliveryFilter, setDeliveryFilter] = useState<DeliveryEvidenceFilter | null>(null);
  const capabilityLabels = {
    event_mirror: "Live events",
    historical_import: "History import",
    title_sync: "Title sync",
    local_runtime: "Local runtime",
    native_resume: "Native resume",
    participant_conversations: "@matrx conversations",
  } as const;

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <MonitorCheck className="h-4 w-4" /> Coding provider support
            </CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Product support is shown separately from this Mac&rsquo;s live
              delivery activity. Queue counts are stored delivery envelopes, not
              provider conversation totals or connection indicators.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={() => void onRefresh()}
            disabled={refreshing}
          >
            {refreshing ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            Refresh status
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <FreshnessLine
            label="Local delivery activity"
            updatedAt={bridgeUpdatedAt}
            stale={bridgeStale && Boolean(bridgeStatus)}
            refreshing={refreshing}
          />
          {!bridgeStatus && !error && (
            <div className="flex items-center gap-2 rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading provider and
              delivery status…
            </div>
          )}
          {!bridgeStatus && error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              Live delivery activity is unavailable. Product support below will
              appear after the local engine responds.
            </div>
          )}
          {bridgeStatus && (
            <div className="grid gap-3 xl:grid-cols-2">
              {(Object.keys(PROVIDER_LABELS) as CodingSessionProvider[]).map(
                (provider) => {
                  const status = bridgeStatus.providers[provider];
                  return (
                    <div key={provider} className="rounded-md border p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-medium">{PROVIDER_LABELS[provider]}</h3>
                        {status.pending > 0 && (
                          <Button type="button" variant="outline" size="sm" onClick={() => setDeliveryFilter({ state: "pending", provider })}>
                            {status.pending.toLocaleString()} waiting envelope{status.pending === 1 ? "" : "s"} · inspect
                          </Button>
                        )}
                        {status.quarantined > 0 && (
                          <Button type="button" variant="destructive" size="sm" onClick={() => setDeliveryFilter({ state: "quarantine", provider })}>
                            {status.quarantined.toLocaleString()} not accepted · inspect
                          </Button>
                        )}
                        {status.pending === 0 && status.quarantined === 0 && (
                          <Badge variant="secondary">No delivery envelopes waiting</Badge>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {PROVIDER_SCOPE[provider]}
                      </p>
                      <div className="mt-2 rounded-md border border-dashed px-2.5 py-2 text-xs text-muted-foreground">
                        <span className="font-medium text-foreground">Connection on this Mac: not reported.</span>{" "}
                        The engine currently reports product capability and delivered activity, but not whether this provider&rsquo;s AI Matrx adapter is installed, trusted, or connected. “Supported” below does not mean connected.
                      </div>
                      <p className="mt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                        Product capabilities
                      </p>
                      <div className="mt-3 flex flex-wrap gap-1.5">
                        {Object.entries(capabilityLabels).map(([key, label]) => {
                          const supported = status.capabilities[key as keyof typeof capabilityLabels];
                          return (
                            <Badge
                              key={key}
                              variant={supported ? "secondary" : "outline"}
                            >
                              {supported ? "Supported: " : "Not available: "}{label}
                            </Badge>
                          );
                        })}
                      </div>
                      {status.capabilities.limitations.length > 0 && (
                        <p className="mt-3 text-xs text-muted-foreground">
                          {status.capabilities.limitations.join(" ")}
                        </p>
                      )}
                      <div className="mt-3 rounded-md bg-muted/30 p-2.5 text-xs text-muted-foreground">
                        <p>
                          Sessions represented in the current queue: {status.pending_sessions?.toLocaleString() ?? "not available"}
                        </p>
                        <p className="mt-1">
                          Delivery envelopes acknowledged from this Mac: {status.acknowledged_envelopes?.toLocaleString() ?? "not available"}
                        </p>
                        <p>
                          Last event stored: {status.last_enqueue
                            ? formatDate(status.last_enqueue.at)
                            : "none recorded"}
                        </p>
                        <p className="mt-1">
                          Last cloud acknowledgement: {status.last_acknowledgement
                            ? formatDate(status.last_acknowledgement.at)
                            : "none recorded"}
                        </p>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {provider === "claude_code" && (
                          <>
                            <Button size="sm" variant="outline" onClick={() => onNavigate("history")}>
                              Review history
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => onNavigate("titles")}>
                              Sync titles
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => onNavigate("runtime")}>
                              Open local runtime
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  );
                },
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card id="delivery-pipeline">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Database className="h-4 w-4" /> Delivery pipeline
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            “Queued” means safely stored on this Mac. “Acknowledged” means AI
            Matrx accepted the exact stored payload. A quarantined item was
            preserved because the server refused it and is not counted as delivered.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {bridgeStatus &&
            (!bridgeStatus.publisher.cloud_enabled || !bridgeStatus.publisher.active) && (
              <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <div>
                  <p className="font-medium">Cloud delivery is paused</p>
                  <p className="mt-1 text-muted-foreground">
                    {!bridgeStatus.publisher.cloud_enabled
                      ? "This runtime has cloud participation disabled. Captured items remain safely queued locally."
                      : "The background publisher is not running. Restart the local engine before expecting acknowledgement."}
                  </p>
                </div>
              </div>
            )}
          {bridgeStatus?.publisher.blocker && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
              <p className="font-medium">Cloud delivery is paused</p>
              <p className="mt-1 text-muted-foreground">
                {bridgeStatus.publisher.blocker.message}
              </p>
              <Button className="mt-3" size="sm" onClick={onOpenAccount}>
                Open account settings
              </Button>
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-4" aria-label="Delivery stages">
            <PipelineStage label="1. Captured" detail="Provider event or selected history" />
            <PipelineStage label="2. Saved locally" detail="Durable, integrity-checked queue" />
            <PipelineStage
              label="3. Waiting for AI Matrx"
              detail={`${(bridgeStatus?.pending.total ?? 0).toLocaleString()} delivery envelopes waiting${bridgeStatus?.pending.payload_bytes !== undefined ? ` · ${formatBytes(bridgeStatus.pending.payload_bytes)} stored` : ""}`}
              active={Boolean(bridgeStatus?.pending.total)}
            />
            <PipelineStage
              label="4. Acknowledged"
              detail={
                bridgeStatus?.last_acknowledgement
                  ? `Last at ${formatDate(bridgeStatus.last_acknowledgement.at)}`
                  : "No acknowledgement recorded yet"
              }
            />
          </div>

          {bridgeStatus?.head_blocker && !bridgeStatus.publisher.blocker && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
              <p className="font-medium">
                Cloud delivery is retrying a {PROVIDER_LABELS[bridgeStatus.head_blocker.provider]} event
              </p>
              <p className="mt-1 text-muted-foreground">
                {codingSessionActionLabel(bridgeStatus.head_blocker.action)} · attempt {bridgeStatus.head_blocker.attempts}
                {bridgeStatus.head_blocker.retry_in_seconds > 0
                  ? ` · retry in ${formatRetryDuration(bridgeStatus.head_blocker.retry_in_seconds)}`
                  : " · retry is due"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Source: {codingSessionSourceLabel(bridgeStatus.head_blocker.source)}. The coding provider already produced this event; AI Matrx acknowledgement is the step being retried.
              </p>
              {bridgeStatus.head_blocker.error?.message && (
                <p className="mt-1 text-amber-800 dark:text-amber-200">
                  {bridgeStatus.head_blocker.error.message}
                </p>
              )}
            </div>
          )}

          {bridgeStatus && bridgeStatus.quarantine.total > 0 && (
            <div className="flex gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div>
                <p className="font-medium">
                  {bridgeStatus.quarantine.total.toLocaleString()} preserved envelope
                  {bridgeStatus.quarantine.total === 1 ? " is" : "s are"} not retrying
                </p>
                <p className="mt-1 text-muted-foreground">
                  These envelopes were not acknowledged by AI Matrx. They remain
                  locally preserved instead of being dropped or reported as synced;
                  they do not block newer envelopes.
                </p>
                {bridgeStatus.quarantine.reasons?.map((reason) => (
                  <Button key={reason.code} type="button" variant="ghost" size="sm" className="mt-1 h-auto justify-start px-0 text-xs text-muted-foreground" onClick={() => setDeliveryFilter({ state: "quarantine" })}>{reason.count.toLocaleString()} · {reason.message} · inspect</Button>
                ))}
                <div><Button type="button" size="sm" variant="outline" className="mt-3" onClick={() => setDeliveryFilter({ state: "quarantine" })}>Inspect all preserved envelopes</Button></div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <RotateCw className="h-4 w-4" /> Recover missed Claude events
            </CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              A background check compares Claude sessions with AI Matrx and
              queues sessions that live hooks missed. It never changes Claude files.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={() => void onReconcile()}
            disabled={reconciling || !captureStatus?.enabled}
          >
            {reconciling ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            Run recovery check
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {captureStatus ? (
            <div className="grid gap-3 sm:grid-cols-3">
              <Summary
                label="Automatic recovery"
                value={captureStatus.enabled ? "Enabled" : "Disabled"}
              />
              <Summary
                label="Background worker"
                value={captureStatus.running ? "Running" : "Stopped"}
              />
              <Summary
                label="Exhausted retries"
                value={captureStatus.exhausted.length.toLocaleString()}
              />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Loading recovery status…</p>
          )}
          <FreshnessLine
            label="Recovery worker status"
            updatedAt={captureUpdatedAt}
            stale={captureStale && Boolean(captureStatus)}
            refreshing={refreshing}
          />
          {captureResult && (
            <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm">
              <p className="font-medium">Recovery check finished</p>
              {captureResult.detail ? (
                <p className="mt-1 text-muted-foreground">{captureResult.detail}</p>
              ) : captureResult.status === "disabled" ? (
                <p className="mt-1 text-muted-foreground">
                  Automatic recovery is disabled; no sessions were changed.
                </p>
              ) : (
                <p className="mt-1 text-muted-foreground">
                  {captureResult.local_sessions?.toLocaleString() ?? "No"} local sessions checked
                  {captureResult.cloud_sessions !== undefined
                    ? ` against ${captureResult.cloud_sessions.toLocaleString()} cloud sessions`
                    : ""}
                  {captureResult.missing !== undefined
                    ? ` · ${captureResult.missing.toLocaleString()} missing`
                    : ""}
                  {` · ${captureResult.enqueued.toLocaleString()} queued for delivery`}
                  {captureResult.skipped_pre_era
                    ? ` · ${captureResult.skipped_pre_era.toLocaleString()} older sessions left untouched`
                    : ""}
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {error && (
        <div
          className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
          role="alert"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <DeliveryEvidenceDialog filter={deliveryFilter} onClose={() => setDeliveryFilter(null)} onChanged={onRefresh} />
    </>
  );
}

function FreshnessLine({
  label,
  updatedAt,
  stale,
  refreshing,
}: {
  label: string;
  updatedAt: Date | null;
  stale: boolean;
  refreshing: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
      <span>
        {label}: {updatedAt ? `updated ${updatedAt.toLocaleTimeString()}` : "not loaded yet"}
      </span>
      {stale && <Badge variant="destructive">Showing last known data</Badge>}
      {refreshing && <span>Refreshing…</span>}
    </div>
  );
}

function PipelineStage({
  label,
  detail,
  active = false,
}: {
  label: string;
  detail: string;
  active?: boolean;
}) {
  return (
    <div className={`rounded-md border p-3 ${active ? "border-blue-500/40 bg-blue-500/5" : ""}`}>
      <p className="text-sm font-medium">{label}</p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}
