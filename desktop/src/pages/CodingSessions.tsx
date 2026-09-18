/**
 * Coding sessions — ONE feature, three tabs, and every row a door.
 *
 * "Coding sessions is one feature and everything needs to live inside of it"
 * (Arman, 2026-09-14). This page is the shell: the header with a Refresh that
 * announces itself, the delivery blocker that stops every provider, the tab
 * bar, and the one dialog host both tabs share. The tabs own their own
 * content (`components/coding-sessions/tabs/`); this file owns nothing else.
 *
 * Status is judged against the CLOUD, not against what this Mac uploaded.
 * Most coding-agent sessions reach AI Matrx straight from the CLI hook, so a
 * screen that only knew its own uploads said "Not synced" for everything.
 */

import { useCallback, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { AlertTriangle, CloudUpload, Loader2, RefreshCw } from "lucide-react";

import {
  DeliveryEvidenceDialog,
  type DeliveryEvidenceFilter,
} from "@/components/coding-sessions/DeliveryEvidenceDialog";
import { SessionDiagnosisDialog } from "@/components/coding-sessions/SessionDiagnosisDialog";
import { SessionsTab } from "@/components/coding-sessions/tabs/SessionsTab";
import { SettingsTab } from "@/components/coding-sessions/tabs/SettingsTab";
import { UsageTab } from "@/components/coding-sessions/tabs/UsageTab";
import { formatStamp, formatWhen } from "@/components/coding-sessions/shared";
import { Button } from "@ai-matrx/design-system";
import { useCodingSessions } from "@/hooks/use-coding-sessions";
import { engine, type ClaudeSyncResult, type CodingSessionProvider } from "@/lib/api";
import {
  CODING_SESSIONS_TABS,
  CODING_SESSIONS_TAB_LABEL,
  parseCodingSessionsTab,
  type CodingSessionsTab,
} from "@/lib/coding-sessions/tabs";
import {
  cloudHeaderText,
  indexNotice as readIndexNotice,
} from "@/lib/coding-sessions/index-state";
import { requestOrganizationPicker } from "@/lib/org/active-org";
import { laneBlockerTone } from "@/lib/lane-blocker";

export function CodingSessions() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const tab = parseCodingSessionsTab(searchParams);
  const { snapshot, refresh } = useCodingSessions();
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [result, setResult] = useState<ClaudeSyncResult | null>(null);
  const [resuming, setResuming] = useState(false);
  const [evidence, setEvidence] = useState<DeliveryEvidenceFilter | null>(null);
  // The diagnosis dialog is opened WITH the row's provider: the diagnosis
  // route is provider-aware, and sending a Cursor chat down the Claude alias
  // is how Claude's prose got written over another provider's facts.
  const [diagnosis, setDiagnosis] = useState<
    { sessionId: string; provider: CodingSessionProvider } | null
  >(null);

  const selectTab = useCallback(
    (next: CodingSessionsTab) => {
      navigate(`/coding-sessions?tab=${next}`, { replace: true });
    },
    [navigate],
  );

  const syncEverything = async () => {
    setSyncing(true);
    setSyncError(null);
    setResult(null);
    try {
      setResult(await engine.syncClaudeEverything());
      await refresh();
    } catch (nextError) {
      setSyncError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setSyncing(false);
    }
  };

  const resumeDelivery = async () => {
    setResuming(true);
    setSyncError(null);
    try {
      await engine.resumeCodingSessionDelivery();
      await refresh();
    } catch (nextError) {
      setSyncError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setResuming(false);
    }
  };

  const totals = snapshot.overview?.totals;
  const cloud = snapshot.overview?.cloud;
  const blocker = snapshot.bridge?.publisher.blocker ?? null;
  const indexNotice = readIndexNotice(snapshot.overview);
  // The engine refreshing its index behind an answer is work in progress too,
  // so the header's Refresh wears the same announced-refresh state it wears
  // for this screen's own read (CS-11) — never a still button over moving data.
  const busy = snapshot.refreshing || indexNotice !== null;

  return (
    // AppLayout mounts every page inside `overflow-hidden`, so a page that does
    // not own its own scroll region is silently clipped at the viewport.
    <div className="flex h-full flex-col overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
        <div>
          <h1 className="text-2xl font-semibold">Coding Sessions</h1>
          <p className="text-sm text-muted-foreground" data-testid="coding-sessions-subtitle">
            {(snapshot.overviewPending && !snapshot.overview) || indexNotice?.state === "cold"
              ? // A cold index has nothing to count yet, so it is never counted:
                // "0 conversations on this Mac" was a lie the engine's own
                // `index.state` now spares this screen.
                indexNotice?.state === "cold"
                ? `${indexNotice.headline} ${indexNotice.detail ?? ""}`.trim()
                : "Reading your conversations and asking AI Matrx what it holds…"
              : `${(totals?.conversations ?? 0).toLocaleString()} conversations on this Mac · ${
                  snapshot.overview?.accounts.length ?? 0
                } accounts · ${cloudHeaderText(cloud, formatStamp)}${
                  snapshot.overviewPending
                    ? " · refreshing…"
                    : snapshot.overviewFromCache && snapshot.overviewAt
                      ? ` · last read ${formatWhen(snapshot.overviewAt)}`
                      : ""
                }`}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => void refresh()}
            disabled={snapshot.refreshing}
            data-testid="coding-sessions-refresh"
            aria-busy={busy}
          >
            <RefreshCw className={`mr-2 h-4 w-4 ${busy ? "animate-spin" : ""}`} />
            {busy ? "Refreshing…" : "Refresh"}
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

      <nav
        className="flex gap-1 border-b px-6"
        aria-label="Coding sessions sections"
        data-testid="coding-sessions-tabs"
      >
        {CODING_SESSIONS_TABS.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={tab === item}
            data-testid={`coding-sessions-tab-${item}`}
            onClick={() => selectTab(item)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm transition-colors ${
              tab === item
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {CODING_SESSIONS_TAB_LABEL[item]}
          </button>
        ))}
      </nav>

      <div data-testid="coding-sessions-scroll" className="flex-1 overflow-y-auto">
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

          {syncError && (
            <div
              className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm"
              role="alert"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
              <p className="font-mono text-xs">{syncError}</p>
            </div>
          )}

          {result && !result.started && result.blocked_reason && (
            <div className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <p>{result.blocked_reason}</p>
            </div>
          )}

          {result?.started && (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm">
              <p>
                Queued {result.conversations.toLocaleString()} conversations for delivery.
                {result.failed.length > 0 &&
                  ` ${result.failed.length} batch${
                    result.failed.length === 1 ? "" : "es"
                  } failed — see below.`}
              </p>
              {result.failed.map((failure) => (
                <p key={failure.sessions} className="mt-2 font-mono text-xs text-destructive">
                  {failure.reason}
                </p>
              ))}
            </div>
          )}

          {tab === "sessions" && (
            <SessionsTab
              snapshot={snapshot}
              onOpenDiagnosis={(sessionId, provider) => setDiagnosis({ sessionId, provider })}
            />
          )}
          {tab === "usage" && <UsageTab snapshot={snapshot} />}
          {tab === "settings" && (
            <SettingsTab snapshot={snapshot} refresh={refresh} onOpenEvidence={setEvidence} />
          )}
        </div>
      </div>

      <DeliveryEvidenceDialog
        filter={evidence}
        onClose={() => setEvidence(null)}
        onChanged={refresh}
      />
      <SessionDiagnosisDialog
        sessionId={diagnosis?.sessionId ?? null}
        provider={diagnosis?.provider ?? "claude_code"}
        onClose={() => setDiagnosis(null)}
        onChanged={refresh}
      />
    </div>
  );
}
