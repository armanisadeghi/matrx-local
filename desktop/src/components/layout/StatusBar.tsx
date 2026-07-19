import { useState } from "react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { LifeBuoy, RefreshCw } from "lucide-react";
import type { EngineStatus } from "@/hooks/use-engine";
import { TerminalToggleButton } from "@/components/DevTerminalPanel";
import { AppVersion } from "@/lib/app-version";

interface StatusBarProps {
  engineStatus: EngineStatus;
  engineUrl: string | null;
  engineVersion?: string;
  onRefresh?: () => void;
  onOpenMonitor?: () => void;
  onOpenRecovery?: () => void;
}

const statusVariants: Record<
  EngineStatus,
  "success" | "warning" | "destructive" | "secondary"
> = {
  discovering: "warning",
  starting: "warning",
  connected: "success",
  disconnected: "secondary",
  error: "destructive",
};

const statusText: Record<EngineStatus, string> = {
  discovering: "Discovering…",
  starting: "Starting…",
  connected: "Connected",
  disconnected: "Offline",
  error: "Error",
};

export function StatusBar({ engineStatus, engineUrl, engineVersion, onRefresh, onOpenMonitor, onOpenRecovery }: StatusBarProps) {
  const [spinning, setSpinning] = useState(false);

  const handleRefresh = async () => {
    if (!onRefresh || spinning) return;
    setSpinning(true);
    onRefresh();
    setTimeout(() => setSpinning(false), 1500);
  };

  const notConnected = engineStatus !== "connected";

  return (
    <footer className="no-select glass flex h-8 items-center justify-between border-t px-4">
      {/* Left side — clickable status indicator → opens Engine Monitor */}
      <button
        onClick={onOpenMonitor}
        className="flex items-center gap-2 hover:opacity-80 transition-opacity"
        title="Open Engine Monitor"
      >
        <div
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            engineStatus === "connected" && "bg-emerald-500",
            engineStatus === "disconnected" && "bg-zinc-500",
            engineStatus === "error" && "bg-red-500",
            (engineStatus === "discovering" || engineStatus === "starting") &&
              "bg-amber-500 animate-pulse-subtle"
          )}
        />
        <Badge variant={statusVariants[engineStatus]} className="h-5 text-[10px] px-1.5">
          {statusText[engineStatus]}
        </Badge>
        {engineUrl && (
          <span className="text-[10px] text-muted-foreground font-mono">
            {engineUrl.replace("http://", "")}
          </span>
        )}
      </button>

      {/* Right side — terminal toggle, version, reconnect */}
      <div className="flex items-center gap-2">
        <AppVersion className="text-[10px] text-muted-foreground" />
        <button onClick={onOpenRecovery} title="Open Recovery Center" className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"><LifeBuoy className="h-3 w-3" /><span>Recovery</span></button>
        <TerminalToggleButton />
        {engineVersion && (
          <span className="text-[10px] text-muted-foreground" title={`Local engine version ${engineVersion}`}>
            Engine v{engineVersion}
          </span>
        )}
        {notConnected && onRefresh && (
          <button
            onClick={handleRefresh}
            title="Reconnect engine"
            className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className={cn("h-3 w-3", spinning && "animate-spin")} />
            <span>Reconnect</span>
          </button>
        )}
      </div>
    </footer>
  );
}
