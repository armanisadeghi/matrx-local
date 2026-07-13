/**
 * EngineDownBanner — the app-wide "stop lying to the user" banner.
 *
 * When the Python engine process is gone, every data fetch fails and pages
 * used to sit on infinite loading spinners with no explanation (the
 * 2026-07-11/12 "image system frozen until I kill the app" reports). The
 * health checker in use-engine.ts already detects the outage within ~10s and
 * flips status to "disconnected"/"error" — this banner is the missing
 * surface that TELLS the user and hands them the fix, on every page, without
 * blocking whatever is still readable underneath.
 *
 * Rendered by AppLayout directly above <main>. Shows only for
 * "disconnected" | "error" — the boot states ("discovering"/"starting") are
 * handled by StartupScreen and must not flash this banner.
 */

import { useState } from "react";
import { AlertTriangle, Loader2, RotateCcw, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { EngineStatus } from "@/hooks/use-engine";

interface EngineDownBannerProps {
  engineStatus: EngineStatus;
  /** Full restart: stop → start → wait → discover → init (use-engine restartEngine). */
  onRestartEngine: () => Promise<void> | void;
  /** Opens the EngineMonitor modal (diagnostics, ports, live logs). */
  onOpenMonitor: () => void;
}

export function EngineDownBanner({
  engineStatus,
  onRestartEngine,
  onOpenMonitor,
}: EngineDownBannerProps) {
  const [restarting, setRestarting] = useState(false);

  if (engineStatus !== "disconnected" && engineStatus !== "error") {
    return null;
  }

  const handleRestart = async () => {
    setRestarting(true);
    try {
      await onRestartEngine();
    } finally {
      setRestarting(false);
    }
  };

  return (
    <div
      role="alert"
      className="flex shrink-0 items-center gap-3 border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-sm"
    >
      <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <span className="font-medium text-destructive">
          The local engine is not running.
        </span>{" "}
        <span className="text-muted-foreground">
          Models, media, files and tools can&apos;t load until it&apos;s back —
          anything still spinning below is waiting on it.
        </span>
      </div>
      <Button
        size="sm"
        variant="destructive"
        onClick={() => void handleRestart()}
        disabled={restarting}
      >
        {restarting ? (
          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
        ) : (
          <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
        )}
        {restarting ? "Restarting…" : "Restart engine"}
      </Button>
      <Button size="sm" variant="outline" onClick={onOpenMonitor}>
        <Terminal className="mr-1.5 h-3.5 w-3.5" />
        Details
      </Button>
    </div>
  );
}
