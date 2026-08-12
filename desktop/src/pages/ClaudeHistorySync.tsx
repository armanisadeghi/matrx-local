import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CloudUpload,
  Copy,
  History,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { engine } from "@/lib/api";
import type {
  ClaudeHistoryImportResult,
  ClaudeHistoryPreview,
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

  const selectedSessions = useMemo(
    () =>
      preview?.sessions.filter((session) => selected.has(session.session_id)) ?? [],
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
          source_revision: session.source_revision,
        })),
      );
      setResult(next);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setSyncing(false);
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
                const checked = selected.has(session.session_id);
                const disabled =
                  !checked && selected.size >= preview.limits.selected_sessions;
                return (
                  <label
                    key={session.session_id}
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3 hover:bg-muted/40"
                  >
                    <Checkbox
                      checked={checked}
                      disabled={disabled || !preview.import_ready}
                      onCheckedChange={() => toggle(session.session_id)}
                      aria-label={`Select ${session.title}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {session.title}
                      </span>
                      <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span>{session.project_name}</span>
                        {session.git_branch && <span>{session.git_branch}</span>}
                        <span>{formatBytes(session.bytes)}</span>
                        {session.subagent_count > 0 && (
                          <span>{session.subagent_count} subagents</span>
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
