/**
 * FirstRunScreen — One-time installation screen shown when:
 *   engineStatus === "connected" AND setup is not complete.
 *
 * Eagerly installs EVERYTHING in one pass:
 *   1. Storage directories
 *   2. Core packages
 *   3. Playwright/Chromium browser engine (~280 MB)
 *   4. Whisper transcription model (~150 MB, base.en)
 *   5. Permissions check
 *
 * Uses POST /setup/install?mode=first_run SSE stream.
 * Shows a grand overall progress bar + per-component sub-bars.
 * After completion → calls onComplete() which transitions to Dashboard.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import {
  CheckCircle2, Circle, Loader2, AlertCircle,
  ChevronDown, ChevronUp, Copy, Check,
} from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import supabase from "@/lib/supabase";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface InstallStep {
  id: string;
  label: string;
  description: string;
  sizeHint?: string;
  status: "pending" | "installing" | "ready" | "error" | "skipped";
  percent: number;
  message?: string;
}

interface FirstRunScreenProps {
  engineUrl: string;
  onComplete: () => void;
}

// ---------------------------------------------------------------------------
// Step definitions
// ---------------------------------------------------------------------------

const INITIAL_STEPS: InstallStep[] = [
  {
    id: "storage_dirs",
    label: "Storage directories",
    description: "Creating local folders for your notes, files, code, and workspaces",
    status: "pending",
    percent: 0,
  },
  {
    id: "core_packages",
    label: "Core engine packages",
    description: "Verifying system libraries (monitoring, audio, networking)",
    status: "pending",
    percent: 0,
  },
  {
    id: "browser_engine",
    label: "Browser engine",
    description: "Installing Playwright Chromium for web automation and scraping",
    sizeHint: "~280 MB",
    status: "pending",
    percent: 0,
  },
  {
    id: "transcription",
    label: "Audio transcription model",
    description: "Downloading Whisper base.en model for local speech-to-text",
    sizeHint: "~150 MB",
    status: "pending",
    percent: 0,
  },
  {
    id: "permissions",
    label: "System permissions",
    description: "Checking microphone, camera, and screen recording access",
    status: "pending",
    percent: 0,
  },
];

// Weight each step for the grand progress bar (must sum to 100)
const STEP_WEIGHTS: Record<string, number> = {
  storage_dirs:   2,
  core_packages:  3,
  browser_engine: 60,
  transcription:  30,
  permissions:    5,
};

function computeTotalPercent(steps: InstallStep[]): number {
  let total = 0;
  for (const step of steps) {
    const weight = STEP_WEIGHTS[step.id] ?? 0;
    total += (step.percent / 100) * weight;
  }
  return Math.round(total);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FirstRunScreen({ engineUrl, onComplete }: FirstRunScreenProps) {
  const [steps, setSteps] = useState<InstallStep[]>(INITIAL_STEPS);
  const [phase, setPhase] = useState<"idle" | "installing" | "complete" | "error">("idle");
  const [serverTotalPercent, setServerTotalPercent] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [rawLogs, setRawLogs] = useState<string[]>([]);
  const [logsOpen, setLogsOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const logScrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Kick off installation automatically on mount
  useEffect(() => {
    runInstall();
    return () => {
      abortRef.current?.abort();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll logs
  useEffect(() => {
    if (logsOpen && logScrollRef.current) {
      logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
    }
  }, [rawLogs, logsOpen]);

  const updateStep = useCallback((id: string, patch: Partial<InstallStep>) => {
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }, []);

  const runInstall = useCallback(async () => {
    setPhase("installing");
    setErrorMessage(null);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const url = `${engineUrl}/setup/install?mode=first_run`;

    try {
      // We use fetch + ReadableStream rather than EventSource so we can POST
      // and pass auth headers. The existing SetupWizard uses EventSource with
      // GET semantics — here we do a POST with proper auth.
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token ?? null;
      const requestHeaders: Record<string, string> = {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      };
      if (token) requestHeaders["Authorization"] = `Bearer ${token}`;

      const response = await fetch(url, {
        method: "POST",
        headers: requestHeaders,
        signal: ctrl.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Setup endpoint returned ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      // Set when the stream delivers a terminal event (`complete` or
      // `cancelled`). If the stream ends without one, the engine connection
      // was lost mid-install and we must surface an error instead of leaving
      // the screen stuck on "Installing..." forever.
      let sawTerminalEvent = false;

      const parseEvent = (block: string) => {
        let eventType = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) eventType = line.slice(7).trim();
          else if (line.startsWith("data: ")) data = line.slice(6).trim();
        }
        if (!data) return;

        try {
          const payload = JSON.parse(data);
          setRawLogs((prev) => [...prev.slice(-999), `[${eventType}] ${data}`]);

          if (eventType === "total_progress") {
            setServerTotalPercent(payload.total_percent ?? 0);
            return;
          }

          if (eventType === "progress") {
            const { component, status, message, percent } = payload;
            if (component && component !== "_system") {
              updateStep(component, {
                status: status === "ready" ? "ready" : status === "error" ? "error" : "installing",
                percent: percent ?? 0,
                message: message,
              });
            }
            // If component is storage_dirs finishing quickly, push server total
            if (status === "error") {
              setErrorMessage((prev) => prev ?? message ?? "An installation step failed");
            }
          }

          if (eventType === "complete") {
            sawTerminalEvent = true;
            const allStepsReady = () =>
              setSteps((prev) => {
                return prev.map((s) =>
                  s.status === "installing" ? { ...s, status: "ready", percent: 100 } : s
                );
              });
            allStepsReady();
            setServerTotalPercent(100);

            if (payload.had_errors) {
              setPhase("error");
              setErrorMessage(payload.errors?.join("; ") ?? "Some components failed to install");
            } else {
              setPhase("complete");
              // Auto-transition after a short celebration pause
              setTimeout(onComplete, 2500);
            }
          }

          if (eventType === "cancelled") {
            sawTerminalEvent = true;
            setPhase("error");
            setErrorMessage("Installation was cancelled");
          }
        } catch {
          // Non-JSON line — ignore
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          if (block.trim()) parseEvent(block);
        }
      }

      if (!sawTerminalEvent && !ctrl.signal.aborted) {
        setPhase("error");
        setErrorMessage("Connection to engine lost during install");
      }
    } catch (err: unknown) {
      if (ctrl.signal.aborted) return;
      const msg = err instanceof Error ? err.message : String(err);
      setPhase("error");
      setErrorMessage(`Connection error: ${msg}`);
    }
  }, [engineUrl, updateStep, onComplete]);

  const copyLogs = useCallback(async () => {
    if (rawLogs.length === 0) return;
    const text = `=== Matrx First-Run Install Log — ${new Date().toLocaleString()} ===\n${rawLogs.join("\n")}\n=== END ===`;
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [rawLogs]);

  // Use server-driven total if available, else compute from steps
  const computedTotal = computeTotalPercent(steps);
  const displayTotal = serverTotalPercent > 0 ? serverTotalPercent : computedTotal;

  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center bg-background px-6 overflow-y-auto">
      <div className="w-full max-w-lg py-10">
        {/* Header */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/20">
            <span className="text-2xl font-bold text-primary">M</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Setting up Matrx Local
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            This only happens once — getting everything ready for you
          </p>
        </div>

        {/* Grand progress bar */}
        <div className="mb-6">
          <div className="flex justify-between text-xs text-muted-foreground mb-1.5">
            <span>
              {phase === "complete"
                ? "All done!"
                : phase === "error"
                ? "Finished with errors"
                : "Installing..."}
            </span>
            <span className="tabular-nums">{displayTotal}%</span>
          </div>
          <Progress
            value={displayTotal}
            className={cn(
              "h-2",
              phase === "complete" ? "[&>div]:bg-emerald-500" :
              phase === "error" ? "[&>div]:bg-red-500" :
              "[&>div]:bg-primary"
            )}
          />
        </div>

        {/* Per-step list */}
        <div className="space-y-3 mb-6">
          {steps.map((step) => (
            <StepCard key={step.id} step={step} />
          ))}
        </div>

        {/* Error notice */}
        {phase === "error" && errorMessage && (
          <div className="mb-4 rounded-lg border border-red-800/50 bg-red-950/30 px-4 py-3">
            <div className="flex items-start gap-2">
              <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-400" />
              <div>
                <p className="text-sm font-medium text-red-400">Some steps failed</p>
                <p className="mt-0.5 text-xs text-red-400/80">{errorMessage}</p>
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              <Button
                size="sm"
                variant="outline"
                className="border-red-800 text-red-400 hover:bg-red-950/50"
                onClick={runInstall}
              >
                Retry
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-muted-foreground"
                onClick={onComplete}
              >
                Continue anyway
              </Button>
            </div>
          </div>
        )}

        {/* Success message */}
        {phase === "complete" && (
          <div className="mb-4 rounded-lg border border-emerald-800/40 bg-emerald-950/20 px-4 py-3 text-center">
            <CheckCircle2 className="mx-auto mb-1 h-6 w-6 text-emerald-500" />
            <p className="text-sm font-medium text-emerald-400">All done! Launching Matrx Local...</p>
          </div>
        )}

        {/* Raw log toggle */}
        <div className="rounded-lg border border-zinc-700/50 bg-zinc-950/60">
          <button
            onClick={() => setLogsOpen((v) => !v)}
            className="flex w-full items-center justify-between px-3 py-2 text-xs font-mono text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            <span>Installation details</span>
            <div className="flex items-center gap-2">
              {rawLogs.length > 0 && (
                <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-500">
                  {rawLogs.length}
                </span>
              )}
              {logsOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </div>
          </button>
          {logsOpen && (
            <div className="border-t border-zinc-700/50">
              <div className="flex justify-end px-2 py-1 border-b border-zinc-800/60">
                <button
                  onClick={copyLogs}
                  disabled={rawLogs.length === 0}
                  className="flex items-center gap-1 rounded px-2 py-0.5 text-[10px] text-zinc-500 hover:text-zinc-200 transition-colors disabled:opacity-40"
                >
                  {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
                  {copied ? "Copied!" : "Copy"}
                </button>
              </div>
              <div
                ref={logScrollRef}
                className="max-h-52 overflow-y-auto p-2 font-mono text-[10px] leading-relaxed space-y-0.5"
              >
                {rawLogs.length === 0 ? (
                  <p className="text-center text-zinc-700">No logs yet</p>
                ) : (
                  rawLogs.map((line, i) => (
                    <div
                      key={i}
                      className={cn(
                        "break-all whitespace-pre-wrap",
                        line.includes('"status":"error"') || line.includes("FAILED")
                          ? "text-red-400"
                          : line.includes('"status":"ready"')
                          ? "text-emerald-400"
                          : "text-zinc-500"
                      )}
                    >
                      {line}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// StepCard sub-component
// ---------------------------------------------------------------------------

function StepCard({ step }: { step: InstallStep }) {
  const isActive = step.status === "installing";
  const isDone = step.status === "ready";
  const isError = step.status === "error";

  return (
    <div className={cn(
      "rounded-lg border px-4 py-3 transition-colors",
      isActive ? "border-primary/30 bg-primary/5" :
      isDone   ? "border-emerald-800/30 bg-emerald-950/10" :
      isError  ? "border-red-800/30 bg-red-950/10" :
      "border-zinc-800/50 bg-zinc-950/20"
    )}>
      <div className="flex items-start gap-3">
        {/* Status icon */}
        <div className="mt-0.5 flex-shrink-0">
          {isDone  && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
          {isActive && <Loader2 className="h-4 w-4 text-primary animate-spin" />}
          {isError && <AlertCircle className="h-4 w-4 text-red-400" />}
          {step.status === "pending" && <Circle className="h-4 w-4 text-zinc-600" />}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className={cn(
              "text-sm font-medium",
              isDone   ? "text-foreground" :
              isActive ? "text-foreground" :
              isError  ? "text-red-400" :
              "text-muted-foreground"
            )}>
              {step.label}
            </span>
            {step.sizeHint && step.status !== "ready" && (
              <span className="text-[11px] text-zinc-500 flex-shrink-0">{step.sizeHint}</span>
            )}
            {isDone && (
              <span className="text-[11px] text-emerald-500 flex-shrink-0">Done</span>
            )}
          </div>

          <p className={cn(
            "mt-0.5 text-[11px]",
            isError ? "text-red-400/80" : "text-muted-foreground/70"
          )}>
            {step.message ?? step.description}
          </p>

          {/* Sub-progress bar */}
          {isActive && (
            <div className="mt-2">
              <Progress value={step.percent} className="h-1 [&>div]:bg-primary" />
              <p className="mt-0.5 text-[10px] text-muted-foreground tabular-nums text-right">
                {step.percent}%
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
