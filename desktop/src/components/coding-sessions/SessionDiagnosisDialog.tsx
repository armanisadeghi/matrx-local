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
import type { ClaudeSessionDiagnosis, ClaudeSessionState } from "@/lib/api";
import {
  codingSessionActionLabel,
  codingSessionSourceLabel,
  formatRetryDuration,
} from "@/lib/coding-session-ui";
import { formatFileSize } from "@ai-matrx/kit/format";
import { requestOrganizationPicker } from "@/lib/org/active-org";

export const SESSION_STATE_LABEL: Record<ClaudeSessionState, string> = {
  in_cloud: "In AI Matrx",
  changed: "Changed since delivery",
  queued: "Queued",
  failed: "Failed",
  not_in_cloud: "Not in AI Matrx",
  unknown: "Unknown",
};

export const SESSION_STATE_HINT: Record<ClaudeSessionState, string> = {
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

export const SESSION_STATE_TONE: Record<ClaudeSessionState, string> = {
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
  onClose,
  onChanged,
}: {
  sessionId: string | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [data, setData] = useState<ClaudeSessionDiagnosis | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyReceipt, setBusyReceipt] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      setData(await engine.getClaudeSessionDiagnosis(sessionId));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

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
        const confirmed = window.confirm(
          `${impact.warning}\n\nDelivery #${impact.receipt_id} holds ${impact.item_count} event${
            impact.item_count === 1 ? "" : "s"
          } (${formatFileSize(impact.payload_bytes)}). Claude's own transcript is untouched.`,
        );
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

  const blocker = data?.delivery.publisher_blocker ?? null;

  return (
    <Dialog open={Boolean(sessionId)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[88vh] max-w-5xl overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {data?.index.pinned && <Pin className="h-4 w-4 text-amber-500" />}
            {data?.index.title ?? "Conversation"}
          </DialogTitle>
          <DialogDescription>
            Every fact this Mac used to judge the row. Session {sessionId}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center justify-between gap-3">
          {data ? (
            <span className={`text-sm font-medium ${SESSION_STATE_TONE[data.state]}`}>
              {SESSION_STATE_LABEL[data.state]}
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

        {data && (
          <div className="flex max-h-[68vh] flex-col gap-3 overflow-y-auto pr-1">
            <div
              className={`rounded-lg border p-3 text-sm ${
                data.state === "failed"
                  ? "border-destructive/40 bg-destructive/10"
                  : data.state === "in_cloud"
                    ? "border-emerald-500/40 bg-emerald-500/10"
                    : "border-amber-500/40 bg-amber-500/10"
              }`}
            >
              <p className="font-medium">{data.verdict.summary}</p>
              {data.verdict.remedy && (
                <p className="mt-1 text-muted-foreground">{data.verdict.remedy}</p>
              )}
              {blocker?.code === "organization_not_chosen" && (
                <Button size="sm" className="mt-2" onClick={() => requestOrganizationPicker()}>
                  Choose organization
                </Button>
              )}
            </div>

            <Section title="AI Matrx (the server's own record)">
              <Row label="Server asked">
                {data.cloud.checked
                  ? `Yes · ${when(data.cloud.checked_at)} · ${data.cloud.sessions.toLocaleString()} of your Claude Code sessions are bound there`
                  : `No — ${data.cloud.detail ?? data.cloud.reason ?? "unknown reason"}`}
              </Row>
              {data.cloud.binding ? (
                <>
                  <Row label="Conversation id"><span className="font-mono text-xs">{data.cloud.binding.conversation_id ?? "—"}</span></Row>
                  <Row label="Bound as"><span className="font-mono text-xs">{data.cloud.binding.provider_session_id}</span></Row>
                  <Row label="Fidelity">{data.cloud.binding.fidelity ?? "—"}</Row>
                  <Row label="Last delivery seen by server">{when(data.cloud.binding.last_seen_at)}</Row>
                  <Row label="Title on server">{data.cloud.binding.conversation_title ?? "—"}{data.cloud.binding.title_source ? ` (owned by: ${data.cloud.binding.title_source})` : ""}</Row>
                </>
              ) : (
                <Row label="Binding">{data.cloud.checked ? "None — the server has never received this session." : "Unknown until the server can be asked."}</Row>
              )}
            </Section>

            <Section title="Transcript on this Mac">
              <Row label="On disk">{data.transcript.on_disk ? "Yes" : "No — only the sidebar record remains"}</Row>
              <Row label="Size">{formatFileSize(data.transcript.bytes)}</Row>
              <Row label="Last modified">{when(data.transcript.modified_at)}</Row>
            </Section>

            <Section title="Claude's sidebar record">
              <Row label="Title">{data.index.title ?? "—"}{data.index.title_source ? ` (source: ${data.index.title_source})` : ""}</Row>
              <Row label="Project">{data.index.project ?? "—"}{data.index.git_branch ? ` · ${data.index.git_branch}` : ""}{data.index.worktree_name ? ` · worktree ${data.index.worktree_name}` : ""}</Row>
              <Row label="Pinned">{data.index.pinned ? `Yes${data.index.pinned_rank !== null ? ` · rank ${data.index.pinned_rank}` : ""}` : "No"}</Row>
              <Row label="Category">{data.index.category ?? "—"}</Row>
              <Row label="Archived">{data.index.archived ? "Yes" : "No"}</Row>
              <Row label="Last activity">{when(data.index.last_activity_at)}</Row>
              <Row label="Index records">{data.index.record_count} across {data.index.accounts.length} account folder{data.index.accounts.length === 1 ? "" : "s"}</Row>
            </Section>

            <Section title="Delivery from this Mac">
              {blocker && (
                <div className="my-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
                  <p className="font-medium">Everything is paused: {blocker.message}</p>
                  {blocker.remedy && <p className="text-muted-foreground">{blocker.remedy}</p>}
                </div>
              )}
              <Row label="Accepted via this Mac">{data.delivery.delivered_by_this_mac_at ? when(data.delivery.delivered_by_this_mac_at) : "Never (a hook-mirrored session is delivered by Claude Code itself, not by this Mac)"}</Row>
              {data.delivery.envelopes.length === 0 ? (
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
                      {data.delivery.envelopes.map((item) => (
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
              {data.capture.length === 0 ? (
                <Row label="Attempts">None recorded — the reconciler has not needed to import this session.</Row>
              ) : (
                data.capture.map((row) => (
                  <Row key={row.session_key} label={`${row.attempts} attempt${row.attempts === 1 ? "" : "s"}`}>
                    {row.last_error ? <span className="text-destructive">{row.last_error}</span> : "No error"}
                    <span className="text-muted-foreground"> · queued {when(row.enqueued_at)} · updated {when(row.updated_at)}</span>
                  </Row>
                ))
              )}
            </Section>

            <Section title="Label sync ledgers">
              <Row label="Labels sent to AI Matrx">{data.labels.metadata_sent ? `Yes · ${when(String(data.labels.metadata_sent.updated_at ?? ""))}` : "Not yet (sent only once the server holds the session)"}</Row>
              <Row label="AI Matrx title pushed into Claude">{data.labels.title_pushed ? `Yes · ${when(String(data.labels.title_pushed.updated_at ?? ""))}` : "No"}</Row>
            </Section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
