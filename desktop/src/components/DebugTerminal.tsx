/**
 * DebugTerminal — fully transparent log viewer for setup and download operations.
 *
 * Shows every event, error, and raw payload with timestamps and color-coded
 * log levels. Includes a "Copy All" button that produces a clean plaintext
 * block ready to paste into a support conversation.
 *
 * Usage:
 *   const { logLine, logs, clearLogs } = useDebugTerminal();
 *   <DebugTerminal logs={logs} onClear={clearLogs} />
 *
 * logLine(level, message) — append a new line. Levels:
 *   "info"    → muted white
 *   "success" → green
 *   "warn"    → amber
 *   "error"   → red
 *   "data"    → blue  (raw JSON payloads, event dumps)
 *   "cmd"     → cyan  (commands being run)
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Check, Trash2, ChevronDown, ChevronUp, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type LogLevel = "info" | "success" | "warn" | "error" | "data" | "cmd";

export interface LogLine {
  id: number;
  time: string;        // "HH:MM:SS"
  level: LogLevel;
  message: string;
}

// ---------------------------------------------------------------------------
// Hook — useDebugTerminal
// ---------------------------------------------------------------------------

let _lineId = 0;

export function useDebugTerminal() {
  const [logs, setLogs] = useState<LogLine[]>([]);

  const logLine = useCallback((level: LogLevel, message: string) => {
    const now = new Date();
    const time = now.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    setLogs((prev) => [
      ...prev,
      { id: ++_lineId, time, level, message },
    ]);
  }, []);

  /** Log a raw object/value as a DATA line (pretty-printed JSON). */
  const logData = useCallback(
    (label: string, payload: unknown) => {
      try {
        const formatted =
          typeof payload === "string"
            ? payload
            : JSON.stringify(payload, null, 0);
        logLine("data", `${label}: ${formatted}`);
      } catch {
        logLine("data", `${label}: [unserializable]`);
      }
    },
    [logLine],
  );

  const clearLogs = useCallback(() => setLogs([]), []);

  return { logs, logLine, logData, clearLogs };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface DebugTerminalProps {
  logs: LogLine[];
  onClear: () => void;
  /** Default collapsed? (terminal auto-expands when logs arrive) */
  defaultOpen?: boolean;
  /** Title shown in the header bar */
  title?: string;
  /** Max height of the scrollable pane */
  maxHeight?: string;
  className?: string;
}

const LEVEL_CLASSES: Record<LogLevel, string> = {
  info:    "text-zinc-400",
  success: "text-emerald-400",
  warn:    "text-amber-400",
  error:   "text-red-400",
  data:    "text-sky-400",
  cmd:     "text-cyan-400",
};

const LEVEL_LABEL: Record<LogLevel, string> = {
  info:    "INFO ",
  success: "OK   ",
  warn:    "WARN ",
  error:   "ERR  ",
  data:    "DATA ",
  cmd:     "CMD  ",
};

const ALL_LEVELS = ["info", "success", "warn", "error", "data", "cmd"] as const;

const LEVEL_FILTER_CLASSES: Record<LogLevel, { active: string; inactive: string }> = {
  info:    { active: "bg-zinc-700 text-zinc-200 border-zinc-500",    inactive: "text-zinc-600 border-zinc-700 hover:text-zinc-400" },
  success: { active: "bg-emerald-900/60 text-emerald-300 border-emerald-700", inactive: "text-zinc-600 border-zinc-700 hover:text-emerald-500" },
  warn:    { active: "bg-amber-900/60 text-amber-300 border-amber-700",   inactive: "text-zinc-600 border-zinc-700 hover:text-amber-500" },
  error:   { active: "bg-red-900/60 text-red-300 border-red-700",     inactive: "text-zinc-600 border-zinc-700 hover:text-red-500" },
  data:    { active: "bg-sky-900/60 text-sky-300 border-sky-700",     inactive: "text-zinc-600 border-zinc-700 hover:text-sky-500" },
  cmd:     { active: "bg-cyan-900/60 text-cyan-300 border-cyan-700",  inactive: "text-zinc-600 border-zinc-700 hover:text-cyan-500" },
};

export function DebugTerminal({
  logs,
  onClear,
  defaultOpen = false,
  title = "Debug Terminal",
  maxHeight = "260px",
  className,
}: DebugTerminalProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [copied, setCopied] = useState(false);
  const [activeFilters, setActiveFilters] = useState<Set<LogLevel>>(new Set(ALL_LEVELS));
  const prevLogCount = useRef(0);

  // Auto-expand when new logs arrive (during active operations)
  useEffect(() => {
    if (logs.length > prevLogCount.current && !open) {
      setOpen(true);
    }
    prevLogCount.current = logs.length;
  }, [logs.length, open]);

  const toggleFilter = useCallback((level: LogLevel) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(level)) {
        // Don't allow deselecting the last active filter
        if (next.size === 1) return prev;
        next.delete(level);
      } else {
        next.add(level);
      }
      return next;
    });
  }, []);

  const visibleLogs = activeFilters.size === ALL_LEVELS.length
    ? logs
    : logs.filter((l) => activeFilters.has(l.level));

  const copyAll = useCallback(async () => {
    if (logs.length === 0) return;
    const now = new Date().toLocaleString("en-US", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false,
    });
    const header = `=== Matrx Debug Log — ${now} ===`;
    const body = visibleLogs
      .map((l) => `${l.time} ${LEVEL_LABEL[l.level]} ${l.message}`)
      .join("\n");
    const footer = "=== END ===";
    await navigator.clipboard.writeText(`${header}\n${body}\n${footer}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [logs, visibleLogs]);

  const hasErrors = logs.some((l) => l.level === "error");
  const hasWarns = logs.some((l) => l.level === "warn");

  return (
    <div className={cn("dark rounded-lg border border-zinc-700/60 bg-zinc-950/80 text-zinc-200", className)}>
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-700/60">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-xs font-mono text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          <Terminal className="h-3.5 w-3.5" />
          <span>{title}</span>
          {logs.length > 0 && (
            <Badge
              variant="outline"
              className={cn(
                "text-[10px] px-1.5 py-0 h-4 font-mono border-zinc-600",
                hasErrors
                  ? "text-red-400 border-red-600"
                  : hasWarns
                  ? "text-amber-400 border-amber-600"
                  : "text-zinc-500",
              )}
            >
              {visibleLogs.length === logs.length
                ? logs.length
                : `${visibleLogs.length}/${logs.length}`}
            </Badge>
          )}
          {open ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )}
        </button>

        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-zinc-500 hover:text-zinc-200"
            onClick={copyAll}
            title="Copy visible logs to clipboard"
            disabled={logs.length === 0}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-zinc-500 hover:text-zinc-200"
            onClick={onClear}
            title="Clear logs"
            disabled={logs.length === 0}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Filter bar — only shown when the pane is open */}
      {open && (
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-zinc-800/60 flex-wrap">
          <span className="text-[10px] text-zinc-600 font-mono mr-1 select-none">filter:</span>
          {ALL_LEVELS.map((level) => {
            const isActive = activeFilters.has(level);
            const count = logs.filter((l) => l.level === level).length;
            const cls = LEVEL_FILTER_CLASSES[level];
            return (
              <button
                key={level}
                onClick={() => toggleFilter(level)}
                title={`${isActive ? "Hide" : "Show"} ${level} logs`}
                className={cn(
                  "inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0 h-4 rounded border transition-colors select-none",
                  isActive ? cls.active : cls.inactive,
                )}
              >
                {LEVEL_LABEL[level].trim()}
                {count > 0 && (
                  <span className="opacity-70">{count}</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* Log pane */}
      {open && (
        <div
          className="overflow-y-auto font-mono text-[11px] leading-relaxed p-2 space-y-0.5"
          style={{ maxHeight }}
        >
          {visibleLogs.length === 0 && (
            <div className="text-zinc-600 py-3 text-center select-none">
              {logs.length === 0
                ? "No output yet — logs will appear here when operations run"
                : "No logs match the active filters"}
            </div>
          )}
          {visibleLogs.map((line) => (
            <div key={line.id} className="flex gap-2 min-w-0">
              <span className="text-zinc-600 shrink-0 select-none tabular-nums">
                {line.time}
              </span>
              <span
                className={cn(
                  "shrink-0 select-none tabular-nums font-semibold",
                  LEVEL_CLASSES[line.level],
                )}
              >
                {LEVEL_LABEL[line.level]}
              </span>
              <span
                className={cn(
                  "break-all whitespace-pre-wrap",
                  LEVEL_CLASSES[line.level],
                )}
              >
                {line.message}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
