/**
 * DevTerminalPanel — Unified, persistent debug terminal.
 *
 * Tabs:
 *   Overview  — live summary: counts by source & level, last error/warn per source
 *   Server    — engine SSE / sidecar-log / raw system.log (source="server"|"tauri"|"syslog")
 *   Client    — auth, engine discovery, voice, setup (all other sources)
 *   HTTP      — structured HTTP request log (source="access")
 *   All       — every log line regardless of source
 *
 * Features:
 *   - Level filter pills per tab (toggle individual levels; counts shown)
 *   - Group similar toggle — collapses identical INFO/OK/DATA/CMD lines; WARN/ERR always individual
 *   - Auto-scroll toggle (off by default)
 *   - Pause/Resume — freezes incoming live events without closing streams
 *   - Copy button copies the currently visible (filtered+grouped) lines
 *   - Copy Issue Report on Overview — structured error/warning digest for AI debugging
 *   - Clear scoped to the active tab's source set
 *   - Drag handle to resize height
 *   - HTTP tab: structured grid with method/status color-coding, text filter, stats bar
 *   - Toggle via DOM event from TerminalToggleButton in the status bar
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Terminal,
  X,
  Copy,
  ClipboardList,
  Check,
  Trash2,
  LayoutDashboard,
  Server,
  Monitor,
  List,
  AlertTriangle,
  AlertCircle,
  Activity,
  Pause,
  Play,
  ArrowDown,
  Globe,
  Clock,
  CheckCircle,
  Filter,
} from "lucide-react";
import { cn } from "@/lib/utils";
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
// Context — broadcasts panel height so AppLayout can compensate
// ---------------------------------------------------------------------------

interface DevTerminalContextValue {
  panelHeight: number;
}

const DevTerminalContext = createContext<DevTerminalContextValue>({
  panelHeight: 0,
});

export function useDevTerminalHeight(): number {
  return useContext(DevTerminalContext).panelHeight;
}

interface DevTerminalProviderProps {
  children: React.ReactNode;
}

export function DevTerminalProvider({ children }: DevTerminalProviderProps) {
  const [panelHeight, setPanelHeight] = useState(0);

  useEffect(() => {
    const handler = (e: Event) => {
      const custom = e as CustomEvent<number>;
      setPanelHeight(custom.detail ?? 0);
    };
    window.addEventListener(DEV_TERMINAL_HEIGHT_EVENT, handler);
    return () => window.removeEventListener(DEV_TERMINAL_HEIGHT_EVENT, handler);
  }, []);

  return (
    <DevTerminalContext.Provider value={{ panelHeight }}>
      {children}
    </DevTerminalContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// DOM events
// ---------------------------------------------------------------------------

export const DEV_TERMINAL_TOGGLE_EVENT = "dev-terminal-toggle";
export const DEV_TERMINAL_HEIGHT_EVENT = "dev-terminal-height";

export function toggleDevTerminal() {
  window.dispatchEvent(new CustomEvent(DEV_TERMINAL_TOGGLE_EVENT));
}

// ---------------------------------------------------------------------------
// Constants & helpers
// ---------------------------------------------------------------------------

const MIN_HEIGHT = 160;
const DEFAULT_HEIGHT = 320;
const MAX_HEIGHT = 750;

const ALL_LEVELS: LogLevel[] = [
  "info",
  "success",
  "warn",
  "error",
  "data",
  "cmd",
];

// Sources that live in the "Server" tab (includes syslog and llama-server)
const SERVER_SOURCES = new Set(["server", "tauri", "syslog", "llm"]);

// Sources that live in the "Client" tab
const CLIENT_SOURCES = new Set([
  "engine",
  "auth",
  "voice",
  "setup",
  "bg-tasks",
]);

// Source for the HTTP tab
const HTTP_SOURCE = "access";

const LEVEL_COLOR: Record<LogLevel, string> = {
  info: "text-zinc-400",
  success: "text-emerald-400",
  warn: "text-amber-400",
  error: "text-red-400",
  data: "text-sky-400",
  cmd: "text-cyan-400",
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
  info: "bg-zinc-700 text-zinc-200 border-zinc-500",
  success: "bg-emerald-900/50 text-emerald-300 border-emerald-700",
  warn: "bg-amber-900/50 text-amber-300 border-amber-700",
  error: "bg-red-900/50 text-red-300 border-red-700",
  data: "bg-sky-900/50 text-sky-300 border-sky-700",
  cmd: "bg-cyan-900/50 text-cyan-300 border-cyan-700",
};

const LEVEL_PILL_INACTIVE =
  "text-zinc-600 border-zinc-800 hover:text-zinc-400 hover:border-zinc-700";

// ---------------------------------------------------------------------------
// Group-similar logic
// ---------------------------------------------------------------------------

// Only these levels are eligible for grouping; warn/error always show individually
const GROUPABLE_LEVELS = new Set<LogLevel>(["info", "success", "data", "cmd"]);

interface GroupedLogRow {
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

function groupSimilarLogs(logs: ClientLogLine[]): GroupedLogRow[] {
  const rows: GroupedLogRow[] = [];
  const groupMap = new Map<string, GroupedLogRow>();

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
        const row: GroupedLogRow = { representative: line, count: 1, key };
        groupMap.set(key, row);
        rows.push(row);
      }
    }
  }

  return rows;
}

// ---------------------------------------------------------------------------
// HTTP helpers (from Activity.tsx)
// ---------------------------------------------------------------------------

function statusColor(code: number): string {
  if (code < 300) return "text-emerald-400";
  if (code < 400) return "text-amber-400";
  if (code < 500) return "text-orange-400";
  return "text-red-400";
}

function methodColor(method: string): string {
  switch (method.toUpperCase()) {
    case "GET":
      return "text-sky-400";
    case "POST":
      return "text-violet-400";
    case "PUT":
      return "text-amber-400";
    case "DELETE":
      return "text-red-400";
    case "PATCH":
      return "text-orange-400";
    default:
      return "text-zinc-400";
  }
}

function formatAccessTime(iso: string): string {
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

// ---------------------------------------------------------------------------
// Filter pill row sub-component
// ---------------------------------------------------------------------------

// Shared filter toolbar for all panes (Log and HTTP).
// Level pills and group-similar are optional — omit them for the HTTP pane.
// Source pills are shown automatically when multiple sources are present.
function LevelFilters({
  logs,
  activeFilters,
  onToggle,
  groupSimilar,
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
  onToggle?: (level: LogLevel) => void;
  groupSimilar?: boolean;
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
  const showGroupSimilar =
    groupSimilar !== undefined && onToggleGroup !== undefined;
  const showSourceFilter =
    sourcesInLogs !== undefined &&
    sourcesInLogs.length > 1 &&
    hiddenSources !== undefined &&
    onToggleSource !== undefined;

  return (
    <>
      {/* Search row */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800/60">
        <Filter className="h-3 w-3 text-zinc-600 shrink-0" />
        <input
          type="text"
          placeholder={searchPlaceholder}
          value={textFilter}
          onChange={(e) => onTextFilter(e.target.value)}
          className="flex-1 bg-transparent text-[11px] font-mono text-zinc-300 placeholder-zinc-700 outline-none"
        />
      </div>
      {/* Source filter row */}
      {showSourceFilter && (
        <div className="flex items-center gap-1 px-3 py-1 border-b border-zinc-800/60 flex-wrap">
          <span className="text-[10px] text-zinc-600 font-mono mr-1 select-none shrink-0">
            source:
          </span>
          {sourcesInLogs!.map((src) => {
            const isVisible = !hiddenSources!.has(src);
            return (
              <button
                key={src}
                onClick={() => onToggleSource!(src)}
                title={`${isVisible ? "Hide" : "Show"} ${SOURCE_LABELS[src] ?? src} logs`}
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
              <div className="w-px h-3 bg-zinc-800 mx-0.5 shrink-0" />
              <button
                onClick={onClearSourceFilter}
                title="Show all sources"
                className="text-[10px] font-mono px-1.5 h-[18px] rounded border text-zinc-600 border-zinc-800 hover:text-zinc-400 hover:border-zinc-700 transition-colors select-none"
              >
                reset
              </button>
            </>
          )}
        </div>
      )}
      {/* Pills row */}
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-zinc-800/60 flex-wrap">
        {showLevelPills ? (
          <>
            <span className="text-[10px] text-zinc-600 font-mono mr-1 select-none shrink-0">
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
                <div className="w-px h-3 bg-zinc-800 mx-0.5 shrink-0" />
                <button
                  onClick={onToggleGroup!}
                  title="Group identical INFO/OK/DATA/CMD messages — WARN and ERR always shown individually"
                  className={cn(
                    "inline-flex items-center gap-1 text-[10px] font-mono px-1.5 h-[18px] rounded border transition-colors select-none",
                    groupSimilar
                      ? "bg-violet-900/50 text-violet-300 border-violet-700"
                      : "text-zinc-600 border-zinc-800 hover:text-zinc-400 hover:border-zinc-700",
                  )}
                >
                  Group similar
                </button>
              </>
            )}
            <div className="w-px h-3 bg-zinc-800 mx-0.5 shrink-0" />
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
              ? "bg-zinc-700 text-zinc-200 border-zinc-500"
              : "text-zinc-600 border-zinc-800 hover:text-zinc-400 hover:border-zinc-700",
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
// Log row
// ---------------------------------------------------------------------------

function LogRow({
  line,
  repeatCount,
}: {
  line: ClientLogLine;
  repeatCount?: number;
}) {
  return (
    <div className="flex gap-2 min-w-0 hover:bg-zinc-900 px-1 rounded items-start">
      <span className="text-zinc-700 shrink-0 tabular-nums select-none text-[10px] pt-px">
        {line.time}
      </span>
      <span
        className={cn(
          "shrink-0 select-none tabular-nums font-semibold text-[10px] pt-px w-8",
          LEVEL_COLOR[line.level],
        )}
      >
        {LEVEL_LABEL[line.level]}
      </span>
      {line.source && (
        <span className="text-zinc-600 shrink-0 select-none text-[10px] pt-px">
          [{line.source}]
        </span>
      )}
      <span
        className={cn(
          "break-all whitespace-pre-wrap text-[11px] flex-1",
          LEVEL_COLOR[line.level],
        )}
      >
        {line.message}
      </span>
      {repeatCount !== undefined && repeatCount > 1 && (
        <span className="shrink-0 self-center text-[9px] font-mono font-bold bg-zinc-700 text-zinc-300 rounded-full px-1.5 py-px tabular-nums select-none">
          {repeatCount}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// HTTP row (structured access entry display)
// ---------------------------------------------------------------------------

function HttpRow({ entry }: { entry: AccessEntry }) {
  return (
    <div
      className="grid gap-2 rounded px-2 py-1 text-[11px] font-mono hover:bg-zinc-900 transition-colors"
      style={{ gridTemplateColumns: "5rem 3.5rem 1fr auto auto" }}
    >
      <span className="text-zinc-600 tabular-nums select-none">
        {formatAccessTime(entry.timestamp)}
      </span>
      <span className={cn("font-bold", methodColor(entry.method))}>
        {entry.method}
      </span>
      <span className="text-zinc-300 truncate">
        {entry.path}
        {entry.query ? (
          <span className="text-zinc-600">?{entry.query}</span>
        ) : null}
      </span>
      <span className="text-zinc-600 tabular-nums text-right">
        {entry.duration_ms.toFixed(0)}ms
      </span>
      <span className={cn("font-bold tabular-nums", statusColor(entry.status))}>
        {entry.status}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HTTP pane (text-filtered, stats bar, auto-scroll)
// ---------------------------------------------------------------------------

function HttpPane({
  logs,
  onCopyTextChange,
}: {
  logs: ClientLogLine[];
  onCopyTextChange: (text: string) => void;
}) {
  const [textFilter, setTextFilter] = useState("");
  const [autoScroll, setAutoScroll] = useState(false);
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

  // Notify parent with copy text
  const onCopyTextChangeRef = useRef(onCopyTextChange);
  onCopyTextChangeRef.current = onCopyTextChange;
  useEffect(() => {
    const text = filtered
      .map(
        (e) =>
          `${formatAccessTime(e.timestamp)} ${e.method.padEnd(7)} ${e.path}${e.query ? `?${e.query}` : ""} ${e.status} ${e.duration_ms.toFixed(0)}ms`,
      )
      .join("\n");
    onCopyTextChangeRef.current(text);
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
    <>
      {/* Filter bar — same component as LogPane, without level pills */}
      <LevelFilters
        autoScroll={autoScroll}
        onToggleAutoScroll={handleToggleAutoScroll}
        textFilter={textFilter}
        onTextFilter={setTextFilter}
        searchPlaceholder="Filter by path, method, origin…"
      />
      {/* Stats bar */}
      <div className="flex items-center gap-4 px-3 py-1 border-b border-zinc-800/60 text-[10px] font-mono text-zinc-600">
        <span className="flex items-center gap-1">
          <Globe className="h-2.5 w-2.5" />
          {filtered.length} req
        </span>
        <span className="flex items-center gap-1 text-emerald-600">
          <CheckCircle className="h-2.5 w-2.5" />
          {successCount} ok
        </span>
        <span
          className={cn(
            "flex items-center gap-1",
            errorCount > 0 ? "text-red-400" : "text-zinc-600",
          )}
        >
          <AlertCircle className="h-2.5 w-2.5" />
          {errorCount} err
        </span>
        {filtered.length > 0 && (
          <span className="flex items-center gap-1">
            <Clock className="h-2.5 w-2.5" />
            avg {avgMs}ms
          </span>
        )}
      </div>
      {/* Rows */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-1">
        {filtered.length === 0 ? (
          <div className="flex h-full min-h-[80px] items-center justify-center text-zinc-700 text-[11px]">
            {entries.length === 0
              ? "No HTTP requests yet"
              : "No requests match the filter"}
          </div>
        ) : (
          filtered.map((entry, i) => <HttpRow key={i} entry={entry} />)
        )}
        {!autoScroll && newLogCount > 0 && (
          <div className="sticky bottom-0 flex items-center justify-center px-3 py-1.5 border-t border-zinc-800/80 bg-zinc-950/95 backdrop-blur-sm">
            <button
              onClick={handleToggleAutoScroll}
              className="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              ↓ {newLogCount} new request{newLogCount !== 1 ? "s" : ""} — click
              to resume
            </button>
          </div>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Log pane (filterable, groupable, auto-scroll, no forced scroll)
// ---------------------------------------------------------------------------

function LogPane({
  logs,
  emptyMessage,
  filterKey,
  onVisibleChange,
}: {
  logs: ClientLogLine[];
  emptyMessage: string;
  filterKey: string;
  /** Called with the copy-ready text string whenever the visible set changes. */
  onVisibleChange: (copyText: string) => void;
}) {
  const [activeFilters, setActiveFilters] = useState<Set<LogLevel>>(
    () => new Set(ALL_LEVELS),
  );
  const [grouped, setGrouped] = useState(false);
  const [autoScroll, setAutoScroll] = useState(false);
  const [textFilter, setTextFilter] = useState("");
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  // Freeze the logs array when paused so the rendered list never shifts
  const [frozenLogs, setFrozenLogs] = useState<ClientLogLine[] | null>(null);
  const activeLogs = frozenLogs ?? logs;

  // Reset filters when the tab source set changes — including any frozen
  // snapshot, which would otherwise bleed the previous tab's log set into
  // this one (and skew the new-log counter).
  useEffect(() => {
    setActiveFilters(new Set(ALL_LEVELS));
    setTextFilter("");
    setHiddenSources(new Set());
    setFrozenLogs(null);
  }, [filterKey]);

  // Distinct sources present in the current pane's log set
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

  useLayoutEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filtered, groupedRows, autoScroll]);

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

  // Build copy text and notify parent
  const onVisibleChangeRef = useRef(onVisibleChange);
  onVisibleChangeRef.current = onVisibleChange;
  useEffect(() => {
    let text: string;
    if (groupedRows) {
      text = groupedRows
        .map(({ representative: l, count }) => {
          const suffix = count > 1 ? ` ×${count}` : "";
          return `${l.time} ${LEVEL_LABEL[l.level]} [${l.source ?? "app"}] ${l.message}${suffix}`;
        })
        .join("\n");
    } else {
      text = filtered
        .map(
          (l) =>
            `${l.time} ${LEVEL_LABEL[l.level]} [${l.source ?? "app"}] ${l.message}`,
        )
        .join("\n");
    }
    onVisibleChangeRef.current(text);
  }, [filtered, groupedRows]);

  return (
    <>
      <LevelFilters
        logs={activeLogs}
        activeFilters={activeFilters}
        onToggle={toggleFilter}
        groupSimilar={grouped}
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
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-2 space-y-px font-mono"
      >
        {filtered.length === 0 ? (
          <div className="flex h-full min-h-[80px] items-center justify-center text-zinc-700 text-[11px]">
            {activeLogs.length === 0
              ? emptyMessage
              : "No logs match the active filters"}
          </div>
        ) : groupedRows ? (
          groupedRows.map(({ representative, count, key }) => (
            <LogRow key={key} line={representative} repeatCount={count} />
          ))
        ) : (
          filtered.map((line) => <LogRow key={line.id} line={line} />)
        )}
        {!autoScroll && newLogCount > 0 && (
          <div className="sticky bottom-0 flex items-center justify-center px-3 py-1.5 border-t border-zinc-800/80 bg-zinc-950/95 backdrop-blur-sm">
            <button
              onClick={handleToggleAutoScroll}
              className="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              ↓ {newLogCount} new log{newLogCount !== 1 ? "s" : ""} — click to
              resume
            </button>
          </div>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Overview tab
// ---------------------------------------------------------------------------

const SOURCE_ORDER = [
  "server",
  "tauri",
  "syslog",
  "engine",
  "auth",
  "voice",
  "setup",
  "access",
];
const SOURCE_LABELS: Record<string, string> = {
  server: "Engine (SSE)",
  tauri: "Sidecar IPC",
  syslog: "System Log",
  engine: "Engine Client",
  auth: "Auth",
  voice: "Voice",
  setup: "Setup Wizard",
  access: "HTTP Requests",
  react: "React / Console",
};

interface SourceStats {
  info: number;
  success: number;
  warn: number;
  error: number;
  data: number;
  cmd: number;
  errors: string[];
  warns: string[];
}

function buildSourceMap(logs: ClientLogLine[]): Map<string, SourceStats> {
  const map = new Map<string, SourceStats>();
  const knownSources = [...SOURCE_ORDER];
  logs.forEach((l) => {
    const s = l.source ?? "unknown";
    if (!knownSources.includes(s)) knownSources.push(s);
  });
  knownSources.forEach((s) => {
    map.set(s, {
      info: 0,
      success: 0,
      warn: 0,
      error: 0,
      data: 0,
      cmd: 0,
      errors: [],
      warns: [],
    });
  });
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
        errors: [],
        warns: [],
      });
    const e = map.get(s)!;
    e[l.level]++;
    if (l.level === "error") e.errors.push(`  ${l.time} ${l.message}`);
    if (l.level === "warn") e.warns.push(`  ${l.time} ${l.message}`);
  });
  return map;
}

function buildIssueReport(logs: ClientLogLine[]): string {
  const map = buildSourceMap(logs);
  const now = new Date().toLocaleString("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  const totalErrors = logs.filter((l) => l.level === "error").length;
  const totalWarns = logs.filter((l) => l.level === "warn").length;

  const lines: string[] = [
    `=== Matrx Issue Report — ${now} ===`,
    `Total: ${logs.length} log lines | ${totalErrors} errors | ${totalWarns} warnings`,
    "",
  ];

  for (const [source, stats] of map.entries()) {
    if (stats.error === 0 && stats.warn === 0) continue;
    const label = SOURCE_LABELS[source] ?? source;
    lines.push(`── ${label} ──`);
    lines.push(`   errors: ${stats.error}  warnings: ${stats.warn}`);
    if (stats.errors.length > 0) {
      lines.push("   ERRORS:");
      stats.errors.forEach((m) => lines.push(m));
    }
    if (stats.warns.length > 0) {
      lines.push("   WARNINGS:");
      stats.warns.forEach((m) => lines.push(m));
    }
    lines.push("");
  }

  if (lines[lines.length - 1] === "") lines.pop();
  lines.push("=== END ===");
  return lines.join("\n");
}

function OverviewTab({ logs }: { logs: ClientLogLine[] }) {
  const [copiedReport, setCopiedReport] = useState(false);

  const sourceMap = useMemo(() => buildSourceMap(logs), [logs]);
  const sources = useMemo(
    () =>
      [...sourceMap.entries()].filter(
        ([, v]) => v.info + v.success + v.warn + v.error + v.data + v.cmd > 0,
      ),
    [sourceMap],
  );

  const totalErrors = logs.filter((l) => l.level === "error").length;
  const totalWarns = logs.filter((l) => l.level === "warn").length;
  const httpEntries = logs.filter((l) => l.source === "access");
  const httpErrors = httpEntries.filter(
    (l) => l.level === "error" || l.level === "warn",
  ).length;
  const hasIssues = totalErrors > 0 || totalWarns > 0;

  const copyIssueReport = useCallback(async () => {
    const report = buildIssueReport(logs);
    await navigator.clipboard.writeText(report);
    setCopiedReport(true);
    setTimeout(() => setCopiedReport(false), 2000);
  }, [logs]);

  if (logs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 gap-3 text-zinc-700">
        <Terminal className="h-8 w-8 opacity-30" />
        <p className="text-[12px] font-mono">No log activity yet</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 space-y-3">
      {/* Top-line summary + copy report button */}
      <div className="flex items-stretch gap-2">
        <div className="flex gap-2 flex-1 flex-wrap">
          <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 flex flex-col gap-0.5 min-w-[80px]">
            <span className="text-[9px] text-zinc-600 font-mono uppercase tracking-wider">
              Lines
            </span>
            <span className="text-lg font-mono text-zinc-300">
              {logs.length.toLocaleString()}
            </span>
          </div>
          <div
            className={cn(
              "rounded-lg border px-3 py-2 flex flex-col gap-0.5 min-w-[72px]",
              totalErrors > 0
                ? "border-red-900 bg-red-950"
                : "border-zinc-800 bg-zinc-900",
            )}
          >
            <span className="text-[9px] text-zinc-600 font-mono uppercase tracking-wider flex items-center gap-1">
              <AlertCircle className="h-2.5 w-2.5" />
              Errors
            </span>
            <span
              className={cn(
                "text-lg font-mono",
                totalErrors > 0 ? "text-red-400" : "text-zinc-600",
              )}
            >
              {totalErrors}
            </span>
          </div>
          <div
            className={cn(
              "rounded-lg border px-3 py-2 flex flex-col gap-0.5 min-w-[72px]",
              totalWarns > 0
                ? "border-amber-900 bg-amber-950"
                : "border-zinc-800 bg-zinc-900",
            )}
          >
            <span className="text-[9px] text-zinc-600 font-mono uppercase tracking-wider flex items-center gap-1">
              <AlertTriangle className="h-2.5 w-2.5" />
              Warns
            </span>
            <span
              className={cn(
                "text-lg font-mono",
                totalWarns > 0 ? "text-amber-400" : "text-zinc-600",
              )}
            >
              {totalWarns}
            </span>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 flex flex-col gap-0.5 min-w-[72px]">
            <span className="text-[9px] text-zinc-600 font-mono uppercase tracking-wider flex items-center gap-1">
              <Globe className="h-2.5 w-2.5" />
              HTTP
            </span>
            <span
              className={cn(
                "text-lg font-mono",
                httpErrors > 0 ? "text-amber-400" : "text-zinc-300",
              )}
            >
              {httpEntries.length.toLocaleString()}
            </span>
          </div>
        </div>

        <button
          onClick={copyIssueReport}
          disabled={!hasIssues}
          title="Copy a clean error/warning report grouped by source — paste into AI or support ticket"
          className={cn(
            "flex flex-col items-center justify-center gap-1 rounded-lg border px-4 py-2 text-[10px] font-mono transition-colors disabled:opacity-30 min-w-[110px] text-center",
            hasIssues
              ? "border-zinc-600 bg-zinc-800 text-zinc-200 hover:bg-zinc-700 hover:border-zinc-500"
              : "border-zinc-800 bg-zinc-900 text-zinc-600",
          )}
        >
          {copiedReport ? (
            <>
              <Check className="h-4 w-4 text-emerald-400" />
              <span className="text-emerald-400">Copied!</span>
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
      <div className="space-y-1.5">
        <p className="text-[10px] text-zinc-600 font-mono uppercase tracking-wider px-0.5">
          By Source
        </p>
        {sources.map(([source, counts]) => {
          const total =
            counts.info +
            counts.success +
            counts.warn +
            counts.error +
            counts.data +
            counts.cmd;
          const label = SOURCE_LABELS[source] ?? source;
          return (
            <div
              key={source}
              className={cn(
                "rounded-lg border bg-zinc-900 p-2.5",
                counts.error > 0
                  ? "border-red-900"
                  : counts.warn > 0
                    ? "border-amber-900"
                    : "border-zinc-800",
              )}
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[11px] font-mono text-zinc-300">
                  {label}
                </span>
                <span className="text-[10px] font-mono text-zinc-600">
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
              {counts.errors.length > 0 && (
                <p className="mt-1.5 text-[10px] text-red-400 font-mono truncate">
                  ↳ {counts.errors[counts.errors.length - 1].trim()}
                </p>
              )}
              {counts.errors.length === 0 && counts.warns.length > 0 && (
                <p className="mt-1.5 text-[10px] text-amber-400 font-mono truncate">
                  ↳ {counts.warns[counts.warns.length - 1].trim()}
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
// Tab definition
// ---------------------------------------------------------------------------

type TabId = "overview" | "server" | "client" | "http" | "all";

interface TabDef {
  id: TabId;
  label: string;
  icon: React.ReactNode;
  filter: (l: ClientLogLine) => boolean;
  clearSources: string[] | null;
  emptyMessage: string;
}

const TABS: TabDef[] = [
  {
    id: "overview",
    label: "Overview",
    icon: <LayoutDashboard className="h-3 w-3" />,
    filter: () => true,
    clearSources: null,
    emptyMessage: "",
  },
  {
    id: "server",
    label: "Server",
    icon: <Server className="h-3 w-3" />,
    filter: (l) => SERVER_SOURCES.has(l.source ?? ""),
    clearSources: ["server", "tauri", "syslog", "llm"],
    emptyMessage: "No server logs yet — engine stdout/stderr will appear here",
  },
  {
    id: "client",
    label: "Client",
    icon: <Monitor className="h-3 w-3" />,
    filter: (l) => {
      const s = l.source ?? "";
      return (
        CLIENT_SOURCES.has(s) ||
        (!SERVER_SOURCES.has(s) && s !== HTTP_SOURCE && !CLIENT_SOURCES.has(s))
      );
    },
    clearSources: [
      "engine",
      "auth",
      "voice",
      "setup",
      "bg-tasks",
      "react",
      "downloads",
      "client",
    ],
    emptyMessage:
      "No client logs yet — engine, auth, voice, and setup events appear here",
  },
  {
    id: "http",
    label: "HTTP",
    icon: <Globe className="h-3 w-3" />,
    filter: (l) => l.source === HTTP_SOURCE,
    clearSources: ["access"],
    emptyMessage: "No HTTP requests yet — engine API calls will appear here",
  },
  {
    id: "all",
    label: "All",
    icon: <List className="h-3 w-3" />,
    filter: () => true,
    clearSources: null,
    emptyMessage: "No log activity yet",
  },
];

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DevTerminalPanel() {
  const [open, setOpen] = useState(false);
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [activeTab, setActiveTab] = useState<TabId>("overview");

  const allLogs = useClientLogSubscriber();
  const paused = useLogsPaused();

  // Drag resize
  const dragStartY = useRef(0);
  const dragStartH = useRef(0);
  const isDragging = useRef(false);

  const [copied, setCopied] = useState(false);
  const [copiedAll, setCopiedAll] = useState(false);

  // Broadcast panel height
  const broadcastHeight = useCallback((isOpen: boolean, h: number) => {
    window.dispatchEvent(
      new CustomEvent<number>(DEV_TERMINAL_HEIGHT_EVENT, {
        detail: isOpen ? h : 0,
      }),
    );
  }, []);

  // Latest height for the mount-only toggle handler below
  const heightRef = useRef(height);
  heightRef.current = height;

  // Toggle via DOM event — mount-only so the cleanup's height:0 broadcast
  // (which collapses AppLayout's content compensation) fires only on true
  // unmount, never after a drag-resize re-run.
  useEffect(() => {
    const handler = () =>
      setOpen((v) => {
        const next = !v;
        broadcastHeight(next, heightRef.current);
        return next;
      });
    window.addEventListener(DEV_TERMINAL_TOGGLE_EVENT, handler);
    return () => {
      window.removeEventListener(DEV_TERMINAL_TOGGLE_EVENT, handler);
      window.dispatchEvent(
        new CustomEvent<number>(DEV_TERMINAL_HEIGHT_EVENT, { detail: 0 }),
      );
    };
  }, [broadcastHeight]);

  // Drag-to-resize
  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isDragging.current = true;
      dragStartY.current = e.clientY;
      dragStartH.current = height;

      const onMove = (ev: MouseEvent) => {
        if (!isDragging.current) return;
        const next = Math.min(
          MAX_HEIGHT,
          Math.max(
            MIN_HEIGHT,
            dragStartH.current + (dragStartY.current - ev.clientY),
          ),
        );
        setHeight(next);
        broadcastHeight(true, next);
      };
      const onUp = () => {
        isDragging.current = false;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [height, broadcastHeight],
  );

  const activeTabDef = TABS.find((t) => t.id === activeTab)!;
  const tabLogs = useMemo(
    () => allLogs.filter(activeTabDef.filter),
    [allLogs, activeTabDef],
  );

  // Tracks the copy-ready text reported by LogPane/HttpPane
  const visibleCopyTextRef = useRef<string>("");
  const handleVisibleChange = useCallback((copyText: string) => {
    visibleCopyTextRef.current = copyText;
  }, []);

  const handleCopy = useCallback(async () => {
    const body = visibleCopyTextRef.current;
    if (!body) return;
    const now = new Date().toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    const header = `=== Matrx Log [${activeTabDef.label}] (View) — ${now} ===`;
    await navigator.clipboard.writeText(`${header}\n${body}\n=== END ===`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [activeTabDef]);

  const handleCopyAll = useCallback(async () => {
    let body: string;
    if (activeTab === "http") {
      body = tabLogs
        .map((l) => l.accessEntry)
        .filter((e): e is AccessEntry => e != null)
        .map(
          (e) =>
            `${formatAccessTime(e.timestamp)} ${e.method.padEnd(7)} ${e.path}${e.query ? `?${e.query}` : ""} ${e.status} ${e.duration_ms.toFixed(0)}ms`,
        )
        .join("\n");
    } else {
      body = tabLogs
        .map(
          (l) =>
            `${l.time} ${LEVEL_LABEL[l.level]} [${l.source ?? ""}] ${l.message}`,
        )
        .join("\n");
    }
    if (!body) return;
    const now = new Date().toLocaleString("en-US", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    const header = `=== Matrx Log [${activeTabDef.label}] (All) — ${now} ===`;
    await navigator.clipboard.writeText(`${header}\n${body}\n=== END ===`);
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 2000);
  }, [activeTab, activeTabDef, tabLogs]);

  const handleClear = useCallback(() => {
    if (activeTabDef.clearSources === null) {
      clearClientLog();
    } else {
      activeTabDef.clearSources.forEach(clearClientLogBySource);
    }
  }, [activeTabDef]);

  // Tab badge counts — errors/warns scoped to each tab's sources
  const serverErrors = allLogs.filter(
    (l) => SERVER_SOURCES.has(l.source ?? "") && l.level === "error",
  ).length;
  const serverWarns = allLogs.filter(
    (l) => SERVER_SOURCES.has(l.source ?? "") && l.level === "warn",
  ).length;
  const clientErrors = allLogs.filter((l) => {
    const s = l.source ?? "";
    return (
      (CLIENT_SOURCES.has(s) ||
        (!SERVER_SOURCES.has(s) &&
          s !== HTTP_SOURCE &&
          !CLIENT_SOURCES.has(s))) &&
      l.level === "error"
    );
  }).length;
  const clientWarns = allLogs.filter((l) => {
    const s = l.source ?? "";
    return (
      (CLIENT_SOURCES.has(s) ||
        (!SERVER_SOURCES.has(s) &&
          s !== HTTP_SOURCE &&
          !CLIENT_SOURCES.has(s))) &&
      l.level === "warn"
    );
  }).length;
  const httpErrors = allLogs.filter(
    (l) => l.source === HTTP_SOURCE && l.level === "error",
  ).length;
  const httpWarns = allLogs.filter(
    (l) => l.source === HTTP_SOURCE && l.level === "warn",
  ).length;
  const totalErrors = allLogs.filter((l) => l.level === "error").length;
  const totalWarns = allLogs.filter((l) => l.level === "warn").length;

  if (!open) return null;

  const HEADER_H = 36;
  const HAS_FILTER_BAR = activeTab !== "overview";
  const FILTER_H = HAS_FILTER_BAR ? 30 : 0;
  const contentH = height - HEADER_H - FILTER_H;

  // Intentionally always-dark: a fixed terminal chrome that does not follow
  // the app light/dark theme (same pattern as shell emulators).
  return (
    <div
      className="dark fixed bottom-0 left-0 right-0 z-50 flex flex-col border-t border-zinc-700/60 bg-zinc-950 text-zinc-200 shadow-2xl"
      style={{ height }}
    >
      {/* Drag handle */}
      <div
        className="absolute -top-1.5 left-0 right-0 flex cursor-row-resize justify-center py-1 group"
        onMouseDown={onDragStart}
      >
        <div className="h-1 w-16 rounded-full bg-zinc-800 group-hover:bg-zinc-600 transition-colors" />
      </div>

      {/* Header */}
      <div
        className="flex items-center gap-0 border-b border-zinc-800/60 px-2 flex-shrink-0 min-w-0"
        style={{ height: HEADER_H }}
      >
        <span className="flex items-center gap-1.5 pr-3 text-zinc-600 select-none shrink-0">
          <Terminal className="h-3.5 w-3.5" />
        </span>

        {/* Tabs */}
        <div className="flex min-w-0 flex-1 items-center gap-0 overflow-x-auto">
          {TABS.map((tab) => {
            const isActive = activeTab === tab.id;

            let badgeCount: number | null = null;
            let badgeStyle = "bg-zinc-800 text-zinc-500";
            let badgeSuffix = "";

            if (tab.id === "server") {
              if (serverErrors > 0) {
                badgeCount = serverErrors;
                badgeStyle = "bg-red-900/50 text-red-400";
                badgeSuffix = "e";
              } else if (serverWarns > 0) {
                badgeCount = serverWarns;
                badgeStyle = "bg-amber-900/50 text-amber-400";
                badgeSuffix = "w";
              }
            } else if (tab.id === "client") {
              if (clientErrors > 0) {
                badgeCount = clientErrors;
                badgeStyle = "bg-red-900/50 text-red-400";
                badgeSuffix = "e";
              } else if (clientWarns > 0) {
                badgeCount = clientWarns;
                badgeStyle = "bg-amber-900/50 text-amber-400";
                badgeSuffix = "w";
              }
            } else if (tab.id === "http") {
              if (httpErrors > 0) {
                badgeCount = httpErrors;
                badgeStyle = "bg-red-900/50 text-red-400";
                badgeSuffix = "e";
              } else if (httpWarns > 0) {
                badgeCount = httpWarns;
                badgeStyle = "bg-amber-900/50 text-amber-400";
                badgeSuffix = "w";
              }
            } else if (tab.id === "all") {
              if (totalErrors > 0) {
                badgeCount = totalErrors;
                badgeStyle = "bg-red-900/50 text-red-400";
                badgeSuffix = "e";
              } else if (totalWarns > 0) {
                badgeCount = totalWarns;
                badgeStyle = "bg-amber-900/50 text-amber-400";
                badgeSuffix = "w";
              }
            } else if (tab.id === "overview") {
              if (totalErrors > 0) {
                badgeCount = totalErrors;
                badgeStyle = "bg-red-900/50 text-red-400";
                badgeSuffix = "e";
              } else if (totalWarns > 0) {
                badgeCount = totalWarns;
                badgeStyle = "bg-amber-900/50 text-amber-400";
                badgeSuffix = "w";
              }
            }

            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "flex shrink-0 items-center gap-1.5 px-3 h-full text-[11px] font-mono border-b-2 transition-colors",
                  isActive
                    ? "text-zinc-200 border-primary"
                    : "text-zinc-500 border-transparent hover:text-zinc-300",
                )}
              >
                {tab.icon}
                {tab.label}
                {badgeCount !== null && badgeCount > 0 && (
                  <span
                    className={cn(
                      "text-[9px] font-mono rounded px-1 py-px",
                      badgeStyle,
                    )}
                  >
                    {badgeCount}
                    {badgeSuffix}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Pause/Resume — always visible */}
        <button
          onClick={() => setLogsPaused(!paused)}
          title={paused ? "Resume live log stream" : "Pause live log stream"}
          className={cn(
            "flex shrink-0 items-center gap-1 px-2 py-1 text-[11px] font-mono transition-colors",
            paused
              ? "text-amber-400 hover:text-amber-300"
              : "text-zinc-500 hover:text-zinc-200",
          )}
        >
          {paused ? (
            <Play className="h-3.5 w-3.5" />
          ) : (
            <Pause className="h-3.5 w-3.5" />
          )}
          {paused && (
            <span className="text-[9px] bg-amber-900/50 text-amber-400 rounded px-1 py-px font-mono">
              PAUSED
            </span>
          )}
        </button>

        {/* Actions — only shown for non-overview tabs */}
        {activeTab !== "overview" && (
          <>
            <button
              onClick={handleCopyAll}
              disabled={tabLogs.length === 0}
              title="Copy all — full buffer, no filters applied"
              className="flex shrink-0 items-center gap-1 px-2 py-1 text-[11px] font-mono text-zinc-500 hover:text-zinc-200 transition-colors disabled:opacity-30"
            >
              {copiedAll ? (
                <Check className="h-3.5 w-3.5 text-emerald-400" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
              {copiedAll ? "Copied!" : "Copy all"}
            </button>
            <button
              onClick={handleCopy}
              disabled={tabLogs.length === 0}
              title="Copy view — respects active filters and grouping"
              className="flex shrink-0 items-center gap-1 px-2 py-1 text-[11px] font-mono text-zinc-500 hover:text-zinc-200 transition-colors disabled:opacity-30"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-emerald-400" />
              ) : (
                <ClipboardList className="h-3.5 w-3.5" />
              )}
              {copied ? "Copied!" : "Copy view"}
            </button>
            <button
              onClick={handleClear}
              disabled={tabLogs.length === 0}
              title="Clear these logs"
              className="flex shrink-0 items-center gap-1 px-2 py-1 text-[11px] font-mono text-zinc-500 hover:text-zinc-200 transition-colors disabled:opacity-30"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </>
        )}

        {/* Activity pulse */}
        <span className="flex shrink-0 items-center gap-1 px-2 text-[10px] text-zinc-600 font-mono">
          <Activity className="h-3 w-3" />
          {allLogs.length.toLocaleString()}
        </span>

        {/* Close */}
        <button
          onClick={() => {
            setOpen(false);
            broadcastHeight(false, height);
          }}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-zinc-600 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors ml-1"
          title="Close terminal"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Content */}
      <div
        className="flex flex-col overflow-hidden"
        style={{ height: contentH + FILTER_H }}
      >
        {activeTab === "overview" ? (
          <OverviewTab logs={allLogs} />
        ) : activeTab === "http" ? (
          <HttpPane logs={tabLogs} onCopyTextChange={handleVisibleChange} />
        ) : (
          <LogPane
            logs={tabLogs}
            emptyMessage={activeTabDef.emptyMessage}
            filterKey={activeTab}
            onVisibleChange={handleVisibleChange}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TerminalToggleButton — placed in the status bar
// ---------------------------------------------------------------------------

interface TerminalToggleButtonProps {
  className?: string;
}

export function TerminalToggleButton({ className }: TerminalToggleButtonProps) {
  return (
    <button
      onClick={toggleDevTerminal}
      title="Toggle debug terminal"
      className={cn(
        "flex items-center gap-1.5 rounded px-2 py-1 text-xs font-mono text-muted-foreground hover:text-foreground hover:bg-muted transition-colors",
        className,
      )}
    >
      <Terminal className="h-3.5 w-3.5" />
      <span>Terminal</span>
    </button>
  );
}
