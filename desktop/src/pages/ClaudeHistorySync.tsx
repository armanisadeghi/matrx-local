import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CloudUpload,
  Copy,
  History,
  Loader2,
  RefreshCw,
  Tags,
  Trash2,
} from "lucide-react";

import { AgentRuntimeCard } from "@/components/coding-sessions/AgentRuntimeCard";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { engine } from "@/lib/api";
import type {
  ClaudeHistoryImportResult,
  ClaudeHistoryPreview,
  ClaudeHistoryStatus,
  ClaudeLabelSyncResult,
  ClaudeLabelSyncStatus,
} from "@/lib/api";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function blockedMessage(reason: string | null): string {
  switch (reason) {
    case "claude_not_installed":
      return "Claude Code is not installed or cannot be found.";
    case "claude_not_signed_in":
      return "Sign in to Claude Code, then review again.";
    case "claude_account_identity_unavailable":
      return "Claude is signed in, but this login does not expose a stable account identity. Sync is paused so histories from different accounts cannot be mixed.";
    default:
      return "Claude account status is unavailable. Open Claude Code and confirm its login, then review again.";
  }
}

export function ClaudeHistorySync() {
  const [preview, setPreview] = useState<ClaudeHistoryPreview | null>(null);
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

  const refreshStatus = useCallback(async () => {
    const next = await engine.getClaudeHistoryStatus();
    setHistoryStatus(next);
  }, []);

  const refreshLabelStatus = useCallback(async () => {
    setLabelStatus(await engine.getClaudeLabelStatus());
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

  const syncLabels = async () => {
    setLabelSyncing(true);
    setLabelError(null);
    try {
      setLabelResult(await engine.syncClaudeLabels());
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
    () => preview?.sessions.filter((session) =>
      selected.has(`${session.project_key}:${session.session_id}`),
    ) ?? [],
    [preview, selected],
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
      const next = await engine.previewClaudeHistory(100);
      setPreview(next);
      setSelected(new Set());
      await refreshStatus();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  };

  const toggle = (sessionId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) next.delete(sessionId);
      else next.add(sessionId);
      return next;
    });
  };

  const sync = async () => {
    if (!preview?.provider_account_key || selectedSessions.length === 0) return;
    setSyncing(true);
    setError(null);
    setResult(null);
    try {
      const next = await engine.importClaudeHistory(
        preview.provider_account_key,
        selectedSessions.map((session) => ({
          session_id: session.session_id,
          provider_project_key: session.project_key,
          source_revision: session.source_revision!,
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
        title="Claude Code history"
        description="Review local sessions, then explicitly copy only the sessions you choose into your private AI Matrx history."
      />
      <div className="flex-1 space-y-4 overflow-y-auto p-6">
        <AgentRuntimeCard />
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-4">
            <div>
              <CardTitle className="text-base">Sync Claude Code now</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Nothing is scanned or uploaded until you press Review. Review never uploads.
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
                <Summary label="Sessions" value={preview.totals.session_count.toLocaleString()} />
                <Summary label="Files" value={preview.totals.file_count.toLocaleString()} />
                <Summary label="Stored locally" value={formatBytes(preview.totals.bytes)} />
                <Summary label="Projects" value={preview.totals.project_count.toLocaleString()} />
              </div>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {preview.account_identity_available ? (
                  <Badge variant="secondary">
                    Claude account {preview.account_fingerprint}
                  </Badge>
                ) : (
                  <Badge variant="destructive">Claude account not verified</Badge>
                )}
                {preview.claude_client_version && (
                  <Badge variant="outline">{preview.claude_client_version}</Badge>
                )}
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
            </CardContent>
          )}
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle className="text-base">Keep titles in sync</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Keeps one label in both places, both ways. A rename in Claude
                Code lands here, and a conversation you rename in AI Matrx is
                written back into Claude Code&rsquo;s own session list. Sessions
                AI Matrx has never seen are never read or touched, and if you
                renamed the same session in both places, Claude Code wins.
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Claude Code reads its session list when it starts, so a title
                sent back appears there the next time Claude Code reloads.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={() => void syncLabels()}
              disabled={labelSyncing}
            >
              {labelSyncing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Tags className="mr-2 h-4 w-4" />
              )}
              Sync titles now
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
                  label="Titles synced"
                  value={labelStatus.synced_sessions.toLocaleString()}
                />
                <Summary
                  label="Renames sent back"
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
              <div className="space-y-2 rounded-md border p-3 text-sm">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  <span>
                    {labelResult.queued.toLocaleString()} title update
                    {labelResult.queued === 1 ? "" : "s"} queued ·{" "}
                    {labelResult.matched.toLocaleString()} of{" "}
                    {labelResult.bound_sessions.toLocaleString()} synced sessions
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
                    {labelResult.unmatched.toLocaleString()} synced session
                    {labelResult.unmatched === 1 ? " has" : "s have"} no Claude
                    record on this machine — they were most likely created on
                    another computer.
                  </p>
                )}
                {labelResult.sample_titles.length > 0 && (
                  <ul className="space-y-1 text-xs text-muted-foreground">
                    {labelResult.sample_titles.slice(0, 5).map((item) => (
                      <li key={item.provider_session_id} className="truncate">
                        {item.title}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {preview && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Choose sessions</CardTitle>
              <p className="text-sm text-muted-foreground">
                Showing the {preview.sessions.length} most recent sessions. Full transcripts can contain code, commands, file contents, and secrets; imported raw data remains owner-only.
              </p>
            </CardHeader>
            <CardContent className="space-y-2">
              {preview.sessions.map((session) => {
                const selectionKey = `${session.project_key}:${session.session_id}`;
                const checked = selected.has(selectionKey);
                const disabled =
                  !session.import_available ||
                  (!checked && selected.size >= preview.limits.selected_sessions);
                return (
                  <label
                    key={selectionKey}
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3 hover:bg-muted/40"
                  >
                    <Checkbox
                      checked={checked}
                      disabled={disabled || !preview.import_ready}
                      onCheckedChange={() => toggle(selectionKey)}
                      aria-label={`Select ${session.title}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {session.title}
                      </span>
                      <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span>{session.project_name}</span>
                        {session.git_branch && <span>{session.git_branch}</span>}
                        {session.worktree_name && (
                          <span>worktree {session.worktree_name}</span>
                        )}
                        {session.is_archived && <span>Archived in Claude</span>}
                        {!session.title_from_claude_index && (
                          <span>No Claude title — using the first prompt</span>
                        )}
                        <span>{formatBytes(session.bytes)}</span>
                        {session.subagent_count > 0 && (
                          <span>{session.subagent_count} subagents</span>
                        )}
                        {!session.import_available && (
                          <span>Too large to import safely</span>
                        )}
                      </span>
                    </span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      title="Copy the native Claude resume command"
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        void navigator.clipboard.writeText(
                          `claude --resume ${session.session_id}`,
                        );
                      }}
                    >
                      <Copy className="mr-1 h-3.5 w-3.5" />
                      Resume
                    </Button>
                  </label>
                );
              })}
              <div className="sticky bottom-0 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-md border bg-background/95 p-3 backdrop-blur">
                <span className="text-sm text-muted-foreground">
                  {selectedSessions.length} selected · {formatBytes(selectedBytes)}
                </span>
                <Button
                  onClick={() => void sync()}
                  disabled={
                    syncing ||
                    !preview.import_ready ||
                    selectedSessions.length === 0 ||
                    selectionOverLimit
                  }
                >
                  {syncing ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <CloudUpload className="mr-2 h-4 w-4" />
                  )}
                  Sync selected now
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {result && (
          <div className="flex gap-3 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" />
            <div>
              <p className="font-medium">Claude history is safely queued</p>
              <p className="mt-1 text-muted-foreground">
                {result.entries.toLocaleString()} entries from {result.selected_sessions} sessions were reconciled into {result.queued_batches} durable batches. {result.pending_outbox} batches are waiting for cloud acknowledgement.
              </p>
              {result.corrupt_lines > 0 && (
                <p className="mt-2 text-amber-700 dark:text-amber-300">
                  {result.corrupt_lines} incomplete or corrupt lines were not uploaded. Valid entries keep their original line positions so the gap remains visible.
                </p>
              )}
            </div>
          </div>
        )}

        {historyStatus && historyStatus.pending_history_imports > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
            <div>
              <p className="font-medium">
                {historyStatus.pending_history_imports.toLocaleString()} history batches are still queued
              </p>
              <p className="mt-1 text-muted-foreground">
                {historyStatus.oldest_history_import?.last_error
                  ? `Delivery is blocked after ${historyStatus.oldest_history_import.attempts} attempts: ${historyStatus.oldest_history_import.last_error}`
                  : "Delivery is waiting for acknowledgement."}
              </p>
              <p className="mt-1 text-muted-foreground">
                Retry after repairing sign-in or server access, or discard the queued copies. Neither action changes Claude files or hook observations.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                disabled={discarding || retrying || syncing}
                onClick={() => void retryPending()}
              >
                {retrying ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                Retry now
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={discarding || retrying || syncing}
                onClick={() => void discardPending()}
              >
                {discarding ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="mr-2 h-4 w-4" />
                )}
                Discard queued copies
              </Button>
            </div>
          </div>
        )}

        {error && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-base">What this can and cannot restore</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>AI Matrx receives exact local JSONL entries and creates a readable private conversation. Repeating Sync reconciles updates idempotently.</p>
            <p>Native Claude resume remains local: use Claude Code with the original session ID while the same transcript, workspace, and active Claude login are available.</p>
            <p>AI Matrx does not claim that a copied transcript can recreate Claude file checkpoints, permissions, credentials, or another machine&apos;s workspace.</p>
            <p>Claude&apos;s local session format exposes names, project grouping, branches, and fork lineage. Stable local pin/archive metadata is not available, so this sync does not invent it.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}
