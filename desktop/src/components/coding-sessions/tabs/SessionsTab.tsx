/**
 * The session list — every provider the engine lists, and every row a door.
 *
 * Three of Arman's complaints land here (2026-09-14):
 *  · "It shows names, but if you click on them, it doesn't actually bring up
 *    the conversations" — the row click now opens the session's conversation
 *    in this app's chat surface. Delivery diagnosis is a secondary action.
 *  · "The refresh button… does nothing. It just stares at you" — the button
 *    spins, the rows dim, and the list keeps its previous answer (labelled
 *    with its age) instead of blanking for four seconds.
 *  · "Almost entirely taken over by Claude code stuff" — the provider chips
 *    come from the engine's own answer, never from a name typed in here.
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  Copy,
  ExternalLink,
  Loader2,
  Pin,
  RefreshCw,
  Search,
  Stethoscope,
} from "lucide-react";

import { Badge, Button, BasicInput as Input } from "@ai-matrx/design-system";
import {
  ArtifactsCell,
  SessionArtifactsDialog,
} from "@/components/coding-sessions/SessionArtifactsDialog";
import {
  SESSION_STATE_HINT,
  SESSION_STATE_LABEL,
  SESSION_STATE_TONE,
} from "@/components/coding-sessions/SessionDiagnosisDialog";
import { Stat, formatStamp, formatWhen } from "@/components/coding-sessions/shared";
import type { ClaudeConversation, ClaudeSessionState, CodingSessionProvider } from "@/lib/api";
import { getWebAppOrigin } from "@/lib/app-config";
import {
  filterRowsByProvider,
  providerChips,
  unlistedProviderNote,
} from "@/lib/coding-sessions/providers";
import {
  conversationChatHref,
  conversationWebUrl,
  resolveSessionConversation,
} from "@/lib/coding-sessions/session-conversation";
import type { CodingSessionsSnapshot } from "@/lib/coding-sessions/overview-store";
import { openExternal } from "@/lib/open-external";
import { formatFileSize } from "@ai-matrx/kit/format";

/** The status cards, in the order a person reads them: good → needs a look. */
const STATE_CARDS: ClaudeSessionState[] = [
  "in_cloud",
  "changed",
  "queued",
  "failed",
  "not_in_cloud",
];

type ListFilter = ClaudeSessionState | "pinned" | "all";

export interface SessionsTabProps {
  snapshot: CodingSessionsSnapshot;
  onOpenDiagnosis: (sessionId: string) => void;
}

/** A row action that reports its own failure instead of swallowing it. */
function RowAction({
  label,
  icon,
  title,
  onRun,
}: {
  label: string;
  icon: React.ReactNode;
  title: string;
  onRun: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="inline-flex items-center">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        title={error ?? title}
        disabled={busy}
        onClick={(event) => {
          event.stopPropagation();
          setBusy(true);
          setError(null);
          void Promise.resolve(onRun())
            .then(() => {
              setDone(true);
              window.setTimeout(() => setDone(false), 1500);
            })
            .catch((reason: unknown) => {
              setError(reason instanceof Error ? reason.message : String(reason));
            })
            .finally(() => setBusy(false));
        }}
      >
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
        <span className="ml-1.5 hidden text-xs xl:inline">
          {error ? "Failed" : done ? "Copied" : label}
        </span>
      </Button>
    </span>
  );
}

export function SessionsTab({ snapshot, onOpenDiagnosis }: SessionsTabProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ListFilter>("all");
  const [provider, setProvider] = useState<CodingSessionProvider | null>(null);
  const [artifactsDialogId, setArtifactsDialogId] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<{ title: string; reason: string; sessionId: string } | null>(
    null,
  );

  const data = snapshot.overview;
  const totals = data?.totals;
  const cloud = data?.cloud;
  const chips = useMemo(
    () => providerChips(data, snapshot.readiness),
    [data, snapshot.readiness],
  );
  const activeChip = provider ? chips.find((chip) => chip.provider === provider) ?? null : null;

  const conversations = useMemo(() => {
    const rows = filterRowsByProvider(data?.conversations ?? [], data, provider);
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
  }, [data, filter, provider, query]);

  const toggleFilter = (next: ListFilter) =>
    setFilter((current) => (current === next ? "all" : next));

  const openRow = (row: ClaudeConversation) => {
    const target = resolveSessionConversation(row);
    if (target.kind === "conversation") {
      setBlocked(null);
      navigate(conversationChatHref(target.conversationId));
      return;
    }
    setBlocked({ title: row.title, reason: target.reason, sessionId: row.session_id });
  };

  return (
    <div className="flex flex-col gap-6">
      {snapshot.error && (
        <div
          className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm"
          role="alert"
          data-testid="sessions-error"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div>
            <p className="font-medium">Couldn't read your conversations</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{snapshot.error}</p>
            {data && (
              <p className="mt-1 text-muted-foreground">
                The list below is the last answer this Mac read
                {snapshot.overviewAt ? ` (${formatWhen(snapshot.overviewAt)})` : ""}.
              </p>
            )}
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
              Until it can, every conversation below reads “Unknown” rather than
              guessing from this Mac's own upload ledger.
            </p>
          </div>
        </div>
      )}

      {blocked && (
        <div
          className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm"
          role="status"
          data-testid="session-not-openable"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div className="flex-1">
            <p className="font-medium">“{blocked.title}” is not in AI Matrx yet</p>
            <p className="mt-1">{blocked.reason}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => onOpenDiagnosis(blocked.sessionId)}>
                <Stethoscope className="mr-2 h-4 w-4" />
                See every delivery fact
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setBlocked(null)}>
                Dismiss
              </Button>
            </div>
          </div>
        </div>
      )}

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
            label="Pinned in the coding agent"
            hint="Conversations pinned in the agent's own sidebar. Pinned there means starred in AI Matrx. Click to show only these."
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

      {/* The provider facet. Every chip is the engine's own answer. */}
      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" data-testid="provider-chips">
          <Button
            size="sm"
            variant={provider === null ? "secondary" : "ghost"}
            onClick={() => setProvider(null)}
          >
            All apps
            <span className="ml-1.5 tabular-nums text-xs text-muted-foreground">
              {(data?.conversations.length ?? 0).toLocaleString()}
            </span>
          </Button>
          {chips.map((chip) => (
            <Button
              key={chip.provider}
              size="sm"
              variant={provider === chip.provider ? "secondary" : "ghost"}
              title={
                chip.listed
                  ? `${chip.count.toLocaleString()} ${chip.label} session${chip.count === 1 ? "" : "s"} on this Mac.`
                  : `This Mac's engine does not list ${chip.label} sessions yet.`
              }
              onClick={() => setProvider(chip.provider === provider ? null : chip.provider)}
            >
              {chip.label}
              <span className="ml-1.5 tabular-nums text-xs text-muted-foreground">
                {chip.listed ? chip.count.toLocaleString() : "—"}
              </span>
            </Button>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-64 flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder={`Search ${conversations.length.toLocaleString()} conversation${
              conversations.length === 1 ? "" : "s"
            }`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        {filter !== "all" && (
          <Button variant="outline" size="sm" onClick={() => setFilter("all")}>
            Showing {filter === "pinned" ? "pinned" : SESSION_STATE_LABEL[filter].toLowerCase()} ·
            clear
          </Button>
        )}
        {snapshot.overviewFromCache && (
          <Badge variant="outline" data-testid="sessions-cached">
            Last read {snapshot.overviewAt ? formatWhen(snapshot.overviewAt) : "earlier"}
            {snapshot.overviewPending ? " · refreshing" : ""}
          </Badge>
        )}
      </div>

      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
            <tr>
              <th
                className="w-8 px-2 py-2 text-center font-medium"
                title="Pinned in the coding agent's own sidebar. Pinned there means starred in AI Matrx."
              >
                <Pin className="mx-auto h-3.5 w-3.5" />
              </th>
              <th className="px-4 py-2 text-left font-medium">Conversation</th>
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
                title="Judged against AI Matrx's own record of the session. Open the row's Delivery action for every fact behind it."
              >
                In AI Matrx?
              </th>
              <th className="px-4 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody
            className={snapshot.overviewPending ? "opacity-50 transition-opacity" : undefined}
            data-testid="sessions-tbody"
            data-refreshing={snapshot.overviewPending ? "true" : "false"}
          >
            {conversations.map((row: ClaudeConversation) => {
              const target = resolveSessionConversation(row);
              const continuation = row.continuation ?? null;
              return (
                <tr
                  key={row.session_id}
                  className="cursor-pointer border-t hover:bg-muted/40"
                  onClick={() => openRow(row)}
                  data-testid="conversation-row"
                >
                  <td className="px-2 py-2 text-center">
                    {row.pinned && (
                      <Pin
                        className="mx-auto h-3.5 w-3.5 text-amber-500"
                        aria-label="Pinned in the coding agent"
                      />
                    )}
                  </td>
                  <td className="max-w-md truncate px-4 py-2">
                    {row.title}
                    {!row.in_claude_sidebar && (
                      <span
                        className="ml-2 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
                        title="On this Mac, but the agent's sidebar never listed it (started from the CLI). It syncs like any other."
                      >
                        CLI only
                      </span>
                    )}
                    {row.category && (
                      <span className="ml-2 text-xs text-muted-foreground">{row.category}</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">{row.project ?? "—"}</td>
                  <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                    {formatWhen(row.last_activity_at)}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                    {formatFileSize(row.bytes)}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">
                    <ArtifactsCell
                      sessions={snapshot.artifactSessions}
                      error={snapshot.artifactSessionsError}
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
                      <span className="ml-1 text-xs">({row.delivery.quarantined} refused)</span>
                    )}
                    {row.state === "queued" && row.delivery.pending > 0 && (
                      <span className="ml-1 text-xs">({row.delivery.pending} waiting)</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1 text-right">
                    {target.kind === "conversation" && (
                      <RowAction
                        label="In AI Matrx"
                        title="Open this conversation on the AI Matrx website."
                        icon={<ExternalLink className="h-3.5 w-3.5" />}
                        onRun={async () => {
                          const origin = await getWebAppOrigin();
                          await openExternal(conversationWebUrl(origin, target.conversationId));
                        }}
                      />
                    )}
                    {continuation && (
                      <RowAction
                        label="Continue"
                        title={`${continuation.command} — ${continuation.note}`}
                        icon={<Copy className="h-3.5 w-3.5" />}
                        onRun={() => navigator.clipboard.writeText(continuation.command)}
                      />
                    )}
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      title="Every delivery fact behind this row's status: the server's binding, the transcript on disk, each envelope and its error."
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpenDiagnosis(row.session_id);
                      }}
                    >
                      <Stethoscope className="h-3.5 w-3.5" />
                      <span className="ml-1.5 hidden text-xs xl:inline">Delivery</span>
                    </Button>
                  </td>
                </tr>
              );
            })}
            {snapshot.overviewPending && !data && (
              <>
                {[0, 1, 2, 3, 4].map((index) => (
                  <tr key={`skeleton-${index}`} className="border-t" data-testid="session-skeleton">
                    <td colSpan={8} className="px-4 py-3">
                      <div className="h-4 w-full animate-pulse rounded bg-muted" />
                    </td>
                  </tr>
                ))}
              </>
            )}
            {!snapshot.overviewPending && conversations.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">
                  {activeChip && !activeChip.listed
                    ? unlistedProviderNote(activeChip)
                    : query || filter !== "all"
                      ? "No conversations match that search or filter."
                      : "No coding-agent conversations found on this Mac."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {snapshot.overviewPending && data && (
          <p
            className="flex items-center gap-2 border-t px-4 py-2 text-xs text-muted-foreground"
            data-testid="sessions-refreshing-note"
          >
            <RefreshCw className="h-3 w-3 animate-spin" />
            Re-reading this Mac's sessions — the list below is the previous answer
            {snapshot.overviewAt ? ` from ${formatWhen(snapshot.overviewAt)}` : ""}.
          </p>
        )}
        {snapshot.overviewCacheTruncated && (
          <p className="border-t px-4 py-2 text-xs text-muted-foreground">
            Only the most recent sessions were kept from the last visit; the full
            list appears when this read finishes.
          </p>
        )}
      </div>

      {totals?.index_limit_reached && (
        <p className="text-xs text-destructive">
          The agent's index has more than 250,000 records; some conversations may be
          missing from this list.
        </p>
      )}

      {totals && totals.unreadable > 0 && (
        <p className="text-xs text-muted-foreground">
          {totals.unreadable.toLocaleString()} of {totals.index_files_read.toLocaleString()} index
          files could not be read.
        </p>
      )}

      {cloud?.checked && (
        <p className="text-xs text-muted-foreground">
          AI Matrx holds {cloud.sessions.toLocaleString()} of these sessions · checked{" "}
          {formatStamp(cloud.checked_at)}
        </p>
      )}

      <SessionArtifactsDialog
        sessionId={artifactsDialogId}
        onClose={() => setArtifactsDialogId(null)}
      />
    </div>
  );
}
