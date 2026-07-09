/**
 * Activity page — Real-time request & log monitor.
 *
 * Consumes the unified log bus (use-unified-log). No owned SSE connections.
 * Data persists across tab switches (module-level ring buffer).
 *
 * Tabs:
 *   Overview      — summary cards + per-source breakdown + Copy Issue Report
 *   Server        — engine SSE + sidecar IPC + raw syslog + LLM server (source="server"|"tauri"|"syslog"|"llm")
 *   Client        — auth, engine discovery, voice, setup (source="engine"|"auth"|"voice"|"setup")
 *   HTTP          — structured HTTP request log (source="access")
 *   All           — every log line regardless of source
 *
 * Features per tab:
 *   - Level filter pills with counts
 *   - Text search filter
 *   - Group similar toggle (INFO/OK/DATA/CMD only; WARN/ERR always individual)
 *   - Auto-scroll toggle
 *   - Pause/Resume (global, shared with DevTerminalPanel)
 *   - Copy filtered (respects grouping)
 *   - Copy Issue Report (Overview + Server + All)
 *   - Error/warn badges on tab triggers
 *   - Clear scoped to tab's sources
 */

import { useState, useLayoutEffect, useRef, useCallback, useMemo } from "react";
import {
  Activity as ActivityIcon,
  Trash2,
  Pause,
  Play,
  ArrowDown,
  Filter,
  CheckCircle,
  AlertCircle,
  AlertTriangle,
  Clock,
  Globe,
  Copy,
  ClipboardList,
  Check,
  LayoutDashboard,
  Server,
  Monitor,
  List,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import type { EngineStatus } from "@/hooks/use-engine";
import {
  useClientLogSubscriber,
  clearClientLog,
  clearClientLogBySource,
  setLogsPaused,
  useLogsPaused,
} from "@/hooks/use-unified-log";
import type {
  LogLevel,
  ClientLogLine,
  AccessEntry,
} from "@/hooks/use-unified-log";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ActivityProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
}

type TabId = "overview" | "server" | "client" | "http" | "all";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ALL_LEVELS: LogLevel[] = [
  "info",
  "success",
  "warn",
  "error",
  "data",
  "cmd",
];
// Only these levels are eligible for grouping; warn/error always show individually
const GROUPABLE_LEVELS = new Set<LogLevel>([
  "info",
  "success",
  "data",
  "cmd",
]);

const SERVER_SOURCES = new Set(["server", "tauri", "syslog", "llm"]);
const CLIENT_SOURCES = new Set([
  "engine",
  "auth",
  "voice",
  "setup",
  "bg-tasks",
  "react",
  "downloads",
]);
const HTTP_SOURCE = "access";

const LEVEL_COLOR: Record<LogLevel, string> = {
  info: "text-zinc-600 dark:text-zinc-400",
  success: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  error: "text-red-700 dark:text-red-400",
  data: "text-sky-700 dark:text-sky-400",
  cmd: "text-cyan-700 dark:text-cyan-400",
};

const LEVEL_LABEL: Record<LogLevel, string> = {
  info: "INFO",
  success: "OK  ",
  warn: "WARN",
  error: "ERR ",
  data: "DATA",
  cmd: "CMD ",
};

const LEVEL_PILL_ACTIVE: Record<LogLevel, string> = {
  info: "bg-zinc-200 text-zinc-800 border-zinc-300 dark:bg-zinc-700 dark:text-zinc-200 dark:border-zinc-500",
  success:
    "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-900/50 dark:text-emerald-300 dark:border-emerald-700",
  warn: "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-900/50 dark:text-amber-300 dark:border-amber-700",
  error:
    "bg-red-100 text-red-800 border-red-300 dark:bg-red-900/50 dark:text-red-300 dark:border-red-700",
  data: "bg-sky-100 text-sky-800 border-sky-300 dark:bg-sky-900/50 dark:text-sky-300 dark:border-sky-700",
  cmd: "bg-cyan-100 text-cyan-800 border-cyan-300 dark:bg-cyan-900/50 dark:text-cyan-300 dark:border-cyan-700",
};

const LEVEL_PILL_INACTIVE =
  "text-muted-foreground/40 border-border/30 hover:text-muted-foreground hover:border-border/60";

const SOURCE_LABELS: Record<string, string> = {
  server: "Engine (SSE)",
  tauri: "Sidecar IPC",
  syslog: "System Log",
  llm: "LLM Server",
  engine: "Engine Client",
  auth: "Auth",
  voice: "Voice",
  setup: "Setup Wizard",
  access: "HTTP Requests",
  "bg-tasks": "Background Tasks",
  react: "React / Console",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function statusColor(code: number): string {
  if (code < 300) return "text-emerald-700 dark:text-emerald-400";
  if (code < 400) return "text-amber-700 dark:text-amber-400";
  if (code < 500) return "text-orange-700 dark:text-orange-400";
  return "text-red-700 dark:text-red-400";
}

function methodColor(method: string): string {
  switch (method.toUpperCase()) {
    case "GET":
      return "text-sky-700 dark:text-sky-400";
    case "POST":
      return "text-violet-700 dark:text-violet-400";
    case "PUT":
      return "text-amber-700 dark:text-amber-400";
    case "DELETE":
      return "text-red-700 dark:text-red-400";
    case "PATCH":
      return "text-orange-700 dark:text-orange-400";
    default:
      return "text-muted-foreground";
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "—";
  }
}

function tabFilter(tab: TabId): (l: ClientLogLine) => boolean {
  if (tab === "server") return (l) => SERVER_SOURCES.has(l.source ?? "");
  if (tab === "client")
    return (l) => {
      const s = l.source ?? "";
      return (
        CLIENT_SOURCES.has(s) ||
        (!SERVER_SOURCES.has(s) && s !== HTTP_SOURCE && !CLIENT_SOURCES.has(s))
      );
    };
  if (tab === "http") return (l) => l.source === HTTP_SOURCE;
  return () => true; // overview + all
}

// ---------------------------------------------------------------------------
// Group similar
// ---------------------------------------------------------------------------

interface GroupedRow {
  representative: ClientLogLine;
  count: number;
  key: string;
}

// Strip variable numeric parts before building a group key so that messages
// which differ only in timing/timestamps/durations are treated as identical.
/**
 * Strip or canonicalize every "dynamic-but-not-meaningful" element from a log
 * message so that semantically identical lines produce the same grouping key.
 *
 * Strategy: replace dynamic tokens with stable placeholders rather than
 * deleting them outright — the message structure stays readable in the key
 * and prevents false-positive merges between structurally different messages.
 *
 * Stripping order matters: longest/most-specific patterns first.
 */
function normalizeForGrouping(msg: string): string {
  return (
    msg
      // Python logger full prefix: "2026-05-08 12:17:51,635 - DEBUG - "
      .replace(
        /^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.\d]*\s+-\s+\w+\s+-\s+/,
        "",
      )
      // Full ISO/syslog timestamps in body: 2026-05-08T18:00:16.886Z or "2026-05-08 18:00:16,886"
      .replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[,.\d]*Z?/g, "<ts>")
      // Bare HH:MM:SS timestamps
      .replace(/\b\d{2}:\d{2}:\d{2}(?:[,.\d]+)?\b/g, "<ts>")
      // Timing annotations in parens: (33ms), (1022ms), (1.5s)
      .replace(/\(\d+(?:\.\d+)?(?:ms|s)\)/g, "")
      // Trailing bare durations: " 239ms", " 1030ms"
      .replace(/\s+\d+(?:\.\d+)?ms\b/g, "")
      // JWT / Bearer tokens: eyJhbGciOiJIUzI1NiIs… (base64url, 20+ chars)
      .replace(/eyJ[A-Za-z0-9_-]{20,}/g, "<token>")
      // Quoted alphanumeric IDs / key fingerprints: "4jRGaVmV53oT4QQD"
      .replace(/"[A-Za-z0-9_-]{6,}"/g, '"<id>"')
      // IP addresses with optional port: 127.0.0.1:22140, 10.0.0.1
      .replace(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b/g, "<ip>")
      // Request / correlation IDs: req-62, req-1234
      .replace(/\breq-\d+\b/g, "req-N")
      // UUIDs: 550e8400-e29b-41d4-a716-446655440000
      .replace(
        /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi,
        "<uuid>",
      )
      // Collapse leftover extra whitespace
      .replace(/\s{2,}/g, " ")
      .trim()
  );
}

function groupSimilarLogs(logs: ClientLogLine[]): GroupedRow[] {
  const rows: GroupedRow[] = [];
  const groupMap = new Map<string, GroupedRow>();
  for (const line of logs) {
    if (!GROUPABLE_LEVELS.has(line.level)) {
      rows.push({ representative: line, count: 1, key: String(line.id) });
    } else {
      const key = `${line.level}|${line.source ?? ""}|${normalizeForGrouping(line.message)}`;
      const existing = groupMap.get(key);
      if (existing) {
        existing.count++;
        existing.representative = line;
      } else {
        const row: GroupedRow = { representative: line, count: 1, key };
        groupMap.set(key, row);
        rows.push(row);
      }
    }
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Issue report builder
// ---------------------------------------------------------------------------

function buildIssueReport(logs: ClientLogLine[]): string {
  // Deduplicate similar issues so the report stays concise for AI consumption.
  // Entries that differ only in timing/timestamps are merged into one with a count.
  interface IssueSummary {
    level: "error" | "warn";
    source: string;
    first: ClientLogLine;
    last: ClientLogLine;
    count: number;
  }

  const issueMap = new Map<string, IssueSummary>();
  logs.forEach((l) => {
    if (l.level !== "error" && l.level !== "warn") return;
    const key = `${l.level}|${l.source ?? "unknown"}|${normalizeForGrouping(l.message)}`;
    const existing = issueMap.get(key);
    if (existing) {
      existing.count++;
      existing.last = l;
    } else {
      issueMap.set(key, {
        level: l.level,
        source: l.source ?? "unknown",
        first: l,
        last: l,
        count: 1,
      });
    }
  });

  const issues = [...issueMap.values()];
  const totalErrors = issues
    .filter((i) => i.level === "error")
    .reduce((s, i) => s + i.count, 0);
  const totalWarns = issues
    .filter((i) => i.level === "warn")
    .reduce((s, i) => s + i.count, 0);
  const uniqueErrors = issues.filter((i) => i.level === "error").length;
  const uniqueWarns = issues.filter((i) => i.level === "warn").length;

  const now = new Date().toLocaleString("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  const lines = [
    `=== Matrx Issue Report — ${now} ===`,
    `Total: ${logs.length} log lines | ${totalErrors} errors (${uniqueErrors} unique) | ${totalWarns} warnings (${uniqueWarns} unique)`,
    "",
  ];

  if (issues.length === 0) {
    lines.push("No errors or warnings found.");
    lines.push("=== END ===");
    return lines.join("\n");
  }

  // Sort: errors before warnings, then most-frequent first within each level
  issues.sort((a, b) => {
    if (a.level !== b.level) return a.level === "error" ? -1 : 1;
    return b.count - a.count;
  });

  // Group by source for readability
  const bySource = new Map<string, IssueSummary[]>();
  issues.forEach((issue) => {
    if (!bySource.has(issue.source)) bySource.set(issue.source, []);
    bySource.get(issue.source)!.push(issue);
  });

  for (const [source, srcIssues] of bySource.entries()) {
    const srcErrors = srcIssues
      .filter((i) => i.level === "error")
      .reduce((s, i) => s + i.count, 0);
    const srcWarns = srcIssues
      .filter((i) => i.level === "warn")
      .reduce((s, i) => s + i.count, 0);
    const uErr = srcIssues.filter((i) => i.level === "error").length;
    const uWrn = srcIssues.filter((i) => i.level === "warn").length;

    lines.push(`── ${SOURCE_LABELS[source] ?? source} ──`);
    const summary: string[] = [];
    if (srcErrors)
      summary.push(
        `${srcErrors} error${srcErrors !== 1 ? "s" : ""} (${uErr} unique)`,
      );
    if (srcWarns)
      summary.push(
        `${srcWarns} warning${srcWarns !== 1 ? "s" : ""} (${uWrn} unique)`,
      );
    lines.push(`   ${summary.join("  |  ")}`);

    for (const issue of srcIssues) {
      const tag = issue.level === "error" ? "ERR " : "WARN";
      const count = issue.count > 1 ? ` ×${issue.count}` : "";
      lines.push(
        `   ${tag}${count}: ${issue.first.time} ${issue.first.message}`,
      );
      if (issue.count > 1) {
        lines.push(`         last: ${issue.last.time} ${issue.last.message}`);
      }
    }
    lines.push("");
  }

  if (lines[lines.length - 1] === "") lines.pop();
  lines.push("=== END ===");
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Tab badge helper
// ---------------------------------------------------------------------------

function TabBadge({ errors, warns }: { errors: number; warns: number }) {
  if (errors > 0)
    return (
      <span className="text-[9px] bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-400 rounded px-1 py-px font-mono">
        {errors}e
      </span>
    );
  if (warns > 0)
    return (
      <span className="text-[9px] bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400 rounded px-1 py-px font-mono">
        {warns}w
      </span>
    );
  return null;
}

// ---------------------------------------------------------------------------
// Shared toolbar: filter bar + group similar + auto-scroll
// ---------------------------------------------------------------------------

// LogFilterBar is shared by all log tabs (Server, Client, HTTP, All).
// Level pills and group-similar are optional — omit them for the HTTP tab.
// Source pills are shown automatically when multiple sources are present.
function LogFilterBar({
  logs,
  activeFilters,
  onToggle,
  grouped,
  onToggleGroup,
  autoScroll,
  onToggleAutoScroll,
  textFilter,
  onTextFilter,
  searchPlaceholder = "Search messages…",
  sourcesInLogs,
  hiddenSources,
  onToggleSource,
  onClearSourceFilter,
}: {
  logs?: ClientLogLine[];
  activeFilters?: Set<LogLevel>;
  onToggle?: (l: LogLevel) => void;
  grouped?: boolean;
  onToggleGroup?: () => void;
  autoScroll: boolean;
  onToggleAutoScroll: () => void;
  textFilter: string;
  onTextFilter: (v: string) => void;
  searchPlaceholder?: string;
  sourcesInLogs?: string[];
  hiddenSources?: Set<string>;
  onToggleSource?: (src: string) => void;
  onClearSourceFilter?: () => void;
}) {
  const showLevelPills =
    logs !== undefined && activeFilters !== undefined && onToggle !== undefined;
  const showGroupSimilar = grouped !== undefined && onToggleGroup !== undefined;
  const showSourceFilter =
    sourcesInLogs !== undefined &&
    sourcesInLogs.length > 1 &&
    hiddenSources !== undefined &&
    onToggleSource !== undefined;

  return (
    <>
      <div className="flex items-center gap-2 px-4 py-2 border-b bg-muted/30">
        <Filter className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        <input
          type="text"
          placeholder={searchPlaceholder}
          value={textFilter}
          onChange={(e) => onTextFilter(e.target.value)}
          className="flex-1 bg-transparent text-xs font-mono text-foreground placeholder-muted-foreground/50 outline-none"
        />
      </div>
      {showSourceFilter && (
        <div className="flex items-center gap-1 px-3 py-1.5 border-b flex-wrap bg-muted/5">
          <span className="text-[10px] text-muted-foreground/60 font-mono mr-1 select-none shrink-0">
            source:
          </span>
          {sourcesInLogs!.map((src) => {
            const isVisible = !hiddenSources!.has(src);
            return (
              <button
                key={src}
                onClick={() => onToggleSource!(src)}
                title={`${isVisible ? "Hide" : "Show only"} ${SOURCE_LABELS[src] ?? src} logs`}
                className={cn(
                  "inline-flex items-center gap-1 text-[10px] font-mono px-1.5 h-[18px] rounded border transition-colors select-none",
                  isVisible ? LEVEL_PILL_ACTIVE["info"] : LEVEL_PILL_INACTIVE,
                )}
              >
                {SOURCE_LABELS[src] ?? src}
              </button>
            );
          })}
          {hiddenSources!.size > 0 && onClearSourceFilter && (
            <>
              <div className="w-px h-3 bg-border/40 mx-0.5 shrink-0" />
              <button
                onClick={onClearSourceFilter}
                title="Show all sources"
                className="text-[10px] font-mono px-1.5 h-[18px] rounded border text-muted-foreground/60 border-border/30 hover:text-foreground hover:border-border transition-colors select-none"
              >
                reset
              </button>
            </>
          )}
        </div>
      )}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b flex-wrap bg-muted/10">
        {showLevelPills ? (
          <>
            <span className="text-[10px] text-muted-foreground/60 font-mono mr-1 select-none shrink-0">
              filter:
            </span>
            {ALL_LEVELS.map((level) => {
              const count = logs!.filter((l) => l.level === level).length;
              const isActive = activeFilters!.has(level);
              return (
                <button
                  key={level}
                  onClick={() => onToggle!(level)}
                  title={`${isActive ? "Hide" : "Show"} ${level} (${count})`}
                  className={cn(
                    "inline-flex items-center gap-1 text-[10px] font-mono px-1.5 h-[18px] rounded border transition-colors select-none",
                    isActive ? LEVEL_PILL_ACTIVE[level] : LEVEL_PILL_INACTIVE,
                  )}
                >
                  {LEVEL_LABEL[level].trim()}
                  {count > 0 && <span className="opacity-60">{count}</span>}
                </button>
              );
            })}
            {showGroupSimilar && (
              <>
                <div className="w-px h-3 bg-border/40 mx-0.5 shrink-0" />
                <button
                  onClick={onToggleGroup!}
                  title="Group identical INFO/OK/DATA/CMD messages — WARN and ERR always shown individually"
                  className={cn(
                    "inline-flex items-center gap-1 text-[10px] font-mono px-1.5 h-[18px] rounded border transition-colors select-none",
                    grouped
                      ? "bg-violet-100 text-violet-800 border-violet-300 dark:bg-violet-900/50 dark:text-violet-300 dark:border-violet-700"
                      : LEVEL_PILL_INACTIVE,
                  )}
                >
                  Group similar
                </button>
              </>
            )}
            <div className="w-px h-3 bg-border/40 mx-0.5 shrink-0" />
          </>
        ) : (
          <div className="flex-1" />
        )}
        <button
          onClick={onToggleAutoScroll}
          title={
            autoScroll
              ? "Auto-scroll ON — click to disable"
              : "Auto-scroll OFF — click to enable"
          }
          className={cn(
            "inline-flex items-center gap-1 text-[10px] font-mono px-1.5 h-[18px] rounded border transition-colors select-none",
            autoScroll
              ? "bg-zinc-200 text-zinc-800 border-zinc-300 dark:bg-zinc-700 dark:text-zinc-200 dark:border-zinc-500"
              : LEVEL_PILL_INACTIVE,
          )}
        >
          <ArrowDown className="h-2.5 w-2.5" />
          Scroll
        </button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Generic log tab (Server / Client / All)
// ---------------------------------------------------------------------------

function LogTab({
  logs,
  emptyMessage,
  clearSources,
}: {
  logs: ClientLogLine[];
  emptyMessage: string;
  clearSources: string[] | null;
}) {
  const [activeFilters, setActiveFilters] = useState<Set<LogLevel>>(
    () => new Set(ALL_LEVELS),
  );
  const [grouped, setGrouped] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [textFilter, setTextFilter] = useState("");
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(new Set());
  const [copiedAll, setCopiedAll] = useState(false);
  const [copiedView, setCopiedView] = useState(false);
  const [copiedReport, setCopiedReport] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // When paused, freeze the logs array so the rendered list never changes.
  // New entries keep accumulating in `logs` but are not rendered until resumed,
  // which prevents any scroll jank or layout shifts.
  const [frozenLogs, setFrozenLogs] = useState<ClientLogLine[] | null>(null);
  const activeLogs = frozenLogs ?? logs;

  // Distinct sources present in the current tab's log set (for source filter pills)
  const sourcesInLogs = useMemo(
    () =>
      [
        ...new Set(activeLogs.map((l) => l.source ?? "").filter(Boolean)),
      ].sort(),
    [activeLogs],
  );

  const toggleFilter = useCallback((level: LogLevel) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(level)) {
        if (next.size === 1) return prev;
        next.delete(level);
      } else {
        next.add(level);
      }
      return next;
    });
  }, []);

  const toggleSource = useCallback((src: string) => {
    setHiddenSources((prev) => {
      const next = new Set(prev);
      if (next.has(src)) next.delete(src);
      else next.add(src);
      return next;
    });
  }, []);

  const clearSourceFilter = useCallback(() => setHiddenSources(new Set()), []);

  const levelFiltered = useMemo(
    () =>
      activeFilters.size === ALL_LEVELS.length
        ? activeLogs
        : activeLogs.filter((l) => activeFilters.has(l.level)),
    [activeLogs, activeFilters],
  );

  const sourceFiltered = useMemo(
    () =>
      hiddenSources.size === 0
        ? levelFiltered
        : levelFiltered.filter((l) => !hiddenSources.has(l.source ?? "")),
    [levelFiltered, hiddenSources],
  );

  const filtered = useMemo(() => {
    if (!textFilter) return sourceFiltered;
    const q = textFilter.toLowerCase();
    return sourceFiltered.filter((l) => l.message.toLowerCase().includes(q));
  }, [sourceFiltered, textFilter]);

  const groupedRows = useMemo(
    () => (grouped ? groupSimilarLogs(filtered) : null),
    [filtered, grouped],
  );

  // useLayoutEffect runs before the browser paints — eliminates the one-frame
  // flash that useEffect causes when scrolling to bottom.
  useLayoutEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filtered, groupedRows, autoScroll]);

  // Count entries that arrived after the view was frozen
  const newLogCount = frozenLogs
    ? Math.max(0, logs.length - frozenLogs.length)
    : 0;

  const hasIssues = activeLogs.some(
    (l) => l.level === "error" || l.level === "warn",
  );

  const handleToggleAutoScroll = useCallback(() => {
    setAutoScroll((prev) => {
      if (prev) {
        // Pausing: snapshot the current logs so the list freezes
        setFrozenLogs(logs);
      } else {
        // Resuming: clear snapshot to re-join live stream
        setFrozenLogs(null);
      }
      return !prev;
    });
  }, [logs]);

  const handleCopyAll = useCallback(async () => {
    const body = logs
      .map(
        (l) =>
          `${l.time} ${LEVEL_LABEL[l.level]} [${l.source ?? ""}] ${l.message}`,
      )
      .join("\n");
    const now = new Date().toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    await navigator.clipboard.writeText(
      `=== Log (All) — ${now} ===\n${body}\n=== END ===`,
    );
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 2000);
  }, [logs]);

  const handleCopyView = useCallback(async () => {
    const rows =
      groupedRows ?? filtered.map((l) => ({ representative: l, count: 1 }));
    const body = rows
      .map(
        ({ representative: l, count }) =>
          `${l.time} ${LEVEL_LABEL[l.level]} [${l.source ?? ""}] ${l.message}${count > 1 ? ` ×${count}` : ""}`,
      )
      .join("\n");
    const now = new Date().toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    await navigator.clipboard.writeText(
      `=== Log (View) — ${now} ===\n${body}\n=== END ===`,
    );
    setCopiedView(true);
    setTimeout(() => setCopiedView(false), 2000);
  }, [filtered, groupedRows]);

  const handleCopyReport = useCallback(async () => {
    await navigator.clipboard.writeText(buildIssueReport(activeLogs));
    setCopiedReport(true);
    setTimeout(() => setCopiedReport(false), 2000);
  }, [activeLogs]);

  const handleClear = useCallback(() => {
    if (clearSources === null) clearClientLog();
    else clearSources.forEach(clearClientLogBySource);
  }, [clearSources]);

  const displayRows = groupedRows;
  const errCount = activeLogs.filter((l) => l.level === "error").length;
  const warnCount = activeLogs.filter((l) => l.level === "warn").length;

  return (
    <div className="flex h-full flex-col">
      {/* Action bar */}
      <div className="flex items-center justify-end gap-1 px-3 py-1.5 border-b bg-muted/20">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={handleCopyAll}
          disabled={logs.length === 0}
          title="Copy all — full buffer, no filters applied"
        >
          {copiedAll ? (
            <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={handleCopyView}
          disabled={filtered.length === 0}
          title="Copy view — respects active filters and grouping"
        >
          {copiedView ? (
            <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
          ) : (
            <ClipboardList className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={handleCopyReport}
          disabled={!hasIssues}
          title="Copy AI issue report — deduplicated errors and warnings with counts"
        >
          {copiedReport ? (
            <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
          ) : (
            <AlertTriangle className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={handleClear}
          disabled={logs.length === 0}
          title="Clear log"
        >
          <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
        </Button>
      </div>

      {/* Filter bar */}
      <LogFilterBar
        logs={activeLogs}
        activeFilters={activeFilters}
        onToggle={toggleFilter}
        grouped={grouped}
        onToggleGroup={() => setGrouped((v) => !v)}
        autoScroll={autoScroll}
        onToggleAutoScroll={handleToggleAutoScroll}
        textFilter={textFilter}
        onTextFilter={setTextFilter}
        sourcesInLogs={sourcesInLogs}
        hiddenSources={hiddenSources}
        onToggleSource={toggleSource}
        onClearSourceFilter={clearSourceFilter}
      />

      {/* Stats */}
      <div className="flex items-center gap-4 px-4 py-1 border-b text-xs text-muted-foreground bg-muted/10">
        <span>
          {filtered.length.toLocaleString()} line
          {filtered.length !== 1 ? "s" : ""}
        </span>
        {errCount > 0 && (
          <span className="flex items-center gap-1 text-red-700 dark:text-red-400">
            <AlertCircle className="h-3 w-3" />
            {errCount} error{errCount !== 1 ? "s" : ""}
          </span>
        )}
        {warnCount > 0 && (
          <span className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
            <AlertTriangle className="h-3 w-3" />
            {warnCount} warning{warnCount !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Rows */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto bg-muted/40">
        <div className="px-4 py-2 font-mono text-[11px] space-y-0.5">
          {filtered.length === 0 ? (
            <p className="text-muted-foreground py-8 text-center">
              {activeLogs.length === 0
                ? emptyMessage
                : "No logs match the current filters"}
            </p>
          ) : displayRows ? (
            displayRows.map(({ representative: l, count, key }) => (
              <div
                key={key}
                className={cn(
                  "flex gap-2 items-start hover:bg-muted/80 px-1 rounded",
                  LEVEL_COLOR[l.level],
                )}
              >
                <span className="text-muted-foreground/70 shrink-0 tabular-nums select-none">
                  {l.time}
                </span>
                <span className="font-semibold shrink-0 w-8 select-none">
                  {LEVEL_LABEL[l.level]}
                </span>
                {l.source && (
                  <span className="text-muted-foreground/70 shrink-0 select-none">
                    [{l.source}]
                  </span>
                )}
                <span className="break-all whitespace-pre-wrap flex-1">
                  {l.message}
                </span>
                {count > 1 && (
                  <span className="shrink-0 self-center text-[9px] font-bold bg-muted text-foreground border border-border rounded-full px-1.5 py-px tabular-nums select-none">
                    {count}
                  </span>
                )}
              </div>
            ))
          ) : (
            filtered.map((l) => (
              <div
                key={l.id}
                className={cn(
                  "flex gap-2 items-start hover:bg-muted/80 px-1 rounded",
                  LEVEL_COLOR[l.level],
                )}
              >
                <span className="text-muted-foreground/70 shrink-0 tabular-nums select-none">
                  {l.time}
                </span>
                <span className="font-semibold shrink-0 w-8 select-none">
                  {LEVEL_LABEL[l.level]}
                </span>
                {l.source && (
                  <span className="text-muted-foreground/70 shrink-0 select-none">
                    [{l.source}]
                  </span>
                )}
                <span className="break-all whitespace-pre-wrap flex-1">
                  {l.message}
                </span>
              </div>
            ))
          )}
        </div>
        {!autoScroll && newLogCount > 0 && (
          <div className="sticky bottom-0 flex items-center justify-center px-4 py-2 border-t border-border bg-background/95 backdrop-blur-sm">
            <button
              onClick={handleToggleAutoScroll}
              className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors"
            >
              ↓ {newLogCount} new log{newLogCount !== 1 ? "s" : ""} — click to
              resume
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HTTP tab
// ---------------------------------------------------------------------------

function HttpTab({ logs }: { logs: ClientLogLine[] }) {
  const [textFilter, setTextFilter] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [copiedAll, setCopiedAll] = useState(false);
  const [copiedView, setCopiedView] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Freeze the logs array when paused so the rendered list never shifts
  const [frozenLogs, setFrozenLogs] = useState<ClientLogLine[] | null>(null);
  const activeLogs = frozenLogs ?? logs;

  const entries = useMemo(
    () =>
      activeLogs
        .map((l) => l.accessEntry)
        .filter((e): e is AccessEntry => e != null),
    [activeLogs],
  );

  const filtered = useMemo(() => {
    if (!textFilter) return entries;
    const q = textFilter.toLowerCase();
    return entries.filter(
      (e) =>
        e.path.toLowerCase().includes(q) ||
        e.method.toLowerCase().includes(q) ||
        e.origin.toLowerCase().includes(q),
    );
  }, [entries, textFilter]);

  useLayoutEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filtered, autoScroll]);

  const newLogCount = frozenLogs
    ? Math.max(0, logs.length - frozenLogs.length)
    : 0;

  const handleToggleAutoScroll = useCallback(() => {
    setAutoScroll((prev) => {
      if (prev) {
        setFrozenLogs(logs);
      } else {
        setFrozenLogs(null);
      }
      return !prev;
    });
  }, [logs]);

  const formatHttpEntry = (e: AccessEntry) =>
    `${formatTime(e.timestamp)} ${e.method.padEnd(7)} ${e.path}${e.query ? `?${e.query}` : ""} ${e.status} ${e.duration_ms.toFixed(0)}ms`;

  const handleCopyAll = useCallback(async () => {
    const allEntries = logs
      .map((l) => l.accessEntry)
      .filter((e): e is AccessEntry => e != null);
    const body = allEntries.map(formatHttpEntry).join("\n");
    const now = new Date().toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    await navigator.clipboard.writeText(
      `=== HTTP Access Log (All) — ${now} ===\n${body}\n=== END ===`,
    );
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 2000);
  }, [logs]);

  const handleCopyView = useCallback(async () => {
    const body = filtered.map(formatHttpEntry).join("\n");
    const now = new Date().toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    await navigator.clipboard.writeText(
      `=== HTTP Access Log (View) — ${now} ===\n${body}\n=== END ===`,
    );
    setCopiedView(true);
    setTimeout(() => setCopiedView(false), 2000);
  }, [filtered]);

  const successCount = filtered.filter((e) => e.status < 400).length;
  const errorCount = filtered.filter((e) => e.status >= 400).length;
  const avgMs =
    filtered.length > 0
      ? Math.round(
          filtered.reduce((s, e) => s + e.duration_ms, 0) / filtered.length,
        )
      : 0;

  return (
    <div className="flex h-full flex-col">
      {/* Action bar — same position/layout as LogTab */}
      <div className="flex items-center justify-end gap-1 px-3 py-1.5 border-b bg-muted/20">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={handleCopyAll}
          disabled={activeLogs.length === 0}
          title="Copy all — full buffer, no filter applied"
        >
          {copiedAll ? (
            <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={handleCopyView}
          disabled={filtered.length === 0}
          title="Copy view — respects active text filter"
        >
          {copiedView ? (
            <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
          ) : (
            <ClipboardList className="h-3.5 w-3.5" />
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={() => clearClientLogBySource("access")}
          disabled={activeLogs.length === 0}
          title="Clear HTTP log"
        >
          <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
        </Button>
      </div>

      {/* Filter bar — same component as LogTab, without level pills */}
      <LogFilterBar
        autoScroll={autoScroll}
        onToggleAutoScroll={handleToggleAutoScroll}
        textFilter={textFilter}
        onTextFilter={setTextFilter}
        searchPlaceholder="Filter by path, method, origin…"
      />

      {/* Stats bar */}
      <div className="flex items-center gap-4 px-4 py-1 border-b text-xs text-muted-foreground bg-muted/10">
        <span className="flex items-center gap-1">
          <Globe className="h-3 w-3" />
          {filtered.length} request{filtered.length !== 1 ? "s" : ""}
        </span>
        <span className="flex items-center gap-1">
          <CheckCircle className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
          {successCount} success
        </span>
        <span
          className={cn(
            "flex items-center gap-1",
            errorCount > 0 ? "text-red-700 dark:text-red-400" : "",
          )}
        >
          <AlertCircle className="h-3 w-3" />
          {errorCount} error{errorCount !== 1 ? "s" : ""}
        </span>
        {filtered.length > 0 && (
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            avg {avgMs}ms
          </span>
        )}
      </div>

      {/* Rows */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto bg-muted/40">
        <div className="px-4 py-2 font-mono">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
              <ActivityIcon className="h-12 w-12 opacity-20 mb-4" />
              <p className="text-sm font-medium">No requests yet</p>
              <p className="text-xs mt-1">
                HTTP calls to the engine will appear here in real time
              </p>
            </div>
          ) : (
            filtered.map((e, i) => (
              <div
                key={i}
                className="grid gap-2 rounded px-1 py-0.5 text-[11px] hover:bg-muted/80 transition-colors"
                style={{ gridTemplateColumns: "5rem 3.5rem 1fr auto auto" }}
              >
                <span className="text-muted-foreground/70 tabular-nums">
                  {formatTime(e.timestamp)}
                </span>
                <span className={cn("font-bold", methodColor(e.method))}>
                  {e.method}
                </span>
                <span className="text-foreground truncate">
                  {e.path}
                  {e.query ? (
                    <span className="text-muted-foreground">?{e.query}</span>
                  ) : null}
                </span>
                <span className="text-muted-foreground tabular-nums text-right">
                  {e.duration_ms.toFixed(0)}ms
                </span>
                <span
                  className={cn(
                    "font-bold tabular-nums",
                    statusColor(e.status),
                  )}
                >
                  {e.status}
                </span>
              </div>
            ))
          )}
        </div>
        {!autoScroll && newLogCount > 0 && (
          <div className="sticky bottom-0 flex items-center justify-center px-4 py-2 border-t border-border bg-background/95 backdrop-blur-sm">
            <button
              onClick={handleToggleAutoScroll}
              className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors"
            >
              ↓ {newLogCount} new request{newLogCount !== 1 ? "s" : ""} — click
              to resume
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview tab
// ---------------------------------------------------------------------------

function OverviewTab({ logs }: { logs: ClientLogLine[] }) {
  const [copiedReport, setCopiedReport] = useState(false);

  const totalErrors = logs.filter((l) => l.level === "error").length;
  const totalWarns = logs.filter((l) => l.level === "warn").length;
  const httpCount = logs.filter((l) => l.source === "access").length;
  const hasIssues = totalErrors > 0 || totalWarns > 0;

  // Per-source breakdown
  const sourceOrder = [
    "server",
    "tauri",
    "syslog",
    "llm",
    "engine",
    "auth",
    "voice",
    "setup",
    "access",
  ];
  type SourceStats = {
    info: number;
    success: number;
    warn: number;
    error: number;
    data: number;
    cmd: number;
    lastErr: string;
    lastWarn: string;
  };
  const sourceMap = useMemo(() => {
    const map = new Map<string, SourceStats>();
    const seen: string[] = [...sourceOrder];
    logs.forEach((l) => {
      const s = l.source ?? "unknown";
      if (!seen.includes(s)) seen.push(s);
    });
    seen.forEach((s) =>
      map.set(s, {
        info: 0,
        success: 0,
        warn: 0,
        error: 0,
        data: 0,
        cmd: 0,
        lastErr: "",
        lastWarn: "",
      }),
    );
    logs.forEach((l) => {
      const s = l.source ?? "unknown";
      if (!map.has(s))
        map.set(s, {
          info: 0,
          success: 0,
          warn: 0,
          error: 0,
          data: 0,
          cmd: 0,
          lastErr: "",
          lastWarn: "",
        });
      const e = map.get(s)!;
      e[l.level]++;
      if (l.level === "error") e.lastErr = `${l.time} ${l.message}`;
      if (l.level === "warn") e.lastWarn = `${l.time} ${l.message}`;
    });
    return map;
  }, [logs]);

  const activeSources = useMemo(
    () =>
      [...sourceMap.entries()].filter(
        ([, v]) => v.info + v.success + v.warn + v.error + v.data + v.cmd > 0,
      ),
    [sourceMap],
  );

  const handleCopyReport = useCallback(async () => {
    await navigator.clipboard.writeText(buildIssueReport(logs));
    setCopiedReport(true);
    setTimeout(() => setCopiedReport(false), 2000);
  }, [logs]);

  if (logs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 gap-3 text-muted-foreground py-20">
        <ActivityIcon className="h-12 w-12 opacity-20" />
        <p className="text-sm font-medium">No log activity yet</p>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      {/* Summary cards */}
      <div className="flex items-stretch gap-3 flex-wrap">
        <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 flex flex-col gap-0.5 min-w-[90px]">
          <span className="text-[9px] text-muted-foreground font-mono uppercase tracking-wider">
            Lines
          </span>
          <span className="text-xl font-mono text-foreground">
            {logs.length.toLocaleString()}
          </span>
        </div>
        <div
          className={cn(
            "rounded-lg border px-4 py-3 flex flex-col gap-0.5 min-w-[80px]",
            totalErrors > 0
              ? "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/50"
              : "border-border bg-muted/20",
          )}
        >
          <span className="text-[9px] text-muted-foreground font-mono uppercase tracking-wider flex items-center gap-1">
            <AlertCircle className="h-2.5 w-2.5" />
            Errors
          </span>
          <span
            className={cn(
              "text-xl font-mono",
              totalErrors > 0
                ? "text-red-700 dark:text-red-400"
                : "text-muted-foreground",
            )}
          >
            {totalErrors}
          </span>
        </div>
        <div
          className={cn(
            "rounded-lg border px-4 py-3 flex flex-col gap-0.5 min-w-[80px]",
            totalWarns > 0
              ? "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/50"
              : "border-border bg-muted/20",
          )}
        >
          <span className="text-[9px] text-muted-foreground font-mono uppercase tracking-wider flex items-center gap-1">
            <AlertTriangle className="h-2.5 w-2.5" />
            Warns
          </span>
          <span
            className={cn(
              "text-xl font-mono",
              totalWarns > 0
                ? "text-amber-700 dark:text-amber-400"
                : "text-muted-foreground",
            )}
          >
            {totalWarns}
          </span>
        </div>
        <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 flex flex-col gap-0.5 min-w-[80px]">
          <span className="text-[9px] text-muted-foreground font-mono uppercase tracking-wider flex items-center gap-1">
            <Globe className="h-2.5 w-2.5" />
            HTTP
          </span>
          <span className="text-xl font-mono text-foreground">
            {httpCount.toLocaleString()}
          </span>
        </div>
        <button
          onClick={handleCopyReport}
          disabled={!hasIssues}
          title="Copy structured error/warning report — paste to AI for debugging"
          className={cn(
            "flex flex-col items-center justify-center gap-1 rounded-lg border px-5 py-3 text-[11px] font-mono transition-colors disabled:opacity-30 min-w-[120px] text-center",
            hasIssues
              ? "border-border bg-muted hover:bg-muted/80 text-foreground"
              : "border-border bg-muted/20 text-muted-foreground",
          )}
        >
          {copiedReport ? (
            <>
              <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
              <span className="text-emerald-600 dark:text-emerald-400">
                Copied!
              </span>
            </>
          ) : (
            <>
              <Copy className="h-4 w-4" />
              <span>
                Copy Issue
                <br />
                Report
              </span>
            </>
          )}
        </button>
      </div>

      {/* Per-source breakdown */}
      <div className="space-y-2">
        <p className="text-[10px] text-muted-foreground font-mono uppercase tracking-wider">
          By Source
        </p>
        {activeSources.map(([source, counts]) => {
          const total =
            counts.info +
            counts.success +
            counts.warn +
            counts.error +
            counts.data +
            counts.cmd;
          return (
            <div
              key={source}
              className={cn(
                "rounded-lg border bg-muted/10 p-3",
                counts.error > 0
                  ? "border-red-300 dark:border-red-900"
                  : counts.warn > 0
                    ? "border-amber-300 dark:border-amber-900"
                    : "border-border",
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-mono text-foreground">
                  {SOURCE_LABELS[source] ?? source}
                </span>
                <span className="text-[10px] font-mono text-muted-foreground">
                  {total.toLocaleString()} lines
                </span>
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                {(
                  [
                    "error",
                    "warn",
                    "success",
                    "info",
                    "data",
                    "cmd",
                  ] as LogLevel[]
                ).map((level) => {
                  const n = counts[level];
                  if (n === 0) return null;
                  return (
                    <span
                      key={level}
                      className={cn(
                        "text-[10px] font-mono px-1.5 py-0 rounded border",
                        LEVEL_PILL_ACTIVE[level],
                      )}
                    >
                      {LEVEL_LABEL[level].trim()} {n}
                    </span>
                  );
                })}
              </div>
              {counts.lastErr && (
                <p className="mt-1.5 text-[10px] text-red-700 dark:text-red-400 font-mono truncate">
                  ↳ {counts.lastErr}
                </p>
              )}
              {!counts.lastErr && counts.lastWarn && (
                <p className="mt-1.5 text-[10px] text-amber-700 dark:text-amber-400 font-mono truncate">
                  ↳ {counts.lastWarn}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export function Activity({ engineStatus }: ActivityProps) {
  const paused = useLogsPaused();
  const [tab, setTab] = useState<TabId>("overview");
  const allLogs = useClientLogSubscriber();

  const serverLogs = useMemo(
    () => allLogs.filter(tabFilter("server")),
    [allLogs],
  );
  const clientLogs = useMemo(
    () => allLogs.filter(tabFilter("client")),
    [allLogs],
  );
  const httpLogs = useMemo(() => allLogs.filter(tabFilter("http")), [allLogs]);

  // Badge counts
  const serverErrors = serverLogs.filter((l) => l.level === "error").length;
  const serverWarns = serverLogs.filter((l) => l.level === "warn").length;
  const clientErrors = clientLogs.filter((l) => l.level === "error").length;
  const clientWarns = clientLogs.filter((l) => l.level === "warn").length;
  const httpErrors = httpLogs.filter((l) => l.level === "error").length;
  const httpWarns = httpLogs.filter((l) => l.level === "warn").length;
  const totalErrors = allLogs.filter((l) => l.level === "error").length;
  const totalWarns = allLogs.filter((l) => l.level === "warn").length;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Activity"
        description="Real-time request & log monitor"
      >
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setLogsPaused(!paused)}
            title={paused ? "Resume live streams" : "Pause live streams"}
          >
            {paused ? (
              <Play className="h-4 w-4" />
            ) : (
              <Pause className="h-4 w-4" />
            )}
          </Button>
          {paused && (
            <Badge variant="warning" className="text-[10px]">
              PAUSED
            </Badge>
          )}
          {engineStatus !== "connected" && (
            <Badge variant="destructive" className="text-[10px]">
              ENGINE OFFLINE
            </Badge>
          )}
        </div>
      </PageHeader>

      <Tabs
        value={tab}
        onValueChange={(v) => setTab(v as TabId)}
        className="flex flex-col flex-1 overflow-hidden"
      >
        <TabsList className="mx-4 mt-2 self-start flex-wrap gap-0.5">
          <TabsTrigger value="overview" className="flex items-center gap-1.5">
            <LayoutDashboard className="h-3.5 w-3.5" />
            Overview
            {totalErrors > 0 && <TabBadge errors={totalErrors} warns={0} />}
            {totalErrors === 0 && totalWarns > 0 && (
              <TabBadge errors={0} warns={totalWarns} />
            )}
          </TabsTrigger>
          <TabsTrigger value="server" className="flex items-center gap-1.5">
            <Server className="h-3.5 w-3.5" />
            Server
            <TabBadge errors={serverErrors} warns={serverWarns} />
          </TabsTrigger>
          <TabsTrigger value="client" className="flex items-center gap-1.5">
            <Monitor className="h-3.5 w-3.5" />
            Client
            <TabBadge errors={clientErrors} warns={clientWarns} />
          </TabsTrigger>
          <TabsTrigger value="http" className="flex items-center gap-1.5">
            <Globe className="h-3.5 w-3.5" />
            HTTP
            <TabBadge errors={httpErrors} warns={httpWarns} />
          </TabsTrigger>
          <TabsTrigger value="all" className="flex items-center gap-1.5">
            <List className="h-3.5 w-3.5" />
            All
            <TabBadge errors={totalErrors} warns={totalWarns} />
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex-1 overflow-hidden mt-2">
          <OverviewTab logs={allLogs} />
        </TabsContent>

        <TabsContent value="server" className="flex-1 overflow-hidden mt-2">
          <LogTab
            logs={serverLogs}
            emptyMessage="No server logs yet — engine stdout/stderr and syslog will appear here"
            clearSources={["server", "tauri", "syslog", "llm"]}
          />
        </TabsContent>

        <TabsContent value="client" className="flex-1 overflow-hidden mt-2">
          <LogTab
            logs={clientLogs}
            emptyMessage="No client logs yet — engine discovery, auth, voice, and setup events appear here"
            clearSources={[
              "engine",
              "auth",
              "voice",
              "setup",
              "bg-tasks",
              "react",
              "downloads",
              "client",
            ]}
          />
        </TabsContent>

        <TabsContent value="http" className="flex-1 overflow-hidden mt-2">
          <HttpTab logs={httpLogs} />
        </TabsContent>

        <TabsContent value="all" className="flex-1 overflow-hidden mt-2">
          <LogTab
            logs={allLogs}
            emptyMessage="No log activity yet"
            clearSources={null}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
