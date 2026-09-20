/**
 * One conversation, fully explained.
 *
 * THE RULE THIS SCREEN LIVES BY (Arman, 2026-09-08): anything reported as a
 * problem must open into the exact evidence it was judged on. This dialog is
 * that evidence for one row — the verdict and its remedy first, then every
 * fact from every system that touched the session: the server's binding, the
 * transcript on disk, Claude's own sidebar record, every queued or preserved
 * delivery envelope with its error and attempts (retry/discard right here),
 * the capture reconciler's attempts, and the label ledgers.
 *
 * ONE DIALOG, FOUR PROVIDERS. The row's own provider picks the route
 * (`/coding-session/sessions/{provider}/{id}/diagnosis`) and
 * `@/lib/coding-sessions/diagnosis-view` flattens whichever payload shape came
 * back, so nothing below branches on a provider string and no provider's prose
 * is written over another's facts. The sections a provider has none of are
 * NAMED in one block with a sentence each, never rendered as empty boxes, and
 * a size the provider does not keep is said in words, never as "0 B".
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Loader2,
  Pin,
  RefreshCw,
  RotateCw,
  Trash2,
} from "lucide-react";

import { Badge, Button } from "@ai-matrx/design-system";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { engine } from "@/lib/api";
import type {
  CodingSessionDiagnosis,
  CodingSessionProvider,
  CodingSessionState,
} from "@/lib/api";
import {
  codingSessionActionLabel,
  codingSessionSourceLabel,
  formatRetryDuration,
} from "@/lib/coding-session-ui";
import { formatFileSize } from "@ai-matrx/kit/format";
// THE ONE confirmation (`@ai-matrx/kit/confirm-opener` + design-system's
// `ConfirmDialogHost`, mounted in App/PanelApp). Never `window.confirm`.
import { confirm } from "@ai-matrx/kit/confirm-opener";
import { requestOrganizationPicker } from "@/lib/org/active-org";
import { laneBlockerTone } from "@/lib/lane-blocker";
import { diagnosisView } from "@/lib/coding-sessions/diagnosis-view";
import { noSizeSentence } from "@/lib/coding-sessions/providers";
import { SyncTruthSection } from "@/components/coding-sessions/SyncTruthSection";

export const SESSION_STATE_LABEL: Record<CodingSessionState, string> = {
  in_cloud: "In AI Matrx",
  changed: "Changed since delivery",
  queued: "Queued",
  failed: "Failed",
  not_in_cloud: "Not in AI Matrx",
  unknown: "Unknown",
};

export const SESSION_STATE_HINT: Record<CodingSessionState, string> = {
  in_cloud: "AI Matrx holds this conversation and it is up to date.",
  changed:
    "AI Matrx holds it, but Claude's last activity on it here is newer than the server's last delivery.",
  queued: "Not on the server yet — its events are waiting in this Mac's delivery queue.",
  failed:
    "AI Matrx refused a delivery for it; the envelope is preserved here and needs a decision.",
  not_in_cloud:
    "Nothing on the server and nothing queued — never mirrored by the hook and never imported.",
  unknown: "AI Matrx could not be asked, so this Mac cannot say whether it is in the cloud.",
};

export const SESSION_STATE_TONE: Record<CodingSessionState, string> = {
  in_cloud: "text-emerald-600 dark:text-emerald-400",
  changed: "text-amber-600 dark:text-amber-400",
  queued: "text-sky-600 dark:text-sky-400",
  failed: "text-destructive",
  not_in_cloud: "text-muted-foreground",
  unknown: "text-muted-foreground",
};

function when(value: string | number | null | undefined): string {
  if (!value) return "—";
  const ms = typeof value === "number" ? value : Date.parse(value);
  if (!Number.isFinite(ms)) return String(value);
  return new Date(ms).toLocaleString();
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[11rem_1fr] gap-3 border-b py-1.5 text-sm last:border-b-0">
      <div className="text-muted-foreground">{label}</div>
      <div className="min-w-0 break-words">{children}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border">
      <h3 className="border-b bg-muted/40 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      <div className="px-3 py-1">{children}</div>
    </section>
  );
}

export function SessionDiagnosisDialog({
  sessionId,
  provider,
  onClose,
  onChanged,
}: {
  sessionId: string | null;
  /** The provider that WROTE this session — never assumed to be Claude Code. */
  provider: CodingSessionProvider;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [data, setData] = useState<CodingSessionDiagnosis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyReceipt, setBusyReceipt] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      setData(await engine.getCodingSessionDiagnosis(provider, sessionId));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [provider, sessionId]);

  useEffect(() => {
    setData(null);
    void load();
  }, [load]);

  const mutate = async (receiptId: number, action: "retry" | "discard") => {
    setBusyReceipt(receiptId);
    setError(null);
    try {
      if (action === "retry") {
        await engine.retryCodingSessionDeliveryEnvelope(receiptId);
      } else {
        const preview = await engine.discardCodingSessionDeliveryEnvelope(receiptId, false);
        const impact = preview.impact;
        if (!impact) throw new Error("The engine did not return discard impact evidence.");
        const confirmed = await confirm({
          title: `Discard delivery #${impact.receipt_id}?`,
          description: `${impact.warning} Delivery #${impact.receipt_id} holds ${impact.item_count} event${
            impact.item_count === 1 ? "" : "s"
          } (${formatFileSize(impact.payload_bytes)}), and discarding removes them from this delivery for good. Claude's own transcript is untouched.`,
          confirmLabel: "Discard delivery",
          variant: "destructive",
        });
        if (!confirmed) return;
        await engine.discardCodingSessionDeliveryEnvelope(receiptId, true);
      }
      await Promise.all([load(), onChanged()]);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setBusyReceipt(null);
    }
  };

  const view = data === null ? null : diagnosisView(data);
  const blocker = view?.delivery.publisherBlocker ?? null;
  // Older engines omit every availability block; their established concrete
  // values remain valid rather than being retroactively called unavailable.
  const deliveryLedger = view?.delivery.ledger ?? null;
  const deliveryLedgerUnavailable = deliveryLedger?.checked === false;
  const captureUnavailable = view?.captureUnavailable === true;
  const labelsUnavailable = view?.labelsUnavailable === true;

  return (
    <Dialog open={Boolean(sessionId)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[88vh] max-w-5xl overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {view?.sidebar?.pinned && <Pin className="h-4 w-4 text-amber-500" />}
            {view?.title ?? "Conversation"}
          </DialogTitle>
          <DialogDescription>
            Every fact this Mac used to judge the row.
            {view ? ` ${view.label} session ` : " Session "}
            {sessionId}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-between gap-3">
          {view ? (
            <span className={`text-sm font-medium ${SESSION_STATE_TONE[view.state]}`}>
              {SESSION_STATE_LABEL[view.state]}
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">Loading…</span>
          )}
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            Refresh
          </Button>
        </div>

        {error && (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {view && (
          <div className="flex max-h-[68vh] flex-col gap-3 overflow-y-auto pr-1">
            {/* FIRST, because "does this match AI Matrx?" is the question the
                person opened this dialog with. Everything below explains the
                answer; this states it. */}
            {/* The content comparison reads Claude's own transcript store, so it
                exists for Claude Code alone; every other provider gets a named
                entry in the not-applicable block below instead of a dead box. */}
            {sessionId && view.hasSyncTruth && (
              <SyncTruthSection sessionId={sessionId} onChanged={onChanged} />
            )}

            <div
              className={`rounded-lg border p-3 text-sm ${
                view.state === "failed"
                  ? "border-destructive/40 bg-destructive/10"
                  : view.state === "in_cloud"
                    ? "border-emerald-500/40 bg-emerald-500/10"
                    : "border-amber-500/40 bg-amber-500/10"
              }`}
            >
              <p className="font-medium">{view.verdict.summary}</p>
              {view.verdict.remedy && (
                <p className="mt-1 text-muted-foreground">{view.verdict.remedy}</p>
              )}
              {blocker?.action === "choose_organization" && (
                <Button size="sm" className="mt-2" onClick={() => requestOrganizationPicker()}>
                  Choose organization
                </Button>
              )}
            </div>

            <Section title="AI Matrx (the server's own record)">
              <Row label="Server asked">
                {view.cloud.meta.checked
                  ? `Yes · ${when(view.cloud.meta.checked_at)} · ${view.cloud.boundCountSentence}`
                  : view.cloud.boundCountSentence}
              </Row>
              {view.cloud.binding ? (
                <>
                  <Row label="Conversation id"><span className="font-mono text-xs">{view.cloud.binding.conversation_id ?? "—"}</span></Row>
                  <Row label="Bound as"><span className="font-mono text-xs">{view.cloud.binding.provider_session_id}</span></Row>
                  <Row label="Fidelity">{view.cloud.binding.fidelity ?? "—"}</Row>
                  <Row label="Last delivery seen by server">{when(view.cloud.binding.last_seen_at)}</Row>
                  <Row label="Title on server">{view.cloud.binding.conversation_title ?? "—"}{view.cloud.binding.title_source ? ` (owned by: ${view.cloud.binding.title_source})` : ""}</Row>
                </>
              ) : (
                <Row label="Binding">{view.cloud.meta.checked ? "None — the server has never received this session." : "Unknown until the server can be asked."}</Row>
              )}
            </Section>

            {view.transcript && (
              <Section title="Record on this Mac">
                <Row label="On disk">
                  {view.transcript.onDisk
                    ? "Yes"
                    : "No — AI Matrx holds it and this Mac keeps no local copy"}
                </Row>
                <Row label="Size">
                  {view.transcript.bytes === null
                    ? noSizeSentence(view.label)
                    : formatFileSize(view.transcript.bytes)}
                </Row>
                {view.transcript.modifiedAt !== null && (
                  <Row label="Last modified">{when(view.transcript.modifiedAt)}</Row>
                )}
                {view.project !== null && <Row label="Project">{view.project}</Row>}
                {view.lastActivityAt !== null && (
                  <Row label="Last activity">{when(view.lastActivityAt)}</Row>
                )}
                {view.localNote && (
                  <Row label={`What ${view.label} cannot show`}>{view.localNote}</Row>
                )}
                {view.facts.map(([key, value]) => (
                  <Row key={key} label={key.replace(/_/g, " ")}>
                    {value === null || value === undefined ? "—" : String(value)}
                  </Row>
                ))}
              </Section>
            )}

            {view.continuation && (
              <Section title="How this session is reopened">
                <Row label="Command">
                  {view.continuation.command === null ? (
                    "None — no command reopens one of these chats."
                  ) : (
                    <span className="font-mono text-xs">{view.continuation.command}</span>
                  )}
                </Row>
                <Row label="What that means">{view.continuation.note}</Row>
              </Section>
            )}

            {view.sidebar && (
              <Section title="Claude's sidebar record">
                <Row label="Title">{view.sidebar.title ?? "—"}{view.sidebar.title_source ? ` (source: ${view.sidebar.title_source})` : ""}</Row>
                <Row label="Project">{view.sidebar.project ?? "—"}{view.sidebar.git_branch ? ` · ${view.sidebar.git_branch}` : ""}{view.sidebar.worktree_name ? ` · worktree ${view.sidebar.worktree_name}` : ""}</Row>
                <Row label="Pinned">{view.sidebar.pinned ? `Yes${view.sidebar.pinned_rank !== null ? ` · rank ${view.sidebar.pinned_rank}` : ""}` : "No"}</Row>
                <Row label="Category">{view.sidebar.category ?? "—"}</Row>
                <Row label="Archived">{view.sidebar.archived ? "Yes" : "No"}</Row>
                <Row label="Last activity">{when(view.sidebar.last_activity_at)}</Row>
                {view.sidebar.in_claude_sidebar ? (
                  <Row label="Index records">{view.sidebar.record_count} across {view.sidebar.accounts.length} account folder{view.sidebar.accounts.length === 1 ? "" : "s"}</Row>
                ) : (
                  <Row label="Claude sidebar">{view.sidebar.note ?? "No record — started from the command line."}</Row>
                )}
              </Section>
            )}

            <Section title="Delivery from this Mac">
              {deliveryLedgerUnavailable && (
                <div className="my-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
                  <p className="font-medium">This Mac could not read its delivery ledger</p>
                  <p className="mt-1 text-muted-foreground">
                    {deliveryLedger?.detail}
                  </p>
                </div>
              )}
              {blocker && (
                <div
                  className={
                    laneBlockerTone(blocker.code).quiet
                      ? "my-2 rounded-md border border-border bg-muted/50 p-2 text-sm"
                      : "my-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm"
                  }
                  role={laneBlockerTone(blocker.code).role}
                >
                  <p className="font-medium">
                    {laneBlockerTone(blocker.code).quiet
                      ? blocker.message
                      : `Everything is paused: ${blocker.message}`}
                  </p>
                  {blocker.remedy && <p className="text-muted-foreground">{blocker.remedy}</p>}
                </div>
              )}
              <Row label="Accepted via this Mac">
                {deliveryLedgerUnavailable
                  ? "Unavailable while this Mac's delivery ledger cannot be read."
                  : view.delivery.deliveredByThisMacAt
                    ? when(view.delivery.deliveredByThisMacAt)
                    : view.delivery.neverDeliveredSentence}
              </Row>
              {view.delivery.queue && (
                <Row label="Waiting in this Mac's queue">
                  {view.delivery.queue.pending} waiting · {view.delivery.queue.quarantined} refused and preserved
                </Row>
              )}
              {deliveryLedgerUnavailable || view.delivery.envelopes === null ? (
                <Row label="Deliveries">
                  Unavailable while this Mac's delivery ledger cannot be read.
                </Row>
              ) : view.delivery.envelopes.length === 0 ? (
                <Row label="Deliveries">None queued or preserved for this session.</Row>
              ) : (
                <div className="my-2 overflow-auto rounded-md border">
                  <table className="w-full min-w-[720px] text-xs">
                    <thead className="bg-muted/40 text-left">
                      <tr>
                        <th className="px-2 py-1">Receipt</th>
                        <th className="px-2 py-1">What</th>
                        <th className="px-2 py-1">Stored</th>
                        <th className="px-2 py-1">Delivery</th>
                        <th className="px-2 py-1">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {view.delivery.envelopes.map((item) => (
                        <tr key={item.receipt_id}>
                          <td className="px-2 py-1.5 align-top font-mono">#{item.receipt_id}</td>
                          <td className="px-2 py-1.5 align-top">
                            <div>{codingSessionActionLabel(item.action)}</div>
                            <div className="text-muted-foreground">{codingSessionSourceLabel(item.source)} · {item.item_count} event{item.item_count === 1 ? "" : "s"} · {formatFileSize(item.payload_bytes)}</div>
                          </td>
                          <td className="px-2 py-1.5 align-top whitespace-nowrap">{when(item.created_at)}</td>
                          <td className="px-2 py-1.5 align-top">
                            <Badge variant={item.state === "quarantine" ? "destructive" : "outline"}>
                              {item.state === "quarantine" ? "Refused, preserved" : "Waiting"}
                            </Badge>
                            <div className="mt-1 text-muted-foreground">
                              {item.attempts} attempt{item.attempts === 1 ? "" : "s"}
                              {item.state === "pending" && (item.retry_in_seconds > 0 ? ` · retry in ${formatRetryDuration(item.retry_in_seconds)}` : " · eligible now")}
                              {item.http_status !== null ? ` · HTTP ${item.http_status}` : ""}
                              {item.quarantined_at ? ` · preserved ${when(item.quarantined_at)}` : ""}
                            </div>
                            {item.error && (
                              <div className="mt-1 max-w-80 text-destructive">
                                <span className="font-mono">{item.error.code}</span> — {item.error.message}
                              </div>
                            )}
                          </td>
                          <td className="px-2 py-1.5 align-top">
                            <div className="flex gap-1">
                              <Button variant="outline" size="sm" disabled={busyReceipt !== null} onClick={() => void mutate(item.receipt_id, "retry")}>
                                <RotateCw className="mr-1 h-3.5 w-3.5" />Retry
                              </Button>
                              <Button variant="ghost" size="sm" disabled={busyReceipt !== null} onClick={() => void mutate(item.receipt_id, "discard")}>
                                <Trash2 className="mr-1 h-3.5 w-3.5" />Discard
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>

            <Section title="Automatic import attempts (capture reconciler)">
              {captureUnavailable || view.capture === null ? (
                <Row label="Attempts">
                  Unavailable while this Mac's session diagnostics cannot be read.
                </Row>
              ) : view.capture.length === 0 ? (
                <Row label="Attempts">None recorded — the reconciler has not needed to import this session.</Row>
              ) : (
                view.capture.map((row) => (
                  <Row key={row.session_key} label={`${row.attempts} attempt${row.attempts === 1 ? "" : "s"}`}>
                    {row.last_error ? <span className="text-destructive">{row.last_error}</span> : "No error"}
                    <span className="text-muted-foreground"> · queued {when(row.enqueued_at)} · updated {when(row.updated_at)}</span>
                  </Row>
                ))
              )}
            </Section>

            {(view.labels !== null || labelsUnavailable) && (
              <Section title="Label sync ledgers">
                {labelsUnavailable || view.labels === null ? (
                  <Row label="Label records">
                    Unavailable while this Mac's session diagnostics cannot be read.
                  </Row>
                ) : (
                  <>
                    <Row label="Labels sent to AI Matrx">{view.labels.metadata_sent ? `Yes · ${when(String(view.labels.metadata_sent.updated_at ?? ""))}` : "Not yet (sent only once the server holds the session)"}</Row>
                    <Row label="AI Matrx title pushed into Claude">{view.labels.title_pushed ? `Yes · ${when(String(view.labels.title_pushed.updated_at ?? ""))}` : "No"}</Row>
                  </>
                )}
              </Section>
            )}

            {/* Law 4: a section this provider has none of is NAMED with its
                reason, never left as an empty box a person has to interpret. */}
            {view.notApplicable.length > 0 && (
              <Section title={`What ${view.label} has none of`}>
                <div data-testid="diagnosis-not-applicable">
                  {view.notApplicable.map((item) => (
                    <Row key={item.title} label={item.title}>
                      {item.sentence}
                    </Row>
                  ))}
                </div>
              </Section>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
