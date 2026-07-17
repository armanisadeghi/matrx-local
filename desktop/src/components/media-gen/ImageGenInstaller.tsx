/**
 * ImageGenInstaller — consumer one-click installer for the shared AI package
 * set (torch / diffusers / transformers / accelerate).
 *
 * Extracted from LocalModels.tsx.  Install state lives in a module-level
 * singleton (lib/image-gen/install-state.ts) so it SURVIVES tab switches,
 * component unmounts, and re-renders.  On mount we poll /install/status to
 * restore any in-progress or completed install.
 *
 * The SAME package set powers image AND video generation (the engine keys
 * video availability off the shared `image-gen-packages` install), so the
 * Video section renders this component too — with adjusted copy via props.
 */

import { useState, useEffect, useRef } from "react";
import {
  CheckCircle2,
  AlertCircle,
  PackagePlus,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  engine,
  startImageGenInstall,
  streamImageGenInstall,
} from "@/lib/api";
import {
  getSnapshot as igGetSnapshot,
  subscribe as igSubscribe,
  applyEvent as igApplyEvent,
  restoreFromPoll as igRestoreFromPoll,
  markStarted as igMarkStarted,
  reset as igReset,
  setSseCleanup as igSetSseCleanup,
  stopSse as igStopSse,
} from "@/lib/image-gen/install-state";
import type { InstallSnapshot } from "@/lib/image-gen/install-state";

/** Minimal model shape for the pre-install preview grid. */
export interface InstallerPreviewModel {
  model_id: string;
  name: string;
  provider: string;
  description: string;
  vram_gb: number;
  ram_gb: number;
  requires_hf_token: boolean;
}

export function ImageGenInstaller({
  models,
  onInstallComplete,
  headline,
  intro,
  /** When true this run is an in-place upgrade of already-installed packages. */
  upgrade = false,
}: {
  models: InstallerPreviewModel[];
  onInstallComplete: () => void;
  headline?: string;
  intro?: string;
  upgrade?: boolean;
}) {
  // Subscribe to the module singleton — never loses state on re-render
  const [snap, setSnap] = useState<InstallSnapshot>(igGetSnapshot);
  const logEndRef = useRef<HTMLDivElement | null>(null);
  const base = engine.engineUrl;

  // Subscribe to singleton updates
  useEffect(() => {
    const unsub = igSubscribe(setSnap);
    return unsub;
  }, []);

  // On mount: poll /install/status to restore state from a previous session
  // or an in-progress install that survived a tab switch.
  useEffect(() => {
    if (!base) return;
    const current = igGetSnapshot();
    if (
      current.phase === "running" ||
      current.phase === "complete" ||
      current.phase === "error"
    ) {
      // Already have live state — reconnect SSE if still running
      if (current.phase === "running") void reconnectSse(base);
      return;
    }
    void (async () => {
      try {
        const { getImageGenInstallStatus } = await import("@/lib/api");
        const resp = await getImageGenInstallStatus(base);
        igRestoreFromPoll(resp);
        if (resp.status === "complete") {
          setTimeout(onInstallComplete, 300);
        } else if (resp.status === "running") {
          void reconnectSse(base);
        }
      } catch {
        // engine not up yet — stay idle
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base]);

  // Auto-scroll log to bottom on new lines
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "instant" });
  }, [snap.logLines.length]);

  // Notify parent when install completes
  useEffect(() => {
    if (snap.phase === "complete") {
      setTimeout(onInstallComplete, 800);
    }
  }, [snap.phase, onInstallComplete]);

  const reconnectSse = async (baseUrl: string) => {
    const headers = await engine.getEngineAuthHeaders();
    const auth = (headers as Record<string, string>)["Authorization"];
    const token = auth ? auth.replace("Bearer ", "") : null;
    const cleanup = streamImageGenInstall(
      baseUrl,
      async () => token,
      igApplyEvent,
    );
    igSetSseCleanup(cleanup);
  };

  const handleInstall = async () => {
    if (!base) return;
    igMarkStarted();
    try {
      const initial = await startImageGenInstall(base);
      igApplyEvent(initial);
      if (initial.status === "complete") return;
    } catch (e) {
      igApplyEvent({
        status: "error",
        stage: "error",
        percent: 0,
        message: "",
        error: e instanceof Error ? e.message : "Failed to start installation",
      });
      return;
    }
    await reconnectSse(base);
  };

  // Stop SSE when component is permanently gone (user navigated away entirely)
  // but NOT on a simple tab switch — the singleton keeps running.
  // We only stop it if the install is already finished.
  useEffect(() => {
    return () => {
      const current = igGetSnapshot();
      if (current.phase === "complete" || current.phase === "error") {
        igStopSse();
      }
    };
  }, []);

  const { phase, stageMessage, percent, logLines, error } = snap;
  const isRunning = phase === "running";
  const isDone = phase === "complete";
  const isError = phase === "error";
  const hasStarted = phase !== "idle";

  const defaultHeadline = upgrade
    ? "Update AI packages"
    : "Set up Image Generation";
  const defaultIntro = upgrade
    ? "A required compatibility update is running automatically. Your downloaded models and LoRAs are kept; generation resumes as soon as verification finishes."
    : "AI Matrx can generate images directly on your computer — no cloud subscription needed. Click Install now and we'll download everything automatically. This is a one-time setup (~500 MB – 1 GB).";

  return (
    <div className="space-y-6 pb-8">
      {/* ── Install card ───────────────────────────────────────────────── */}
      <div
        className={`rounded-xl border px-5 py-5 space-y-4 transition-colors ${
          isDone
            ? "border-green-500/30 bg-green-500/5"
            : isError
              ? "border-destructive/30 bg-destructive/5"
              : "border-violet-500/30 bg-violet-500/5"
        }`}
      >
        {/* Header */}
        <div className="flex items-start gap-3">
          <div
            className={`mt-0.5 shrink-0 rounded-lg p-2 ${
              isDone
                ? "bg-green-500/15"
                : isError
                  ? "bg-destructive/15"
                  : "bg-violet-500/15"
            }`}
          >
            {isDone ? (
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            ) : isError ? (
              <AlertCircle className="h-4 w-4 text-destructive" />
            ) : (
              <PackagePlus className="h-4 w-4 text-violet-500" />
            )}
          </div>
          <div className="space-y-1 flex-1">
            <p className="font-semibold text-sm">
              {isDone
                ? "AI Packages Ready!"
                : isError
                  ? "Installation Failed"
                  : isRunning
                    ? upgrade
                      ? "Updating AI packages…"
                      : "Installing AI packages…"
                    : (headline ?? defaultHeadline)}
            </p>
            {!hasStarted && (
              <p className="text-xs text-muted-foreground leading-relaxed max-w-lg">
                {intro ?? defaultIntro}
              </p>
            )}
            {isDone && (
              <p className="text-xs text-muted-foreground">
                All packages installed successfully.
              </p>
            )}
          </div>
        </div>

        {/* Progress bar */}
        {(isRunning || isDone) && (
          <div className="space-y-1.5">
            <div className="flex justify-between items-center text-xs text-muted-foreground">
              <span className="truncate max-w-[80%]">
                {stageMessage || "Downloading…"}
              </span>
              <span className="shrink-0 ml-2 tabular-nums font-mono">
                {Math.round(percent)}%
              </span>
            </div>
            <div className="h-2 rounded-full bg-muted/60 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${isDone ? "bg-green-500" : "bg-violet-500"}`}
                style={{ width: `${isDone ? 100 : percent}%` }}
              />
            </div>
          </div>
        )}

        {/* Live pip log — always shown once started, never disappears */}
        {hasStarted && (
          <div className="rounded-lg border bg-black/50 overflow-hidden">
            <div className="px-2.5 py-1.5 border-b flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                {isRunning ? (
                  <div className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
                ) : isDone ? (
                  <div className="h-1.5 w-1.5 rounded-full bg-green-500" />
                ) : (
                  <div className="h-1.5 w-1.5 rounded-full bg-destructive" />
                )}
                <span className="text-[10px] text-muted-foreground font-mono uppercase tracking-wide">
                  {isRunning
                    ? "Live install log"
                    : isDone
                      ? "Install log — complete"
                      : "Install log — failed"}
                </span>
              </div>
              <span className="text-[10px] text-muted-foreground font-mono">
                {logLines.length} lines
              </span>
            </div>
            <div className="h-52 overflow-y-auto px-2.5 py-2 space-y-0.5 font-mono text-[11px] leading-snug">
              {logLines.length === 0 && (
                <div className="text-muted-foreground/50 italic">
                  Waiting for output…
                </div>
              )}
              {logLines.map((line, i) => (
                <div
                  key={i}
                  className={`break-all ${
                    line.toLowerCase().includes("error")
                      ? "text-red-400"
                      : line.toLowerCase().includes("warning")
                        ? "text-amber-400/80"
                        : "text-green-400/80"
                  }`}
                >
                  {line}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </div>
        )}

        {/* Error summary */}
        {isError && error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5">
            <p className="text-xs font-semibold text-destructive mb-1">
              What went wrong:
            </p>
            <p className="text-xs text-muted-foreground leading-relaxed font-mono break-all">
              {error}
            </p>
          </div>
        )}

        {/* Actions */}
        <div className="flex flex-wrap gap-2 items-center">
          {!isRunning && !isDone && (
            <Button
              size="sm"
              className={
                isError
                  ? "bg-destructive/80 hover:bg-destructive text-white"
                  : "bg-violet-600 hover:bg-violet-700 text-white"
              }
              disabled={!base}
              onClick={() => void handleInstall()}
            >
              <PackagePlus className="h-3.5 w-3.5 mr-1.5" />
              {isError
                ? "Retry installation"
                : upgrade
                  ? "Update now"
                  : "Install now"}
            </Button>
          )}
          {isRunning && (
            <Button size="sm" variant="outline" disabled>
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              {upgrade ? "Updating — please wait…" : "Installing — please wait…"}
            </Button>
          )}
          {isError && (
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground text-xs"
              onClick={() => igReset()}
            >
              Clear and start over
            </Button>
          )}
        </div>

        {!hasStarted && (
          <p className="text-[11px] text-muted-foreground">
            Packages are installed to your user account — nothing is changed
            system-wide. Internet connection required for the initial download.
          </p>
        )}
      </div>

      {/* Preview of available models — only shown before install starts */}
      {!hasStarted && models.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Models you'll be able to use
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {models.map((m) => (
              <div
                key={m.model_id}
                className="rounded-lg border bg-card p-3.5 space-y-1.5 opacity-60"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-medium text-sm">{m.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {m.provider}
                    </p>
                  </div>
                  {m.requires_hf_token && (
                    <Badge
                      variant="outline"
                      className="text-[10px] shrink-0 border-amber-500/40 text-amber-500"
                    >
                      Gated
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {m.description}
                </p>
                <div className="flex flex-wrap gap-1.5 text-[10px]">
                  <span className="rounded bg-muted px-1.5 py-0.5">
                    {m.vram_gb} GB VRAM
                  </span>
                  <span className="rounded bg-muted px-1.5 py-0.5">
                    {m.ram_gb} GB RAM
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
