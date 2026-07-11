import { useEffect, useRef, useState } from "react";
import { Download, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

/** Minimal download progress shape accepted by this component.
 *  Compatible with both DownloadProgress (transcription) and LlmDownloadProgress (LLM). */
export interface DownloadProgressData {
  filename: string;
  bytes_downloaded: number;
  total_bytes: number;
  percent: number;
  /** Part index (1-based). Present only for multi-part (split) LLM downloads. */
  part?: number;
  /** Total number of parts. Present only for multi-part downloads. */
  total_parts?: number;
}

// ── Speed tracking ─────────────────────────────────────────────────────────

interface SpeedSample {
  bytes: number;
  time: number;
}

function useDownloadStats(progress: DownloadProgressData | null) {
  const samplesRef = useRef<SpeedSample[]>([]);
  const [speedBps, setSpeedBps] = useState<number>(0);
  const [etaSec, setEtaSec] = useState<number | null>(null);

  useEffect(() => {
    if (!progress || progress.bytes_downloaded === 0) {
      samplesRef.current = [];
      setSpeedBps(0);
      setEtaSec(null);
      return;
    }

    const now = Date.now();
    samplesRef.current.push({ bytes: progress.bytes_downloaded, time: now });

    // Keep only samples from the last 3 seconds for rolling speed
    const cutoff = now - 3000;
    samplesRef.current = samplesRef.current.filter((s) => s.time >= cutoff);

    if (samplesRef.current.length >= 2) {
      const oldest = samplesRef.current[0]!;
      const elapsed = (now - oldest.time) / 1000;
      const byteDelta = progress.bytes_downloaded - oldest.bytes;
      const bps = elapsed > 0 ? byteDelta / elapsed : 0;
      setSpeedBps(bps);

      if (bps > 0 && progress.total_bytes > 0) {
        const remaining = progress.total_bytes - progress.bytes_downloaded;
        setEtaSec(Math.ceil(remaining / bps));
      } else {
        setEtaSec(null);
      }
    }
  }, [progress]);

  return { speedBps, etaSec };
}

// ── Smoothed percent ───────────────────────────────────────────────────────

/**
 * Returns a smoothed display percent that:
 * - Never jumps backward
 * - Lerps toward the actual value each frame
 * - Stays at 100 for `holdMs` after completion before callers hide the component
 */
function useSmoothedPercent(
  actual: number,
  active: boolean,
  holdMs = 900
): { display: number; held: boolean } {
  const displayRef = useRef(0);
  const [display, setDisplay] = useState(0);
  const [held, setHeld] = useState(false);
  const rafRef = useRef<number | null>(null);
  const holdTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!active && actual === 0) {
      displayRef.current = 0;
      setDisplay(0);
      setHeld(false);
      return;
    }

    const target = actual;

    const tick = () => {
      const cur = displayRef.current;
      // Never go backward; lerp toward target at 12% per frame (≈ 200ms at 60fps)
      const next = Math.min(100, cur + Math.max(0, (target - cur) * 0.18));
      displayRef.current = next;
      setDisplay(next);

      if (next < 99.5) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        displayRef.current = 100;
        setDisplay(100);
        // Hold at 100% for holdMs before allowing parent to unmount
        setHeld(true);
        holdTimerRef.current = setTimeout(() => setHeld(false), holdMs);
      }
    };

    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      if (holdTimerRef.current !== null) clearTimeout(holdTimerRef.current);
    };
  }, [actual, active, holdMs]);

  return { display, held };
}

// ── Formatters ─────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i >= 2 ? 1 : 0)} ${units[i]}`;
}

function formatSpeed(bps: number): string {
  if (bps <= 0) return "";
  return `${formatBytes(bps)}/s`;
}

function formatEta(sec: number | null): string {
  if (sec === null || sec <= 0) return "";
  if (sec < 60) return `~${sec}s remaining`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `~${m}m ${s}s remaining`;
}

// ── Status phases ──────────────────────────────────────────────────────────

type Phase = "idle" | "connecting" | "downloading" | "validating" | "done" | "error";

function resolvePhase(
  isDownloading: boolean,
  progress: DownloadProgressData | null,
  error: string | null
): Phase {
  if (error) return "error";
  if (!isDownloading && !progress) return "idle";
  if (isDownloading && !progress) return "connecting";
  if (progress && progress.percent >= 100) return "validating";
  if (progress && progress.percent > 0) return "downloading";
  return "connecting";
}

// ── Component ──────────────────────────────────────────────────────────────

interface DownloadProgressProps {
  /** Live progress payload from the Tauri event stream */
  progress: DownloadProgressData | null;
  /** True while the download invoke is in flight */
  isDownloading: boolean;
  /** Optional override label shown above the bar */
  label?: string;
  /** Optional error message to surface */
  error?: string | null;
  className?: string;
}

export function DownloadProgress({
  progress,
  isDownloading,
  label,
  error = null,
  className,
}: DownloadProgressProps) {
  const phase = resolvePhase(isDownloading, progress, error);
  const actualPercent = progress?.percent ?? 0;
  const { display, held } = useSmoothedPercent(actualPercent, isDownloading);
  const { speedBps, etaSec } = useDownloadStats(progress);

  // Remain visible while downloading OR while held at 100%
  const visible = isDownloading || held || phase === "error";

  if (!visible) return null;

  const filename = label ?? progress?.filename ?? "Downloading…";
  const shortName =
    filename.length > 40 ? `…${filename.slice(-37)}` : filename;
  const partLabel =
    progress?.total_parts && progress.total_parts > 1
      ? ` — Part ${progress.part ?? 1} of ${progress.total_parts}`
      : "";

  return (
    <div
      className={cn(
        "rounded-lg border bg-muted/30 p-4 space-y-3 transition-opacity duration-300",
        phase === "error" ? "border-red-500/20 bg-red-500/5" : "border-border",
        className
      )}
    >
      {/* Header row */}
      <div className="flex items-center gap-2">
        {phase === "done" || held ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-500 flex-shrink-0" />
        ) : phase === "error" ? (
          <AlertCircle className="h-4 w-4 text-red-500 flex-shrink-0" />
        ) : (
          <Loader2 className="h-4 w-4 text-primary animate-spin flex-shrink-0" />
        )}
        <span className="text-sm font-medium truncate flex-1" title={filename}>
          {shortName}{partLabel}
        </span>
        <span
          className={cn(
            "text-xs font-mono tabular-nums",
            phase === "error" ? "text-red-400" : "text-muted-foreground"
          )}
        >
          {phase === "error"
            ? "Failed"
            : phase === "validating"
            ? "Verifying…"
            : phase === "connecting"
            ? "Connecting…"
            : `${Math.round(display)}%`}
        </span>
      </div>

      {/* Progress bar — only while not in error state */}
      {phase !== "error" && (
        <Progress
          value={display}
          className={cn(
            "h-2",
            phase === "validating" && "animate-pulse"
          )}
        />
      )}

      {/* Stats row */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        {phase === "error" ? (
          <span className="text-red-400">{error}</span>
        ) : progress ? (
          <>
            <span className="tabular-nums">
              {formatBytes(progress.bytes_downloaded)}
              {progress.total_bytes > 0 && ` / ${formatBytes(progress.total_bytes)}`}
            </span>
            <div className="flex items-center gap-3 tabular-nums">
              {speedBps > 0 && (
                <span className="flex items-center gap-1">
                  <Download className="h-3 w-3" />
                  {formatSpeed(speedBps)}
                </span>
              )}
              {etaSec !== null && etaSec > 0 && (
                <span>{formatEta(etaSec)}</span>
              )}
            </div>
          </>
        ) : (
          <span>Waiting for server…</span>
        )}
      </div>
    </div>
  );
}
