import { useState, useEffect, useCallback, useRef } from "react";
import { useLocation } from "react-router-dom";
import {
  Network,
  RefreshCw,
  ShieldAlert,
  Search,
  Server,
  Activity,
  Cpu,
  TerminalSquare,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Copy,
  MonitorDot,
  FileText,
  Clock,
  FolderOpen,
  X,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { engine } from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";
import { logWarn } from "@/lib/error-reporting";

// ─── Types ────────────────────────────────────────────────────────────────────

interface PortsProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
}

interface PortProcess {
  pid: number;
  name: string;
  port: number;
  address: string;
  protocol: string;
  user: string;
}

interface TerminalProcess {
  pid: number;
  name: string;
  status: string;
  user: string;
  cwd: string;
  tty: string;
  cmdline: string;
  elapsed_s: number;
}

interface TailResult {
  output: string;
  source: string;
  open_files?: { path: string; fd: number }[];
  connections?: { fd: number; laddr: string; raddr: string; status: string }[];
  note?: string;
}

type PortSortCol = "port" | "name" | "pid" | "address" | "protocol";
type TermSortCol = "name" | "pid" | "cwd" | "tty" | "elapsed_s";
type SortDir = "asc" | "desc";

// ─── Port category quick-filters ─────────────────────────────────────────────

interface PortCategory {
  id: string;
  label: string;
  color: string;
  match: (p: PortProcess) => boolean;
}

const PORT_CATEGORIES: PortCategory[] = [
  {
    id: "dev",
    label: "Dev Servers",
    color: "text-green-700 dark:text-green-400 border-green-400/40 bg-green-400/10",
    match: (p) => {
      const devPorts = [
        3000, 3001, 3002, 3003, 4000, 5000, 5173, 5174, 4200, 8080, 8000, 8888,
        9000,
      ];
      const devNames = [
        "node",
        "npm",
        "pnpm",
        "yarn",
        "vite",
        "webpack",
        "parcel",
        "next",
        "nuxt",
        "remix",
        "astro",
        "bun",
      ];
      return (
        devPorts.includes(p.port) ||
        devNames.some((n) => p.name.toLowerCase().includes(n))
      );
    },
  },
  {
    id: "python",
    label: "Python",
    color: "text-blue-700 dark:text-blue-400 border-blue-400/40 bg-blue-400/10",
    match: (p) => {
      const pyPorts = [
        8000, 8001, 8002, 8888, 8889, 5000, 5001,
        // Matrx engine port bases: live (packaged) and dev (source-run)
        22140, 22141, 22142, 22240, 22241, 22242,
      ];
      return (
        pyPorts.includes(p.port) ||
        p.name.toLowerCase().includes("python") ||
        p.name.toLowerCase().includes("uvicorn") ||
        p.name.toLowerCase().includes("gunicorn") ||
        p.name.toLowerCase().includes("fastapi") ||
        p.name.toLowerCase().includes("flask") ||
        p.name.toLowerCase().includes("django")
      );
    },
  },
  {
    id: "database",
    label: "Databases",
    color: "text-orange-700 dark:text-orange-400 border-orange-400/40 bg-orange-400/10",
    match: (p) => {
      const dbPorts = [
        5432, 5433, 3306, 3307, 27017, 27018, 6379, 6380, 9200, 9300, 1433,
        1521, 5984, 8086, 7474, 7687,
      ];
      const dbNames = [
        "postgres",
        "mysql",
        "mongo",
        "redis",
        "elastic",
        "cassandra",
        "influx",
        "neo4j",
        "sqlite",
        "cockroach",
      ];
      return (
        dbPorts.includes(p.port) ||
        dbNames.some((n) => p.name.toLowerCase().includes(n))
      );
    },
  },
  {
    id: "docker",
    label: "Docker",
    color: "text-sky-700 dark:text-sky-400 border-sky-400/40 bg-sky-400/10",
    match: (p) =>
      p.name.toLowerCase().includes("docker") ||
      p.name.toLowerCase().includes("containerd") ||
      p.name.toLowerCase().includes("dockerd"),
  },
  {
    id: "local",
    label: "Localhost Only",
    color: "text-violet-700 dark:text-violet-400 border-violet-400/40 bg-violet-400/10",
    match: (p) => p.address === "127.0.0.1" || p.address === "::1",
  },
  {
    id: "external",
    label: "External / 0.0.0.0",
    color: "text-red-700 dark:text-red-400 border-red-400/40 bg-red-400/10",
    match: (p) => p.address === "0.0.0.0" || p.address === "::",
  },
  {
    id: "ai",
    label: "AI / LLM",
    color: "text-fuchsia-700 dark:text-fuchsia-400 border-fuchsia-400/40 bg-fuchsia-400/10",
    match: (p) => {
      const aiPorts = [11434, 8080, 5000, 7860, 7861, 7862, 3001, 1234, 4891];
      const aiNames = [
        "ollama",
        "llama",
        "lmstudio",
        "localai",
        "gradio",
        "diffus",
        "stable",
        "comfy",
        "text-generation",
        "tgi",
        "vllm",
        "llamacpp",
      ];
      return (
        aiPorts.includes(p.port) ||
        aiNames.some((n) => p.name.toLowerCase().includes(n))
      );
    },
  },
];

// ─── Terminal category quick-filters ─────────────────────────────────────────

interface TermCategory {
  id: string;
  label: string;
  color: string;
  match: (t: TerminalProcess) => boolean;
}

const TERM_CATEGORIES: TermCategory[] = [
  {
    id: "shells",
    label: "Active Shells",
    color: "text-emerald-700 dark:text-emerald-400 border-emerald-400/40 bg-emerald-400/10",
    match: (t) =>
      ["zsh", "bash", "fish", "sh", "dash", "ksh", "tcsh"].some(
        (s) => t.name.toLowerCase() === s,
      ),
  },
  {
    id: "running",
    label: "Running Processes",
    color: "text-green-700 dark:text-green-400 border-green-400/40 bg-green-400/10",
    match: (t) => t.status === "running",
  },
  {
    id: "dev_code",
    label: "Dev / Code",
    color: "text-blue-700 dark:text-blue-400 border-blue-400/40 bg-blue-400/10",
    match: (t) => {
      const names = [
        "node",
        "python",
        "python3",
        "uvicorn",
        "npm",
        "pnpm",
        "yarn",
        "bun",
        "deno",
        "cargo",
        "go",
        "ruby",
        "php",
        "java",
        "gradle",
        "mvn",
        "sbt",
        "mix",
        "elixir",
      ];
      return names.some(
        (n) =>
          t.name.toLowerCase().includes(n) ||
          t.cmdline.toLowerCase().includes(n),
      );
    },
  },
  {
    id: "editors",
    label: "Editors / IDEs",
    color: "text-yellow-700 dark:text-yellow-400 border-yellow-400/40 bg-yellow-400/10",
    match: (t) => {
      const editors = [
        "cursor",
        "code",
        "vim",
        "nvim",
        "neovim",
        "nano",
        "emacs",
        "helix",
        "zed",
        "sublime",
        "atom",
        "intellij",
        "pycharm",
        "webstorm",
        "goland",
      ];
      return editors.some(
        (e) =>
          t.name.toLowerCase().includes(e) ||
          t.cmdline.toLowerCase().includes(e),
      );
    },
  },
  {
    id: "git",
    label: "Git",
    color: "text-orange-700 dark:text-orange-400 border-orange-400/40 bg-orange-400/10",
    match: (t) =>
      t.cmdline.toLowerCase().includes("git") ||
      t.name.toLowerCase().includes("git"),
  },
  {
    id: "recent",
    label: "Recent (< 10 min)",
    color: "text-cyan-700 dark:text-cyan-400 border-cyan-400/40 bg-cyan-400/10",
    match: (t) => t.elapsed_s < 600,
  },
  {
    id: "long_running",
    label: "Long Running (> 1h)",
    color: "text-purple-700 dark:text-purple-400 border-purple-400/40 bg-purple-400/10",
    match: (t) => t.elapsed_s > 3600,
  },
  {
    id: "project_dir",
    label: "In Code Dir",
    color: "text-pink-700 dark:text-pink-400 border-pink-400/40 bg-pink-400/10",
    match: (t) => {
      const cwd = t.cwd.toLowerCase();
      return (
        cwd.includes("/code/") ||
        cwd.includes("/projects/") ||
        cwd.includes("/dev/") ||
        cwd.includes("/workspace/") ||
        cwd.includes("/src/")
      );
    },
  },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sortPorts(
  ports: PortProcess[],
  col: PortSortCol,
  dir: SortDir,
): PortProcess[] {
  return [...ports].sort((a, b) => {
    const aVal =
      col === "port" || col === "pid" ? a[col] : a[col].toLowerCase();
    const bVal =
      col === "port" || col === "pid" ? b[col] : b[col].toLowerCase();
    if (aVal < bVal) return dir === "asc" ? -1 : 1;
    if (aVal > bVal) return dir === "asc" ? 1 : -1;
    return 0;
  });
}

function sortTerminals(
  terms: TerminalProcess[],
  col: TermSortCol,
  dir: SortDir,
): TerminalProcess[] {
  return [...terms].sort((a, b) => {
    const aVal =
      col === "pid" || col === "elapsed_s" ? a[col] : a[col].toLowerCase();
    const bVal =
      col === "pid" || col === "elapsed_s" ? b[col] : b[col].toLowerCase();
    if (aVal < bVal) return dir === "asc" ? -1 : 1;
    if (aVal > bVal) return dir === "asc" ? 1 : -1;
    return 0;
  });
}

function formatElapsed(s: number): string {
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function CategoryPills<T extends { id: string; label: string; color: string }>({
  categories,
  active,
  counts,
  onToggle,
}: {
  categories: T[];
  active: string | null;
  counts: Record<string, number>;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5 px-4 py-2 border-b border-border bg-muted/10 shrink-0">
      {categories.map((cat) => {
        const isActive = active === cat.id;
        const count = counts[cat.id] ?? 0;
        return (
          <button
            key={cat.id}
            onClick={() => onToggle(cat.id)}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-medium transition-all ${
              isActive
                ? cat.color + " shadow-sm"
                : "text-muted-foreground border-border/50 bg-transparent hover:border-border hover:text-foreground"
            }`}
          >
            {cat.label}
            <span
              className={`inline-flex items-center justify-center rounded-full px-1.5 py-0 text-[10px] font-semibold leading-4 ${
                isActive ? "bg-white/20" : "bg-muted text-muted-foreground"
              }`}
            >
              {count}
            </span>
            {isActive && <X className="h-2.5 w-2.5 ml-0.5" />}
          </button>
        );
      })}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function Ports({ engineStatus, engineUrl: _engineUrl }: PortsProps) {
  const [ports, setPorts] = useState<PortProcess[]>([]);
  const [terminals, setTerminals] = useState<TerminalProcess[]>([]);
  const [loading, setLoading] = useState(false);
  const [terminalsLoading, setTerminalsLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState("user");

  // Port sorting
  const [portSortCol, setPortSortCol] = useState<PortSortCol>("port");
  const [portSortDir, setPortSortDir] = useState<SortDir>("asc");
  const [portCategory, setPortCategory] = useState<string | null>(null);

  // Terminal sorting + filtering
  const [termSortCol, setTermSortCol] = useState<TermSortCol>("name");
  const [termSortDir, setTermSortDir] = useState<SortDir>("asc");
  const [termCategory, setTermCategory] = useState<string | null>(null);

  // Kill dialog
  const [killDialogOpen, setKillDialogOpen] = useState(false);
  const [processToKill, setProcessToKill] = useState<PortProcess | null>(null);
  const [isKilling, setIsKilling] = useState(false);
  const [killError, setKillError] = useState<string | null>(null);
  const [inlineKillError, setInlineKillError] = useState<string | null>(null);

  // Pages stay mounted (AppLayout keep-alive) — only poll while visible.
  const isVisible = useLocation().pathname === "/ports";

  const fetchPorts = useCallback(async () => {
    if (engineStatus !== "connected") return;
    // ``invokeToolWs`` now waits up to 3s for the WS upgrade to complete
    // (engine status flips to "connected" the moment REST is reachable,
    // but the WebSocket connects a beat later). If we're still not ready
    // after the wait, skip this tick — the next interval call will pick
    // it up. Without this we'd log spurious "WebSocket not connected"
    // warnings on every page mount during startup.
    if (!engine.isWsConnected() && !(await engine.waitForWs(3000))) return;
    setLoading(true);
    try {
      const result = await engine.invokeToolWs("ListPorts", { limit: 500 });
      if (result.type === "success" && result.metadata?.ports) {
        setPorts(result.metadata.ports as PortProcess[]);
      }
    } catch (error) {
      logWarn("ports", "fetch ports", error);
    } finally {
      setLoading(false);
    }
  }, [engineStatus]);

  const fetchTerminals = useCallback(async () => {
    if (engineStatus !== "connected") return;
    if (!engine.isWsConnected() && !(await engine.waitForWs(3000))) return;
    setTerminalsLoading(true);
    try {
      const result = await engine.invokeToolWs("ListTerminals", {});
      if (result.type === "success" && result.metadata?.terminals) {
        setTerminals(result.metadata.terminals as TerminalProcess[]);
      }
    } catch (error) {
      logWarn("ports", "fetch terminals", error);
    } finally {
      setTerminalsLoading(false);
    }
  }, [engineStatus]);

  useEffect(() => {
    if (!isVisible) return;
    fetchPorts();
    const iv = setInterval(fetchPorts, 10000);
    return () => clearInterval(iv);
  }, [fetchPorts, isVisible]);

  useEffect(() => {
    if (!isVisible) return;
    if (activeTab !== "terminals") return;
    fetchTerminals();
    const iv = setInterval(fetchTerminals, 15000);
    return () => clearInterval(iv);
  }, [activeTab, fetchTerminals, isVisible]);

  const handleKill = async (force: boolean) => {
    if (!processToKill) return;
    setIsKilling(true);
    setKillError(null);
    try {
      const result = await engine.invokeToolWs("KillProcess", {
        pid: processToKill.pid,
        force,
      });
      if (result.type !== "success") {
        setKillError(result.output || "Failed to kill process.");
        return;
      }
      fetchPorts();
      setKillDialogOpen(false);
    } catch (error) {
      console.error("Failed to kill process:", error);
      setKillError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsKilling(false);
    }
  };

  const handleForceKillDirect = async (p: PortProcess) => {
    setIsKilling(true);
    setInlineKillError(null);
    try {
      const result = await engine.invokeToolWs("KillProcess", {
        pid: p.pid,
        force: true,
      });
      if (result.type !== "success") {
        setInlineKillError(
          `Could not kill ${p.name} (PID ${p.pid}): ${result.output || "unknown error"}`,
        );
        return;
      }
      fetchPorts();
    } catch (error) {
      console.error("Failed to force kill process:", error);
      setInlineKillError(
        `Could not kill ${p.name} (PID ${p.pid}): ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setIsKilling(false);
    }
  };

  const isUserItem = (p: PortProcess) => {
    const commonPorts = [
      3000, 3001, 8000, 8080, 5000, 5173, 4200, 8888, 5432, 6379, 27017, 3306,
      9200,
    ];
    const commonNames = [
      "node",
      "python",
      "docker",
      "java",
      "ruby",
      "php",
      "go",
      "npm",
      "pnpm",
      "yarn",
    ];
    return (
      commonPorts.includes(p.port) ||
      commonNames.some((n) => p.name.toLowerCase().includes(n))
    );
  };

  // ── Derived port lists ───────────────────────────────────────────────────
  const searchFilteredPorts = ports.filter((p) => {
    const term = search.toLowerCase();
    if (!term) return true;
    return (
      p.name.toLowerCase().includes(term) ||
      String(p.port).includes(term) ||
      p.address.toLowerCase().includes(term) ||
      p.protocol.toLowerCase().includes(term) ||
      (p.pid > 0 && String(p.pid).includes(term))
    );
  });

  const categoryFilter = portCategory
    ? PORT_CATEGORIES.find((c) => c.id === portCategory)
    : null;

  const filteredPorts = sortPorts(
    categoryFilter
      ? searchFilteredPorts.filter(categoryFilter.match)
      : searchFilteredPorts,
    portSortCol,
    portSortDir,
  );

  const userItems = sortPorts(
    searchFilteredPorts
      .filter(isUserItem)
      .filter((p) => (categoryFilter ? categoryFilter.match(p) : true)),
    portSortCol,
    portSortDir,
  );

  // Category counts computed against search-filtered set (ignoring current category)
  const portCategoryCounts = Object.fromEntries(
    PORT_CATEGORIES.map((cat) => [
      cat.id,
      searchFilteredPorts.filter(cat.match).length,
    ]),
  );

  // ── Derived terminal lists ───────────────────────────────────────────────
  const searchFilteredTerminals = terminals.filter((t) => {
    const term = search.toLowerCase();
    if (!term) return true;
    return (
      t.name.toLowerCase().includes(term) ||
      t.cmdline.toLowerCase().includes(term) ||
      t.cwd.toLowerCase().includes(term) ||
      t.tty.toLowerCase().includes(term) ||
      String(t.pid).includes(term)
    );
  });

  const termCategoryFilter = termCategory
    ? TERM_CATEGORIES.find((c) => c.id === termCategory)
    : null;

  const filteredTerminals = sortTerminals(
    termCategoryFilter
      ? searchFilteredTerminals.filter(termCategoryFilter.match)
      : searchFilteredTerminals,
    termSortCol,
    termSortDir,
  );

  const termCategoryCounts = Object.fromEntries(
    TERM_CATEGORIES.map((cat) => [
      cat.id,
      searchFilteredTerminals.filter(cat.match).length,
    ]),
  );

  const isRefreshing = activeTab === "terminals" ? terminalsLoading : loading;

  const handleRefresh = () => {
    if (activeTab === "terminals") fetchTerminals();
    else fetchPorts();
  };

  const handlePortSort = (col: PortSortCol) => {
    if (portSortCol === col)
      setPortSortDir(portSortDir === "asc" ? "desc" : "asc");
    else {
      setPortSortCol(col);
      setPortSortDir("asc");
    }
  };

  const handleTermSort = (col: TermSortCol) => {
    if (termSortCol === col)
      setTermSortDir(termSortDir === "asc" ? "desc" : "asc");
    else {
      setTermSortCol(col);
      setTermSortDir("asc");
    }
  };

  const getProcessIcon = (name: string) => {
    const n = name.toLowerCase();
    if (n.includes("node") || n.includes("npm"))
      return <TerminalSquare className="h-4 w-4 text-green-500" />;
    if (n.includes("python") || n.includes("uvicorn"))
      return <TerminalSquare className="h-4 w-4 text-blue-500" />;
    if (n.includes("docker"))
      return <Server className="h-4 w-4 text-blue-700 dark:text-blue-400" />;
    if (n.includes("java")) return <Cpu className="h-4 w-4 text-orange-500" />;
    return <Activity className="h-4 w-4 text-muted-foreground" />;
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Port Manager"
        description="Monitor network ports, services, and terminal sessions"
      >
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              type="text"
              placeholder={
                activeTab === "terminals"
                  ? "Filter terminals…"
                  : "Filter ports…"
              }
              className="h-9 w-64 bg-background/50 pl-9"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button
                className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground"
                onClick={() => setSearch("")}
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
          <Button
            variant="outline"
            size="icon"
            onClick={() => {
              const data =
                activeTab === "terminals"
                  ? filteredTerminals
                  : activeTab === "user"
                    ? userItems
                    : filteredPorts;
              navigator.clipboard.writeText(JSON.stringify(data, null, 2));
            }}
            title="Copy Filtered Data (JSON)"
            className="h-9 w-9 bg-background/50 backdrop-blur-md"
          >
            <Copy className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="h-9 w-9 bg-background/50 backdrop-blur-md"
          >
            <RefreshCw
              className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`}
            />
          </Button>
        </div>
      </PageHeader>

      {inlineKillError && (
        <div className="mx-6 mt-4 flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <span className="flex items-center gap-2 min-w-0">
            <ShieldAlert className="h-4 w-4 shrink-0" />
            <span className="truncate">{inlineKillError}</span>
          </span>
          <button
            className="shrink-0 hover:opacity-70"
            onClick={() => setInlineKillError(null)}
            title="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <div className="flex-1 px-6 pb-6 overflow-hidden flex flex-col min-h-0">
        <Tabs
          defaultValue="user"
          className="flex-1 flex flex-col min-h-0"
          onValueChange={(v) => {
            setActiveTab(v);
            setSearch("");
          }}
        >
          <TabsList className="w-full max-w-[620px] grid grid-cols-3 bg-background/40 backdrop-blur-xl border border-border shadow-sm shrink-0 mt-6 mb-4">
            <TabsTrigger
              value="user"
              className="data-[state=active]:bg-primary/20 data-[state=active]:text-primary"
            >
              <Server className="h-4 w-4 mr-2" />
              Dev Services
              <Badge variant="secondary" className="ml-2 bg-background/50">
                {userItems.length}
              </Badge>
            </TabsTrigger>
            <TabsTrigger
              value="all"
              className="data-[state=active]:bg-primary/20 data-[state=active]:text-primary"
            >
              <Network className="h-4 w-4 mr-2" />
              All Ports
              <Badge variant="secondary" className="ml-2 bg-background/50">
                {filteredPorts.length}
              </Badge>
            </TabsTrigger>
            <TabsTrigger
              value="terminals"
              className="data-[state=active]:bg-primary/20 data-[state=active]:text-primary"
            >
              <MonitorDot className="h-4 w-4 mr-2" />
              Terminals
              <Badge variant="secondary" className="ml-2 bg-background/50">
                {filteredTerminals.length}
              </Badge>
            </TabsTrigger>
          </TabsList>

          <TabsContent
            value="user"
            className="flex-1 min-h-0 data-[state=inactive]:hidden data-[state=active]:flex flex-col"
          >
            <div className="flex flex-col flex-1 min-h-0 rounded-xl border border-border bg-card/40 backdrop-blur-xl overflow-hidden shadow-sm">
              <CategoryPills
                categories={PORT_CATEGORIES}
                active={portCategory}
                counts={portCategoryCounts}
                onToggle={(id) =>
                  setPortCategory(portCategory === id ? null : id)
                }
              />
              <PortTable
                ports={userItems}
                loading={loading}
                sortCol={portSortCol}
                sortDir={portSortDir}
                onSort={handlePortSort}
                getProcessIcon={getProcessIcon}
                onKill={(p) => {
                  setProcessToKill(p);
                  setKillDialogOpen(true);
                }}
                onForceKill={handleForceKillDirect}
              />
            </div>
          </TabsContent>

          <TabsContent
            value="all"
            className="flex-1 min-h-0 data-[state=inactive]:hidden data-[state=active]:flex flex-col"
          >
            <div className="flex flex-col flex-1 min-h-0 rounded-xl border border-border bg-card/40 backdrop-blur-xl overflow-hidden shadow-sm">
              <CategoryPills
                categories={PORT_CATEGORIES}
                active={portCategory}
                counts={portCategoryCounts}
                onToggle={(id) =>
                  setPortCategory(portCategory === id ? null : id)
                }
              />
              <PortTable
                ports={filteredPorts}
                loading={loading}
                sortCol={portSortCol}
                sortDir={portSortDir}
                onSort={handlePortSort}
                getProcessIcon={getProcessIcon}
                onKill={(p) => {
                  setProcessToKill(p);
                  setKillDialogOpen(true);
                }}
                onForceKill={handleForceKillDirect}
              />
            </div>
          </TabsContent>

          <TabsContent
            value="terminals"
            className="flex-1 min-h-0 data-[state=inactive]:hidden data-[state=active]:flex flex-col"
          >
            <div className="flex flex-col flex-1 min-h-0 rounded-xl border border-border bg-card/40 backdrop-blur-xl overflow-hidden shadow-sm">
              <CategoryPills
                categories={TERM_CATEGORIES}
                active={termCategory}
                counts={termCategoryCounts}
                onToggle={(id) =>
                  setTermCategory(termCategory === id ? null : id)
                }
              />
              <TerminalTable
                terminals={filteredTerminals}
                loading={terminalsLoading}
                sortCol={termSortCol}
                sortDir={termSortDir}
                onSort={handleTermSort}
                engineStatus={engineStatus}
                onRefresh={fetchTerminals}
              />
            </div>
          </TabsContent>
        </Tabs>
      </div>

      <Dialog
        open={killDialogOpen}
        onOpenChange={(open) => {
          setKillDialogOpen(open);
          if (!open) setKillError(null);
        }}
      >
        <DialogContent className="sm:max-w-[425px] bg-background/80 backdrop-blur-2xl border-border">
          <DialogHeader>
            <DialogTitle asChild>
              <h2 className="flex items-center gap-2 text-destructive font-semibold">
                <ShieldAlert className="h-5 w-5" /> Kill Process
              </h2>
            </DialogTitle>
            <DialogDescription asChild>
              <p>
                Are you sure you want to terminate{" "}
                <strong>{processToKill?.name}</strong> (PID:{" "}
                {processToKill?.pid}) listening on port{" "}
                <strong>{processToKill?.port}</strong>?
              </p>
            </DialogDescription>
          </DialogHeader>
          {killError && (
            <p className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <ShieldAlert className="h-4 w-4 shrink-0" />
              {killError}
            </p>
          )}
          <DialogFooter className="flex gap-2 sm:justify-start">
            <Button variant="ghost" onClick={() => setKillDialogOpen(false)}>
              Cancel
            </Button>
            <div className="flex-1" />
            <Button
              variant="outline"
              className="border-destructive/50 text-destructive hover:bg-destructive/10"
              onClick={() => handleKill(false)}
              disabled={isKilling}
            >
              Graceful Kill
            </Button>
            <Button
              variant="destructive"
              onClick={() => handleKill(true)}
              disabled={isKilling}
            >
              Force Kill
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── Sort icon ────────────────────────────────────────────────────────────────

function SortIcon<T extends string>({
  col,
  sortCol,
  sortDir,
}: {
  col: T;
  sortCol: T;
  sortDir: SortDir;
}) {
  if (col !== sortCol)
    return <ArrowUpDown className="w-3 h-3 ml-1 opacity-20" />;
  return sortDir === "asc" ? (
    <ArrowUp className="w-3 h-3 ml-1" />
  ) : (
    <ArrowDown className="w-3 h-3 ml-1" />
  );
}

// ─── Port table (no outer wrapper — parent wraps in the card) ─────────────────

function PortTable({
  ports,
  loading,
  sortCol,
  sortDir,
  onSort,
  getProcessIcon,
  onKill,
  onForceKill,
}: {
  ports: PortProcess[];
  loading: boolean;
  sortCol: PortSortCol;
  sortDir: SortDir;
  onSort: (col: PortSortCol) => void;
  getProcessIcon: (name: string) => React.ReactNode;
  onKill: (p: PortProcess) => void;
  onForceKill: (p: PortProcess) => void;
}) {
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  if (loading && ports.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-3">
        <RefreshCw className="h-8 w-8 opacity-30 animate-spin" />
        <p className="text-sm">Discovering ports…</p>
      </div>
    );
  }

  if (ports.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-3">
        <Network className="h-10 w-10 opacity-20" />
        <p className="text-sm">No ports match the current filters.</p>
      </div>
    );
  }

  const col = (id: PortSortCol, label: string, span: string) => (
    <div
      className={`${span} flex items-center cursor-pointer hover:text-foreground transition-colors`}
      onClick={() => onSort(id)}
    >
      {label} <SortIcon col={id} sortCol={sortCol} sortDir={sortDir} />
    </div>
  );

  return (
    <>
      <div className="grid grid-cols-12 gap-4 border-b border-border bg-muted/30 px-4 py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider select-none shrink-0">
        {col("port", "Port", "col-span-2")}
        {col("name", "Process", "col-span-3")}
        {col("address", "Address", "col-span-3")}
        {col("pid", "PID", "col-span-1")}
        {col("protocol", "Protocol", "col-span-1")}
        <div className="col-span-2 text-right">Actions</div>
      </div>
      <ScrollArea className="flex-1">
        <div className="divide-y divide-border">
          {ports.map((port) => {
            const rowKey = `${port.pid}-${port.port}-${port.protocol}`;
            const isExpanded = expandedKey === rowKey;
            return (
              <div key={rowKey}>
                <div
                  className={`grid grid-cols-12 gap-4 items-center px-4 py-3 hover:bg-muted/40 transition-colors group cursor-pointer ${isExpanded ? "bg-muted/40" : ""}`}
                  onClick={() => setExpandedKey(isExpanded ? null : rowKey)}
                >
                  <div className="col-span-2">
                    <span className="font-mono text-foreground/90 font-semibold text-sm">
                      {port.port}
                    </span>
                  </div>
                  <div className="col-span-3 flex items-center gap-3 min-w-0">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted border border-border shadow-sm">
                      {getProcessIcon(port.name)}
                    </div>
                    <span className="font-medium text-foreground/90 truncate text-sm">
                      {port.name}
                    </span>
                  </div>
                  <div className="col-span-3 flex items-center">
                    <span className="text-sm font-mono text-muted-foreground bg-muted px-2 py-0.5 rounded border border-border truncate">
                      {port.address || "*"}
                    </span>
                  </div>
                  <div className="col-span-1 text-sm text-muted-foreground font-mono">
                    {port.pid > 0 ? port.pid : "—"}
                  </div>
                  <div className="col-span-1">
                    <Badge
                      variant="outline"
                      className="text-[10px] bg-muted border-border text-muted-foreground font-medium"
                    >
                      {port.protocol}
                    </Badge>
                  </div>
                  <div className="col-span-2 flex justify-end opacity-50 group-hover:opacity-100 transition-opacity text-xs text-muted-foreground font-medium">
                    {isExpanded ? "Close" : "Expand"}
                  </div>
                </div>

                {isExpanded && (
                  <div className="bg-muted/30 border-t border-border p-4 mx-4 mb-2 rounded-b-md shadow-inner">
                    <div className="flex justify-between items-start gap-4">
                      <pre className="text-xs text-muted-foreground bg-muted p-3 rounded-md overflow-x-auto w-full font-mono border border-border">
                        {JSON.stringify(port, null, 2)}
                      </pre>
                      <div className="flex flex-col gap-2 min-w-[140px] shrink-0">
                        <Button
                          variant="secondary"
                          size="sm"
                          className="w-full justify-start text-xs h-8"
                          onClick={(e) => {
                            e.stopPropagation();
                            navigator.clipboard.writeText(
                              JSON.stringify(port, null, 2),
                            );
                          }}
                        >
                          <Copy className="h-3 w-3 mr-2" /> Copy JSON
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          className="w-full justify-start text-xs h-8 border-destructive/30 text-destructive hover:bg-destructive/10"
                          disabled={port.pid === 0}
                          onClick={(e) => {
                            e.stopPropagation();
                            onKill(port);
                          }}
                        >
                          <ShieldAlert className="h-3 w-3 mr-2" /> Grace Kill
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          className="w-full justify-start text-xs h-8 hover:bg-red-600"
                          disabled={port.pid === 0}
                          onClick={(e) => {
                            e.stopPropagation();
                            onForceKill(port);
                          }}
                        >
                          Force Kill
                        </Button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </>
  );
}

// ─── Terminal table ───────────────────────────────────────────────────────────

function TerminalTable({
  terminals,
  loading,
  sortCol,
  sortDir,
  onSort,
  onRefresh,
}: {
  terminals: TerminalProcess[];
  loading: boolean;
  sortCol: TermSortCol;
  sortDir: SortDir;
  onSort: (col: TermSortCol) => void;
  engineStatus: EngineStatus;
  onRefresh: () => void;
}) {
  const [expandedPid, setExpandedPid] = useState<number | null>(null);
  const [tailData, setTailData] = useState<Record<number, TailResult | null>>(
    {},
  );
  const [tailLoading, setTailLoading] = useState<Record<number, boolean>>({});
  const tailScrollRef = useRef<HTMLDivElement>(null);

  const fetchTail = async (t: TerminalProcess) => {
    setTailLoading((prev) => ({ ...prev, [t.pid]: true }));
    try {
      const result = await engine.invokeToolWs("TailTerminal", {
        pid: t.pid,
        tty: t.tty || undefined,
        lines: 80,
      });
      if (result.type === "success") {
        const openFiles = result.metadata?.open_files as
          | TailResult["open_files"]
          | undefined;
        const connections = result.metadata?.connections as
          | TailResult["connections"]
          | undefined;
        const note = result.metadata?.note as string | undefined;
        setTailData((prev) => ({
          ...prev,
          [t.pid]: {
            output: result.output ?? "",
            source: (result.metadata?.source as string) ?? "unknown",
            ...(openFiles !== undefined ? { open_files: openFiles } : {}),
            ...(connections !== undefined ? { connections } : {}),
            ...(note !== undefined ? { note } : {}),
          },
        }));
      } else {
        setTailData((prev) => ({
          ...prev,
          [t.pid]: {
            output: result.output ?? "Error fetching output.",
            source: "error",
          },
        }));
      }
    } catch {
      setTailData((prev) => ({
        ...prev,
        [t.pid]: {
          output: "Failed to fetch terminal output.",
          source: "error",
        },
      }));
    } finally {
      setTailLoading((prev) => ({ ...prev, [t.pid]: false }));
    }
  };

  const handleExpand = (t: TerminalProcess) => {
    if (expandedPid === t.pid) {
      setExpandedPid(null);
    } else {
      setExpandedPid(t.pid);
      if (!tailData[t.pid]) fetchTail(t);
    }
  };

  if (loading && terminals.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-3">
        <RefreshCw className="h-8 w-8 opacity-30 animate-spin" />
        <p className="text-sm">Discovering terminal sessions…</p>
      </div>
    );
  }

  if (!loading && terminals.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-3">
        <MonitorDot className="h-10 w-10 opacity-20" />
        <p className="text-sm">
          No terminal processes match the current filters.
        </p>
        <Button variant="outline" size="sm" onClick={onRefresh}>
          <RefreshCw className="h-4 w-4 mr-2" /> Refresh
        </Button>
      </div>
    );
  }

  const col = (
    id: TermSortCol,
    label: string,
    span: string,
    extra?: string,
  ) => (
    <div
      className={`${span} flex items-center cursor-pointer hover:text-foreground transition-colors ${extra ?? ""}`}
      onClick={() => onSort(id)}
    >
      {label} <SortIcon col={id} sortCol={sortCol} sortDir={sortDir} />
    </div>
  );

  return (
    <>
      <div className="grid grid-cols-12 gap-3 border-b border-border bg-muted/30 px-4 py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider select-none shrink-0">
        {col("name", "Process", "col-span-3")}
        {col("tty", "TTY", "col-span-2")}
        {col("cwd", "Working Directory", "col-span-4")}
        <div
          className="col-span-1 flex items-center gap-1 cursor-pointer hover:text-foreground transition-colors"
          onClick={() => onSort("elapsed_s")}
        >
          <Clock className="h-3 w-3" /> Age{" "}
          <SortIcon col="elapsed_s" sortCol={sortCol} sortDir={sortDir} />
        </div>
        {col("pid", "PID", "col-span-1")}
        <div className="col-span-1 text-right">Output</div>
      </div>

      <ScrollArea className="flex-1">
        <div className="divide-y divide-border">
          {terminals.map((t) => {
            const isExpanded = expandedPid === t.pid;
            const tail = tailData[t.pid];
            const isTailLoading = tailLoading[t.pid] ?? false;

            return (
              <div key={t.pid}>
                <div
                  className={`grid grid-cols-12 gap-3 items-center px-4 py-3 hover:bg-muted/40 transition-colors group cursor-pointer ${isExpanded ? "bg-muted/40" : ""}`}
                  onClick={() => handleExpand(t)}
                >
                  {/* Process */}
                  <div className="col-span-3 flex items-center gap-3 min-w-0">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted border border-border shadow-sm">
                      <TerminalSquare className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
                    </div>
                    <div className="flex flex-col min-w-0">
                      <span className="font-medium text-foreground/90 truncate text-sm">
                        {t.name}
                      </span>
                      <span className="text-[11px] text-muted-foreground truncate font-mono">
                        {t.cmdline.length > 40
                          ? t.cmdline.slice(0, 40) + "…"
                          : t.cmdline}
                      </span>
                    </div>
                  </div>

                  {/* TTY */}
                  <div className="col-span-2">
                    <span className="text-xs font-mono text-muted-foreground bg-muted px-2 py-0.5 rounded border border-border truncate block">
                      {t.tty || "—"}
                    </span>
                  </div>

                  {/* CWD */}
                  <div className="col-span-4 flex items-center gap-1.5 min-w-0">
                    <FolderOpen className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                    <span
                      className="text-xs font-mono text-muted-foreground truncate"
                      title={t.cwd}
                    >
                      {t.cwd ? t.cwd.replace(/^\/Users\/[^/]+/, "~") : "—"}
                    </span>
                  </div>

                  {/* Age */}
                  <div className="col-span-1 text-xs text-muted-foreground font-mono">
                    {formatElapsed(t.elapsed_s)}
                  </div>

                  {/* PID */}
                  <div className="col-span-1 text-xs text-muted-foreground font-mono">
                    {t.pid}
                  </div>

                  {/* Expand */}
                  <div className="col-span-1 flex justify-end opacity-50 group-hover:opacity-100 transition-opacity">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                  </div>
                </div>

                {isExpanded && (
                  <div className="border-t border-border bg-muted/20 mx-4 mb-2 rounded-b-md shadow-inner overflow-hidden">
                    <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-muted/30">
                      <span className="text-xs text-muted-foreground font-mono">
                        {tail?.source ? `source: ${tail.source}` : "fetching…"}
                      </span>
                      <div className="flex gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 text-xs px-2"
                          onClick={(e) => {
                            e.stopPropagation();
                            fetchTail(t);
                          }}
                          disabled={isTailLoading}
                        >
                          <RefreshCw
                            className={`h-3 w-3 mr-1 ${isTailLoading ? "animate-spin" : ""}`}
                          />
                          Refresh
                        </Button>
                        {tail?.output && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs px-2"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigator.clipboard.writeText(tail.output);
                            }}
                          >
                            <Copy className="h-3 w-3 mr-1" /> Copy
                          </Button>
                        )}
                      </div>
                    </div>

                    {isTailLoading && !tail && (
                      <div className="flex items-center justify-center py-8 gap-2 text-muted-foreground text-sm">
                        <RefreshCw className="h-4 w-4 animate-spin" /> Reading
                        terminal output…
                      </div>
                    )}

                    {tail && (
                      <div className="p-3 space-y-3">
                        {tail.note && (
                          <p className="text-xs text-amber-700 dark:text-amber-400/80 bg-amber-400/10 border border-amber-400/20 rounded px-3 py-2">
                            {tail.note}
                          </p>
                        )}

                        {tail.output && (
                          <div ref={tailScrollRef}>
                            <pre className="text-xs text-foreground/80 bg-muted border border-border rounded-md p-3 overflow-x-auto font-mono max-h-64 overflow-y-auto whitespace-pre-wrap">
                              {tail.output}
                            </pre>
                          </div>
                        )}

                        {tail.open_files && tail.open_files.length > 0 && (
                          <div>
                            <p className="text-[11px] text-muted-foreground uppercase tracking-wider mb-1">
                              Open Files ({tail.open_files.length})
                            </p>
                            <div className="grid grid-cols-1 gap-0.5 max-h-40 overflow-y-auto">
                              {tail.open_files.map((f) => (
                                <div
                                  key={`${f.fd}-${f.path}`}
                                  className="flex items-center gap-2 px-2 py-1 rounded text-xs font-mono bg-muted/30 hover:bg-muted/50"
                                >
                                  <span className="text-muted-foreground w-8 shrink-0">
                                    fd{f.fd}
                                  </span>
                                  <span className="text-foreground/70 truncate">
                                    {f.path}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {tail.connections && tail.connections.length > 0 && (
                          <div>
                            <p className="text-[11px] text-muted-foreground uppercase tracking-wider mb-1">
                              Network Connections ({tail.connections.length})
                            </p>
                            <div className="grid grid-cols-1 gap-0.5 max-h-32 overflow-y-auto">
                              {tail.connections.map((c, i) => (
                                <div
                                  key={i}
                                  className="flex items-center gap-3 px-2 py-1 rounded text-xs font-mono bg-muted/30"
                                >
                                  <Badge
                                    variant="outline"
                                    className="text-[10px] shrink-0"
                                  >
                                    {c.status}
                                  </Badge>
                                  <span className="text-muted-foreground">
                                    {c.laddr}
                                    {c.raddr ? ` → ${c.raddr}` : ""}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </>
  );
}
