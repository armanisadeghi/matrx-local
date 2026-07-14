/**
 * DownloadIndicator — always-mounted header button.
 *
 * - Renders at full opacity when downloads are active/queued.
 * - Also renders when a failed download is waiting on a user action.
 * - Renders with opacity-0 when idle (never unmounts → no layout shift).
 * - Shows a count badge and a thin linear progress bar for the primary active download.
 * - Clicking opens the Download Manager modal.
 */

import { AlertTriangle, Download } from "lucide-react";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";

export function DownloadIndicator() {
  const { downloads, activeCount, openModal } = useDownloadManager();

  // Primary active download (first in sorted list)
  const primary = downloads.find((d) => d.status === "active");

  // Percent for the thin progress bar
  const percent = primary && primary.total_bytes > 0 ? primary.percent : null;
  const actionNeededCount = downloads.filter(
    (d) => d.status === "failed" && d.resolution != null,
  ).length;

  const isIdle = activeCount === 0 && actionNeededCount === 0;
  const count = actionNeededCount > 0 ? actionNeededCount : activeCount;
  const tone = actionNeededCount > 0 ? "amber" : "blue";
  const label =
    actionNeededCount > 0
      ? `${actionNeededCount} download${actionNeededCount !== 1 ? "s" : ""} need your action — click to view`
      : activeCount > 0
        ? `${activeCount} download${activeCount !== 1 ? "s" : ""} in progress — click to view`
        : "Download manager";

  return (
    <button
      onClick={openModal}
      aria-label={label}
      title={label}
      className={[
        "relative flex h-8 items-center gap-1.5 rounded px-2",
        "transition-all duration-200",
        "hover:bg-muted",
        isIdle
          ? "pointer-events-none opacity-0"
          : "pointer-events-auto opacity-100",
      ].join(" ")}
      tabIndex={isIdle ? -1 : 0}
    >
      {/* Icon */}
      {actionNeededCount > 0 ? (
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
      ) : (
        <Download className="h-4 w-4 shrink-0 text-blue-600 dark:text-blue-400" />
      )}

      {/* Count badge */}
      <span
        className={[
          "min-w-[1.25rem] rounded-full px-1.5 py-0.5 text-center text-[10px] font-semibold tabular-nums leading-none",
          tone === "amber"
            ? "bg-amber-500/20 text-amber-700 dark:text-amber-400"
            : "bg-blue-500/20 text-blue-600 dark:text-blue-400",
        ].join(" ")}
      >
        {count}
      </span>

      {/* Thin progress bar — always rendered, transitions via width */}
      <div
        className={[
          "absolute bottom-0 left-0 right-0 h-[2px] overflow-hidden rounded-b",
          "transition-opacity duration-200",
          percent !== null && actionNeededCount === 0 ? "opacity-100" : "opacity-0",
        ].join(" ")}
        aria-hidden="true"
      >
        {/* Track */}
        <div className="absolute inset-0 bg-muted" />
        {/* Fill */}
        <div
          className="absolute inset-y-0 left-0 bg-blue-500 transition-[width] duration-300"
          style={{ width: `${percent ?? 0}%` }}
        />
      </div>
    </button>
  );
}
