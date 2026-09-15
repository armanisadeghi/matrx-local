/**
 * EngineSupervisorBanner — the loud half of the engine supervisor.
 *
 * Rust restarts a dead engine automatically, with backoff and a bound
 * (`src-tauri/src/lib.rs`, `supervise_terminated_engine`). This banner is what
 * makes that visible: while it retries it says which attempt is running and
 * why the engine died, and once the bound is spent it stops pretending and
 * hands over real controls. It never shows a spinner with no explanation, and
 * it never shows a dead-looking button.
 *
 * Rendered by AppLayout next to EngineDownBanner, which owns the plain
 * "engine is not running" case. This one owns "the engine is failing to
 * START", which is a different sentence and a different remedy.
 */

import { useState, useSyncExternalStore } from "react";
import { AlertTriangle, Download, Loader2, RotateCcw, Terminal } from "lucide-react";
import { Button } from "@ai-matrx/design-system";
import {
  engineSupervisor,
  engineSupervisorHeadline,
  type EngineSupervisorStatus,
} from "@/lib/engine-supervisor";
import { recovery } from "@/lib/recovery";
import { checkForUpdates } from "@/lib/sidecar";

interface EngineSupervisorBannerProps {
  /** Opens the EngineMonitor modal (diagnostics, ports, live engine log). */
  onOpenMonitor: () => void;
}

export function EngineSupervisorBanner({ onOpenMonitor }: EngineSupervisorBannerProps) {
  const status = useSyncExternalStore(engineSupervisor.subscribe, engineSupervisor.getSnapshot);
  return <EngineSupervisorStrip status={status} onOpenMonitor={onOpenMonitor} />;
}

/** The pure surface, so its sentences are testable without a Tauri host. */
export function EngineSupervisorStrip({
  status,
  onOpenMonitor,
}: {
  status: EngineSupervisorStatus;
  onOpenMonitor: () => void;
}) {
  const [restarting, setRestarting] = useState(false);
  const [reinstalling, setReinstalling] = useState(false);
  const [reinstallNote, setReinstallNote] = useState<string | null>(null);

  if (status.phase === "ok") return null;
  const retrying = status.phase === "restarting";

  const handleRestart = async () => {
    setRestarting(true);
    try {
      await recovery.restartEngine();
    } finally {
      setRestarting(false);
    }
  };

  // A corrupt bundled engine file is repaired by replacing the app, which is
  // what the updater does. If there is nothing newer, say so — an update check
  // that quietly does nothing would be exactly the silent failure this banner
  // exists to prevent.
  const handleReinstall = async () => {
    setReinstalling(true);
    setReinstallNote(null);
    try {
      const result = await checkForUpdates(true);
      setReinstallNote(
        result.status === "up_to_date"
          ? "You are already on the newest build, so there is nothing newer to install. Open Details for the engine's log — the failing line is at the end."
          : `Installing ${result.version ?? "the newest build"} — AI Matrx will restart when it finishes.`,
      );
    } catch (error) {
      setReinstallNote(
        `The reinstall could not start: ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setReinstalling(false);
    }
  };

  return (
    <div
      role="alert"
      className={`flex shrink-0 flex-wrap items-center gap-3 border-b px-4 py-2 text-sm ${
        retrying
          ? "border-amber-500/30 bg-amber-500/10"
          : "border-destructive/30 bg-destructive/10"
      }`}
    >
      {retrying ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-amber-600" />
      ) : (
        <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
      )}
      <div className="min-w-0 flex-1">
        <span className={`font-medium ${retrying ? "text-amber-700 dark:text-amber-400" : "text-destructive"}`}>
          {engineSupervisorHeadline(status)}
        </span>
        {status.remedy ? (
          <span className="text-muted-foreground"> {status.remedy}</span>
        ) : null}
        {reinstallNote ? (
          <div className="mt-0.5 text-muted-foreground">{reinstallNote}</div>
        ) : null}
      </div>
      {retrying ? null : (
        <>
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
          <Button
            size="sm"
            variant="outline"
            onClick={() => void handleReinstall()}
            disabled={reinstalling}
          >
            {reinstalling ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="mr-1.5 h-3.5 w-3.5" />
            )}
            {reinstalling ? "Checking…" : "Reinstall AI Matrx"}
          </Button>
        </>
      )}
      <Button size="sm" variant="outline" onClick={onOpenMonitor}>
        <Terminal className="mr-1.5 h-3.5 w-3.5" />
        Details
      </Button>
    </div>
  );
}
