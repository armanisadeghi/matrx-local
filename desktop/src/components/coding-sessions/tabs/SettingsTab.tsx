/**
 * Settings & diagnostics — the operational half of coding sessions.
 *
 * Everything on this tab used to sit ON TOP of the session list: readiness per
 * app, the delivery bridge's own queue and publisher ticks, the artifacts
 * lane, the accounts on this Mac, and the local agent runtime. None of it
 * answers "show me my conversation", so none of it belongs between a person
 * and their list (audit 2026-09-14, UI-05).
 */

import { useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { Badge, Button } from "@ai-matrx/design-system";
import { AgentRuntimeCard } from "@/components/coding-sessions/AgentRuntimeCard";
import type { DeliveryEvidenceFilter } from "@/components/coding-sessions/DeliveryEvidenceDialog";
import { QueueCount, RevealButton, formatStamp } from "@/components/coding-sessions/shared";
import { engine } from "@/lib/api";
import type { CodingSessionProvider } from "@/lib/api";
import { laneBlockerTone } from "@/lib/lane-blocker";
import { PROVIDER_LABELS } from "@/lib/coding-sessions/providers";
import type { CodingSessionsSnapshot } from "@/lib/coding-sessions/overview-store";
import { requestOrganizationPicker } from "@/lib/org/active-org";
import { formatCount, formatFileSize } from "@ai-matrx/kit/format";

const PROVIDERS: CodingSessionProvider[] = ["claude_code", "codex", "cursor", "vscode"];

export interface SettingsTabProps {
  snapshot: CodingSessionsSnapshot;
  refresh: () => Promise<void>;
  onOpenEvidence: (filter: DeliveryEvidenceFilter) => void;
}

export function SettingsTab({ snapshot, refresh, onOpenEvidence }: SettingsTabProps) {
  const [artifactsSyncing, setArtifactsSyncing] = useState(false);
  const [showAccounts, setShowAccounts] = useState(false);
  const [artifactsSyncError, setArtifactsSyncError] = useState<string | null>(null);

  const syncArtifactsNow = async () => {
    setArtifactsSyncing(true);
    setArtifactsSyncError(null);
    try {
      await engine.syncCodingSessionArtifacts();
      await refresh();
    } catch (nextError) {
      setArtifactsSyncError(
        nextError instanceof Error ? nextError.message : String(nextError),
      );
    } finally {
      setArtifactsSyncing(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
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
              const ready = snapshot.readiness?.providers?.[provider];
              const pending = snapshot.bridge?.pending.by_provider?.[provider] ?? 0;
              const failed = snapshot.bridge?.quarantine.by_provider?.[provider] ?? 0;
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
                      onOpen={() => onOpenEvidence({ state: "pending", provider })}
                    />
                  </td>
                  <td className="px-4 py-2 text-right">
                    <QueueCount
                      value={failed}
                      tone="text-destructive"
                      hint="Open the refused deliveries for this app, with the server's reason for each."
                      onOpen={() => onOpenEvidence({ state: "quarantine", provider })}
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
        {snapshot.bridge && (
          <p className="border-t px-4 py-2 text-xs text-muted-foreground">
            {formatCount(snapshot.bridge.pending.total)} deliver
            {snapshot.bridge.pending.total === 1 ? "y" : "ies"} waiting across all apps
            {snapshot.bridge.pending.item_count
              ? ` (${formatCount(snapshot.bridge.pending.item_count)} events, ${formatFileSize(snapshot.bridge.pending.payload_bytes)})`
              : ""}
            {snapshot.bridge.quarantine.total > 0
              ? ` · ${formatCount(snapshot.bridge.quarantine.total)} refused and preserved`
              : ""}
            {snapshot.bridge.publisher.active
              ? snapshot.bridge.publisher.blocker
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
        {snapshot.bridge?.publisher.ticks && (
          <p className="border-t px-4 py-2 text-xs text-muted-foreground">
            Publisher: last tick{" "}
            {formatStamp(snapshot.bridge.publisher.ticks.last_tick_at)} · sent{" "}
            {formatCount(snapshot.bridge.publisher.ticks.last_tick_sent)} · failed{" "}
            {formatCount(snapshot.bridge.publisher.ticks.last_tick_failed)} · eligible{" "}
            {snapshot.bridge.publisher.ticks.last_tick_eligible === null
              ? "not measured"
              : formatCount(snapshot.bridge.publisher.ticks.last_tick_eligible)}
            {/* The one field that explains a zero. */}
            {snapshot.bridge.publisher.ticks.last_tick_blocked
              ? ` · blocked: ${snapshot.bridge.publisher.ticks.last_tick_blocked}`
              : ""}
            {snapshot.bridge.publisher.transport_circuit.config.delivery_concurrency
              ? ` · concurrency ${snapshot.bridge.publisher.transport_circuit.config.delivery_concurrency}`
              : ""}
            {snapshot.bridge.publisher.ticks.last_error
              ? ` · ${snapshot.bridge.publisher.ticks.last_error.message}`
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
            disabled={artifactsSyncing || !snapshot.artifacts}
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

        {(snapshot.artifactsError ?? artifactsSyncError) && (
          <div
            className="flex items-start gap-3 border-b border-destructive/40 bg-destructive/10 p-4 text-sm"
            role="alert"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <div>
              <p className="font-medium">Couldn't read the artifacts lane</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">{snapshot.artifactsError ?? artifactsSyncError}</p>
            </div>
          </div>
        )}

        {!snapshot.artifacts && !snapshot.artifactsError && (
          <p className="px-4 py-3 text-sm text-muted-foreground">
            Asking the engine what it has kept…
          </p>
        )}

        {snapshot.artifacts && (
          <>
            {snapshot.artifacts.blocker && (
              <div
                className={`flex items-start gap-3 border-b p-4 text-sm ${laneBlockerTone(snapshot.artifacts.blocker.code).container}`}
                role={laneBlockerTone(snapshot.artifacts.blocker.code).role}
                data-testid="artifacts-blocker"
              >
                {laneBlockerTone(snapshot.artifacts.blocker.code).quiet ? (
                  <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                )}
                <div className="flex-1">
                  <p className="font-medium">
                    {laneBlockerTone(snapshot.artifacts.blocker.code).quiet
                      ? "Artifact publishing resumes as soon as your session is back"
                      : "Artifact publishing to AI Matrx is paused"}
                  </p>
                  <p className="mt-1">{snapshot.artifacts.blocker.message}</p>
                  {snapshot.artifacts.blocker.remedy && (
                    <p className="mt-1 text-muted-foreground">{snapshot.artifacts.blocker.remedy}</p>
                  )}
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    {snapshot.artifacts.blocker.code}
                    {snapshot.artifacts.blocker.since ? ` · since ${formatStamp(snapshot.artifacts.blocker.since)}` : ""}
                  </p>
                  {snapshot.artifacts.blocker.code === "no_organization" && (
                    <div className="mt-3">
                      <Button size="sm" onClick={() => requestOrganizationPicker()}>
                        Choose organization
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            )}

            {snapshot.artifacts.last_error && (
              <div className="flex items-start gap-3 border-b border-amber-500/40 bg-amber-500/10 p-4 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <div>
                  <p className="font-medium">The last artifacts pass hit an error</p>
                  <p className="mt-1 font-mono text-xs text-muted-foreground">
                    {snapshot.artifacts.last_error.code ? `${snapshot.artifacts.last_error.code} — ` : ""}
                    {snapshot.artifacts.last_error.message ?? JSON.stringify(snapshot.artifacts.last_error)}
                  </p>
                </div>
              </div>
            )}

            <p className="px-4 py-2 text-sm">
              <span className="tabular-nums">{formatCount(snapshot.artifacts.sessions)}</span> session
              {snapshot.artifacts.sessions === 1 ? "" : "s"} ·{" "}
              <span className="tabular-nums">{formatCount(snapshot.artifacts.files)}</span> file
              {snapshot.artifacts.files === 1 ? "" : "s"} · {formatFileSize(snapshot.artifacts.bytes)} ·{" "}
              <span
                className="text-emerald-600 dark:text-emerald-400"
                title={`Confirmed by reading the file id back from AI Matrx. AI Matrx holds ${formatCount(snapshot.artifacts.cloud_rows)} file(s) for these ${formatCount(snapshot.artifacts.files)} captured path(s): ${formatCount(snapshot.artifacts.deduplicated)} path(s) are byte-identical copies that share an existing file, and ${formatCount(snapshot.artifacts.superseded_versions)} earlier version(s) were superseded.`}
              >
                {formatCount(snapshot.artifacts.uploaded)} confirmed in AI Matrx
              </span>{" "}
              ·{" "}
              <span className="tabular-nums">
                {formatCount(snapshot.artifacts.cloud_rows)} file
                {snapshot.artifacts.cloud_rows === 1 ? "" : "s"} there
              </span>
              {snapshot.artifacts.deduplicated > 0 ? (
                <>
                  {" · "}
                  <span className="tabular-nums">
                    {formatCount(snapshot.artifacts.deduplicated)} share an identical file
                  </span>
                </>
              ) : null}
              {snapshot.artifacts.awaiting_confirmation > 0 ? (
                <>
                  {" · "}
                  <span className="tabular-nums">
                    {formatCount(snapshot.artifacts.awaiting_confirmation)} not read back yet
                  </span>
                </>
              ) : null}
              {" · "}
              <span className="tabular-nums">{formatCount(snapshot.artifacts.pending_upload)}</span> pending
              {" · "}
              <span className={snapshot.artifacts.missing_in_cloud > 0 ? "text-destructive" : "tabular-nums"}>
                {formatCount(snapshot.artifacts.missing_in_cloud)} missing in AI Matrx
              </span>
              {" · "}
              <span className={snapshot.artifacts.failed_upload > 0 ? "text-destructive" : "tabular-nums"}>
                {formatCount(snapshot.artifacts.failed_upload)} failed
              </span>
              {" · "}
              <span className={snapshot.artifacts.abandoned_upload > 0 ? "text-destructive" : "tabular-nums"}>
                {formatCount(snapshot.artifacts.abandoned_upload)} abandoned
              </span>
              {" · "}
              <span
                className="tabular-nums"
                title={`Skipped: ${formatCount(snapshot.artifacts.skipped_over_size)} over ${formatFileSize(snapshot.artifacts.limits.max_file_bytes)}, ${formatCount(snapshot.artifacts.skipped_over_count)} past the ${formatCount(snapshot.artifacts.limits.max_files_per_session)}-file per-session cap.`}
              >
                skipped {formatCount(snapshot.artifacts.skipped_over_size)} over size /{" "}
                {formatCount(snapshot.artifacts.skipped_over_count)} over count
              </span>
            </p>

            <p className="border-t px-4 py-2 text-xs text-muted-foreground">
              {snapshot.artifacts.active
                ? snapshot.artifacts.blocker
                  ? "Lane running · publishing paused"
                  : "Lane running"
                : "Lane stopped"}
              {snapshot.artifacts.cloud_enabled ? "" : " · cloud publishing off"}
              {" · last tick "}
              {"at" in snapshot.artifacts.last_tick
                ? `${formatStamp(snapshot.artifacts.last_tick.at)} · ${snapshot.artifacts.last_tick.seconds}s · captured ${formatCount(snapshot.artifacts.last_tick.captured)} · uploaded ${formatCount(snapshot.artifacts.last_tick.uploaded)} · failed ${formatCount(snapshot.artifacts.last_tick.failed)} · read back ${formatCount((snapshot.artifacts.last_tick.confirmed ?? 0))} · missing ${formatCount((snapshot.artifacts.last_tick.missing_in_cloud ?? 0))}`
                : "never"}
              {` · scans every ${snapshot.artifacts.limits.scan_interval_seconds}s`}
            </p>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-2 text-xs text-muted-foreground">
              <span className="min-w-0 break-all font-mono" title="Durable copies live here, outside /tmp, one folder per session.">
                {snapshot.artifacts.durable_root}
              </span>
              <RevealButton path={snapshot.artifacts.durable_root} />
            </div>
          </>
        )}
      </div>

      {snapshot.overview && (
        <div>
          <button
            type="button"
            className="flex items-center gap-1 text-sm font-medium"
            onClick={() => setShowAccounts((open: boolean) => !open)}
          >
            {showAccounts ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            Claude accounts on this Mac ({snapshot.overview.accounts.length})
          </button>
          {showAccounts && (
            <div className="mt-2 overflow-hidden rounded-lg border">
              {snapshot.overview.accounts.map((account) => (
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
                    {formatCount(account.conversations)} records
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}


      <AgentRuntimeCard />

      {snapshot.cacheError && (
        <p className="text-xs text-muted-foreground" role="status">
          {snapshot.cacheError} The list is still read fresh from this Mac every
          time you open the tab.
        </p>
      )}
    </div>
  );
}
