/**
 * Coding sessions — every provider on this Mac, and whether it is IN AI MATRX.
 *
 * Numbers are the interface, and every number is a door (Arman, 2026-09-08):
 * a count you cannot click into is a count you cannot trust. So each status
 * card filters the list, each queue number opens the exact envelopes behind
 * it, and each conversation opens a full diagnosis — the server's own record,
 * the transcript, Claude's sidebar entry, every delivery attempt and error.
 *
 * Status is judged against the CLOUD, not against what this Mac uploaded.
 * Most Claude Code sessions reach AI Matrx straight from the CLI hook, so a
 * screen that only knew its own uploads said "Not synced" for everything.
 *
 * All four providers stay visible. Claude Code is the only one with local
 * transcripts to list, but Codex, Cursor and VS Code deliver through the same
 * bridge, so their queue state belongs on this screen too.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  CloudUpload,
  FolderOpen,
  Loader2,
  Pin,
  RefreshCw,
  Search,
} from "lucide-react";

import { AgentRuntimeCard } from "@/components/coding-sessions/AgentRuntimeCard";
import {
  DeliveryEvidenceDialog,
  type DeliveryEvidenceFilter,
} from "@/components/coding-sessions/DeliveryEvidenceDialog";
import {
  SESSION_STATE_HINT,
  SESSION_STATE_LABEL,
  SESSION_STATE_TONE,
  SessionDiagnosisDialog,
} from "@/components/coding-sessions/SessionDiagnosisDialog";
import { Badge, Button, BasicInput as Input } from "@ai-matrx/design-system";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { engine } from "@/lib/api";
import type {
  ClaudeConversation,
  ClaudeOverview,
  ClaudeSessionState,
  ClaudeSyncResult,
  CodingSessionArtifactsSessionDetail,
  CodingSessionArtifactsSessionSummary,
  CodingSessionArtifactsStatus,
  CodingSessionBridgeStatus,
  CodingSessionProvider,
  CodingSessionProviderReadinessStatus,
} from "@/lib/api";
import { requestOrganizationPicker } from "@/lib/org/active-org";
import { laneBlockerTone } from "@/lib/lane-blocker";

// THE package byte-size formatter (`@ai-matrx/kit/format`, duplication
// census H1 2026-09-07). This repo alone carried THIRTEEN `formatBytes`
// bodies with twelve different roundings and five different words for
// "unknown" — the clearest case in the fleet for one owner.
import { formatFileSize, formatRelativeTime } from "@ai-matrx/kit/format";

const PROVIDERS: CodingSessionProvider[] = [
  "claude_code",
  "codex",
  "cursor",
  "vscode",
];

const PROVIDER_LABELS: Record<CodingSessionProvider, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
  vscode: "VS Code",
};

/** The status cards, in the order a person reads them: good → needs a look. */
const STATE_CARDS: ClaudeSessionState[] = [
  "in_cloud",
  "changed",
  "queued",
  "failed",
  "not_in_cloud",
];

type ListFilter = ClaudeSessionState | "pinned" | "all";

/**
 * A wrapper that BINDS the "0 means never stamped" guard and delegates the
 * voice to THE package formatter — `0` is an epoch timestamp to
 * `formatRelativeTime`, not an absence, so the guard cannot move into it.
 */
function formatWhen(ms: number): string {
  if (!ms) return "—";
  return formatRelativeTime(ms);
}

function formatStamp(value: string | null | undefined): string {
  if (!value) return "Never";
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? formatWhen(ms) : value;
}

function Stat({
  value,
  label,
  hint,
  tone,
  selected,
  onClick,
}: {
  value: number;
  label: string;
  hint: string;
  tone?: string | undefined;
  selected?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      title={hint}
      aria-pressed={selected}
      onClick={onClick}
      className={`rounded-lg border px-4 py-3 text-left transition-colors hover:bg-muted/50 ${
        selected ? "border-primary ring-1 ring-primary" : ""
      }`}
    >
      <div className={`text-2xl font-semibold tabular-nums ${tone ?? ""}`}>
        {value.toLocaleString()}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </button>
  );
}

/** A queue number that opens the envelopes behind it. Zero is plain text. */
function QueueCount({
  value,
  tone,
  onOpen,
  hint,
}: {
  value: number;
  tone?: string;
  onOpen: () => void;
  hint: string;
}) {
  if (value === 0) {
    return <span className="tabular-nums text-muted-foreground">0</span>;
  }
  return (
    <button
      type="button"
      title={hint}
      onClick={onOpen}
      className={`tabular-nums underline decoration-dotted underline-offset-4 hover:decoration-solid ${tone ?? ""}`}
    >
      {value.toLocaleString()}
    </button>
  );
}

/**
 * Reveal a durable folder in the OS file manager through the ONE validated
 * native command this app already exposes (`open_filesystem_path`, Rust
 * `filesystem.rs`) — never plugin-shell, which would hand the renderer an
 * arbitrary executable surface.
 */
async function revealInFinder(path: string): Promise<void> {
  await invoke("open_filesystem_path", { path, reveal: true });
}

function RevealButton({ path, label = "Reveal in Finder" }: { path: string; label?: string }) {
  const [revealing, setRevealing] = useState(false);
  const [revealError, setRevealError] = useState<string | null>(null);
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={revealing}
        title={path}
        onClick={() => {
          setRevealing(true);
          setRevealError(null);
          revealInFinder(path)
            .catch((reason: unknown) => {
              setRevealError(reason instanceof Error ? reason.message : String(reason));
            })
            .finally(() => setRevealing(false));
        }}
      >
        <FolderOpen className="mr-2 h-4 w-4" />
        {label}
      </Button>
      {revealError && (
        <span className="max-w-64 truncate text-xs text-destructive" role="alert" title={revealError}>
          {revealError}
        </span>
      )}
    </span>
  );
}

function whenLocal(value: string | null | undefined): string {
  if (!value) return "—";
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) return value;
  return new Date(ms).toLocaleString();
}

/**
 * The per-row artifacts number: the lane's file count for this session, or
 * "—" when the lane holds nothing for it. Loading and endpoint failure are
 * each shown as themselves — never as a dash that looks like "none".
 */
function ArtifactsCell({
  sessions,
  error,
  sessionId,
  onOpen,
}: {
  sessions: Map<string, CodingSessionArtifactsSessionSummary> | null;
  error: string | null;
  sessionId: string;
  onOpen: () => void;
}) {
  if (error) {
    return (
      <span className="text-destructive" title={`The artifacts lane could not be read: ${error}`}>
        ?
      </span>
    );
  }
  if (!sessions) {
    return <span className="text-muted-foreground" title="Asking the artifacts lane…">…</span>;
  }
  const summary = sessions.get(sessionId);
  if (!summary) {
    return (
      <span className="text-muted-foreground" title="The artifacts lane holds no files for this session.">
        —
      </span>
    );
  }
  return (
    <button
      type="button"
      title={`${summary.files.toLocaleString()} file${summary.files === 1 ? "" : "s"} kept (${formatFileSize(summary.bytes)}) · ${summary.uploaded.toLocaleString()} in AI Matrx · ${summary.pending_upload.toLocaleString()} pending${summary.failed_upload > 0 ? ` · ${summary.failed_upload.toLocaleString()} failed` : ""}. Click to see every file.`}
      className="underline decoration-dotted underline-offset-4 hover:decoration-solid"
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
    >
      {summary.files.toLocaleString()}
      <span className="ml-1 text-xs text-muted-foreground">
        ({summary.uploaded.toLocaleString()}↑
        {summary.pending_upload > 0 ? ` ${summary.pending_upload.toLocaleString()} pending` : ""}
        {summary.failed_upload > 0 ? (
          <span className="text-destructive"> {summary.failed_upload.toLocaleString()} failed</span>
        ) : null}
        )
      </span>
    </button>
  );
}

/**
 * Every artifact the lane holds for one session — the manifest, row by row.
 * Same shape as the conversation diagnosis: the engine's record, never a
 * client-side guess; a 404 from the lane is shown as the lane's own words.
 */
function SessionArtifactsDialog({
  sessionId,
  onClose,
}: {
  sessionId: string | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<CodingSessionArtifactsSessionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      setDetail(await engine.getCodingSessionArtifactsSession(sessionId));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    setDetail(null);
    void load();
  }, [load]);

  const entries = detail
    ? Object.entries(detail.entries).sort(([a], [b]) => a.localeCompare(b))
    : [];

  return (
    <Dialog open={Boolean(sessionId)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[88vh] max-w-5xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Artifacts this session built</DialogTitle>
          <DialogDescription>
            Every file the artifacts lane kept for session {sessionId}, and whether AI Matrx holds it.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-between gap-3">
          {detail ? (
            <span className="text-sm">
              {detail.files.toLocaleString()} file{detail.files === 1 ? "" : "s"} ·{" "}
              {formatFileSize(detail.bytes)} · {detail.uploaded.toLocaleString()} in AI Matrx ·{" "}
              {detail.pending_upload.toLocaleString()} pending
              {detail.failed_upload > 0 ? ` · ${detail.failed_upload.toLocaleString()} failed` : ""}
              {detail.abandoned_upload > 0 ? ` · ${detail.abandoned_upload.toLocaleString()} abandoned` : ""}
              {detail.skipped_over_size > 0 ? ` · ${detail.skipped_over_size.toLocaleString()} skipped (over size)` : ""}
              {detail.skipped_over_count > 0 ? ` · ${detail.skipped_over_count.toLocaleString()} skipped (over count)` : ""}
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">{error ? "Not available" : "Loading…"}</span>
          )}
          <div className="flex items-center gap-2">
            {detail && <RevealButton path={detail.durable_dir} />}
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
              {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              Refresh
            </Button>
          </div>
        </div>

        {error && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {detail && (
          <div className="flex max-h-[68vh] flex-col gap-3 overflow-y-auto pr-1">
            <div className="grid grid-cols-[11rem_1fr] gap-3 rounded-lg border px-3 py-2 text-sm">
              <div className="text-muted-foreground">Project</div>
              <div className="min-w-0 break-words">{detail.project_slug}</div>
              <div className="text-muted-foreground">Scratchpad</div>
              <div className="min-w-0 break-all font-mono text-xs">{detail.scratchpad}</div>
              <div className="text-muted-foreground">Durable folder</div>
              <div className="min-w-0 break-all font-mono text-xs">{detail.durable_dir}</div>
            </div>
            {entries.length === 0 ? (
              <p className="px-1 text-sm text-muted-foreground">
                The lane holds no files for this session.
              </p>
            ) : (
              <div className="overflow-auto rounded-md border">
                <table className="w-full min-w-[820px] text-xs">
                  <thead className="bg-muted/40 text-left">
                    <tr>
                      <th className="px-2 py-1">File</th>
                      <th className="px-2 py-1 text-right">Size</th>
                      <th className="px-2 py-1">Captured</th>
                      <th className="px-2 py-1">AI Matrx</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {entries.map(([relativePath, entry]) => (
                      <tr key={relativePath}>
                        <td className="px-2 py-1.5 align-top">
                          <div className="break-all font-mono">{relativePath}</div>
                          <div className="text-muted-foreground" title={entry.sha256}>
                            sha256 {entry.sha256.slice(0, 12)}
                          </div>
                        </td>
                        <td className="px-2 py-1.5 text-right align-top tabular-nums">
                          {formatFileSize(entry.size)}
                        </td>
                        <td className="px-2 py-1.5 align-top whitespace-nowrap">
                          {whenLocal(entry.captured_at)}
                        </td>
                        <td className="px-2 py-1.5 align-top">
                          {entry.file_id ? (
                            <>
                              <Badge variant="outline">Uploaded</Badge>
                              <div className="mt-1 text-muted-foreground">
                                {whenLocal(entry.uploaded_at)} · file{" "}
                                <span className="font-mono">{entry.file_id}</span>
                              </div>
                            </>
                          ) : (
                            <>
                              <Badge variant={entry.upload_error ? "destructive" : "outline"}>
                                {entry.upload_error ? "Upload failed" : "Pending"}
                              </Badge>
                              <div className="mt-1 text-muted-foreground">
                                {entry.upload_attempts} attempt{entry.upload_attempts === 1 ? "" : "s"}
                              </div>
                              {entry.upload_error && (
                                <div className="mt-1 max-w-96 break-words text-destructive">
                                  {entry.upload_error}
                                </div>
                              )}
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function CodingSessions() {
  const [data, setData] = useState<ClaudeOverview | null>(null);
  const [bridge, setBridge] = useState<CodingSessionBridgeStatus | null>(null);
  const [readiness, setReadiness] =
    useState<CodingSessionProviderReadinessStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [result, setResult] = useState<ClaudeSyncResult | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ListFilter>("all");
  const [showAccounts, setShowAccounts] = useState(false);
  const [evidence, setEvidence] = useState<DeliveryEvidenceFilter | null>(null);
  const [diagnosisId, setDiagnosisId] = useState<string | null>(null);
  // The artifacts lane: status is null until the engine answers (loading),
  // and an endpoint failure is its own error — never a zero, never a blank.
  const [artifacts, setArtifacts] = useState<CodingSessionArtifactsStatus | null>(null);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);
  const [artifactSessions, setArtifactSessions] = useState<Map<
    string,
    CodingSessionArtifactsSessionSummary
  > | null>(null);
  const [artifactSessionsError, setArtifactSessionsError] = useState<string | null>(null);
  const [artifactsSyncing, setArtifactsSyncing] = useState(false);
  const [artifactsDialogId, setArtifactsDialogId] = useState<string | null>(null);

  const load = useCallback(async () => {
    // Provider state must survive a Claude-specific failure: a broken
    // transcript read is no reason to stop reporting Codex or Cursor.
    const [overview, status, ready, artifactStatus, artifactList] =
      await Promise.allSettled([
        engine.getClaudeOverview(),
        engine.getCodingSessionStatus(),
        engine.getCodingSessionProviderReadiness(),
        engine.getCodingSessionArtifactsStatus(),
        engine.getCodingSessionArtifactsSessions(),
      ]);
    if (overview.status === "fulfilled") {
      setData(overview.value);
      setError(null);
    } else {
      setError(
        overview.reason instanceof Error
          ? overview.reason.message
          : String(overview.reason),
      );
    }
    if (status.status === "fulfilled") setBridge(status.value);
    if (ready.status === "fulfilled") setReadiness(ready.value);
    if (artifactStatus.status === "fulfilled") {
      setArtifacts(artifactStatus.value);
      setArtifactsError(null);
    } else {
      setArtifactsError(
        artifactStatus.reason instanceof Error
          ? artifactStatus.reason.message
          : String(artifactStatus.reason),
      );
    }
    if (artifactList.status === "fulfilled") {
      setArtifactSessions(
        new Map(artifactList.value.sessions.map((row) => [row.cli_session_id, row])),
      );
      setArtifactSessionsError(null);
    } else {
      setArtifactSessionsError(
        artifactList.reason instanceof Error
          ? artifactList.reason.message
          : String(artifactList.reason),
      );
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const syncEverything = async () => {
    setSyncing(true);
    setError(null);
    setResult(null);
    try {
      setResult(await engine.syncClaudeEverything());
      await load();
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setSyncing(false);
    }
  };

  const resumeDelivery = async () => {
    setResuming(true);
    try {
      await engine.resumeCodingSessionDelivery();
      await load();
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setResuming(false);
    }
  };

  const syncArtifactsNow = async () => {
    setArtifactsSyncing(true);
    setArtifactsError(null);
    try {
      await engine.syncCodingSessionArtifacts();
      await load();
    } catch (nextError) {
      setArtifactsError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setArtifactsSyncing(false);
    }
  };

  const conversations = useMemo(() => {
    const rows = data?.conversations ?? [];
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (filter === "pinned" && !row.pinned) return false;
      if (filter !== "all" && filter !== "pinned" && row.state !== filter) return false;
      if (!needle) return true;
      return (
        row.title.toLowerCase().includes(needle) ||
        (row.project ?? "").toLowerCase().includes(needle) ||
        (row.category ?? "").toLowerCase().includes(needle)
      );
    });
  }, [data?.conversations, query, filter]);

  const totals = data?.totals;
  const blocker = bridge?.publisher.blocker ?? null;
  const cloud = data?.cloud;

  const toggleFilter = (next: ListFilter) =>
    setFilter((current) => (current === next ? "all" : next));

  return (
    // AppLayout mounts every page inside `overflow-hidden`, so a page that does
    // not own its own scroll region is silently clipped at the viewport.
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-2xl font-semibold">Coding Sessions</h1>
          <p className="text-sm text-muted-foreground">
            {loading
              ? "Reading your conversations and asking AI Matrx what it holds…"
              : `${(totals?.conversations ?? 0).toLocaleString()} Claude Code conversations on this Mac · ${
                  data?.accounts.length ?? 0
                } accounts · ${
                  cloud?.checked
                    ? `AI Matrx holds ${cloud.sessions.toLocaleString()} of them (checked ${formatStamp(cloud.checked_at)})`
                    : "AI Matrx could not be asked"
                }`}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void load()} disabled={loading}>
            <RefreshCw
              className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
          <Button onClick={() => void syncEverything()} disabled={syncing}>
            {syncing ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <CloudUpload className="mr-2 h-4 w-4" />
            )}
            {syncing ? "Syncing…" : "Sync everything"}
          </Button>
        </div>
      </header>

      <div
        data-testid="coding-sessions-scroll"
        className="flex-1 overflow-y-auto"
      >
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
          {blocker && (
            <div
              className={`flex items-start gap-3 rounded-lg border p-4 text-sm ${laneBlockerTone(blocker.code).container}`}
              role={laneBlockerTone(blocker.code).role}
              data-testid="delivery-blocker"
            >
              {laneBlockerTone(blocker.code).quiet ? (
                <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
              ) : (
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              )}
              <div className="flex-1">
                <p className="font-medium">
                  {laneBlockerTone(blocker.code).quiet
                    ? "Delivery to AI Matrx resumes as soon as your session is back"
                    : "Delivery to AI Matrx is paused for every provider"}
                </p>
                <p className="mt-1">{blocker.message}</p>
                {blocker.remedy && (
                  <p className="mt-1 text-muted-foreground">{blocker.remedy}</p>
                )}
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  {blocker.code}
                  {blocker.since ? ` · since ${formatStamp(blocker.since)}` : ""}
                  {blocker.receipt_id ? ` · first delivery #${blocker.receipt_id}` : ""}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {blocker.code === "organization_not_chosen" && (
                    <Button size="sm" onClick={() => requestOrganizationPicker()}>
                      Choose organization
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={resuming}
                    onClick={() => void resumeDelivery()}
                  >
                    {resuming ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <RefreshCw className="mr-2 h-4 w-4" />
                    )}
                    Retry delivery now
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setEvidence({ state: "pending" })}
                  >
                    Show what is waiting
                  </Button>
                </div>
              </div>
            </div>
          )}

          {cloud && !cloud.checked && (
            <div className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div>
                <p className="font-medium">
                  AI Matrx could not be asked which conversations it holds
                </p>
                <p className="mt-1">{cloud.detail ?? cloud.reason}</p>
                <p className="mt-1 text-muted-foreground">
                  Until it can, every conversation below reads “Unknown” rather
                  than guessing from this Mac's own upload ledger.
                </p>
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              <div>
                <p className="font-medium">Couldn't read your conversations</p>
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  {error}
                </p>
              </div>
            </div>
          )}

          {result && !result.started && result.blocked_reason && (
            <div className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p>{result.blocked_reason}</p>
            </div>
          )}

          {result?.started && (
            <div className="flex items-start gap-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm">
              <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              <p>
                Queued {result.conversations.toLocaleString()} conversations for
                delivery.
                {result.failed.length > 0 &&
                  ` ${result.failed.length} batch${
                    result.failed.length === 1 ? "" : "es"
                  } failed — see below.`}
              </p>
            </div>
          )}

          {result?.failed.map((failure) => (
            <div
              key={failure.sessions}
              className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 font-mono text-xs"
            >
              {failure.reason}
            </div>
          ))}

          {totals && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {STATE_CARDS.map((state) => (
                <Stat
                  key={state}
                  value={totals[state]}
                  label={SESSION_STATE_LABEL[state]}
                  hint={`${SESSION_STATE_HINT[state]} Click to show only these.`}
                  tone={totals[state] > 0 ? SESSION_STATE_TONE[state] : undefined}
                  selected={filter === state}
                  onClick={() => toggleFilter(state)}
                />
              ))}
              <Stat
                value={totals.pinned}
                label="Pinned in Claude Code"
                hint="Conversations pinned in Claude Code's sidebar. Pinned there means starred in AI Matrx. Click to show only these."
                tone="text-amber-600 dark:text-amber-400"
                selected={filter === "pinned"}
                onClick={() => toggleFilter("pinned")}
              />
              {totals.unknown > 0 && (
                <Stat
                  value={totals.unknown}
                  label={SESSION_STATE_LABEL.unknown}
                  hint={`${SESSION_STATE_HINT.unknown} Click to show only these.`}
                  selected={filter === "unknown"}
                  onClick={() => toggleFilter("unknown")}
                />
              )}
            </div>
          )}

          {/* Every provider on the bridge, not just Claude Code. */}
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">App</th>
                  <th
                    className="px-4 py-2 text-left font-medium"
                    title="Whether the app is installed on this Mac (its command on PATH, its bundle in /Applications, or a running process)."
                  >
                    Installed here
                  </th>
                  <th
                    className="px-4 py-2 text-right font-medium"
                    title="Deliveries stored on this Mac that still have to be sent to AI Matrx. One delivery holds one or more session events. Click a number to see them."
                  >
                    Waiting to send
                  </th>
                  <th
                    className="px-4 py-2 text-right font-medium"
                    title="Deliveries AI Matrx refused and this Mac preserved (never deleted). Each needs a retry or a discard. Click a number to see why."
                  >
                    Refused, preserved
                  </th>
                  <th
                    className="px-4 py-2 text-right font-medium"
                    title="The last time AI Matrx accepted a delivery from this Mac for this app. Sessions mirrored straight from the app's own hook do not pass through this Mac and are not counted here."
                  >
                    Last accepted by AI Matrx
                  </th>
                </tr>
              </thead>
              <tbody>
                {PROVIDERS.map((provider) => {
                  const ready = readiness?.providers?.[provider];
                  const pending = bridge?.pending.by_provider?.[provider] ?? 0;
                  const failed = bridge?.quarantine.by_provider?.[provider] ?? 0;
                  const sent = ready?.activity.last_cloud_acknowledgement_at;
                  const installed = ready?.product.installed;
                  return (
                    <tr key={provider} className="border-t">
                      <td className="px-4 py-2 font-medium">
                        {PROVIDER_LABELS[provider]}
                      </td>
                      <td className="px-4 py-2 text-muted-foreground">
                        {installed === true
                          ? ready?.product.version
                            ? `Yes · ${ready.product.version}`
                            : "Yes"
                          : installed === false
                            ? "No"
                            : "—"}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <QueueCount
                          value={pending}
                          hint="Open the deliveries waiting to be sent for this app."
                          onOpen={() => setEvidence({ state: "pending", provider })}
                        />
                      </td>
                      <td className="px-4 py-2 text-right">
                        <QueueCount
                          value={failed}
                          tone="text-destructive"
                          hint="Open the refused deliveries for this app, with the server's reason for each."
                          onOpen={() => setEvidence({ state: "quarantine", provider })}
                        />
                      </td>
                      <td className="px-4 py-2 text-right text-muted-foreground">
                        {formatStamp(sent)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {bridge && (
              <p className="border-t px-4 py-2 text-xs text-muted-foreground">
                {bridge.pending.total.toLocaleString()} deliver
                {bridge.pending.total === 1 ? "y" : "ies"} waiting across all apps
                {bridge.pending.item_count
                  ? ` (${bridge.pending.item_count.toLocaleString()} events, ${formatFileSize(bridge.pending.payload_bytes)})`
                  : ""}
                {bridge.quarantine.total > 0
                  ? ` · ${bridge.quarantine.total.toLocaleString()} refused and preserved`
                  : ""}
                {bridge.publisher.active
                  ? blocker
                    ? " · delivery paused"
                    : " · delivery running"
                  : " · delivery stopped"}
              </p>
            )}
            {/*
              What the publisher's most recent pass actually did. A tick that
              delivers nothing while rows are eligible used to be invisible on
              every screen and in every log.
            */}
            {bridge?.publisher.ticks && (
              <p className="border-t px-4 py-2 text-xs text-muted-foreground">
                Publisher: last tick{" "}
                {formatStamp(bridge.publisher.ticks.last_tick_at)} · sent{" "}
                {bridge.publisher.ticks.last_tick_sent.toLocaleString()} · failed{" "}
                {bridge.publisher.ticks.last_tick_failed.toLocaleString()} · eligible{" "}
                {bridge.publisher.ticks.last_tick_eligible === null
                  ? "not measured"
                  : bridge.publisher.ticks.last_tick_eligible.toLocaleString()}
                {/* The one field that explains a zero. */}
                {bridge.publisher.ticks.last_tick_blocked
                  ? ` · blocked: ${bridge.publisher.ticks.last_tick_blocked}`
                  : ""}
                {bridge.publisher.transport_circuit.config.delivery_concurrency
                  ? ` · concurrency ${bridge.publisher.transport_circuit.config.delivery_concurrency}`
                  : ""}
                {bridge.publisher.ticks.last_error
                  ? ` · ${bridge.publisher.ticks.last_error.message}`
                  : ""}
              </p>
            )}
          </div>

          {/*
            The artifacts lane: what sessions BUILT, kept durably on this Mac
            and published to AI Matrx. Every number is the engine's own
            manifest count; a file count in the table below is the door.
          */}
          <div className="overflow-hidden rounded-lg border" data-testid="artifacts-lane">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-muted/50 px-4 py-2">
              <div className="text-xs font-medium uppercase text-muted-foreground">
                Artifacts
                <span className="ml-2 normal-case">
                  files a session built, kept on this Mac and published to AI Matrx
                </span>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={artifactsSyncing || !artifacts}
                onClick={() => void syncArtifactsNow()}
              >
                {artifactsSyncing ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                {artifactsSyncing ? "Syncing…" : "Sync now"}
              </Button>
            </div>

            {artifactsError && (
              <div
                className="flex items-start gap-3 border-b border-destructive/40 bg-destructive/10 p-4 text-sm"
                role="alert"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                <div>
                  <p className="font-medium">Couldn't read the artifacts lane</p>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">{artifactsError}</p>
                </div>
              </div>
            )}

            {!artifacts && !artifactsError && (
              <p className="px-4 py-3 text-sm text-muted-foreground">
                Asking the engine what it has kept…
              </p>
            )}

            {artifacts && (
              <>
                {artifacts.blocker && (
                  <div
                    className={`flex items-start gap-3 border-b p-4 text-sm ${laneBlockerTone(artifacts.blocker.code).container}`}
                    role={laneBlockerTone(artifacts.blocker.code).role}
                    data-testid="artifacts-blocker"
                  >
                    {laneBlockerTone(artifacts.blocker.code).quiet ? (
                      <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
                    ) : (
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                    )}
                    <div className="flex-1">
                      <p className="font-medium">
                        {laneBlockerTone(artifacts.blocker.code).quiet
                          ? "Artifact publishing resumes as soon as your session is back"
                          : "Artifact publishing to AI Matrx is paused"}
                      </p>
                      <p className="mt-1">{artifacts.blocker.message}</p>
                      {artifacts.blocker.remedy && (
                        <p className="mt-1 text-muted-foreground">{artifacts.blocker.remedy}</p>
                      )}
                      <p className="mt-1 font-mono text-xs text-muted-foreground">
                        {artifacts.blocker.code}
                        {artifacts.blocker.since ? ` · since ${formatStamp(artifacts.blocker.since)}` : ""}
                      </p>
                      {artifacts.blocker.code === "no_organization" && (
                        <div className="mt-3">
                          <Button size="sm" onClick={() => requestOrganizationPicker()}>
                            Choose organization
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {artifacts.last_error && (
                  <div className="flex items-start gap-3 border-b border-amber-500/40 bg-amber-500/10 p-4 text-sm">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                    <div>
                      <p className="font-medium">The last artifacts pass hit an error</p>
                      <p className="mt-1 font-mono text-xs text-muted-foreground">
                        {artifacts.last_error.code ? `${artifacts.last_error.code} — ` : ""}
                        {artifacts.last_error.message ?? JSON.stringify(artifacts.last_error)}
                      </p>
                    </div>
                  </div>
                )}

                <p className="px-4 py-2 text-sm">
                  <span className="tabular-nums">{artifacts.sessions.toLocaleString()}</span> session
                  {artifacts.sessions === 1 ? "" : "s"} ·{" "}
                  <span className="tabular-nums">{artifacts.files.toLocaleString()}</span> file
                  {artifacts.files === 1 ? "" : "s"} · {formatFileSize(artifacts.bytes)} ·{" "}
                  <span className="text-emerald-600 dark:text-emerald-400">
                    {artifacts.uploaded.toLocaleString()} in AI Matrx
                  </span>{" "}
                  · <span className="tabular-nums">{artifacts.pending_upload.toLocaleString()}</span> pending
                  {" · "}
                  <span className={artifacts.failed_upload > 0 ? "text-destructive" : "tabular-nums"}>
                    {artifacts.failed_upload.toLocaleString()} failed
                  </span>
                  {" · "}
                  <span className={artifacts.abandoned_upload > 0 ? "text-destructive" : "tabular-nums"}>
                    {artifacts.abandoned_upload.toLocaleString()} abandoned
                  </span>
                  {" · "}
                  <span
                    className="tabular-nums"
                    title={`Skipped: ${artifacts.skipped_over_size.toLocaleString()} over ${formatFileSize(artifacts.limits.max_file_bytes)}, ${artifacts.skipped_over_count.toLocaleString()} past the ${artifacts.limits.max_files_per_session.toLocaleString()}-file per-session cap.`}
                  >
                    skipped {artifacts.skipped_over_size.toLocaleString()} over size /{" "}
                    {artifacts.skipped_over_count.toLocaleString()} over count
                  </span>
                </p>

                <p className="border-t px-4 py-2 text-xs text-muted-foreground">
                  {artifacts.active
                    ? artifacts.blocker
                      ? "Lane running · publishing paused"
                      : "Lane running"
                    : "Lane stopped"}
                  {artifacts.cloud_enabled ? "" : " · cloud publishing off"}
                  {" · last tick "}
                  {"at" in artifacts.last_tick
                    ? `${formatStamp(artifacts.last_tick.at)} · ${artifacts.last_tick.seconds}s · captured ${artifacts.last_tick.captured.toLocaleString()} · uploaded ${artifacts.last_tick.uploaded.toLocaleString()} · failed ${artifacts.last_tick.failed.toLocaleString()}`
                    : "never"}
                  {` · scans every ${artifacts.limits.scan_interval_seconds}s`}
                </p>

                <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-2 text-xs text-muted-foreground">
                  <span className="min-w-0 break-all font-mono" title="Durable copies live here, outside /tmp, one folder per session.">
                    {artifacts.durable_root}
                  </span>
                  <RevealButton path={artifacts.durable_root} />
                </div>
              </>
            )}
          </div>

          {data && (
            <div>
              <button
                type="button"
                className="flex items-center gap-1 text-sm font-medium"
                onClick={() => setShowAccounts((open) => !open)}
              >
                {showAccounts ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
                Claude accounts on this Mac ({data.accounts.length})
              </button>
              {showAccounts && (
                <div className="mt-2 overflow-hidden rounded-lg border">
                  {data.accounts.map((account) => (
                    <div
                      key={account.account_id}
                      className="flex items-center justify-between border-b px-4 py-2 text-sm last:border-b-0"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={account.name ? "" : "font-mono text-xs"}
                        >
                          {account.name ?? account.account_id.slice(0, 8)}
                        </span>
                        {account.active && (
                          <Badge variant="secondary">Signed in</Badge>
                        )}
                      </div>
                      <span className="tabular-nums text-muted-foreground">
                        {account.conversations.toLocaleString()} records
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-64 flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="pl-9"
                placeholder={`Search ${(
                  data?.conversations.length ?? 0
                ).toLocaleString()} Claude Code conversations`}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            {filter !== "all" && (
              <Button variant="outline" size="sm" onClick={() => setFilter("all")}>
                Showing {filter === "pinned" ? "pinned" : SESSION_STATE_LABEL[filter].toLowerCase()} · clear
              </Button>
            )}
          </div>

          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
                <tr>
                  <th
                    className="w-8 px-2 py-2 text-center font-medium"
                    title="Pinned in Claude Code's sidebar. Pinned there means starred in AI Matrx."
                  >
                    <Pin className="mx-auto h-3.5 w-3.5" />
                  </th>
                  <th className="px-4 py-2 text-left font-medium">
                    Conversation
                  </th>
                  <th className="px-4 py-2 text-left font-medium">Project</th>
                  <th className="px-4 py-2 text-right font-medium">Updated</th>
                  <th className="px-4 py-2 text-right font-medium">Size</th>
                  <th
                    className="px-4 py-2 text-right font-medium"
                    title="Files this session built that the artifacts lane kept on this Mac (in AI Matrx / pending). Click a number to see every file."
                  >
                    Artifacts
                  </th>
                  <th
                    className="px-4 py-2 text-right font-medium"
                    title="Judged against AI Matrx's own record of the session. Click a row for every fact behind it."
                  >
                    In AI Matrx?
                  </th>
                </tr>
              </thead>
              <tbody>
                {conversations.map((row: ClaudeConversation) => (
                  <tr
                    key={row.session_id}
                    className="cursor-pointer border-t hover:bg-muted/40"
                    onClick={() => setDiagnosisId(row.session_id)}
                    data-testid="conversation-row"
                  >
                    <td className="px-2 py-2 text-center">
                      {row.pinned && (
                        <Pin
                          className="mx-auto h-3.5 w-3.5 text-amber-500"
                          aria-label="Pinned in Claude Code"
                        />
                      )}
                    </td>
                    <td className="max-w-md truncate px-4 py-2">
                      {row.title}
                      {!row.in_claude_sidebar && (
                        <span
                          className="ml-2 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                          title="On this Mac, but Claude's sidebar never listed it (started from the CLI). It syncs like any other."
                        >
                          CLI only
                        </span>
                      )}
                      {row.category && (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {row.category}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {row.project ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                      {formatWhen(row.last_activity_at)}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                      {formatFileSize(row.bytes)}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      <ArtifactsCell
                        sessions={artifactSessions}
                        error={artifactSessionsError}
                        sessionId={row.session_id}
                        onOpen={() => setArtifactsDialogId(row.session_id)}
                      />
                    </td>
                    <td
                      className={`px-4 py-2 text-right ${SESSION_STATE_TONE[row.state]}`}
                      title={SESSION_STATE_HINT[row.state]}
                    >
                      {SESSION_STATE_LABEL[row.state]}
                      {row.delivery.quarantined > 0 && (
                        <span className="ml-1 text-xs">
                          ({row.delivery.quarantined} refused)
                        </span>
                      )}
                      {row.state === "queued" && row.delivery.pending > 0 && (
                        <span className="ml-1 text-xs">
                          ({row.delivery.pending} waiting)
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {!loading && conversations.length === 0 && (
                  <tr>
                    <td
                      colSpan={7}
                      className="px-4 py-8 text-center text-muted-foreground"
                    >
                      {query || filter !== "all"
                        ? "No conversations match that search or filter."
                        : "No Claude Code conversations found on this Mac."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <AgentRuntimeCard />

          {totals?.index_limit_reached && (
            <p className="text-xs text-destructive">
              Claude's index has more than 250,000 records; some conversations
              may be missing from this list.
            </p>
          )}


          {totals && totals.unreadable > 0 && (
            <p className="text-xs text-muted-foreground">
              {totals.unreadable.toLocaleString()} of{" "}
              {totals.index_files_read.toLocaleString()} index files could not be
              read.
            </p>
          )}
        </div>
      </div>

      <DeliveryEvidenceDialog
        filter={evidence}
        onClose={() => setEvidence(null)}
        onChanged={load}
      />
      <SessionDiagnosisDialog
        sessionId={diagnosisId}
        onClose={() => setDiagnosisId(null)}
        onChanged={load}
      />
      <SessionArtifactsDialog
        sessionId={artifactsDialogId}
        onClose={() => setArtifactsDialogId(null)}
      />
    </div>
  );
}
