/**
 * Usage, every provider, ONE table — inside the one coding-sessions feature.
 *
 * "Usage for every provider, in that tab, provider as a facet — same shapes,
 * same table, not four different screens" (Arman, 2026-09-17). The engine
 * hands every provider back as the same `UsageReport`, so this tab has one
 * grid (by day / by model / by session / by project, with a totals row), one
 * "limits" card and one "source" card, and switches only the provider chip.
 *
 * What a provider does not record on this Mac is a state the report carries
 * (`metrics`, `source.kind`, `cost.reason`, `limits.reason`) and the tab shows
 * it in words — never a dead tab, never a zero pretending to be a measurement.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { Badge, Button, BasicInput as Input } from "@ai-matrx/design-system";
import { formatStamp, whenLocal } from "@/components/coding-sessions/shared";
import { providerChips, providerLabel } from "@/lib/coding-sessions/providers";
import type { CodingSessionsSnapshot } from "@/lib/coding-sessions/overview-store";
import { engine, type CodingSessionProvider, type UsageReport, type UsageRow } from "@/lib/api";

export type UsagePreset = "today" | "yesterday" | "last7" | "last30" | "custom";
export type UsageView = "day" | "model" | "session" | "project";

const PRESET_LABEL: Record<UsagePreset, string> = {
  today: "Today",
  yesterday: "Yesterday",
  last7: "Last 7 days",
  last30: "Last 30 days",
  custom: "Custom",
};
const VIEW_LABEL: Record<UsageView, string> = {
  day: "By day",
  model: "By model",
  session: "By session",
  project: "By project",
};
/** Every provider the tab always offers, in the readiness order. */
const ALL_PROVIDERS: CodingSessionProvider[] = ["claude_code", "codex", "cursor", "vscode"];

const count = new Intl.NumberFormat();
const fmt = (value: number) => count.format(value);

/** The viewer's minutes ahead of UTC, the sign the engine expects. */
export function tzOffsetMinutes(now = new Date()): number {
  return -now.getTimezoneOffset();
}

/** The [start, end) the preset means, in the viewer's local days. */
export function usageRangeFor(
  preset: UsagePreset,
  start: string,
  end: string,
  now = new Date(),
): { start: string; end: string } {
  const dayStart = (date: Date) => {
    const copy = new Date(date);
    copy.setHours(0, 0, 0, 0);
    return copy;
  };
  if (preset === "custom") {
    const from = new Date(`${start}T00:00:00`);
    const until = new Date(`${end}T00:00:00`);
    until.setDate(until.getDate() + 1);
    return { start: from.toISOString(), end: until.toISOString() };
  }
  const today = dayStart(now);
  if (preset === "yesterday") {
    const from = new Date(today);
    from.setDate(from.getDate() - 1);
    return { start: from.toISOString(), end: today.toISOString() };
  }
  const from = new Date(today);
  if (preset === "last7") from.setDate(from.getDate() - 6);
  if (preset === "last30") from.setDate(from.getDate() - 29);
  return { start: from.toISOString(), end: now.toISOString() };
}

function rowsFor(report: UsageReport, view: UsageView): UsageRow[] {
  if (view === "day") return report.by_day;
  if (view === "model") return report.by_model;
  if (view === "session") return report.by_session;
  return report.by_project;
}

function costText(value: number | null, unit: "usd" | "credits" | null): string {
  if (value == null) return "—";
  if (unit === "usd") return `$${value.toFixed(2)}`;
  return value.toFixed(2);
}

export interface UsageTabProps {
  snapshot: CodingSessionsSnapshot;
}

export function UsageTab({ snapshot }: UsageTabProps) {
  const chips = providerChips(snapshot.overview, snapshot.readiness);
  const providers: CodingSessionProvider[] = [
    ...new Set<CodingSessionProvider>([...chips.map((chip) => chip.provider), ...ALL_PROVIDERS]),
  ];
  const [provider, setProvider] = useState<CodingSessionProvider>(providers[0] ?? "claude_code");
  const [preset, setPreset] = useState<UsagePreset>("today");
  const [view, setView] = useState<UsageView>("day");
  const [start, setStart] = useState(() => new Date().toISOString().slice(0, 10));
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [report, setReport] = useState<UsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);

  const load = useCallback(
    async (refresh: boolean) => {
      const mine = ++generation.current;
      setLoading(true);
      setError(null);
      try {
        const range = usageRangeFor(preset, start, end, new Date());
        const next = await engine.getCodingSessionUsage({
          provider,
          ...range,
          refresh,
          tzOffsetMinutes: tzOffsetMinutes(),
        });
        if (mine === generation.current) setReport(next);
      } catch (cause) {
        if (mine === generation.current) {
          setError(cause instanceof Error ? cause.message : `Unable to read ${providerLabel(provider)} usage.`);
        }
      } finally {
        if (mine === generation.current) setLoading(false);
      }
    },
    [provider, preset, start, end],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  const showing = report && report.provider === provider ? report : null;
  const rows = showing ? rowsFor(showing, view) : [];
  const metrics = showing?.metrics ?? { tokens: false, requests: false, cost: false, lines: false, subagents: false };
  const unit = showing?.cost.unit ?? null;
  const columns = [
    ...(metrics.tokens
      ? ["Input", "Cache read", "Cache write", "Output", "Total tokens"]
      : []),
    ...(metrics.requests ? ["Requests"] : []),
    ...(metrics.subagents ? ["Sub-agent tokens"] : []),
    ...(metrics.cost ? [showing?.cost.unit === "credits" ? "Est. credits" : "Est. cost"] : []),
    ...(metrics.lines ? ["Suggested lines", "Accepted lines"] : []),
  ];
  const nothingMeasured = showing ? !metrics.tokens && !metrics.requests && !metrics.lines : false;
  const viewsAvailable: UsageView[] = showing
    ? (["day", "model", "session", "project"] as UsageView[]).filter(
        (item) => item === "day" || rowsFor(showing, item).length > 0 || metrics.tokens,
      )
    : ["day"];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2" data-testid="usage-provider-chips">
        {providers.map((item) => (
          <Button
            key={item}
            size="sm"
            variant={provider === item ? "secondary" : "ghost"}
            aria-pressed={provider === item}
            onClick={() => {
              setProvider(item);
              setView("day");
            }}
          >
            {providerLabel(item)}
          </Button>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-md border p-0.5" data-testid="usage-range">
            {(Object.keys(PRESET_LABEL) as UsagePreset[]).map((item) => (
              <Button
                key={item}
                size="sm"
                variant={preset === item ? "default" : "ghost"}
                onClick={() => setPreset(item)}
              >
                {PRESET_LABEL[item]}
              </Button>
            ))}
          </div>
          {preset === "custom" && (
            <>
              <Input type="date" aria-label="Start date" value={start} onChange={(event) => setStart(event.target.value)} />
              <Input type="date" aria-label="End date" value={end} onChange={(event) => setEnd(event.target.value)} />
            </>
          )}
          <div className="flex rounded-md border p-0.5" data-testid="usage-view">
            {viewsAvailable.map((item) => (
              <Button
                key={item}
                size="sm"
                variant={view === item ? "default" : "ghost"}
                onClick={() => setView(item)}
              >
                {VIEW_LABEL[item]}
              </Button>
            ))}
          </div>
        </div>
        <Button size="sm" disabled={loading} onClick={() => void load(true)} data-testid="usage-refresh">
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {showing?.source.can_resume ? "Continue reading" : "Refresh"}
        </Button>
      </div>

      {error && (
        <div
          className="flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm"
          role="alert"
          data-testid="usage-error"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div>
            <p className="font-medium">Couldn't read {providerLabel(provider)} usage</p>
            <p className="mt-1 font-mono text-xs text-muted-foreground">{error}</p>
            {showing && (
              <p className="mt-1 text-muted-foreground">
                The numbers below are the last answer this Mac read
                {showing.generated_at ? ` (${formatStamp(showing.generated_at)})` : ""}.
              </p>
            )}
          </div>
        </div>
      )}

      {loading && !showing && !error && (
        <p className="flex items-center gap-2 rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground" data-testid="usage-loading">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Reading this Mac's {providerLabel(provider)} usage…
        </p>
      )}

      {showing && (
        <>
          <div className="grid gap-3 md:grid-cols-4" data-testid="usage-totals">
            <Metric
              label={metrics.tokens ? "Total tokens" : metrics.lines ? "Accepted lines" : "Measured"}
              value={
                metrics.tokens
                  ? fmt(showing.totals.total_tokens)
                  : metrics.lines
                    ? fmt(showing.totals.extra.accepted_lines ?? 0)
                    : "Nothing"
              }
              sub={
                metrics.tokens
                  ? `${fmt(showing.totals.input_tokens)} in · ${fmt(showing.totals.output_tokens)} out · ${fmt(showing.totals.cache_read_tokens + showing.totals.cache_creation_tokens)} cache`
                  : metrics.lines
                    ? `${fmt(showing.totals.extra.suggested_lines ?? 0)} suggested`
                    : showing.source.description
              }
            />
            <Metric
              label="Requests"
              value={metrics.requests ? fmt(showing.totals.requests) : "Not recorded"}
              sub={
                metrics.requests
                  ? metrics.subagents
                    ? `Counted once each · ${fmt(showing.totals.main_requests)} main · ${fmt(showing.totals.subagent_requests)} sub-agent`
                    : "Model turns counted once each"
                  : `${providerLabel(provider)} keeps no request count on this Mac`
              }
            />
            <Metric
              label={showing.cost.label}
              value={showing.cost.available ? costText(showing.totals.cost, unit) : "Unavailable"}
              sub={
                showing.cost.reason ??
                (unit === "credits" ? "Not actual allowance debits" : "List price; not a bill")
              }
            />
            <Metric
              label="Source"
              value={
                showing.source.kind === "none"
                  ? "None"
                  : showing.source.complete
                    ? "Complete"
                    : showing.source.pending_sessions
                      ? `${fmt(showing.source.pending_sessions)} pending`
                      : "Partial"
              }
              sub={showing.source.updated_at ? `Read ${formatStamp(showing.source.updated_at)}` : showing.source.description}
              badge={showing.source.kind === "none" ? undefined : showing.source.complete ? "Complete" : "Still reading"}
            />
          </div>

          {nothingMeasured ? (
            <p className="rounded-lg border bg-muted/30 p-4 text-sm" data-testid="usage-no-source">
              {showing.source.notes.join(" ")}
            </p>
          ) : (
            <section className="overflow-hidden rounded-lg border bg-card" data-testid="usage-table">
              <div className="flex items-center justify-between border-b px-4 py-3 text-sm font-medium">
                <span>
                  {providerLabel(provider)} · {VIEW_LABEL[view]}
                </span>
                <span className="text-xs font-normal text-muted-foreground">
                  {whenLocal(showing.range.start)} → {whenLocal(showing.range.end)}
                </span>
              </div>
              <div className="max-h-[28rem] overflow-auto" data-matrx-table-scroll="">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-muted text-xs text-muted-foreground">
                    <tr>
                      <th className="px-4 py-2 text-left">{VIEW_LABEL[view].replace("By ", "")}</th>
                      {columns.map((column) => (
                        <th key={column} className="px-4 py-2 text-right">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <UsageTr key={row.key} row={row} metrics={metrics} unit={unit} view={view} />
                    ))}
                    {rows.length === 0 && (
                      <tr>
                        <td className="p-6 text-sm text-muted-foreground" colSpan={columns.length + 1} data-testid="usage-empty">
                          No {providerLabel(provider)} usage recorded on this Mac in this range.
                        </td>
                      </tr>
                    )}
                  </tbody>
                  {rows.length > 0 && (
                    <tfoot className="sticky bottom-0 border-t bg-muted/60 font-medium">
                      <UsageTr row={showing.totals} metrics={metrics} unit={unit} view={view} total />
                    </tfoot>
                  )}
                </table>
              </div>
              {loading && (
                <p className="flex items-center gap-2 border-t px-4 py-2 text-xs text-muted-foreground" data-testid="usage-refreshing-note">
                  <RefreshCw className="h-3 w-3 animate-spin" />
                  Re-reading this Mac's {providerLabel(provider)} usage — the table is the previous answer.
                </p>
              )}
            </section>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="rounded-lg border p-4" data-testid="usage-limits">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-medium">{providerLabel(provider)} limits</h3>
                {showing.limits.plan && <Badge variant="outline">{showing.limits.plan}</Badge>}
              </div>
              {showing.limits.status === "available" ? (
                <ul className="space-y-2">
                  {showing.limits.windows.map((window, index) => (
                    <li key={`${window.label}-${index}`} className="text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <span className="min-w-0 truncate" title={window.label}>
                          {window.label}
                        </span>
                        <span className="shrink-0 tabular-nums text-muted-foreground">
                          {window.used_percent == null ? "—" : `${window.used_percent.toFixed(0)}% used`}
                          {window.resets_at ? ` · resets ${whenLocal(window.resets_at)}` : ""}
                        </span>
                      </div>
                      {window.used_percent != null && (
                        <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-muted" aria-hidden>
                          <div
                            className={`h-full ${window.used_percent >= 90 ? "bg-destructive" : "bg-primary"}`}
                            style={{ width: `${Math.max(0, Math.min(100, window.used_percent))}%` }}
                          />
                        </div>
                      )}
                    </li>
                  ))}
                  <li className="text-xs text-muted-foreground">
                    As {providerLabel(provider)} last cached it{showing.limits.observed_at ? ` (${formatStamp(showing.limits.observed_at)})` : ""}; not a live account read.
                  </li>
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground" data-testid="usage-limits-unavailable">
                  {showing.limits.reason ?? `${providerLabel(provider)} exposes no limits on this Mac.`}
                </p>
              )}
            </section>
            <section className="rounded-lg border p-4 text-xs text-muted-foreground" data-testid="usage-source">
              <h3 className="mb-2 text-sm font-medium text-foreground">Where these numbers come from</h3>
              <p className="mb-1">{showing.source.description}</p>
              {showing.source.notes.map((note) => (
                <p key={note} className="mb-1">
                  {note}
                </p>
              ))}
              {showing.cost.reason && showing.cost.available && <p className="mb-1">{showing.cost.reason}</p>}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function UsageTr({
  row,
  metrics,
  unit,
  view,
  total = false,
}: {
  row: UsageRow;
  metrics: UsageReport["metrics"];
  unit: "usd" | "credits" | null;
  view: UsageView;
  total?: boolean;
}) {
  const cell = "px-4 py-2 text-right tabular-nums";
  return (
    <tr className={total ? "" : "border-t"} data-testid={total ? "usage-total-row" : undefined}>
      <td className="max-w-[24rem] truncate px-4 py-2" title={row.label}>
        {row.label}
        {!total && view === "session" && row.project ? (
          <span className="ml-2 text-xs text-muted-foreground">{row.project}</span>
        ) : null}
      </td>
      {metrics.tokens && (
        <>
          <td className={cell}>{fmt(row.input_tokens)}</td>
          <td className={cell}>{fmt(row.cache_read_tokens)}</td>
          <td className={cell}>{fmt(row.cache_creation_tokens)}</td>
          <td className={cell}>{fmt(row.output_tokens)}</td>
          <td className={cell}>{fmt(row.total_tokens)}</td>
        </>
      )}
      {metrics.requests && <td className={cell}>{fmt(row.requests)}</td>}
      {metrics.subagents && (
        <td className={cell} title={`${fmt(row.subagent_requests)} of ${fmt(row.requests)} turns ran in sub-agents`}>
          {row.total_tokens === 0 ? (
            "—"
          ) : (
            <>
              {fmt(row.subagent_total_tokens)}
              <span className="ml-2 text-xs text-muted-foreground">
                {Math.round((row.subagent_total_tokens / row.total_tokens) * 100)}%
              </span>
            </>
          )}
        </td>
      )}
      {metrics.cost && <td className={cell}>{costText(row.cost, unit)}</td>}
      {metrics.lines && (
        <>
          <td className={cell}>{fmt(row.extra.suggested_lines ?? 0)}</td>
          <td className={cell}>{fmt(row.extra.accepted_lines ?? 0)}</td>
        </>
      )}
    </tr>
  );
}

function Metric({ label, value, sub, badge }: { label: string; value: string; sub: string; badge?: string | undefined }) {
  return (
    <div className="rounded-lg border p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 truncate text-xl font-semibold tabular-nums" title={value}>
        {value}
      </p>
      <p className="truncate text-xs text-muted-foreground" title={sub}>
        {sub}
      </p>
      {badge && (
        <Badge className="mt-2" variant={badge === "Complete" ? "default" : "secondary"}>
          {badge}
        </Badge>
      )}
    </div>
  );
}
