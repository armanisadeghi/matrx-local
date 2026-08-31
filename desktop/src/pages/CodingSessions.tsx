/**
 * Coding sessions — every provider on this Mac, and whether it reached the cloud.
 *
 * Replaces a screen that made the user run a scan, page a review, select rows,
 * preview an operation and then apply it, and narrated the result in outbox
 * nouns ("waiting envelopes", "delivery pipeline", "adapter spool"). Nobody
 * wants a subset of their own history, so there is no selection and no preview:
 * one button syncs everything. Numbers are the interface; prose appears only
 * when something is wrong, and then it names the next action.
 *
 * All four providers stay visible. Claude Code is the only one with local
 * transcripts to list, but Codex, Cursor and VS Code deliver through the same
 * bridge, so their state belongs on this screen too.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  CloudUpload,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";

import { AgentRuntimeCard } from "@/components/coding-sessions/AgentRuntimeCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { engine } from "@/lib/api";
import type {
  ClaudeConversation,
  ClaudeOverview,
  ClaudeSyncResult,
  ClaudeSyncState,
  CodingSessionBridgeStatus,
  CodingSessionProvider,
  CodingSessionProviderReadinessStatus,
} from "@/lib/api";

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

const STATE_LABEL: Record<ClaudeSyncState, string> = {
  synced: "Synced",
  behind: "Changed",
  not_synced: "Not synced",
};

const STATE_STYLE: Record<ClaudeSyncState, string> = {
  synced: "text-emerald-600 dark:text-emerald-400",
  behind: "text-amber-600 dark:text-amber-400",
  not_synced: "text-muted-foreground",
};

function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(ms: number): string {
  if (!ms) return "—";
  const minutes = Math.round((Date.now() - ms) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(ms).toLocaleDateString();
}

function Stat({
  value,
  label,
  tone,
}: {
  value: number;
  label: string;
  tone?: string | undefined;
}) {
  return (
    <div className="rounded-lg border px-4 py-3">
      <div className={`text-2xl font-semibold tabular-nums ${tone ?? ""}`}>
        {value.toLocaleString()}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
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
  const [result, setResult] = useState<ClaudeSyncResult | null>(null);
  const [query, setQuery] = useState("");
  const [showAccounts, setShowAccounts] = useState(false);

  const load = useCallback(async () => {
    // Provider state must survive a Claude-specific failure: a broken
    // transcript read is no reason to stop reporting Codex or Cursor.
    const [overview, status, ready] = await Promise.allSettled([
      engine.getClaudeOverview(),
      engine.getCodingSessionStatus(),
      engine.getCodingSessionProviderReadiness(),
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

  const conversations = useMemo(() => {
    const rows = data?.conversations ?? [];
    const needle = query.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter(
      (row) =>
        row.title.toLowerCase().includes(needle) ||
        (row.project ?? "").toLowerCase().includes(needle),
    );
  }, [data?.conversations, query]);

  const totals = data?.totals;

  return (
    // AppLayout mounts every page inside `overflow-hidden`, so a page that does
    // not own its own scroll region is silently clipped at the viewport.
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-2xl font-semibold">Coding Sessions</h1>
          <p className="text-sm text-muted-foreground">
            {loading
              ? "Reading your conversations…"
              : `${(totals?.conversations ?? 0).toLocaleString()} Claude Code conversations · ${
                  data?.accounts.length ?? 0
                } accounts on this Mac`}
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
                Sent {result.conversations.toLocaleString()} conversations.
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
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <Stat
                value={totals.synced}
                label="Synced"
                tone="text-emerald-600 dark:text-emerald-400"
              />
              <Stat
                value={totals.behind}
                label="Changed since sync"
                tone="text-amber-600 dark:text-amber-400"
              />
              <Stat value={totals.not_synced} label="Not synced" />
              <Stat value={totals.waiting} label="Uploading" />
              <Stat
                value={totals.failed}
                label="Failed"
                tone={totals.failed > 0 ? "text-destructive" : undefined}
              />
            </div>
          )}

          {/* Every provider on the bridge, not just Claude Code. */}
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">Editor</th>
                  <th className="px-4 py-2 text-left font-medium">Installed</th>
                  <th className="px-4 py-2 text-right font-medium">Uploading</th>
                  <th className="px-4 py-2 text-right font-medium">Failed</th>
                  <th className="px-4 py-2 text-right font-medium">Last sent</th>
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
                      <td className="px-4 py-2 text-right tabular-nums">
                        {pending.toLocaleString()}
                      </td>
                      <td
                        className={`px-4 py-2 text-right tabular-nums ${
                          failed > 0 ? "text-destructive" : ""
                        }`}
                      >
                        {failed.toLocaleString()}
                      </td>
                      <td className="px-4 py-2 text-right text-muted-foreground">
                        {sent ? formatWhen(Date.parse(sent)) : "Never"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
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
                Accounts ({data.accounts.length})
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

          <div className="relative">
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

          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 text-left font-medium">
                    Conversation
                  </th>
                  <th className="px-4 py-2 text-left font-medium">Project</th>
                  <th className="px-4 py-2 text-right font-medium">Updated</th>
                  <th className="px-4 py-2 text-right font-medium">Size</th>
                  <th className="px-4 py-2 text-right font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {conversations.map((row: ClaudeConversation) => (
                  <tr key={row.session_id} className="border-t">
                    <td className="max-w-md truncate px-4 py-2">{row.title}</td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {row.project ?? "—"}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                      {formatWhen(row.last_activity_at)}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                      {formatBytes(row.bytes)}
                    </td>
                    <td
                      className={`px-4 py-2 text-right ${STATE_STYLE[row.state]}`}
                    >
                      {STATE_LABEL[row.state]}
                    </td>
                  </tr>
                ))}
                {!loading && conversations.length === 0 && (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-4 py-8 text-center text-muted-foreground"
                    >
                      {query
                        ? "No conversations match that search."
                        : "No Claude Code conversations found on this Mac."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <AgentRuntimeCard />

          {totals && totals.unreadable > 0 && (
            <p className="text-xs text-muted-foreground">
              {totals.unreadable.toLocaleString()} of{" "}
              {totals.index_files_read.toLocaleString()} index files could not be
              read.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
