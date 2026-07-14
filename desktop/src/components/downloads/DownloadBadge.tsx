/**
 * Floating download badge / pill.
 *
 * Visible whenever downloads are active or queued. Clicking opens the
 * DownloadManagerModal. Shows circular progress for the active download
 * and a pulsing indicator when the size isn't known yet.
 */

import { AlertTriangle, Download } from "lucide-react";
import { CircularProgress } from "@/components/downloads/CircularProgress";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";

interface DownloadBadgeProps {
  className?: string;
}

export function DownloadBadge({ className = "" }: DownloadBadgeProps) {
  const { downloads, activeCount, openModal } = useDownloadManager();
  const actionNeededCount = downloads.filter(
    (d) => d.status === "failed" && d.resolution != null,
  ).length;

  if (activeCount === 0 && actionNeededCount === 0) return null;

  const active = downloads.find((d) => d.status === "active");
  const queuedCount = downloads.filter((d) => d.status === "queued").length;
  const hasProgress = active && active.total_bytes > 0 && active.percent > 0;
  const pct = hasProgress ? Math.round(active.percent) : null;
  const needsAction = actionNeededCount > 0;

  return (
    <button
      onClick={openModal}
      className={`
        inline-flex items-center gap-2 h-8 pl-2 pr-3 rounded-full
        bg-background/90 backdrop-blur-sm
        border border-border/60 shadow-md shadow-black/10
        text-foreground text-xs font-medium
        hover:border-primary/40 hover:bg-primary/5
        transition-all duration-200 hover:shadow-lg hover:shadow-primary/10
        active:scale-95
        ${className}
      `}
      title={needsAction ? "Downloads need your action" : "View downloads"}
      aria-label={
        needsAction
          ? `${actionNeededCount} download${actionNeededCount !== 1 ? "s" : ""} need your action`
          : `${activeCount} active download${activeCount !== 1 ? "s" : ""}`
      }
    >
      {/* Progress indicator */}
      <span className="shrink-0 relative">
        {needsAction ? (
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500 opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500" />
          </span>
        ) : hasProgress ? (
          <CircularProgress
            percent={active.percent}
            size={20}
            strokeWidth={2.5}
            label=""
            className="shrink-0"
          />
        ) : (
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-primary" />
          </span>
        )}
      </span>

      {needsAction ? (
        <AlertTriangle className="h-3 w-3 text-amber-500 shrink-0" />
      ) : (
        <Download className="h-3 w-3 text-muted-foreground shrink-0" />
      )}

      {/* Text */}
      <span className="tabular-nums leading-none">
        {needsAction ? (
          <span className="text-amber-600 dark:text-amber-400 font-semibold">
            {actionNeededCount} needs action
          </span>
        ) : pct !== null ? (
          <span className="text-foreground font-semibold">{pct}%</span>
        ) : (
          <span className="text-muted-foreground">Downloading</span>
        )}
        {queuedCount > 0 && (
          <span className="text-muted-foreground font-normal ml-1">
            +{queuedCount} queued
          </span>
        )}
      </span>
    </button>
  );
}
