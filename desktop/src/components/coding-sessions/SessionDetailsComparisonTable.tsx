import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, RotateCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { engine } from "@/lib/api";
import type { ClaudeLabelSyncResult, ClaudeSessionDetailComparison } from "@/lib/api";

export function displaySessionDetailValue(value: unknown, observed = true): string {
  if (!observed) return "Not reported by AI Matrx";
  if (value === null || value === undefined || value === "") return "Empty";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function SessionDetailsComparisonTable({ result, busy, onVerified }: {
  result: ClaudeLabelSyncResult;
  busy: boolean;
  onVerified: (result: ClaudeLabelSyncResult) => void;
}) {
  const [rows, setRows] = useState<ClaudeSessionDetailComparison[]>(result.comparisons ?? []);
  const [cursor, setCursor] = useState<string | null>(result.comparisons_truncated ? rows[rows.length - 1]?.session_ref ?? null : null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(result.comparisons ?? []);
    setCursor(result.comparisons_truncated ? result.comparisons?.[result.comparisons.length - 1]?.session_ref ?? null : null);
  }, [result.operation_id]);

  if (!result.operation_id || !result.operation) return null;

  const loadMore = async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await engine.getClaudeSessionDetailOperation(result.operation_id!, cursor ?? undefined);
      setRows((current) => [...current, ...page.items]);
      setCursor(page.has_more ? page.next_cursor : null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const verify = async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await engine.verifyClaudeSessionDetailOperation(result.operation_id!);
      setRows(page.items);
      setCursor(page.has_more ? page.next_cursor : null);
      onVerified({
        ...result,
        dry_run: false,
        operation_id: page.operation.operation_id,
        operation: page.operation,
        comparisons: page.items,
        comparisons_truncated: page.has_more,
        acknowledged: page.operation.acknowledged_sessions,
        verified: page.operation.verified_sessions,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const retryIntent = async (intentId: string) => {
    setLoading(true);
    setError(null);
    try {
      const page = await engine.retryClaudeSessionDetailPushIntent(intentId);
      setRows(page.items);
      setCursor(page.has_more ? page.next_cursor : null);
      onVerified({ ...result, operation_id: page.operation.operation_id, operation: page.operation, comparisons: page.items, comparisons_truncated: page.has_more });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const stats: Array<[string, number]> = [
    ["Compared", result.operation.compared_sessions],
    ["Changes detected", result.operation.detected_sessions],
    ["Stored for delivery", result.operation.enqueued_sessions],
    ["Cloud acknowledged", result.operation.acknowledged_sessions],
    ["Verified equal", result.operation.verified_sessions],
  ];

  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {stats.map(([label, count]) => <div key={label} className="rounded-md border p-2"><p className="text-xs text-muted-foreground">{label}</p><p className="text-lg font-semibold">{count.toLocaleString()}</p></div>)}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">Operation <span className="font-mono">{result.operation_id}</span> · {result.operation.status}</p>
        {!result.dry_run && <Button type="button" variant="outline" size="sm" onClick={() => void verify()} disabled={busy || loading}>{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}Reread both sides and verify</Button>}
      </div>
      {error && <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive" role="alert"><AlertTriangle className="h-4 w-4 shrink-0" />{error}</div>}
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full min-w-[1050px] text-xs">
          <thead className="border-b bg-muted/40 text-left"><tr><th className="px-3 py-2">Session</th><th className="px-3 py-2">Field</th><th className="px-3 py-2">Claude Code</th><th className="px-3 py-2">AI Matrx before</th><th className="px-3 py-2">Chosen result</th><th className="px-3 py-2">Evidence state</th></tr></thead>
          <tbody className="divide-y">
            {rows.flatMap((row) => row.comparisons.map((field, index) => (
              <tr key={`${row.session_ref}-${field.field}`} className={field.equal ? "" : "bg-blue-500/5"}>
                {index === 0 && <td rowSpan={row.comparisons.length} className="px-3 py-2 align-top font-mono"><div>{row.session_ref}</div><div className="mt-1 font-sans"><Badge variant="outline">{row.direction}</Badge></div>{row.write_intent_id && <Button type="button" variant="outline" size="sm" className="mt-2" disabled={loading} onClick={() => void retryIntent(row.write_intent_id!)}><RotateCw className="mr-1 h-3 w-3" />Retry write</Button>}</td>}
                <td className="px-3 py-2 font-medium">{field.field}</td>
                <td className="max-w-64 px-3 py-2">{displaySessionDetailValue(field.local, field.local_observed)}</td>
                <td className="max-w-64 px-3 py-2">{displaySessionDetailValue(field.ai_matrx, field.ai_matrx_observed)}</td>
                <td className="max-w-64 px-3 py-2">{displaySessionDetailValue(row.chosen[field.field])}</td>
                {index === 0 && <td rowSpan={row.comparisons.length} className="px-3 py-2 align-top"><Badge variant={row.state === "verified" ? "secondary" : row.state === "failed" ? "destructive" : "outline"}>{row.state}</Badge><p className="mt-1 max-w-52 text-muted-foreground">{row.reason}</p>{row.receipt_id && <p className="mt-1 font-mono">Receipt #{row.receipt_id}</p>}</td>}
              </tr>
            )))}
            {rows.length === 0 && <tr><td colSpan={6} className="p-8 text-center text-muted-foreground"><CheckCircle2 className="mx-auto mb-2 h-5 w-5" />No row-level changes were detected.</td></tr>}
          </tbody>
        </table>
      </div>
      {cursor && <div className="text-right"><Button type="button" variant="outline" size="sm" onClick={() => void loadMore()} disabled={loading}>{loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Load more comparisons</Button></div>}
    </div>
  );
}
