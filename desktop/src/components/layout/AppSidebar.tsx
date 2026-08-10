import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Globe,
  Wrench,
  Settings,
  SlidersHorizontal,
  FileText,
  MessageSquare,
  Cloud,
  Zap,
  Network,
  Radio,
  Cpu,
  PanelLeftClose,
  PanelLeft,
  LogOut,
  Sparkles,
  MonitorSmartphone,
  Mic,
  BrainCircuit,
  Wand2,
  BookOpen,
  AudioLines,
  RefreshCw,
  Plug,
  FolderClosed,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import type { EngineStatus } from "@/hooks/use-engine";
import type { User as SupabaseUser } from "@supabase/supabase-js";
import { saveSetting, broadcastSettingsChanged } from "@/lib/settings";
import { triggerPageRefresh } from "@/lib/page-refresh";
import { isTauri, invokeTauri } from "@/lib/sidecar";
import { ROUTE_TO_PANEL } from "@/panels/manifest";
import { windowRole } from "@/lib/window-role";

interface AppSidebarProps {
  engineStatus: EngineStatus;
  user: SupabaseUser | null;
  onSignOut: () => void;
}

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/chat", icon: MessageSquare, label: "Chat" },
  { to: "/cloud-chat", icon: Cloud, label: "Cloud Chat" },
  { to: "/notes", icon: FileText, label: "Notes" },
  { to: "/files", icon: FolderClosed, label: "Files" },
  { to: "/scraping", icon: Globe, label: "Scraping" },
  { to: "/tools", icon: Wrench, label: "Tools" },
  { to: "/activity", icon: Radio, label: "Activity" },
  { to: "/ports", icon: Network, label: "Ports" },
  { to: "/devices", icon: Cpu, label: "Devices" },
  { to: "/voice", icon: Mic, label: "Speech to Text" },
  { to: "/tts", icon: AudioLines, label: "Text to Speech" },
  { to: "/local-models", icon: BrainCircuit, label: "Confidential Chat" },
  { to: "/media-generation", icon: Wand2, label: "Media Generation" },
  { to: "/system-prompts", icon: BookOpen, label: "Prompts" },
  { to: "/aimatrx", icon: Sparkles, label: "AiMatrx" },
  { to: "/browser", icon: MonitorSmartphone, label: "Browser" },
  { to: "/bridge-test", icon: Plug, label: "Bridge Test" },
  { to: "/configurations", icon: SlidersHorizontal, label: "Configurations" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

const statusColors: Record<EngineStatus, string> = {
  discovering: "bg-amber-500",
  starting: "bg-amber-500 animate-pulse-subtle",
  connected: "bg-emerald-500",
  disconnected: "bg-zinc-500",
  error: "bg-red-500",
};

const statusLabels: Record<EngineStatus, string> = {
  discovering: "Discovering engine...",
  starting: "Starting engine...",
  connected: "Engine connected",
  disconnected: "Engine offline",
  error: "Engine error",
};

// Multi-window: the main window owns the canonical (settings-synced) collapse
// state; peer windows keep a per-label local key so collapsing a peer's
// sidebar never yanks the main window's.
const IS_MAIN_WINDOW = windowRole.label === "main";
const COLLAPSED_KEY = IS_MAIN_WINDOW
  ? "sidebar-collapsed"
  : `sidebar-collapsed:${windowRole.label}`;

export function AppSidebar({ engineStatus, user, onSignOut }: AppSidebarProps) {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(() => {
    // Read from unified settings (also falls back to old localStorage key).
    // Peer windows check their own key first, then inherit the shared state.
    if (!IS_MAIN_WINDOW) {
      const own = localStorage.getItem(COLLAPSED_KEY);
      if (own !== null) return own === "true";
    }
    try {
      const raw = localStorage.getItem("matrx-settings");
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed.sidebarCollapsed !== undefined)
          return Boolean(parsed.sidebarCollapsed);
      }
    } catch {
      /* ignore */
    }
    const saved = localStorage.getItem("sidebar-collapsed");
    return saved === "true";
  });
  const [profileOpen, setProfileOpen] = useState(false);

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(COLLAPSED_KEY, String(next));
    // Only the main window writes through the canonical settings API (engine +
    // cloud sync) — a peer's layout preference is window-local.
    if (IS_MAIN_WINDOW) {
      saveSetting("sidebarCollapsed", next).then(() =>
        broadcastSettingsChanged(),
      );
    }
  };

  const isActive = (to: string) =>
    to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);

  const displayName =
    user?.user_metadata?.full_name ??
    user?.user_metadata?.name ??
    user?.user_metadata?.user_name ??
    user?.email?.split("@")[0] ??
    "User";

  const avatarUrl = user?.user_metadata?.avatar_url;
  const initials = (displayName[0] ?? "U").toUpperCase();

  return (
    <aside
      className={cn(
        "no-select flex h-full flex-col border-r bg-sidebar transition-[width] duration-200 ease-in-out backdrop-blur-xl",
        collapsed
          ? "w-[var(--sidebar-width-collapsed)]"
          : "w-[var(--sidebar-width)]",
      )}
    >
      {/* Top: Logo + Toggle */}
      <div
        className={cn(
          "flex items-center border-b h-14 shrink-0",
          collapsed ? "justify-center px-0" : "px-3 gap-2",
        )}
      >
        {!collapsed && (
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 flex-shrink-0">
            <Zap className="h-4 w-4 text-primary" />
          </div>
        )}
        {!collapsed && (
          <span className="text-sm font-semibold text-sidebar-foreground flex-1 whitespace-nowrap overflow-hidden">
            Matrx Local
          </span>
        )}
        <button
          onClick={toggleCollapsed}
          className="flex h-7 w-7 items-center justify-center rounded-md text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors flex-shrink-0"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <PanelLeft className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex flex-1 flex-col gap-1 p-2 overflow-y-auto overflow-x-hidden">
        {navItems.map(({ to, icon: Icon, label }) => {
          const active = isActive(to);
          return (
            <Tooltip key={to} delayDuration={collapsed ? 0 : 700}>
              <TooltipTrigger asChild>
                <div className="group relative flex items-center">
                  <Link
                    to={to}
                    onContextMenu={(e) => {
                      // Right-click a panel-capable page → pop it into its own
                      // window (mirrors the toolbar OpenInWindowButton).
                      const page = ROUTE_TO_PANEL[to];
                      if (!page || !isTauri()) return;
                      e.preventDefault();
                      void invokeTauri<string>("open_panel_window", { page }).catch(
                        (err) => console.error("[sidebar] open_panel_window failed:", err),
                      );
                    }}
                    className={cn(
                      "flex flex-1 items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors whitespace-nowrap overflow-hidden",
                      collapsed && "justify-center px-0",
                      active
                        ? "bg-sidebar-accent text-sidebar-foreground font-medium"
                        : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
                    )}
                  >
                    <Icon className="h-4 w-4 flex-shrink-0" />
                    {!collapsed && <span className="flex-1">{label}</span>}
                  </Link>
                  {/* Per-tab refresh button — only shown on active tab when sidebar is expanded */}
                  {active && !collapsed && (
                    <button
                      onClick={async (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        await triggerPageRefresh(to);
                      }}
                      title={`Refresh ${label}`}
                      className="absolute right-2 flex h-5 w-5 items-center justify-center rounded opacity-0 group-hover:opacity-100 text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent/70 transition-all"
                    >
                      <RefreshCw className="h-3 w-3" />
                    </button>
                  )}
                </div>
              </TooltipTrigger>
              {collapsed && (
                <TooltipContent side="right">{label}</TooltipContent>
              )}
            </Tooltip>
          );
        })}
      </nav>

      {/* User profile */}
      {user && (
        <div className={cn("border-t p-2", collapsed && "flex justify-center")}>
          <Popover open={profileOpen} onOpenChange={setProfileOpen}>
            <Tooltip delayDuration={collapsed ? 0 : 700}>
              <TooltipTrigger asChild>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className={cn(
                      "flex w-full items-center gap-2 overflow-hidden whitespace-nowrap rounded-lg px-2 py-2 transition-colors hover:bg-sidebar-accent/50",
                      collapsed && "justify-center px-0",
                    )}
                  >
                    <Avatar className="h-7 w-7 flex-shrink-0">
                      {avatarUrl && (
                        <AvatarImage src={avatarUrl} alt={displayName} />
                      )}
                      <AvatarFallback className="text-xs">
                        {initials}
                      </AvatarFallback>
                    </Avatar>
                    {!collapsed && (
                      <span className="flex-1 truncate text-xs text-sidebar-foreground/80">
                        {displayName}
                      </span>
                    )}
                  </button>
                </PopoverTrigger>
              </TooltipTrigger>
              {collapsed && (
                <TooltipContent side="right">{displayName}</TooltipContent>
              )}
            </Tooltip>
            <PopoverContent side="top" align="start" className="w-56">
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Avatar className="h-8 w-8">
                    {avatarUrl && (
                      <AvatarImage src={avatarUrl} alt={displayName} />
                    )}
                    <AvatarFallback className="text-xs">
                      {initials}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">
                      {displayName}
                    </p>
                    <p className="text-xs text-muted-foreground truncate">
                      {user.email}
                    </p>
                  </div>
                </div>
                <div className="border-t pt-2">
                  <button
                    onClick={() => {
                      setProfileOpen(false);
                      onSignOut();
                    }}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                  >
                    <LogOut className="h-3.5 w-3.5" />
                    Sign out
                  </button>
                </div>
              </div>
            </PopoverContent>
          </Popover>
        </div>
      )}

      {/* Engine Status (bottom) */}
      <div className="border-t p-2 shrink-0">
        <Tooltip delayDuration={0}>
          <TooltipTrigger asChild>
            <div
              className={cn(
                "flex items-center gap-2 rounded-lg px-3 py-2 whitespace-nowrap overflow-hidden",
                collapsed && "justify-center px-0",
              )}
            >
              <div
                className={cn(
                  "h-2 w-2 rounded-full flex-shrink-0",
                  statusColors[engineStatus],
                )}
              />
              {!collapsed && (
                <span className="text-xs text-sidebar-foreground/60">
                  {statusLabels[engineStatus]}
                </span>
              )}
            </div>
          </TooltipTrigger>
          {collapsed && (
            <TooltipContent side="right">
              {statusLabels[engineStatus]}
            </TooltipContent>
          )}
        </Tooltip>
      </div>
    </aside>
  );
}
