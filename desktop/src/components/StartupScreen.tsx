/**
 * StartupScreen — Full-screen branded loading screen shown during:
 *   1. auth.loading (checking session from local storage)
 *   2. engineStatus "discovering" | "starting" (engine starting up)
 *
 * Layout:
 *   LEFT  — Phase checklist driven by [phase:X] prefixed stdout lines.
 *   RIGHT — Live raw log panel (temporary debug view, always visible).
 *
 * The listener attaches immediately on mount and also replays any buffered
 * sidecar logs from Rust's ring buffer so restarts don't lose phase signals
 * that fired before the component mounted.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { isTauri, getSidecarLogs } from "@/lib/sidecar";
import type { UnlistenFn } from "@tauri-apps/api/event";
import {
  CheckCircle2,
  XCircle,
  Circle,
  Loader2,
  Copy,
  Check,
  AlertTriangle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { EngineStatus } from "@/hooks/use-engine";
import { AppVersion } from "@/lib/app-version";

// ---------------------------------------------------------------------------
// Phase definitions
// ---------------------------------------------------------------------------

interface PhaseStep {
  id: string;
  label: string;
  detail?: string;
  status: "pending" | "active" | "done" | "error";
}

const AUTH_PHASE: PhaseStep = {
  id: "auth",
  label: "Authenticating",
  detail: "Checking your session...",
  status: "pending",
};

const ENGINE_PHASES: PhaseStep[] = [
  {
    id: "starting",
    label: "Starting engine",
    detail: "Initializing binary...",
    status: "pending",
  },
  {
    id: "port",
    label: "Selecting port",
    detail: "Finding available port...",
    status: "pending",
  },
  {
    id: "server",
    label: "Starting server",
    detail: "Launching FastAPI server...",
    status: "pending",
  },
  {
    id: "database",
    label: "Opening local database",
    detail: "Connecting to SQLite...",
    status: "pending",
  },
  {
    id: "browsers",
    label: "Checking browser engine",
    detail: "Verifying Playwright...",
    status: "pending",
  },
  {
    id: "ai",
    label: "Initializing AI engine",
    detail: "Loading AI configuration...",
    status: "pending",
  },
  {
    id: "tools",
    label: "Loading tool registry",
    detail: "Registering tools...",
    status: "pending",
  },
  {
    id: "scraper",
    label: "Starting scraper",
    detail: "Launching browser engine...",
    status: "pending",
  },
  {
    id: "proxy",
    label: "Starting HTTP proxy",
    detail: "Binding local proxy...",
    status: "pending",
  },
  {
    id: "tunnel",
    label: "Setting up tunnel",
    detail: "Connecting Cloudflare...",
    status: "pending",
  },
  {
    id: "ready",
    label: "Engine ready",
    detail: "Connecting...",
    status: "pending",
  },
];

const PHASE_MAP: Record<string, string> = {
  starting: "starting",
  port: "port",
  server: "server",
  database: "database",
  browsers: "browsers",
  ai: "ai",
  tools: "tools",
  scraper: "scraper",
  proxy: "proxy",
  tunnel: "tunnel",
  ready: "ready",
};

// ANSI escape stripper so log lines display cleanly
const ANSI_RE = /\x1b\[[0-9;]*m/g;
function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, "");
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface StartupScreenProps {
  authLoading: boolean;
  engineStatus: EngineStatus;
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

function classifyLog(raw: string): "error" | "warn" | "success" | "info" {
  const t = stripAnsi(raw).toLowerCase();
  if (
    t.includes("error") ||
    t.includes("failed") ||
    t.includes("traceback") ||
    t.includes("exception") ||
    t.includes("terminated") ||
    t.includes("sigkill") ||
    t.includes("signal: some(9)")
  )
    return "error";
  if (t.includes("warning") || t.includes("warn")) return "warn";
  if (
    t.includes("ready") ||
    t.includes("✓") ||
    t.includes("started") ||
    t.includes("loaded") ||
    t.includes("initialized") ||
    t.includes("complete")
  )
    return "success";
  return "info";
}

/** Apply a single log line to the phase steps array. Pure — returns new array. */
function applyLogToSteps(steps: PhaseStep[], line: string): PhaseStep[] {
  const clean = stripAnsi(line);
  const match = clean.match(/\[phase:([^\]]+)\]/);
  if (!match) return steps;

  const phaseKey = match[1]?.toLowerCase();
  const stepId = phaseKey ? PHASE_MAP[phaseKey] : undefined;
  if (!stepId) return steps;

  const detail = clean.replace(/^\[[^\]]+\]\s*/, "").trim() || undefined;
  const isError = classifyLog(clean) === "error";
  const isDone =
    !isError &&
    (clean.toLowerCase().includes("ready") ||
      clean.toLowerCase().includes("complete") ||
      clean.toLowerCase().includes("started") ||
      clean.toLowerCase().includes("loaded") ||
      clean.toLowerCase().includes("initialized"));

  return steps.map((s, i) => {
    const idx = steps.findIndex((x) => x.id === stepId);
    if (i < idx && s.status === "active")
      return { ...s, status: "done" as const };
    if (s.id === stepId) {
      const mergedDetail = detail ?? s.detail;
      if (isError)
        return {
          ...s,
          status: "error" as const,
          ...(mergedDetail !== undefined ? { detail: mergedDetail } : {}),
        };
      return {
        ...s,
        status: isDone ? ("done" as const) : ("active" as const),
        ...(mergedDetail !== undefined ? { detail: mergedDetail } : {}),
      };
    }
    return s;
  });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StartupScreen({
  authLoading,
  engineStatus,
}: StartupScreenProps) {
  const [steps, setSteps] = useState<PhaseStep[]>(() => [
    { ...AUTH_PHASE },
    ...ENGINE_PHASES.map((p) => ({ ...p })),
  ]);
  const [logs, setLogs] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const logScrollRef = useRef<HTMLDivElement>(null);
  const unlistenRef = useRef<UnlistenFn | null>(null);
  // Track how many buffered lines we've already applied so we don't double-process
  const bufferedCountRef = useRef(0);

  // ── Auth step ──────────────────────────────────────────────────────────────
  useEffect(() => {
    setSteps((prev) =>
      prev.map((s) => {
        if (s.id !== "auth") return s;
        if (authLoading) return { ...s, status: "active" };
        return { ...s, status: "done", detail: "Session verified" };
      }),
    );
  }, [authLoading]);

  // ── Mark "starting" active when engine status changes ──────────────────────
  useEffect(() => {
    if (engineStatus === "starting" || engineStatus === "discovering") {
      setSteps((prev) =>
        prev.map((s) =>
          s.id === "starting" && s.status === "pending"
            ? { ...s, status: "active" }
            : s,
        ),
      );
    }
  }, [engineStatus]);

  // ── Mark all remaining steps done when engine connects ────────────────────
  useEffect(() => {
    if (engineStatus === "connected") {
      setSteps((prev) =>
        prev.map((s) => ({
          ...s,
          status:
            s.status === "pending" || s.status === "active" ? "done" : s.status,
        })),
      );
    }
  }, [engineStatus]);

  // ── Core log + phase listener ──────────────────────────────────────────────
  // 1. On mount: load Rust ring-buffer (catches logs that fired before mount)
  // 2. Subscribe to live sidecar-log events going forward
  // Both paths feed into the same log display and phase parser.
  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;

    const processLine = (line: string) => {
      if (cancelled) return;
      const clean = stripAnsi(line);
      setLogs((prev) => [...prev.slice(-999), clean]);
      setSteps((prev) => applyLogToSteps(prev, line));
    };

    (async () => {
      // ── Step 1: Replay buffered logs from Rust ring buffer ─────────────────
      try {
        const buffered = await getSidecarLogs();
        if (!cancelled && buffered.length > 0) {
          bufferedCountRef.current = buffered.length;
          // Rebuild phase state from scratch based on buffered history
          // so a restart that already completed phases shows them as done.
          setSteps((prev) => {
            let updated = prev;
            for (const line of buffered) {
              updated = applyLogToSteps(updated, line);
            }
            return updated;
          });
          setLogs(buffered.map(stripAnsi).slice(-999));
        }
      } catch {
        // Not fatal — live events will still work
      }

      // ── Step 2: Subscribe to live events ──────────────────────────────────
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const unlisten = await listen<string>("sidecar-log", (event) => {
          if (cancelled) return;
          const line =
            typeof event.payload === "string"
              ? event.payload
              : String(event.payload);
          processLine(line);
        });
        unlistenRef.current = unlisten;
      } catch {
        // Not in Tauri or listen unavailable
      }
    })();

    return () => {
      cancelled = true;
      unlistenRef.current?.();
    };
  }, []);

  // Auto-scroll log panel
  useEffect(() => {
    if (logScrollRef.current) {
      logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
    }
  }, [logs]);

  const copyLogs = useCallback(async () => {
    if (logs.length === 0) return;
    await navigator.clipboard.writeText(
      `=== Matrx Startup Log — ${new Date().toLocaleString()} ===\n${logs.join("\n")}\n=== END ===`,
    );
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [logs]);

  const activeStep = steps.find((s) => s.status === "active");
  const errorSteps = steps.filter((s) => s.status === "error");
  const subtitle = activeStep?.detail ?? activeStep?.label ?? "Starting up...";

  return (
    <div className="flex h-screen w-screen bg-background overflow-hidden">
      {/* ── LEFT: Checklist ──────────────────────────────────────────────── */}
      <div className="flex flex-col items-center justify-center w-1/2 min-w-[320px] px-8 border-r border-border/40">
        {/* Branding */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/20">
            <span className="text-3xl font-bold text-primary">M</span>
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">
              Matrx Local
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              AI-powered local engine
            </p>
            <AppVersion
              prefix="Version "
              className="mt-2 inline-block rounded-full border border-border bg-muted/50 px-2.5 py-1 text-xs font-medium text-muted-foreground"
            />
          </div>
        </div>

        {/* Steps */}
        <div className="w-full max-w-xs space-y-2">
          {steps.map((step) => (
            <StepRow key={step.id} step={step} />
          ))}
        </div>

        {/* Animated subtitle */}
        <p className="mt-6 text-sm text-muted-foreground animate-pulse">
          {subtitle}
        </p>

        {/* Error summary */}
        {errorSteps.length > 0 && (
          <div className="mt-4 w-full max-w-xs rounded-lg border border-red-500/30 bg-red-500/5 p-3 space-y-1">
            {errorSteps.map((s) => (
              <div
                key={s.id}
                className="flex items-start gap-2 text-xs text-red-600 dark:text-red-400"
              >
                <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                <span>
                  <span className="font-medium">{s.label}:</span>{" "}
                  {s.detail ?? "Failed"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── RIGHT: Live log panel (always-dark terminal chrome) ───────────── */}
      <div className="dark flex flex-col flex-1 min-w-0 bg-zinc-950 text-zinc-200">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-zinc-800 bg-zinc-900/60">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-medium text-zinc-300">
              Engine Output
            </span>
            {logs.length > 0 && (
              <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500 font-mono">
                {logs.length}
              </span>
            )}
          </div>
          <button
            onClick={copyLogs}
            disabled={logs.length === 0}
            className="flex items-center gap-1 rounded px-2 py-1 text-[10px] font-mono text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors disabled:opacity-40"
            title="Copy all logs"
          >
            {copied ? (
              <Check className="h-3 w-3 text-emerald-400" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>

        {/* Log lines */}
        <div
          ref={logScrollRef}
          className="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed space-y-0.5"
        >
          {logs.length === 0 ? (
            <p className="text-zinc-700 italic">Waiting for engine output...</p>
          ) : (
            logs.map((line, i) => {
              const cls = classifyLog(line);
              return (
                <div
                  key={i}
                  className={cn(
                    "break-all whitespace-pre-wrap",
                    cls === "error"
                      ? "text-red-400"
                      : cls === "warn"
                        ? "text-amber-400"
                        : cls === "success"
                          ? "text-emerald-400"
                          : "text-zinc-400",
                  )}
                >
                  {line}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StepRow
// ---------------------------------------------------------------------------

function StepRow({ step }: { step: PhaseStep }) {
  return (
    <div className="flex items-center gap-3 px-1">
      <div className="flex-shrink-0">
        {step.status === "done" && (
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
        )}
        {step.status === "active" && (
          <Loader2 className="h-4 w-4 text-primary animate-spin" />
        )}
        {step.status === "error" && (
          <XCircle className="h-4 w-4 text-red-500" />
        )}
        {step.status === "pending" && (
          <Circle className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <span
          className={cn(
            "text-sm",
            step.status === "done"
              ? "text-foreground"
              : step.status === "active"
                ? "text-foreground font-medium"
                : step.status === "error"
                  ? "text-red-600 dark:text-red-400"
                  : "text-muted-foreground",
          )}
        >
          {step.label}
        </span>
        {(step.status === "active" || step.status === "error") &&
          step.detail && (
            <p
              className={cn(
                "text-[11px] truncate",
                step.status === "error"
                  ? "text-red-400"
                  : "text-muted-foreground",
              )}
            >
              {step.detail}
            </p>
          )}
      </div>
    </div>
  );
}
