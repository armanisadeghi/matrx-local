/**
 * The app shell's single engine-lifecycle surface.
 *
 * React owns reachability; Rust owns process recovery. Presenting either in
 * isolation created the contradictory startup UI where the app said both
 * "not running" and "restarting automatically". This component composes the
 * two truths and exposes manual recovery only after recovery is terminal.
 */

import { useState, useSyncExternalStore } from "react";
import { AlertTriangle, Download, Loader2, RotateCcw, Terminal } from "lucide-react";
import { Button } from "@ai-matrx/design-system";
import type { EngineStatus } from "@/hooks/use-engine";
import {
  engineSupervisor,
  resolveEngineLifecyclePresentation,
} from "@/lib/engine-supervisor";
import { checkForUpdates } from "@/lib/sidecar";

interface EngineLifecycleBannerProps {
  engineStatus: EngineStatus;
  onRestartEngine: () => Promise<void> | void;
  onOpenMonitor: () => void;
}

export function EngineLifecycleBanner({
  engineStatus,
  onRestartEngine,
  onOpenMonitor,
}: EngineLifecycleBannerProps) {
  const supervisor = useSyncExternalStore(
    engineSupervisor.subscribe,
    engineSupervisor.getSnapshot,
    engineSupervisor.getSnapshot,
  );
  const lifecycle = resolveEngineLifecyclePresentation(engineStatus, supervisor);
  const [restarting, setRestarting] = useState(false);
  const [reinstalling, setReinstalling] = useState(false);
  const [reinstallNote, setReinstallNote] = useState<string | null>(null);

  if (lifecycle.kind === "ready") return null;

  const loading = lifecycle.kind === "loading";

  const handleRestart = async () => {
    setRestarting(true);
    try {
      await onRestartEngine();
    } finally {
      setRestarting(false);
    }
  };

  const handleReinstall = async () => {
    setReinstalling(true);
    setReinstallNote(null);
    try {
      const result = await checkForUpdates(true);
      setReinstallNote(
        result.status === "up_to_date"
          ? "You are already on the newest build. Open Details for the engine log."
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
      role={loading ? "status" : "alert"}
      aria-live={loading ? "polite" : "assertive"}
      className={`flex shrink-0 flex-wrap items-center gap-3 border-b px-4 py-2 text-sm ${
        loading
          ? "border-primary/20 bg-primary/5"
          : "border-destructive/30 bg-destructive/10"
      }`}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
      ) : (
        <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
      )}
      <div className="min-w-0 flex-1">
        <span className={`font-medium ${loading ? "text-foreground" : "text-destructive"}`}>
          {loading ? "Loading and syncing your data" : "The local engine needs attention"}
        </span>{" "}
        <span className="text-muted-foreground">{lifecycle.detail}</span>
        {reinstallNote ? (
          <div className="mt-0.5 text-muted-foreground">{reinstallNote}</div>
        ) : null}
      </div>
      {!loading ? (
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
          {lifecycle.supervisorFailed ? (
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
          ) : null}
        </>
      ) : null}
      <Button size="sm" variant="outline" onClick={onOpenMonitor}>
        <Terminal className="mr-1.5 h-3.5 w-3.5" />
        Details
      </Button>
    </div>
  );
}
