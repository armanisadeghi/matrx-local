import { useState, useEffect, useRef, useCallback } from "react";
import { Activity, Cpu, HardDrive, Battery, BatteryCharging, RefreshCw, Zap, Search, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { GaugeRing } from "@/components/tools/shared/GaugeRing";
import { Sparkline } from "@/components/tools/shared/Sparkline";
import { ToolSection } from "@/components/tools/shared/ToolSection";
import type { ToolUISchema } from "@/types/tool-schema";
import { cn } from "@/lib/utils";

interface MonitoringPanelProps {
  onInvoke: (toolName: string, params: Record<string, unknown>) => Promise<void>;
  loading: boolean;
  result: unknown;
  tools?: ToolUISchema[];
}

interface ResourceData {
  cpu_percent?: number;
  memory_percent?: number;
  memory_used_gb?: number;
  memory_total_gb?: number;
  disk_percent?: number;
  disk_used_gb?: number;
  disk_total_gb?: number;
  battery_percent?: number;
  battery_plugged?: boolean;
  uptime_seconds?: number;
  net_sent_bytes?: number;
  net_recv_bytes?: number;
}

interface ProcessRow {
  name?: string;
  pid?: number;
  cpu_percent?: number;
  memory_percent?: number;
  status?: string;
}

const MAX_HISTORY = 30;

function formatUptime(seconds: number) {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function MonitoringPanel({ onInvoke, loading, result }: MonitoringPanelProps) {
  const [resources, setResources] = useState<ResourceData | null>(null);
  const [processes, setProcesses] = useState<ProcessRow[]>([]);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [cpuHistory, setCpuHistory] = useState<number[]>([]);
  const [memHistory, setMemHistory] = useState<number[]>([]);
  const [diskHistory, setDiskHistory] = useState<number[]>([]);
  const [processSearch, setProcessSearch] = useState("");
  const [processSort, setProcessSort] = useState<"cpu" | "memory" | "name">("cpu");
  const [view, setView] = useState<"overview" | "processes">("overview");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const parseResult = useCallback((r: unknown) => {
    try {
      const d = r as { output?: string; type?: string; metadata?: Record<string, unknown> };
      if (!d || d.type === "error") return;
      // The structured data lives in metadata — `output` is human-readable
      // text ("System Resources:\n  CPU: ..."), so JSON.parse(output) always
      // threw and the dashboard stayed empty while polling every 3s.
      const meta = d.metadata;
      if (!meta || typeof meta !== "object") return;
      if (Array.isArray((meta as { processes?: unknown }).processes)) {
        setProcesses((meta as { processes: ProcessRow[] }).processes);
        return;
      }
      const parsed = meta as unknown as ResourceData;
      if (parsed.cpu_percent != null) {
        setResources(parsed);
        setCpuHistory((h) => [...h.slice(-MAX_HISTORY + 1), parsed.cpu_percent ?? 0]);
        setMemHistory((h) => [...h.slice(-MAX_HISTORY + 1), parsed.memory_percent ?? 0]);
        setDiskHistory((h) => [...h.slice(-MAX_HISTORY + 1), parsed.disk_percent ?? 0]);
      }
    } catch { /* ignore */ }
  }, []);

  const refresh = useCallback(async () => {
    await onInvoke("SystemResources", {});
  }, [onInvoke]);

  const refreshProcesses = useCallback(async (sortKey?: "cpu" | "memory" | "name") => {
    // Accept the key explicitly — calling right after setProcessSort used the
    // PREVIOUS render's value (stale closure), fetching by the wrong sort.
    await onInvoke("TopProcesses", { limit: 20, sort_by: sortKey ?? processSort });
  }, [onInvoke, processSort]);

  useEffect(() => { parseResult(result); }, [result, parseResult]);

  useEffect(() => {
    refresh();
    refreshProcesses();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (autoRefresh) {
      intervalRef.current = setInterval(() => { refresh(); }, 3000);
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [autoRefresh, refresh]);

  const cpu  = resources?.cpu_percent ?? 0;
  const mem  = resources?.memory_percent ?? 0;
  const disk = resources?.disk_percent ?? 0;
  const bat  = resources?.battery_percent;

  const filteredProcesses = processes
    .filter((p) => !processSearch || p.name?.toLowerCase().includes(processSearch.toLowerCase()))
    .sort((a, b) => {
      if (processSort === "cpu")    return (b.cpu_percent ?? 0) - (a.cpu_percent ?? 0);
      if (processSort === "memory") return (b.memory_percent ?? 0) - (a.memory_percent ?? 0);
      return (a.name ?? "").localeCompare(b.name ?? "");
    });

  return (
    <div className="flex h-full flex-col gap-4 p-5 overflow-auto">
      {/* Controls */}
      <div className="flex items-center justify-between">
        <div className="flex gap-1 rounded-lg border bg-muted/20 p-0.5">
          {(["overview", "processes"] as const).map((v) => (
            <button key={v} onClick={() => { setView(v); if (v === "processes") refreshProcesses(); }}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-all capitalize",
                view === v ? "bg-background shadow text-foreground" : "text-muted-foreground hover:text-foreground"
              )}>
              {v}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAutoRefresh((v) => !v)}
            className={cn(
              "text-[11px] px-2.5 py-1 rounded-full border transition-colors font-medium",
              autoRefresh ? "border-violet-500/50 text-violet-400 bg-violet-500/10" : "border-border text-muted-foreground"
            )}
          >
            {autoRefresh ? "Live" : "Paused"}
          </button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={refresh} disabled={loading}>
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      {view === "overview" && (
        <>
          {/* Gauge rings */}
          <ToolSection title="System Resources" icon={Activity} iconColor="text-violet-400" noPadding>
            <div className="flex items-center justify-around py-5 px-4">
              <GaugeRing value={cpu}  label="CPU"    color="violet" icon={Cpu}      />
              <GaugeRing value={mem}  label="Memory" color="blue"   icon={Activity} />
              <GaugeRing value={disk} label="Disk"   color="teal"   icon={HardDrive} />
              {bat != null && (
                <GaugeRing value={bat} label="Battery" color="amber"
                  icon={resources?.battery_plugged ? BatteryCharging : Battery} />
              )}
            </div>
          </ToolSection>

          {/* Sparkline history */}
          {cpuHistory.length > 1 && (
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-xl border bg-card/50 p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold">CPU</span>
                  <span className="text-xs font-bold text-violet-400 tabular-nums">{Math.round(cpu)}%</span>
                </div>
                <Sparkline data={cpuHistory} width={160} height={36} color="violet" min={0} max={100} />
              </div>
              <div className="rounded-xl border bg-card/50 p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold">Memory</span>
                  <span className="text-xs font-bold text-blue-400 tabular-nums">{Math.round(mem)}%</span>
                </div>
                <Sparkline data={memHistory} width={160} height={36} color="blue" min={0} max={100} />
              </div>
              <div className="rounded-xl border bg-card/50 p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold">Disk</span>
                  <span className="text-xs font-bold text-teal-400 tabular-nums">{Math.round(disk)}%</span>
                </div>
                <Sparkline data={diskHistory} width={160} height={36} color="teal" min={0} max={100} />
              </div>
            </div>
          )}

          {/* Stats row */}
          {resources && (
            <div className="grid grid-cols-4 gap-2">
              <div className="rounded-xl border bg-card/40 px-3 py-2.5 text-center">
                <p className="text-[10px] text-muted-foreground font-medium">Memory</p>
                <p className="text-sm font-bold tabular-nums">
                  {resources.memory_used_gb?.toFixed(1) ?? "—"}
                  <span className="text-[10px] text-muted-foreground font-normal"> / {resources.memory_total_gb?.toFixed(0) ?? "—"} GB</span>
                </p>
              </div>
              <div className="rounded-xl border bg-card/40 px-3 py-2.5 text-center">
                <p className="text-[10px] text-muted-foreground font-medium">Disk Used</p>
                <p className="text-sm font-bold tabular-nums">
                  {resources.disk_used_gb?.toFixed(1) ?? "—"}
                  <span className="text-[10px] text-muted-foreground font-normal"> / {resources.disk_total_gb?.toFixed(0) ?? "—"} GB</span>
                </p>
              </div>
              <div className="rounded-xl border bg-card/40 px-3 py-2.5 text-center">
                <p className="text-[10px] text-muted-foreground font-medium">Uptime</p>
                <p className="text-sm font-bold tabular-nums">
                  {resources.uptime_seconds ? formatUptime(resources.uptime_seconds) : "—"}
                </p>
              </div>
              <div className="rounded-xl border bg-card/40 px-3 py-2.5 text-center">
                <p className="text-[10px] text-muted-foreground font-medium">Network</p>
                <p className="text-sm font-bold tabular-nums">
                  {resources.net_sent_bytes ? formatBytes(resources.net_sent_bytes) : "—"}
                </p>
              </div>
            </div>
          )}

          {/* Quick process preview */}
          {processes.length > 0 && (
            <ToolSection title="Top Processes" icon={Zap} iconColor="text-violet-400"
              actions={
                <button onClick={() => refreshProcesses()} className="text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1">
                  <RefreshCw className="h-3 w-3" /> Refresh
                </button>
              } noPadding>
              <div className="divide-y divide-border/30">
                {processes.slice(0, 6).map((p, i) => (
                  <div key={p.pid ?? i} className="flex items-center gap-3 px-4 py-2 hover:bg-muted/20 transition-colors">
                    <span className="text-[10px] w-4 text-center text-muted-foreground tabular-nums">{i + 1}</span>
                    <span className="flex-1 text-xs font-medium truncate">{p.name ?? "unknown"}</span>
                    <span className="text-[11px] tabular-nums text-violet-400 w-14 text-right">{(p.cpu_percent ?? 0).toFixed(1)}%</span>
                    <span className="text-[11px] tabular-nums text-blue-400 w-14 text-right">{(p.memory_percent ?? 0).toFixed(1)}%</span>
                  </div>
                ))}
              </div>
              <div className="flex justify-end gap-4 px-4 py-1.5 text-[9px] border-t border-border/30">
                <span className="text-violet-400 uppercase tracking-wider">CPU</span>
                <span className="text-blue-400 uppercase tracking-wider">MEM</span>
              </div>
            </ToolSection>
          )}
        </>
      )}

      {view === "processes" && (
        <>
          {/* Process controls */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input value={processSearch} onChange={(e) => setProcessSearch(e.target.value)}
                placeholder="Filter processes..." className="pl-8 h-8 text-xs" />
            </div>
            {(["cpu", "memory", "name"] as const).map((s) => (
              <button key={s} onClick={() => { setProcessSort(s); refreshProcesses(s); }}
                className={cn(
                  "rounded-lg border px-2.5 py-1 text-[11px] font-medium transition-colors",
                  processSort === s
                    ? "border-primary/50 bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground"
                )}>
                {s.toUpperCase()}
              </button>
            ))}
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => refreshProcesses()} disabled={loading}>
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            </Button>
          </div>

          {/* Process table */}
          {filteredProcesses.length > 0 ? (
            <div className="flex-1 overflow-auto rounded-xl border">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-background/90 backdrop-blur-sm">
                  <tr className="border-b">
                    <th className="text-left px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider w-16">PID</th>
                    <th className="text-left px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Name</th>
                    <th className="text-right px-3 py-2 text-[10px] font-semibold text-violet-400 uppercase tracking-wider w-16">CPU%</th>
                    <th className="text-right px-3 py-2 text-[10px] font-semibold text-blue-400 uppercase tracking-wider w-16">MEM%</th>
                    <th className="text-center px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider w-16">Status</th>
                    <th className="w-10" />
                  </tr>
                </thead>
                <tbody>
                  {filteredProcesses.map((p, i) => (
                    <tr key={p.pid ?? i} className="border-b border-border/20 hover:bg-muted/20 transition-colors group">
                      <td className="px-3 py-1.5 font-mono text-muted-foreground/70 tabular-nums">{p.pid}</td>
                      <td className="px-3 py-1.5 font-medium truncate max-w-[200px]">{p.name}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-violet-400">{(p.cpu_percent ?? 0).toFixed(1)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-blue-400">{(p.memory_percent ?? 0).toFixed(1)}</td>
                      <td className="px-3 py-1.5 text-center">
                        <span className={cn(
                          "text-[9px] px-1.5 py-0.5 rounded-full",
                          p.status === "running" ? "bg-emerald-500/10 text-emerald-400" : "bg-muted text-muted-foreground"
                        )}>
                          {p.status ?? "—"}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-center">
                        {p.pid && (
                          <button onClick={() => onInvoke("KillProcess", { pid: p.pid })}
                            className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
              {loading ? "Loading processes..." : "No processes to display"}
            </div>
          )}
        </>
      )}

      {!resources && !loading && view === "overview" && (
        <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
          Click refresh to load system data
        </div>
      )}
    </div>
  );
}
