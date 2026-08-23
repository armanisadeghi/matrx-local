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
import { SessionDetailsComparisonTable } from "@/components/coding-sessions/SessionDetailsComparisonTable";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { engine } from "@/lib/api";
import type {
  ClaudeCaptureStatus,
  ClaudeCaptureReconcileResult,
  ClaudeHistoryImportResult,
  ClaudeHistoryChangeType,
  ClaudeHistoryInventoryPage,
  ClaudeHistoryReview,
  ClaudeHistoryStatus,
  ClaudeLabelSyncResult,
  ClaudeLabelSyncStatus,
  CodingSessionBridgeStatus,
  CodingSessionProvider,
  CodingSessionProviderReadinessStatus,
} from "@/lib/api";
import {
  claudeAccountReasonMessage,
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
  return reason
    ? claudeAccountReasonMessage(reason)
    : "Claude account status is unavailable. Open Claude Code and confirm its login, then review again.";
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
  const [historyFocus, setHistoryFocus] = useState<{ token: number; change?: ClaudeHistoryChangeType; availability?: "all" | "available" | "blocked" }>();
  const [showScanScope, setShowScanScope] = useState(false);
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
  const [showLabelStatusDetails, setShowLabelStatusDetails] = useState(false);
  const [bridgeStatus, setBridgeStatus] = useState<CodingSessionBridgeStatus | null>(null);
  const [providerReadiness, setProviderReadiness] = useState<CodingSessionProviderReadinessStatus | null>(null);
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
        const [bridgeResult, captureStatusResult, readinessResult] = await Promise.allSettled([
          engine.getCodingSessionStatus(),
          engine.getClaudeCaptureStatus(),
          engine.getCodingSessionProviderReadiness(),
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
        if (readinessResult.status === "fulfilled") {
          setProviderReadiness(readinessResult.value);
        } else {
          failures.push(`Provider readiness: ${readinessResult.reason instanceof Error ? readinessResult.reason.message : String(readinessResult.reason)}`);
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
      const next = await engine.syncClaudeLabels(dryRun);
      if (next.schema_version !== 3 || !next.operation_id || !next.operation) {
        setLabelResult(null);
        throw new Error(
          "This local engine cannot provide the required per-session comparison operation. Update and restart AI Matrx Local before applying session-detail changes.",
        );
      }
      setLabelResult(next);
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
  const selectionLimitReason = preview && selectedSessions.length > preview.limits.selected_sessions
    ? `The selection has ${selectedSessions.length} sessions; this operation allows ${preview.limits.selected_sessions}.`
    : preview && selectedBytes > preview.limits.import_bytes
      ? `The selection is ${formatBytes(selectedBytes)}; this operation allows ${formatBytes(preview.limits.import_bytes)}.`
      : null;

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
            providerReadiness={providerReadiness}
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
                <Summary label="Sessions present" value={preview.scan.present_count.toLocaleString()} onClick={() => setHistoryFocus({ token: Date.now() })} />
                <Summary label="Files examined" value={preview.scan.file_count.toLocaleString()} onClick={() => setShowScanScope((value) => !value)} />
                <Summary label="Stored locally" value={formatBytes(preview.scan.total_bytes)} onClick={() => setShowScanScope((value) => !value)} />
                <Summary label="Projects" value={preview.scan.project_count.toLocaleString()} onClick={() => setShowScanScope((value) => !value)} />
              </div>
              {showScanScope && <div className="rounded-md border p-3 text-sm"><p className="font-medium">Exact review scope</p><dl className="mt-2 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2"><div><dt className="font-medium text-foreground">Review ID</dt><dd className="font-mono">{preview.scan.scan_id}</dd></div><div><dt className="font-medium text-foreground">Completed</dt><dd>{formatDate(preview.scan.completed_at)}</dd></div><div><dt className="font-medium text-foreground">Transcript files examined</dt><dd>{preview.scan.file_count.toLocaleString()}</dd></div><div><dt className="font-medium text-foreground">Bytes represented</dt><dd>{preview.scan.total_bytes.toLocaleString()} bytes ({formatBytes(preview.scan.total_bytes)})</dd></div><div><dt className="font-medium text-foreground">Projects represented</dt><dd>{preview.scan.project_count.toLocaleString()}</dd></div><div><dt className="font-medium text-foreground">Rows retained</dt><dd>{preview.scan.session_count.toLocaleString()}</dd></div></dl></div>}
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
                  <div className="mt-2 flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => setHistoryFocus({ token: Date.now(), change: "new" })}>{reviewChanges.new.toLocaleString()} new</Button><Button size="sm" variant="outline" onClick={() => setHistoryFocus({ token: Date.now(), change: "content_changed" })}>{reviewChanges.contentChanged.toLocaleString()} transcript changes</Button><Button size="sm" variant="outline" onClick={() => setHistoryFocus({ token: Date.now(), change: "metadata_changed" })}>{reviewChanges.metadataChanged.toLocaleString()} detail changes</Button><Button size="sm" variant="outline" onClick={() => setHistoryFocus({ token: Date.now(), change: "missing" })}>{reviewChanges.missing.toLocaleString()} missing locally</Button><Button size="sm" variant="outline" onClick={() => setHistoryFocus({ token: Date.now(), change: "unchanged" })}>{reviewChanges.unchanged.toLocaleString()} unchanged</Button><Button size="sm" variant="outline" onClick={() => setHistoryFocus({ token: Date.now(), availability: "blocked" })}>{reviewChanges.blocked.toLocaleString()} blocked</Button></div>
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
                {...(historyFocus ? { focusFilter: historyFocus } : {})}
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
              {selectionLimitReason && <p className="text-sm text-destructive" role="alert">{selectionLimitReason} Remove sessions before copying.</p>}
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
            <div><p className="font-medium">{historyStatus.pending_history_imports.toLocaleString()} history envelopes await cloud acknowledgement</p><p className="mt-1 text-muted-foreground">{historyStatus.oldest_history_import?.error?.message ? `Blocked after ${historyStatus.oldest_history_import.attempts} attempts: ${historyStatus.oldest_history_import.error.message}` : "Stored safely on this Mac and waiting for delivery."}</p></div>
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
              {labelResult?.dry_run && labelResult.operation_id ? `Apply ${labelResult.queued.toLocaleString()} proposed updates` : "Preview session detail changes"}
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
            {labelStatus?.index_available && !labelStatus.index_writable && <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" /><span>Claude session indexes can be read but not safely opened for writing. Preview remains available; AI Matrx-to-Claude changes will be refused rather than silently claimed.</span></div>}
            {labelStatus?.index_limit_reached && <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" /><span>The Claude index scan reached its {labelStatus.index_limit?.toLocaleString() ?? "configured"}-file limit. Results are incomplete; choose Preview only after narrowing the local index footprint.</span></div>}
            {Boolean(labelStatus?.index_unreadable) && <div className="rounded-md border p-3 text-sm text-muted-foreground">{labelStatus?.index_unreadable?.toLocaleString()} index files could not be read and are excluded from the comparison.</div>}
            {labelStatus && labelStatus.index_available && (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Summary
                  label="Claude sessions on this Mac"
                  value={labelStatus.index_records.toLocaleString()}
                  onClick={() => setShowLabelStatusDetails((value) => !value)}
                />
                <Summary
                  label="Sessions with a recorded cloud settlement"
                  value={(labelStatus.acknowledged_sessions ?? labelStatus.synced_sessions).toLocaleString()}
                  onClick={() => setShowLabelStatusDetails((value) => !value)}
                />
                <Summary
                  label="Claude indexes previously written"
                  value={labelStatus.pushed_sessions.toLocaleString()}
                  onClick={() => setShowLabelStatusDetails((value) => !value)}
                />
                <Summary
                  label="Index files read"
                  value={labelStatus.index_files.toLocaleString()}
                  onClick={() => setShowLabelStatusDetails((value) => !value)}
                />
              </div>
            )}
            {showLabelStatusDetails && labelStatus && <div className="rounded-md border p-3 text-sm"><p className="font-medium">Session details synchronization evidence</p><dl className="mt-2 grid gap-2 text-xs text-muted-foreground sm:grid-cols-2"><div><dt className="font-medium text-foreground">Index records available</dt><dd>{labelStatus.index_records.toLocaleString()}</dd></div><div><dt className="font-medium text-foreground">Index files read</dt><dd>{labelStatus.index_files.toLocaleString()} · {labelStatus.index_unreadable?.toLocaleString() ?? 0} unreadable</dd></div><div><dt className="font-medium text-foreground">Cloud settlements recorded</dt><dd>{(labelStatus.acknowledged_sessions ?? labelStatus.synced_sessions).toLocaleString()}</dd></div><div><dt className="font-medium text-foreground">Claude indexes previously written</dt><dd>{labelStatus.pushed_sessions.toLocaleString()}</dd></div><div><dt className="font-medium text-foreground">Index write check</dt><dd>{labelStatus.index_writable ? "Writable now" : "Not writable now"}</dd></div><div><dt className="font-medium text-foreground">Pending write intents by state</dt><dd>{labelStatus.push_intents_by_state ? Object.entries(labelStatus.push_intents_by_state).map(([state, count]) => `${state}: ${count}`).join(" · ") || "None" : "Not reported"}</dd></div></dl><p className="mt-2 text-xs text-muted-foreground">Use Preview session detail changes to open exact per-session rows. These totals alone are not convergence proof.</p></div>}
            {labelStatus?.latest_operation && <div className="rounded-md border p-3 text-xs text-muted-foreground">Latest operation <span className="font-mono">{labelStatus.latest_operation.operation_id}</span> · {labelStatus.latest_operation.mode} · {labelStatus.latest_operation.status} · {labelStatus.latest_operation.verified_sessions.toLocaleString()} verified · {labelStatus.latest_operation.failed_sessions.toLocaleString()} failed</div>}
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
                    {labelResult.dry_run ? <Tags className="h-4 w-4 text-blue-600" /> : <CheckCircle2 className="h-4 w-4 text-emerald-600" />}
                    <span>
                      {labelResult.push_down.written.toLocaleString()} AI Matrx
                      rename
                      {labelResult.push_down.written === 1 ? "" : "s"} {labelResult.dry_run ? "would be written" : "written"}
                      {" "}back into Claude Code
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
                <SessionDetailsComparisonTable result={labelResult} busy={labelSyncing} onVerified={setLabelResult} />
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
  providerReadiness,
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
  providerReadiness: CodingSessionProviderReadinessStatus | null;
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
  const [guidedInstruction, setGuidedInstruction] = useState<{ label: string; instruction: string } | null>(null);
  const [showRecoveryDetails, setShowRecoveryDetails] = useState(false);
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
                  const readiness = providerReadiness?.providers[provider];
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
                      {readiness ? <div className="mt-2 space-y-2 rounded-md border px-2.5 py-2 text-xs">
                        <div className="flex flex-wrap gap-1.5"><Badge variant={readiness.product.installed === true ? "secondary" : "outline"}>{readiness.product.installed === true ? "Product installed" : readiness.product.installed === false ? "Product not found" : "Product installation unknown"}</Badge><Badge variant={readiness.product.running === true ? "secondary" : "outline"}>{readiness.product.running === true ? "Running now" : readiness.product.running === false ? "Not running" : "Running state unknown"}</Badge><Badge variant={readiness.adapter.detected ? "secondary" : "outline"}>{readiness.adapter.detected ? "AI Matrx adapter found" : "AI Matrx adapter not found"}</Badge><Badge variant="outline">Connection unverified</Badge></div>
                        <p className="text-muted-foreground">Product version: {readiness.product.version ?? "not reported"} · adapter version: {readiness.adapter.version ?? "not reported"} · adapter setup: {readiness.adapter.configured === true ? "configured" : readiness.adapter.configured === false ? "not configured" : "not verified"} · hook trust: {readiness.adapter.hook_trust.replace(/_/g, " ")}</p>
                        <p className="text-muted-foreground">{readiness.connection.detail}</p>
                        {readiness.activity.most_recent && <p className="text-muted-foreground">Most recent evidence: {readiness.activity.most_recent.kind === "cloud_acknowledgement" ? "AI Matrx acknowledged a delivery" : "this Mac stored provider work"} at {formatDate(readiness.activity.most_recent.at)}. This is activity evidence, not a live connection claim.</p>}
                        {readiness.upstream_spool.supported && <button type="button" className="text-left text-muted-foreground underline-offset-2 hover:underline" onClick={() => setGuidedInstruction({ label: `${PROVIDER_LABELS[provider]} adapter spool evidence`, instruction: `${readiness.upstream_spool.pending ?? "Unknown"} pending, ${readiness.upstream_spool.poison ?? "unknown"} needing attention, ${readiness.upstream_spool.in_flight ?? "unknown"} in flight, and ${readiness.upstream_spool.temporary ?? "unknown"} temporary adapter artifacts. ${readiness.upstream_spool.oldest_pending_at ? `The oldest pending artifact was observed at ${formatDate(readiness.upstream_spool.oldest_pending_at)}.` : "No oldest-pending timestamp was reported."} These are provider adapter aggregates, not AI Matrx delivery-envelope rows. Use the provider action below to inspect or repair the adapter itself.` })}>Adapter spool: {readiness.upstream_spool.pending ?? "unknown"} pending · {readiness.upstream_spool.poison ?? "unknown"} needs attention · {readiness.upstream_spool.in_flight ?? "unknown"} in flight · {readiness.upstream_spool.temporary ?? "unknown"} temporary · explain</button>}
                        {(readiness.product.evidence.length > 0 || readiness.adapter.evidence.length > 0 || readiness.connection.evidence?.length) && <details><summary className="cursor-pointer text-muted-foreground">Show detection evidence</summary><ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">{[...readiness.product.evidence, ...readiness.adapter.evidence, ...(readiness.connection.evidence ?? [])].map((item, index) => <li key={`${index}:${item}`}>{item}</li>)}</ul></details>}
                        {readiness.actions.length > 0 && <div className="flex flex-wrap gap-2">{readiness.actions.map((action) => <Button key={action.id} type="button" size="sm" variant="outline" onClick={() => setGuidedInstruction({ label: action.label, instruction: action.instruction })}>{action.label}</Button>)}</div>}
                      </div> : <div className="mt-2 rounded-md border border-dashed px-2.5 py-2 text-xs text-muted-foreground"><span className="font-medium text-foreground">Readiness is loading or unavailable.</span> Product support below is not a connection claim.</div>}
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
                        <p>{status.pending_sessions !== undefined ? <button type="button" className="underline-offset-2 hover:underline" onClick={() => setDeliveryFilter({ state: "pending", provider })}>Sessions represented in the current queue: {status.pending_sessions.toLocaleString()} · inspect envelopes</button> : "Sessions represented in the current queue: not available"}</p>
                        <p className="mt-1">{status.acknowledged_envelopes !== undefined ? <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => setGuidedInstruction({ label: `${PROVIDER_LABELS[provider]} acknowledgement total`, instruction: `${status.acknowledged_envelopes?.toLocaleString()} delivery envelopes from this Mac have recorded cloud acknowledgements. This is a cumulative aggregate; exact historical acknowledgement receipts were intentionally not retained by the current activity ledger, so there are no row records to show. Waiting and preserved envelope counts do retain exact rows.` })}>Delivery envelopes acknowledged from this Mac: {status.acknowledged_envelopes.toLocaleString()} · explain evidence</button> : "Delivery envelopes acknowledged from this Mac: not available"}</p>
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
          {bridgeStatus && bridgeStatus.publisher.transport_circuit.state !== "closed" && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
              <p className="font-medium">AI Matrx transport is temporarily paused</p>
              <p className="mt-1 text-muted-foreground">
                {bridgeStatus.publisher.transport_circuit.state === "open" ? "Repeated offline transport failures opened a bounded cooldown." : "The publisher is testing one delivery after the cooldown."}
                {bridgeStatus.publisher.transport_circuit.retry_in_seconds !== null && bridgeStatus.publisher.transport_circuit.retry_in_seconds > 0 ? ` Next probe in ${formatRetryDuration(bridgeStatus.publisher.transport_circuit.retry_in_seconds)}.` : " A probe is due now."}
                {` ${bridgeStatus.publisher.transport_circuit.failure_count.toLocaleString()} transport failure${bridgeStatus.publisher.transport_circuit.failure_count === 1 ? "" : "s"} were observed; stored envelopes remain local.`}
              </p>
              {(bridgeStatus.pending.total > 0 || bridgeStatus.quarantine.total > 0) && <Button type="button" className="mt-3" size="sm" variant="outline" onClick={() => setDeliveryFilter({ state: bridgeStatus.pending.total > 0 ? "pending" : "quarantine" })}>Inspect affected stored envelopes</Button>}
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-4" aria-label="Delivery stages">
            <PipelineStage label="1. Captured" detail="Provider event or selected history" />
            <PipelineStage label="2. Saved locally" detail="Durable, integrity-checked queue" />
            <PipelineStage
              label="3. Waiting for AI Matrx"
              detail={`${(bridgeStatus?.pending.total ?? 0).toLocaleString()} delivery envelopes waiting${bridgeStatus?.pending.payload_bytes !== undefined ? ` · ${formatBytes(bridgeStatus.pending.payload_bytes)} stored` : ""}`}
              active={Boolean(bridgeStatus?.pending.total)}
              {...(bridgeStatus?.pending.total ? { onClick: () => setDeliveryFilter({ state: "pending" as const }) } : {})}
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
                onClick={() => setShowRecoveryDetails((value) => !value)}
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
          {showRecoveryDetails && captureStatus && <div className="overflow-x-auto rounded-md border"><table className="w-full min-w-[700px] text-sm"><thead className="border-b bg-muted/40 text-left"><tr><th className="px-3 py-2">Session reference</th><th className="px-3 py-2">State</th><th className="px-3 py-2">Attempts</th><th className="px-3 py-2">Last result</th><th className="px-3 py-2">Stored for delivery</th></tr></thead><tbody className="divide-y">{captureStatus.exhausted.map((item) => <tr key={`exhausted-${item.session_key}`}><td className="px-3 py-2 font-mono text-xs">{item.session_key}</td><td className="px-3 py-2"><Badge variant="destructive">Exhausted</Badge></td><td className="px-3 py-2">{item.attempts}</td><td className="px-3 py-2 text-destructive">{item.last_error ?? "No error detail reported"}</td><td className="px-3 py-2">{item.enqueued_at ? formatDate(item.enqueued_at) : "Not stored"}</td></tr>)}{captureStatus.recent.map((item) => <tr key={`recent-${item.session_key}`}><td className="px-3 py-2 font-mono text-xs">{item.session_key}</td><td className="px-3 py-2"><Badge variant="outline">Recent</Badge></td><td className="px-3 py-2">{item.attempts}</td><td className="px-3 py-2">{item.last_error ?? "No error"}</td><td className="px-3 py-2">{item.enqueued_at ? formatDate(item.enqueued_at) : "Not stored"}</td></tr>)}{captureStatus.exhausted.length === 0 && captureStatus.recent.length === 0 && <tr><td colSpan={5} className="p-6 text-center text-muted-foreground">No recovery attempts are reported.</td></tr>}</tbody></table></div>}
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
              {captureResult.batch && captureResult.batch.length > 0 && <div className="mt-2 text-xs text-muted-foreground">Exact sessions selected in this pass: <span className="font-mono">{captureResult.batch.join(", ")}</span></div>}
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
      <Dialog
        open={guidedInstruction !== null}
        onOpenChange={(open) => {
          if (!open) setGuidedInstruction(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{guidedInstruction?.label ?? "Coding provider guidance"}</DialogTitle>
            <DialogDescription className="whitespace-pre-wrap text-left">
              {guidedInstruction?.instruction}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end">
            <DialogClose asChild>
              <Button type="button">Done</Button>
            </DialogClose>
          </div>
        </DialogContent>
      </Dialog>
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
  onClick,
}: {
  label: string;
  detail: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const content = (
    <div className={`rounded-md border p-3 ${active ? "border-blue-500/40 bg-blue-500/5" : ""}`}>
      <p className="text-sm font-medium">{label}</p>
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  );
  return onClick ? <button type="button" className="text-left" onClick={onClick}>{content}</button> : content;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Summary({ label, value, onClick }: { label: string; value: string; onClick?: () => void }) {
  const content = (
    <div className={`rounded-md border p-3 text-left ${onClick ? "transition-colors hover:bg-muted/40" : ""}`}>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
  return onClick ? <button type="button" onClick={onClick} aria-label={`Inspect ${label}: ${value}`}>{content}</button> : content;
}
