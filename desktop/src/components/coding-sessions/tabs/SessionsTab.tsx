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
  ExternalLink,
  Loader2,
  Pin,
  Play,
  RefreshCw,
  Stethoscope,
} from "lucide-react";

import { Badge, Button } from "@ai-matrx/design-system";
import {
  MatrxDataTable,
  type MatrxColumnDef,
  type MatrxDataTableQueryState,
} from "@ai-matrx/design-system/data-table";
import {
  ArtifactsCell,
  SessionArtifactsDialog,
} from "@/components/coding-sessions/SessionArtifactsDialog";
import { ContinueSessionDialog } from "@/components/coding-sessions/ContinueSessionDialog";
import {
  SESSION_STATE_HINT,
  SESSION_STATE_LABEL,
  SESSION_STATE_TONE,
} from "@/components/coding-sessions/SessionDiagnosisDialog";
import { Stat, formatStamp, formatWhen } from "@/components/coding-sessions/shared";
import type { ClaudeConversation, ClaudeSessionState, CodingSessionProvider } from "@/lib/api";
import { getWebAppOrigin } from "@/lib/app-config";
import {
  cloudAgeLabel,
  cloudCheckPending,
  indexCountsAreReal,
  indexNotice,
} from "@/lib/coding-sessions/index-state";
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

const INITIAL_TABLE_QUERY: MatrxDataTableQueryState = {
  page: 1,
  pageSize: 0,
  search: "",
  anyOf: "",
  columnFilters: {},
  sort: null,
};

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
  const [tableQuery, setTableQuery] = useState<MatrxDataTableQueryState>(INITIAL_TABLE_QUERY);
  const [filter, setFilter] = useState<ListFilter>("all");
  const [provider, setProvider] = useState<CodingSessionProvider | null>(null);
  const [artifactsDialogId, setArtifactsDialogId] = useState<string | null>(null);
  // The row whose native continue is open. A real continue needs a prompt and
  // shows live status, so it is a dialog, not a one-click copy (lane XT-04).
  const [continueRow, setContinueRow] = useState<ClaudeConversation | null>(null);
  const [blocked, setBlocked] = useState<{ title: string; reason: string; sessionId: string } | null>(
    null,
  );

  const data = snapshot.overview;
  const cloud = data?.cloud;
  const deliveryLedger = data?.delivery_ledger;
  const deliveryLedgerUnavailable = deliveryLedger?.checked === false;
  const notice = indexNotice(data);
  const cloudPending = cloudCheckPending(cloud);
  // Counts belong to an index that has been read. While it is cold they are
  // zeroes about nothing, so the cards stay away instead of reporting them.
  const totals = indexCountsAreReal(data) ? data?.totals : undefined;
  const chips = useMemo(
    () => providerChips(data, snapshot.readiness),
    [data, snapshot.readiness],
  );
  const activeChip = provider ? chips.find((chip) => chip.provider === provider) ?? null : null;

  const conversations = useMemo(() => {
    const rows = filterRowsByProvider(data?.conversations ?? [], data, provider);
    return rows.filter((row) => {
      if (filter === "pinned" && !row.pinned) return false;
      if (filter !== "all" && filter !== "pinned" && row.state !== filter) return false;
      return true;
    });
  }, [data, filter, provider]);

  /**
   * "Unknown" earns its warning voice only when the server COULD NOT be asked.
   * While the check is merely in flight the row says "Checking…" quietly —
   * the next read replaces it with the real answer.
   */
  const quietUnknown = (row: ClaudeConversation) => cloudPending && row.state === "unknown";

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

  const columns: MatrxColumnDef<ClaudeConversation>[] = [
    {
      id: "pinned",
      header: <Pin className="mx-auto h-3.5 w-3.5" />,
      label: "Pinned",
      sortable: true,
      filter: false,
      width: 48,
      sortValue: (row) => row.pinned,
      cell: (row) =>
        row.pinned ? (
          <Pin
            className="mx-auto h-3.5 w-3.5 text-amber-500"
            aria-label="Pinned in the coding agent"
          />
        ) : null,
    },
    {
      id: "conversation",
      header: "Conversation",
      sortable: true,
      filter: false,
      width: 360,
      sortValue: (row) => row.title,
      cell: (row) => (
        <div className="max-w-md truncate">
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
        </div>
      ),
    },
    {
      id: "project",
      header: "Project",
      sortable: true,
      filter: false,
      width: 160,
      sortValue: (row) => row.project ?? "",
      cell: (row) => <span className="text-muted-foreground">{row.project ?? "—"}</span>,
    },
    {
      id: "updated",
      header: "Updated",
      sortable: true,
      defaultSortDirection: "desc",
      filter: false,
      width: 112,
      sortValue: (row) => row.last_activity_at,
      cell: (row) => (
        <span className="whitespace-nowrap tabular-nums text-muted-foreground">
          {formatWhen(row.last_activity_at)}
        </span>
      ),
    },
    {
      id: "size",
      header: "Size",
      sortable: true,
      filter: false,
      width: 88,
      sortValue: (row) => row.bytes,
      cell: (row) => (
        <span className="whitespace-nowrap tabular-nums text-muted-foreground">
          {formatFileSize(row.bytes)}
        </span>
      ),
    },
    {
      id: "artifacts",
      header: "Artifacts",
      sortable: false,
      filter: false,
      width: 112,
      cell: (row) => (
        <ArtifactsCell
          sessions={snapshot.artifactSessions}
          error={snapshot.artifactSessionsError}
          sessionId={row.session_id}
          onOpen={() => setArtifactsDialogId(row.session_id)}
        />
      ),
    },
    {
      id: "state",
      header: "In AI Matrx?",
      sortable: true,
      filter: false,
      width: 144,
      sortValue: (row) => row.state,
      cell: (row) => (
        <span
          className={quietUnknown(row) ? "text-muted-foreground" : SESSION_STATE_TONE[row.state]}
          title={
            quietUnknown(row)
              ? "AI Matrx has not been asked yet. The next read has the answer."
              : SESSION_STATE_HINT[row.state]
          }
          data-testid={quietUnknown(row) ? "state-checking" : undefined}
        >
          {quietUnknown(row) ? "Checking…" : SESSION_STATE_LABEL[row.state]}
          {row.delivery.quarantined !== null && row.delivery.quarantined > 0 && (
            <span className="ml-1 text-xs">({row.delivery.quarantined} refused)</span>
          )}
          {row.state === "queued" && row.delivery.pending !== null && row.delivery.pending > 0 && (
            <span className="ml-1 text-xs">({row.delivery.pending} waiting)</span>
          )}
        </span>
      ),
    },
  ];

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
            {snapshot.overviewRetry === "scheduled" && (
              <p className="mt-1 text-muted-foreground" data-testid="sessions-retry-scheduled">
                Trying this read once more shortly.
              </p>
            )}
            {snapshot.overviewRetry === "exhausted" && (
              <p className="mt-1 text-muted-foreground" data-testid="sessions-retry-exhausted">
                The automatic retry did not finish. You can refresh when ready.
              </p>
            )}
            {data && (
              <p className="mt-1 text-muted-foreground">
                The list below is the last answer this Mac read
                {snapshot.overviewAt ? ` (${formatWhen(snapshot.overviewAt)})` : ""}.
              </p>
            )}
          </div>
        </div>
      )}

      {/* A first read of this Mac is not an empty Mac. While the engine's index
          is cold there is nothing to count, so nothing is counted: the counter
          moves and the rows arrive. */}
      {notice?.state === "cold" && (
        <div
          className="flex items-start gap-3 rounded-lg border p-4 text-sm"
          role="status"
          data-testid="index-cold"
        >
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
          <div>
            <p className="font-medium">{notice.headline}</p>
            {notice.detail && <p className="mt-1 text-muted-foreground">{notice.detail}</p>}
          </div>
        </div>
      )}

      {cloudPending && (
        // Not asked YET is not a failure: quiet voice, no warning colour, and
        // it clears itself on the next read.
        <div
          className="flex items-start gap-3 rounded-lg border p-4 text-sm"
          role="status"
          data-testid="cloud-in-flight"
        >
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
          <div>
            <p className="font-medium">
              AI Matrx has not been asked yet which conversations it holds
            </p>
            <p className="mt-1 text-muted-foreground">
              {cloud?.detail ??
                "The answer lands within a few seconds — until it does, these rows say so rather than guessing."}
            </p>
          </div>
        </div>
      )}

      {cloud && !cloud.checked && !cloudPending && (
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

      {deliveryLedgerUnavailable && (
        <div
          className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm"
          role="alert"
          data-testid="delivery-ledger-unavailable"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div>
            <p className="font-medium">This Mac could not read its delivery ledger</p>
            <p className="mt-1">{deliveryLedger.detail}</p>
            <p className="mt-1 text-muted-foreground">
              Waiting and refused delivery counts are unavailable until it can be read again.
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
          {STATE_CARDS.map((state) => {
            const value = totals[state];
            if (value === null) return null;
            return (
              <Stat
                key={state}
                value={value}
                label={SESSION_STATE_LABEL[state]}
                hint={`${SESSION_STATE_HINT[state]} Click to show only these.`}
                tone={value > 0 ? SESSION_STATE_TONE[state] : undefined}
                selected={filter === state}
                onClick={() => toggleFilter(state)}
              />
            );
          })}
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

      <div className="min-w-0" data-testid="sessions-table">
        <MatrxDataTable
          data={conversations}
          columns={columns}
          getRowId={(row) => row.session_id}
          searchText={(row) => [row.title, row.project, row.category].filter(Boolean).join(" ")}
          query={{
            mode: "controlled-local",
            state: tableQuery,
            onStateChange: setTableQuery,
          }}
          isLoading={snapshot.overviewPending && !data}
          isFetching={snapshot.overviewPending && Boolean(data)}
          hidePagination
          detail={{ enabled: false }}
          copy={false}
          onRowOpen={openRow}
          rowClassName={() =>
            snapshot.overviewPending ? "opacity-50 transition-opacity" : undefined
          }
          toolbar={{
            searchPlaceholder: `Search ${conversations.length.toLocaleString()} conversation${
              conversations.length === 1 ? "" : "s"
            }`,
            leading: (
              <>
                {filter !== "all" && (
                  <Button variant="outline" size="sm" onClick={() => setFilter("all")}>
                    Showing{" "}
                    {filter === "pinned"
                      ? "pinned"
                      : SESSION_STATE_LABEL[filter].toLowerCase()} · clear
                  </Button>
                )}
                {snapshot.overviewFromCache && (
                  <Badge variant="outline" data-testid="sessions-cached">
                    Last read {snapshot.overviewAt ? formatWhen(snapshot.overviewAt) : "earlier"}
                    {snapshot.overviewPending ? " · refreshing" : ""}
                  </Badge>
                )}
              </>
            ),
          }}
          emptyState={{
            ...(notice?.state === "cold"
              ? {
                  icon: (
                    <Loader2
                      className="h-4 w-4 animate-spin"
                      data-testid="sessions-empty-cold"
                    />
                  ),
                  ...(notice.detail ? { description: notice.detail } : {}),
                }
              : {}),
            title:
              notice?.state === "cold"
                ? notice.headline
                : activeChip && !activeChip.listed
                  ? unlistedProviderNote(activeChip)
                  : tableQuery.search || filter !== "all"
                    ? "No conversations match that search or filter."
                    : "No coding-agent conversations found on this Mac.",
          }}
          rowActions={(row) => {
            const target = resolveSessionConversation(row);
            const continuation = row.continuation ?? null;
            return (
              <>
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
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    title="Continue this session with a new turn on this Mac — or see exactly why that is not possible here, with the resume command to copy."
                    onClick={() => setContinueRow(row)}
                  >
                    <Play className="h-3.5 w-3.5" />
                    <span className="ml-1.5 hidden text-xs xl:inline">Continue</span>
                  </Button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  title="Every delivery fact behind this row's status: the server's binding, the transcript on disk, each envelope and its error."
                  onClick={() => onOpenDiagnosis(row.session_id)}
                >
                  <Stethoscope className="h-3.5 w-3.5" />
                  <span className="ml-1.5 hidden text-xs xl:inline">Delivery</span>
                </Button>
              </>
            );
          }}
        />
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
        {!snapshot.overviewPending && notice?.state === "refreshing" && (
          // The engine is re-reading behind the answer we are showing. Say so,
          // with what it is actually doing when it knows.
          <p
            className="flex items-center gap-2 border-t px-4 py-2 text-xs text-muted-foreground"
            data-testid="index-refreshing-note"
          >
            <RefreshCw className="h-3 w-3 animate-spin" />
            {notice.headline}
            {notice.detail ? ` ${notice.detail}.` : ""}
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
        <p className="text-xs text-muted-foreground" data-testid="cloud-checked-note">
          AI Matrx holds {cloud.sessions.toLocaleString()} of these sessions ·{" "}
          {cloudAgeLabel(cloud) ?? `checked ${formatStamp(cloud.checked_at)}`}
          {cloud.refreshing ? " · asking again now" : ""}
        </p>
      )}

      <SessionArtifactsDialog
        sessionId={artifactsDialogId}
        onClose={() => setArtifactsDialogId(null)}
      />

      <ContinueSessionDialog row={continueRow} onClose={() => setContinueRow(null)} />
    </div>
  );
}
