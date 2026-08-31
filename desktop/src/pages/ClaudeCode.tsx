/**
 * Claude Code — what is on this Mac, and has it reached the cloud.
 *
 * The screen this replaces asked the user to run a scan, page a review, select
 * rows, preview an operation and then apply it, and described the result in
 * outbox nouns ("waiting envelopes", "delivery pipeline", "adapter spool").
 * Nobody wants a subset of their own history, so there is no selection here and
 * no preview: one button syncs everything. Numbers are the interface; prose
 * only appears when something is actually wrong, and then it says what to do.
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
} from "@/lib/api";

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

export function ClaudeCode() {
  const [data, setData] = useState<ClaudeOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<ClaudeSyncResult | null>(null);
  const [query, setQuery] = useState("");
  const [showAccounts, setShowAccounts] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await engine.getClaudeOverview());
      setError(null);
    } catch (nextError) {
      setError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const syncEverything = async () => {
    setSyncing(true);
    setError(null);
    setResult(null);
    try {
      const next = await engine.syncClaudeEverything();
      setResult(next);
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
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Claude Code</h1>
          <p className="text-sm text-muted-foreground">
            {loading
              ? "Reading your conversations…"
              : `${(totals?.conversations ?? 0).toLocaleString()} conversations · ${
                  data?.accounts.length ?? 0
                } accounts on this Mac`}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
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

      {error && (
        <div className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div>
            <p className="font-medium">Couldn't reach the local engine</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{error}</p>
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
              ` ${result.failed.length} batch${result.failed.length === 1 ? "" : "es"} failed — see below.`}
          </p>
        </div>
      )}

      {result?.failed.map((failure) => (
        <div
          key={failure.sessions}
          className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs"
        >
          <p className="font-mono">{failure.reason}</p>
        </div>
      ))}

      {totals && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Stat value={totals.synced} label="Synced" tone="text-emerald-600 dark:text-emerald-400" />
          <Stat value={totals.behind} label="Changed since sync" tone="text-amber-600 dark:text-amber-400" />
          <Stat value={totals.not_synced} label="Not synced" />
          <Stat value={totals.waiting} label="Uploading" />
          <Stat
            value={totals.failed}
            label="Failed"
            tone={totals.failed > 0 ? "text-destructive" : undefined}
          />
        </div>
      )}

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
                    <span className={account.name ? "" : "font-mono text-xs"}>
                      {account.name ?? account.account_id.slice(0, 8)}
                    </span>
                    {account.active && <Badge variant="secondary">Signed in</Badge>}
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
          placeholder="Search conversations"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="px-4 py-2 text-left font-medium">Conversation</th>
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
                <td className={`px-4 py-2 text-right ${STATE_STYLE[row.state]}`}>
                  {STATE_LABEL[row.state]}
                </td>
              </tr>
            ))}
            {!loading && conversations.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
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
          {totals.index_files_read.toLocaleString()} index files could not be read.
        </p>
      )}
    </div>
  );
}
