/**
 * EngineMonitor — a user-controlled live engine monitor.
 *
 * Accessible from:
 *   - StatusBar (click the status dot/badge)
 *   - Dashboard Engine status card
 *   - Settings Reconnect/Restart buttons
 *   - Auto-shows on engine error (but user can dismiss)
 *
 * Tabs:
 *   1. Status — diagnostics + action buttons
 *   2. Ports  — live port scan of 22140-22159
 *   3. Logs   — live sidecar stdout/stderr
 */

import { useState, useEffect, useRef, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  RefreshCw,
  Play,
  RotateCcw,
  Square,
  Copy,
  CheckCheck,
  Loader2,
  CheckCircle2,
  XCircle,
  Circle,
  AlertTriangle,
  Terminal,
  Cpu,
  Network,
} from "lucide-react";
import {
  isTauri,
  startSidecar,
  stopSidecar,
  getSidecarStatus,
  getSidecarLogs,
  waitForEngine,
  discoverEnginePort,
} from "@/lib/sidecar";
import { getPlatformSnapshot } from "@/lib/platformCtx";
import type { EngineStatus } from "@/hooks/use-engine";

// ── Types ──────────────────────────────────────────────────────────────

type StepStatus = "pending" | "running" | "pass" | "fail" | "skip";

interface DiagnosticStep {
  label: string;
  status: StepStatus;
  detail?: string;
}

interface PortScanResult {
  port: number;
  status: "open" | "closed" | "scanning";
  detail?: string;
}

type MonitorTab = "status" | "ports" | "logs";

// ── Props ──────────────────────────────────────────────────────────────

interface EngineMonitorProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  engineStatus: EngineStatus;
  engineError: string | null;
  onRestartEngine: () => Promise<void>;
  onRefresh: () => Promise<void>;
}

// ── Component ──────────────────────────────────────────────────────────

export function EngineMonitor({
  open,
  onOpenChange,
  engineStatus,
  engineError,
  onRestartEngine,
  onRefresh,
}: EngineMonitorProps) {
  const [activeTab, setActiveTab] = useState<MonitorTab>("status");
  const [steps, setSteps] = useState<DiagnosticStep[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [ports, setPorts] = useState<PortScanResult[]>([]);
  const [actionRunning, setActionRunning] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [logsCopied, setLogsCopied] = useState(false);
  const [portScanning, setPortScanning] = useState(false);
  const logScrollRef = useRef<HTMLDivElement>(null);
  // How many lines of the Rust ring buffer have already been ingested —
  // prevents re-appending the whole buffer on every modal open.
  const bufferedIngestedRef = useRef(0);

  const addLog = useCallback((line: string) => {
    const ts = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev.slice(-200), `[${ts}] ${line}`]);
  }, []);

  // Auto-scroll logs — scroll only within the log container, never the page
  useEffect(() => {
    if (logScrollRef.current) {
      logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
    }
  }, [logs]);

  // Subscribe to sidecar-log Tauri events for live output
  useEffect(() => {
    if (!open || !isTauri()) return;

    let unlisten: (() => void) | null = null;

    (async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        unlisten = await listen<string>("sidecar-log", (event) => {
          addLog(event.payload);
        });
      } catch {
        // Not in Tauri — ignore
      }
    })();

    return () => {
      if (unlisten) unlisten();
    };
  }, [open, addLog]);

  // Load buffered sidecar logs and run diagnostics when modal opens
  useEffect(() => {
    if (open) {
      (async () => {
        if (isTauri()) {
          const buffered = await getSidecarLogs();
          // Ring buffer may shrink (e.g. sidecar restart) — re-ingest from 0
          if (buffered.length < bufferedIngestedRef.current) {
            bufferedIngestedRef.current = 0;
          }
          const newLines = buffered
            .slice(bufferedIngestedRef.current)
            .map((line) => `[buffered] ${line}`);
          bufferedIngestedRef.current = buffered.length;
          if (newLines.length > 0) {
            setLogs((prev) => [...prev, ...newLines].slice(-200));
          }
        }
        runDiagnostics();
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // ── Diagnostics ────────────────────────────────────────────────────

  const updateStep = (index: number, patch: Partial<DiagnosticStep>) => {
    setSteps((prev) =>
      prev.map((s, i) => (i === index ? { ...s, ...patch } : s))
    );
  };

  const runDiagnostics = async () => {
    const initialSteps: DiagnosticStep[] = [
      { label: "Environment check", status: "pending" },
      { label: "Sidecar process status", status: "pending" },
      { label: "Port scan (22140–22159)", status: "pending" },
      { label: "Engine health endpoint", status: "pending" },
    ];
    setSteps(initialSteps);
    addLog("Running diagnostics...");

    // Step 1
    updateStep(0, { status: "running" });
    const inTauri = isTauri();
    updateStep(0, {
      status: "pass",
      detail: inTauri ? "Tauri desktop app" : "Browser dev mode",
    });

    // Step 2
    updateStep(1, { status: "running" });
    if (inTauri) {
      const info = await getSidecarStatus();
      if (info) {
        updateStep(1, {
          status: info.running ? "pass" : "fail",
          detail: info.running
            ? `Running (port config: ${info.port})`
            : "Not running",
        });
      } else {
        updateStep(1, { status: "fail", detail: "Could not query status" });
      }
      // Load fresh sidecar output
      const freshLogs = await getSidecarLogs();
      if (freshLogs.length > 0) {
        for (const line of freshLogs.slice(-20)) {
          addLog(line);
        }
      }
    } else {
      updateStep(1, { status: "skip", detail: "Not in Tauri" });
    }

    // Step 3
    updateStep(2, { status: "running" });
    const engineUrl = await discoverEnginePort();
    updateStep(2, {
      status: engineUrl ? "pass" : "fail",
      detail: engineUrl
        ? `Found at ${engineUrl}`
        : "No engine on any port",
    });

    // Step 4
    updateStep(3, { status: "running" });
    if (engineUrl) {
      try {
        const resp = await fetch(`${engineUrl}/tools/list`, {
          signal: AbortSignal.timeout(3000),
        });
        if (resp.ok) {
          const data = await resp.json();
          const n = Array.isArray(data?.tools)
            ? data.tools.length
            : Array.isArray(data)
            ? data.length
            : "?";
          updateStep(3, { status: "pass", detail: `OK — ${n} tools` });
        } else {
          updateStep(3, {
            status: "fail",
            detail: `HTTP ${resp.status}`,
          });
        }
      } catch (err) {
        updateStep(3, { status: "fail", detail: String(err) });
      }
    } else {
      updateStep(3, { status: "skip", detail: "No engine found" });
    }

    addLog("Diagnostics complete.");
  };

  // ── Port Scanner ───────────────────────────────────────────────────
  // On Windows, JS fetch() to 127.0.0.1 is blocked by WebView2 loopback
  // isolation. We delegate to the Rust check_engine_health command instead.

  const checkPortRust = async (port: number): Promise<boolean> => {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return (await invoke("check_engine_health", { port })) as boolean;
    } catch {
      return false;
    }
  };

  const isWin = typeof navigator !== "undefined" &&
    (navigator.platform?.startsWith("Win") || navigator.userAgent.includes("Windows"));

  const scanPorts = async () => {
    setPortScanning(true);
    const results: PortScanResult[] = Array.from({ length: 20 }, (_, i) => ({
      port: 22140 + i,
      status: "scanning" as const,
    }));
    setPorts(results);

    for (let i = 0; i < 20; i++) {
      const port = 22140 + i;
      try {
        let open = false;
        let detail = "";

        if (isTauri() && isWin) {
          // Windows: use Rust IPC to bypass WebView2 loopback isolation
          open = await checkPortRust(port);
          detail = open ? "Engine" : "";
        } else {
          const resp = await fetch(`http://127.0.0.1:${port}/tools/list`, {
            signal: AbortSignal.timeout(1000),
          });
          if (resp.ok) {
            open = true;
            const data = await resp.json();
            const n = Array.isArray(data?.tools)
              ? data.tools.length
              : Array.isArray(data)
              ? data.length
              : 0;
            detail = `Engine (${n} tools)`;
          } else {
            open = true;
            detail = `HTTP ${resp.status} (not engine)`;
          }
        }

        setPorts((prev) =>
          prev.map((p) =>
            p.port === port
              ? { ...p, status: open ? "open" : "closed", detail }
              : p
          )
        );
      } catch {
        setPorts((prev) =>
          prev.map((p) =>
            p.port === port ? { ...p, status: "closed" } : p
          )
        );
      }
    }
    setPortScanning(false);
  };

  // ── Actions ────────────────────────────────────────────────────────

  const handleAction = async (
    name: string,
    fn: () => Promise<void>
  ) => {
    setActionRunning(name);
    addLog(`Action: ${name}...`);
    try {
      await fn();
      addLog(`${name} completed.`);
    } catch (err) {
      addLog(`ERROR (${name}): ${err}`);
    } finally {
      setActionRunning(null);
      await runDiagnostics();
    }
  };

  const handleStartEngine = () =>
    handleAction("Start Engine", async () => {
      await startSidecar();
      addLog("Sidecar spawned. Waiting for engine...");
      const ready = await waitForEngine("http://127.0.0.1:22140", 60, 1000);
      if (ready) {
        addLog("Engine ready. Connecting...");
        await onRefresh();
      } else {
        const alt = await discoverEnginePort();
        if (alt) {
          addLog(`Found at ${alt}. Connecting...`);
          await onRefresh();
        } else {
          addLog("Engine never became reachable after 60s.");
        }
      }
    });

  const handleRestartEngine = () =>
    handleAction("Restart Engine", async () => {
      await stopSidecar();
      addLog("Stopped. Waiting 1s...");
      await new Promise((r) => setTimeout(r, 1000));
      await onRestartEngine();
    });

  const handleKillEngine = () =>
    handleAction("Kill Engine", async () => {
      await stopSidecar();
    });

  const handleRetryConnection = () =>
    handleAction("Retry Connection", async () => {
      await onRefresh();
    });

  // ── Clipboard ──────────────────────────────────────────────────────

  const buildDiagnosticDump = (): string => {
    const snap = getPlatformSnapshot();
    const p = snap.platform;
    const hasFailed = steps.some((s) => s.status === "fail");
    const openPorts = ports.filter((pt) => pt.status === "open");

    const dedup = (raw: string[]): string[] => {
      const out: string[] = [];
      let prev = "";
      let count = 0;
      for (const line of raw) {
        if (line === prev) {
          count++;
        } else {
          if (count > 0) out.push(`  ... repeated ${count} more time${count > 1 ? "s" : ""}`);
          out.push(line);
          prev = line;
          count = 0;
        }
      }
      if (count > 0) out.push(`  ... repeated ${count} more time${count > 1 ? "s" : ""}`);
      return out;
    };

    const errorLines = logs.filter(
      (l) =>
        /\b(error|exception|traceback|nameerror|importerror|modulenot|syntaxerror|typeerror|attributeerror|crash|failed|fatal)\b/i.test(l) ||
        /^\s*(File "|  )/.test(l)
    );

    const lines: string[] = [
      "=== AI Matrx Engine Diagnostic Report ===",
      `Timestamp: ${new Date().toISOString()}`,
      `Engine Status: ${engineStatus}`,
      ...(engineError ? [`Engine Error: ${engineError}`] : []),
      `Environment: ${isTauri() ? "Tauri Desktop" : "Browser Dev Mode"}`,
      `OS: ${p.os} ${p.os_version !== "unknown" ? p.os_version : ""} (${p.machine !== "unknown" ? p.machine : "arch unknown"})`.trim(),
      ...(p.python_version !== "unknown" ? [`Python: ${p.python_version}`] : []),
      ...(p.hostname !== "unknown" ? [`Host: ${p.hostname}`] : []),
      "",
    ];

    lines.push("=== Diagnostics ===");
    const failedSteps = steps.filter((s) => s.status === "fail");
    const passedSteps = steps.filter((s) => s.status === "pass");
    const skippedSteps = steps.filter((s) => s.status === "skip");
    if (failedSteps.length > 0) {
      for (const s of failedSteps)
        lines.push(`  [FAIL] ${s.label}${s.detail ? ` — ${s.detail}` : ""}`);
    }
    if (passedSteps.length > 0) {
      lines.push(`  [PASS] ${passedSteps.map((s) => s.label).join(", ")}`);
    }
    if (skippedSteps.length > 0) {
      lines.push(`  [SKIP] ${skippedSteps.map((s) => s.label).join(", ")}`);
    }
    lines.push("");

    if (openPorts.length > 0) {
      lines.push("=== Open Ports ===");
      for (const pt of openPorts) lines.push(`  ${pt.port}: ${pt.detail || "open"}`);
      lines.push("");
    } else if (hasFailed) {
      lines.push("=== Port Scan === No engine found on 22140–22159", "");
    }

    if (hasFailed && errorLines.length > 0) {
      lines.push("=== Errors (extracted) ===");
      lines.push(...dedup(errorLines));
      lines.push("");
    }

    if (hasFailed) {
      const tail = logs.slice(-60);
      lines.push("=== Recent Logs (last 60, deduplicated) ===");
      lines.push(...dedup(tail));
      lines.push("");
    }

    lines.push("=== End Report ===");
    return lines.join("\n");
  };

  const handleCopyDiagnostics = () => {
    navigator.clipboard.writeText(buildDiagnosticDump());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCopyLogs = () => {
    navigator.clipboard.writeText(logs.join("\n"));
    setLogsCopied(true);
    setTimeout(() => setLogsCopied(false), 2000);
  };

  // ── Rendering helpers ──────────────────────────────────────────────

  const StepIcon = ({ status }: { status: StepStatus }) => {
    switch (status) {
      case "running":
        return <Loader2 className="h-4 w-4 animate-spin text-blue-400" />;
      case "pass":
        return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
      case "fail":
        return <XCircle className="h-4 w-4 text-red-500" />;
      case "skip":
        return <Circle className="h-4 w-4 text-muted-foreground" />;
      default:
        return <Circle className="h-4 w-4 text-muted-foreground/70" />;
    }
  };

  const isInTauri = isTauri();
  const isRunningAction = actionRunning !== null;

  const tabs: { key: MonitorTab; label: string }[] = [
    { key: "status", label: "Status" },
    { key: "ports", label: "Ports" },
    { key: "logs", label: "Logs" },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-hidden flex flex-col">
        {/* Header */}
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle className="flex items-center gap-2 text-base">
              <Cpu className="h-5 w-5 text-primary" />
              Engine Monitor
            </DialogTitle>
            <div className="flex items-center gap-2 pr-8">
              <Badge
                variant={
                  engineStatus === "connected"
                    ? "success"
                    : engineStatus === "error"
                    ? "destructive"
                    : "secondary"
                }
              >
                {engineStatus}
              </Badge>
            </div>
          </div>
          <DialogDescription className="sr-only">
            Live engine health monitor and diagnostics
          </DialogDescription>
        </DialogHeader>

        {/* Error banner */}
        {engineError && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm text-red-400">
            <AlertTriangle className="mr-1.5 inline h-4 w-4" />
            {engineError}
          </div>
        )}

        {/* Tab bar */}
        <div className="flex border-b border-border/50">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => {
                setActiveTab(tab.key);
                if (tab.key === "ports" && ports.length === 0) scanPorts();
              }}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === tab.key
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* ── Status Tab ──────────────────────────────────────── */}
        {activeTab === "status" && (
          <div className="flex-1 overflow-auto space-y-4">
            {/* Diagnostic Steps */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Diagnostics</h3>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={runDiagnostics}
                  disabled={isRunningAction}
                  className="h-7 px-2 text-xs"
                >
                  <RefreshCw className="h-3 w-3" />
                  Re-run
                </Button>
              </div>
              <div className="space-y-1.5 rounded-lg border border-border/50 bg-muted/30 p-3">
                {steps.map((step, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <div className="mt-0.5">
                      <StepIcon status={step.status} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm">{step.label}</div>
                      {step.detail && (
                        <div className="text-xs text-muted-foreground mt-0.5">
                          {step.detail}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Action Buttons */}
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Actions</h3>
              <div className="grid grid-cols-2 gap-2">
                {isInTauri && (
                  <>
                    <ActionButton
                      label="Start Engine"
                      icon={<Play className="h-4 w-4" />}
                      onClick={handleStartEngine}
                      disabled={isRunningAction}
                      active={actionRunning === "Start Engine"}
                    />
                    <ActionButton
                      label="Restart Engine"
                      icon={<RotateCcw className="h-4 w-4" />}
                      onClick={handleRestartEngine}
                      disabled={isRunningAction}
                      active={actionRunning === "Restart Engine"}
                    />
                    <ActionButton
                      label="Kill Engine"
                      icon={<Square className="h-4 w-4" />}
                      onClick={handleKillEngine}
                      disabled={isRunningAction}
                      active={actionRunning === "Kill Engine"}
                      className="text-red-400 hover:text-red-300"
                    />
                  </>
                )}
                <ActionButton
                  label="Retry Connection"
                  icon={<RefreshCw className="h-4 w-4" />}
                  onClick={handleRetryConnection}
                  disabled={isRunningAction}
                  active={actionRunning === "Retry Connection"}
                />
              </div>
              {!isInTauri && (
                <p className="text-xs text-muted-foreground">
                  Start/Restart/Kill available in desktop app only.
                  Dev: <code className="text-xs">uv run python run.py</code>
                </p>
              )}
            </div>

            {/* Copy buttons */}
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleCopyDiagnostics}
                className="flex-1 text-xs"
              >
                {copied ? (
                  <CheckCheck className="h-3 w-3 text-emerald-500" />
                ) : (
                  <Copy className="h-3 w-3" />
                )}
                Copy Full Report
              </Button>
            </div>
          </div>
        )}

        {/* ── Ports Tab ───────────────────────────────────────── */}
        {activeTab === "ports" && (
          <div className="flex-1 overflow-auto space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium flex items-center gap-1.5">
                <Network className="h-4 w-4 text-primary" />
                Port Scan (22140–22159)
              </h3>
              <Button
                variant="ghost"
                size="sm"
                onClick={scanPorts}
                disabled={portScanning}
                className="h-7 px-2 text-xs"
              >
                {portScanning ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="h-3 w-3" />
                )}
                Scan
              </Button>
            </div>
            <ScrollArea className="h-[340px]">
              <div className="space-y-1">
                {ports.map((p) => (
                  <div
                    key={p.port}
                    className={`flex items-center justify-between px-3 py-1.5 rounded-md text-sm ${
                      p.status === "open"
                        ? "bg-emerald-500/10 border border-emerald-500/20"
                        : p.status === "scanning"
                        ? "bg-muted/30"
                        : "bg-transparent"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      {p.status === "open" ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                      ) : p.status === "scanning" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-400" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                      <span className="font-mono text-xs">{p.port}</span>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {p.status === "open"
                        ? p.detail || "Open"
                        : p.status === "scanning"
                        ? "Scanning..."
                        : "Closed"}
                    </span>
                  </div>
                ))}
                {ports.length === 0 && (
                  <p className="text-sm text-muted-foreground text-center py-8">
                    Click "Scan" to check ports 22140–22159
                  </p>
                )}
              </div>
            </ScrollArea>
          </div>
        )}

        {/* ── Logs Tab ────────────────────────────────────────── */}
        {activeTab === "logs" && (
          <div className="flex-1 min-h-0 space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium flex items-center gap-1.5">
                <Terminal className="h-4 w-4 text-primary" />
                Engine Output
              </h3>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleCopyLogs}
                className="h-7 px-2 text-xs"
              >
                {logsCopied ? (
                  <CheckCheck className="h-3 w-3 text-emerald-500" />
                ) : (
                  <Copy className="h-3 w-3" />
                )}
                Copy
              </Button>
            </div>
            <div ref={logScrollRef} className="dark h-[380px] overflow-y-auto rounded-lg border border-zinc-700/50 bg-zinc-950 p-3 text-zinc-200">
              <div className="font-mono text-xs text-zinc-400 space-y-0.5">
                {logs.length === 0 && (
                  <div className="text-zinc-600 italic">
                    Waiting for engine output...
                  </div>
                )}
                {logs.map((line, i) => (
                  <div
                    key={i}
                    className={`break-words leading-relaxed ${
                      line.includes("[stderr]") || line.includes("ERROR")
                        ? "text-red-400"
                        : line.includes("[terminated]")
                        ? "text-amber-400"
                        : line.includes("[pass]") || line.includes("complete")
                        ? "text-emerald-400"
                        : ""
                    }`}
                  >
                    {line}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── Sub-components ──────────────────────────────────────────────────

function ActionButton({
  label,
  icon,
  onClick,
  disabled,
  active,
  className = "",
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled: boolean;
  active: boolean;
  className?: string;
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={onClick}
      disabled={disabled}
      className={className}
    >
      {active ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {label}
    </Button>
  );
}
