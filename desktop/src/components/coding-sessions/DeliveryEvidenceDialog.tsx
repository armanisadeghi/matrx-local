import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, RotateCw, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { engine } from "@/lib/api";
import type { CodingSessionDeliveryEnvelopePage, CodingSessionProvider } from "@/lib/api";
import { codingSessionActionLabel, codingSessionSourceLabel, formatRetryDuration } from "@/lib/coding-session-ui";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

export interface DeliveryEvidenceFilter {
  state: "pending" | "quarantine";
  provider?: CodingSessionProvider;
  action?: string;
  source?: string;
}

export function DeliveryEvidenceDialog({
  filter,
  onClose,
  onChanged,
}: {
  filter: DeliveryEvidenceFilter | null;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [page, setPage] = useState<CodingSessionDeliveryEnvelopePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyReceipt, setBusyReceipt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (afterReceiptId?: number, append = false) => {
    if (!filter) return;
    setLoading(true);
    setError(null);
    try {
      const next = await engine.getCodingSessionDeliveryEnvelopes({
        ...filter,
        limit: 50,
        ...(afterReceiptId === undefined ? {} : { afterReceiptId }),
      });
      setPage((current) => append && current ? { ...next, items: [...current.items, ...next.items] } : next);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => { setPage(null); void load(); }, [load]);

  const mutate = async (receiptId: number, action: "retry" | "discard") => {
    setBusyReceipt(receiptId);
    setError(null);
    try {
      if (action === "retry") await engine.retryCodingSessionDeliveryEnvelope(receiptId);
      else {
        const preview = await engine.discardCodingSessionDeliveryEnvelope(receiptId, false);
        const impact = preview.impact;
        if (!impact) throw new Error("The engine did not return discard impact evidence.");
        const confirmed = window.confirm(`${impact.warning}\n\nEnvelope #${impact.receipt_id} contains ${impact.item_count} event${impact.item_count === 1 ? "" : "s"} (${formatBytes(impact.payload_bytes)}) from ${impact.provider}. The provider's original session is unchanged.`);
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

  const qualifier = [filter?.provider, filter?.action, filter?.source].filter(Boolean).join(" · ");

  return (
    <Dialog open={Boolean(filter)} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[85vh] max-w-6xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>{filter?.state === "quarantine" ? "Preserved delivery envelopes" : "Waiting delivery envelopes"}</DialogTitle>
          <DialogDescription>
            Exact payload-free evidence stored on this Mac{qualifier ? `, filtered to ${qualifier}` : ""}. One envelope can contain multiple provider events.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-muted-foreground">{page ? `${page.total.toLocaleString()} matching envelopes` : "Loading count…"}</span>
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}Refresh</Button>
        </div>
        {error && <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div>}
        <div className="max-h-[60vh] overflow-auto rounded-md border">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="sticky top-0 border-b bg-background text-left"><tr><th className="px-3 py-2">Receipt</th><th className="px-3 py-2">Provider work</th><th className="px-3 py-2">Contents</th><th className="px-3 py-2">Stored</th><th className="px-3 py-2">Delivery</th><th className="px-3 py-2">Actions</th></tr></thead>
            <tbody className="divide-y">
              {page?.items.map((item) => (
                <tr key={item.receipt_id}>
                  <td className="px-3 py-3 align-top font-mono">#{item.receipt_id}</td>
                  <td className="px-3 py-3 align-top"><div className="font-medium">{item.provider}</div><div className="text-xs text-muted-foreground">{codingSessionActionLabel(item.action)} · {codingSessionSourceLabel(item.source)}</div></td>
                  <td className="px-3 py-3 align-top"><div>{item.item_count.toLocaleString()} event{item.item_count === 1 ? "" : "s"} · {formatBytes(item.payload_bytes)}</div><div className="font-mono text-xs text-muted-foreground">{item.session_ref ?? "No session reference reported"}</div></td>
                  <td className="whitespace-nowrap px-3 py-3 align-top">{new Date(item.created_at).toLocaleString()}</td>
                  <td className="px-3 py-3 align-top"><Badge variant={item.state === "quarantine" ? "destructive" : "outline"}>{item.state === "quarantine" ? "Not accepted" : "Waiting"}</Badge><div className="mt-1 text-xs text-muted-foreground">{item.attempts} attempt{item.attempts === 1 ? "" : "s"}{item.retry_in_seconds > 0 ? ` · retry ${formatRetryDuration(item.retry_in_seconds)}` : ""}</div>{item.error?.message && <div className="mt-1 max-w-72 text-xs text-destructive">{item.error.message}</div>}</td>
                  <td className="px-3 py-3 align-top"><div className="flex gap-1"><Button variant="outline" size="sm" disabled={busyReceipt !== null || !item.actions.retry} onClick={() => void mutate(item.receipt_id, "retry")}><RotateCw className="mr-1 h-3.5 w-3.5" />Retry</Button><Button variant="ghost" size="sm" disabled={busyReceipt !== null || !item.actions.discard} onClick={() => void mutate(item.receipt_id, "discard")}><Trash2 className="mr-1 h-3.5 w-3.5" />Discard</Button></div></td>
                </tr>
              ))}
              {!loading && page?.items.length === 0 && <tr><td colSpan={6} className="px-4 py-10 text-center text-muted-foreground">No matching envelopes remain.</td></tr>}
            </tbody>
          </table>
        </div>
        {page?.has_more && <div className="flex items-center justify-between gap-3"><p className="text-xs text-muted-foreground">Showing {page.items.length} of {page.total} matching envelopes.</p><Button type="button" variant="outline" size="sm" disabled={loading || page.next_cursor === null} onClick={() => void load(page.next_cursor ?? undefined, true)}>Load more</Button></div>}
      </DialogContent>
    </Dialog>
  );
}
