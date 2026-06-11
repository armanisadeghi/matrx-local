import { useState, useCallback } from "react";
import { Search, Cpu, Trash2, RefreshCw, Activity } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { AiBadge } from "@/components/tools/panels/AiBadge";

interface ProcessPanelProps {
  onInvoke: (toolName: string, params: Record<string, unknown>) => Promise<void>;
  loading: boolean;
  result: unknown;
}

interface ProcessEntry {
  pid?: number;
  name?: string;
  cpu_percent?: number;
  memory_percent?: number;
  status?: string;
}

function tryParse(result: unknown): ProcessEntry[] | null {
  try {
    const d = result as { output?: string; type?: string };
    if (!d || d.type === "error") return null;
    if (d.output) {
      const arr = JSON.parse(d.output);
      if (Array.isArray(arr)) return arr;
    }
    return null;
  } catch { return null; }
}

export function ProcessPanel({ onInvoke, loading, result }: ProcessPanelProps) {
  const [search, setSearch]   = useState("");
  const [sort, setSort]       = useState<"cpu" | "memory" | "name">("cpu");
  const [launch, setLaunch]   = useState("");
  const [launched, setLaunched] = useState(false);

  const processes = (tryParse(result) ?? [])
    .filter((p) => !search || p.name?.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sort === "cpu")    return (b.cpu_percent ?? 0) - (a.cpu_percent ?? 0);
      if (sort === "memory") return (b.memory_percent ?? 0) - (a.memory_percent ?? 0);
      return (a.name ?? "").localeCompare(b.name ?? "");
    });

  const refresh = useCallback(
    (sortKey?: "cpu" | "memory" | "name") =>
      // Explicit key avoids the stale-closure fetch right after setSort.
      onInvoke("ListProcesses", { sort_by: sortKey ?? sort, limit: 50 }),
    [onInvoke, sort],
  );
  const kill    = useCallback((pid: number) => onInvoke("KillProcess", { pid }), [onInvoke]);
  const launchApp = useCallback(async () => {
    if (!launch.trim()) return;
    await onInvoke("LaunchApp", { application: launch });
    setLaunched(true);
    setTimeout(() => setLaunched(false), 2000);
  }, [onInvoke, launch]);

  return (
    <div className="flex h-full flex-col gap-4 p-5 overflow-hidden">
      <AiBadge text="Your AI can list, launch, and kill processes on your system" />

      {/* Launch app */}
      <div className="flex gap-2">
        <Input value={launch} onChange={(e) => setLaunch(e.target.value)}
          placeholder="App to launch (e.g., Safari)"
          className="text-sm"
          onKeyDown={(e) => { if (e.key === "Enter") launchApp(); }}
        />
        <Button onClick={launchApp} disabled={loading || !launch.trim()} className={`shrink-0 gap-1.5 ${launched ? "bg-emerald-600 hover:bg-emerald-600" : ""}`}>
          Launch
        </Button>
      </div>

      {/* Process list controls */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter processes…" className="pl-8 h-8 text-xs" />
        </div>
        {(["cpu", "memory", "name"] as const).map((s) => (
          <button key={s} onClick={() => { setSort(s); refresh(s); }}
            className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
              sort === s ? "border-primary/50 bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground"
            }`}>
            {s.toUpperCase()}
          </button>
        ))}
        <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={() => refresh()} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {/* Table */}
      {processes.length > 0 ? (
        <div className="flex-1 overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-background/80 backdrop-blur-sm">
              <tr className="border-b">
                <th className="text-left px-2 py-1.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">PID</th>
                <th className="text-left px-2 py-1.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">Name</th>
                <th className="text-right px-2 py-1.5 text-[11px] font-semibold text-violet-400 uppercase tracking-wider">CPU%</th>
                <th className="text-right px-2 py-1.5 text-[11px] font-semibold text-blue-400 uppercase tracking-wider">MEM%</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {processes.map((p, i) => (
                <tr key={p.pid ?? i} className="border-b border-border/30 hover:bg-muted/30 transition-colors group">
                  <td className="px-2 py-1.5 font-mono text-muted-foreground/70">{p.pid}</td>
                  <td className="px-2 py-1.5 font-medium max-w-[180px]">
                    <div className="flex items-center gap-1.5">
                      <Activity className="h-3 w-3 text-muted-foreground/50 shrink-0" />
                      <span className="truncate">{p.name}</span>
                    </div>
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-violet-400">
                    {(p.cpu_percent ?? 0).toFixed(1)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-blue-400">
                    {(p.memory_percent ?? 0).toFixed(1)}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {p.pid && (
                      <button onClick={() => kill(p.pid!)}
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
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground">
          <Cpu className="h-10 w-10 opacity-20" />
          <p className="text-sm">Click Refresh to load processes</p>
          <Button variant="outline" onClick={() => refresh()}>Load Processes</Button>
        </div>
      )}
    </div>
  );
}
