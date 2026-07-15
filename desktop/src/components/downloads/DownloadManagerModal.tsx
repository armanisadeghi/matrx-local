/**
 * Download Manager Modal — wide tabular layout.
 *
 * Three always-rendered sections: In Progress / Waiting / Completed & Failed
 * plus a collapsible live-log panel filtered to the "downloads" log source.
 *
 * Layout rules (no jumps):
 * - All sections are always mounted; empty state shows a placeholder row.
 * - Progress bars transition via CSS width only — no conditional mounts.
 * - Text cells always render; show "—" when values are unavailable.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  X,
  XCircle,
  CheckCircle2,
  AlertCircle,
  RotateCcw,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  Download,
  Cpu,
  Mic,
  ImageIcon,
  Film,
  Volume2,
  RefreshCw,
  HardDrive,
} from "lucide-react";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { useResolutionAction } from "@/components/downloads/useResolutionAction";
import { useClientLogSubscriber } from "@/hooks/use-unified-log";
import type { DownloadEntry } from "@/lib/downloads/types";

// ── Helpers ──────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatSpeed(bps: number | undefined): string {
  if (!bps || bps <= 0) return "—";
  return `${formatBytes(bps)}/s`;
}

function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function formatPercent(pct: number): string {
  if (pct <= 0) return "0%";
  if (pct >= 100) return "100%";
  return `${Math.round(pct)}%`;
}

function CategoryIcon({ category }: { category: string }) {
  const cls = "h-3.5 w-3.5 shrink-0 text-muted-foreground";
  switch (category) {
    case "llm":
      return <Cpu className={cls} />;
    case "whisper":
      return <Mic className={cls} />;
    case "image_gen":
      return <ImageIcon className={cls} />;
    case "video_gen":
      return <Film className={cls} />;
    case "tts":
      return <Volume2 className={cls} />;
    case "file_sync":
      return <RefreshCw className={cls} />;
    default:
      return <HardDrive className={cls} />;
  }
}

// ── Inline progress bar (always rendered, no jump) ───────────────────────

function ProgressBar({
  percent,
  indeterminate = false,
}: {
  percent: number;
  indeterminate?: boolean;
}) {
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted">
      {indeterminate ? (
        <div className="absolute inset-y-0 left-0 w-1/3 animate-[slide_1.5s_ease-in-out_infinite] rounded-full bg-blue-500" />
      ) : (
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-blue-500 transition-[width] duration-300"
          style={{ width: `${clamped}%` }}
        />
      )}
    </div>
  );
}

// ── Section header ────────────────────────────────────────────────────────

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center gap-2 border-b border-border px-4 py-2">
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium text-foreground">
        {count}
      </span>
    </div>
  );
}

// ── Column headers ────────────────────────────────────────────────────────

function TableHeader({
  columns,
}: {
  columns: Array<{ label: string; className?: string }>;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-border bg-muted/50 px-4 py-1.5">
      {columns.map((col, index) => (
        <span
          key={`${index}:${col.label}`}
          className={`text-[10px] font-semibold uppercase tracking-wider text-muted-foreground ${col.className ?? ""}`}
        >
          {col.label}
        </span>
      ))}
    </div>
  );
}

// ── In-Progress row ────────────────────────────────────────────────────────

function ActiveRow({
  entry,
  onCancel,
}: {
  entry: DownloadEntry;
  onCancel: (id: string) => void;
}) {
  const indeterminate =
    entry.percent <= 0 && entry.bytes_done <= 0 && entry.status === "active";

  return (
    <div className="group flex h-14 items-center gap-3 border-b border-border/60 px-4 transition-colors hover:bg-muted/50">
      {/* Category icon + name */}
      <div className="flex w-52 min-w-0 items-center gap-2">
        <CategoryIcon category={entry.category} />
        <span
          className="truncate text-sm text-foreground"
          title={entry.display_name || entry.filename}
        >
          {entry.display_name || entry.filename}
        </span>
      </div>

      {/* Progress bar + percent */}
      <div className="flex flex-1 items-center gap-2">
        <ProgressBar percent={entry.percent} indeterminate={indeterminate} />
        <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
          {indeterminate ? "…" : formatPercent(entry.percent)}
        </span>
      </div>

      {/* Bytes */}
      <div className="w-32 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {entry.total_bytes > 0
          ? `${formatBytes(entry.bytes_done)} / ${formatBytes(entry.total_bytes)}`
          : formatBytes(entry.bytes_done)}
      </div>

      {/* Speed */}
      <div className="w-20 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {formatSpeed(entry.speed_bps)}
      </div>

      {/* ETA */}
      <div className="w-16 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {formatEta(entry.eta_seconds)}
      </div>

      {/* Part info */}
      <div className="w-12 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {entry.part_total > 1
          ? `${entry.part_current}/${entry.part_total}`
          : ""}
      </div>

      {/* Cancel */}
      <button
        onClick={() => onCancel(entry.id)}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:text-red-600 dark:hover:text-red-600 dark:text-red-400 group-hover:opacity-100"
        aria-label={`Cancel ${entry.display_name || entry.filename}`}
        title="Cancel"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ── Waiting row ───────────────────────────────────────────────────────────

function WaitingRow({
  entry,
  position,
  onCancel,
}: {
  entry: DownloadEntry;
  position: number;
  onCancel: (id: string) => void;
}) {
  return (
    <div className="group flex h-12 items-center gap-3 border-b border-border/60 px-4 transition-colors hover:bg-muted/50">
      {/* Position badge */}
      <div className="w-6 shrink-0 text-center text-xs text-muted-foreground">
        {position}
      </div>

      {/* Category icon + name */}
      <div className="flex flex-1 min-w-0 items-center gap-2">
        <CategoryIcon category={entry.category} />
        <span
          className="truncate text-sm text-foreground"
          title={entry.display_name || entry.filename}
        >
          {entry.display_name || entry.filename}
        </span>
      </div>

      {/* Size */}
      <div className="w-24 shrink-0 text-right text-xs text-muted-foreground">
        {entry.total_bytes > 0 ? formatBytes(entry.total_bytes) : "—"}
      </div>

      {/* Priority */}
      <div className="w-16 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
        {entry.priority !== 0 ? `p${entry.priority}` : "—"}
      </div>

      {/* Cancel */}
      <button
        onClick={() => onCancel(entry.id)}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:text-red-600 dark:hover:text-red-600 dark:text-red-400 group-hover:opacity-100"
        aria-label={`Remove ${entry.display_name || entry.filename} from queue`}
        title="Remove from queue"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ── "Needs your action" prompt card ───────────────────────────────────────
//
// A failed download that carries a `resolution` is NOT an error — it is a
// question the app must ask the user (accept a model license, add an API key,
// install the AI packages). Those entries surface HERE, as a first-class
// prompt card at the top of the panel with the explanation and the one button
// that fixes it — never as a red row buried in history.

function ActionNeededCard({
  entry,
  onRetry,
}: {
  entry: DownloadEntry;
  onRetry: (entry: DownloadEntry) => void;
}) {
  const dispatchAction = useResolutionAction();
  const resolution = entry.resolution;
  if (!resolution) return null;

  return (
    <div className="mx-4 my-3 rounded-lg border border-amber-300/70 bg-amber-50 p-4 dark:border-amber-700/50 dark:bg-amber-950/30">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-900/60">
          <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-500" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">
            {resolution.title}
          </p>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            {resolution.message}
          </p>
          <p
            className="mt-1.5 truncate text-xs text-muted-foreground/70"
            title={entry.display_name || entry.filename}
          >
            {entry.display_name || entry.filename}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => void dispatchAction(resolution)}
              className="rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
            >
              {resolution.action_label}
            </button>
            <button
              onClick={() => onRetry(entry)}
              className="flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
              title="Re-check access and start the download again"
            >
              <RotateCcw className="h-3 w-3" />
              Check again & retry
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Completed/Failed/Cancelled row ────────────────────────────────────────

function HistoryRow({
  entry,
  onRetry,
}: {
  entry: DownloadEntry;
  onRetry?: (entry: DownloadEntry) => void;
}) {
  const isCompleted = entry.status === "completed";
  const isFailed = entry.status === "failed";
  // Failures the user can fix never land here — the modal routes entries with
  // a `resolution` to the "Needs your action" prompt cards up top. A failed
  // row in history is a genuine error and keeps its raw message.

  return (
    <div className="group flex h-12 items-center gap-3 border-b border-border/60 px-4 transition-colors hover:bg-muted/50">
      {/* Status icon */}
      <div className="w-5 shrink-0">
        {isCompleted && <CheckCircle2 className="h-4 w-4 text-emerald-500" />}
        {isFailed && (
          <AlertCircle className="h-4 w-4 text-red-600 dark:text-red-400" />
        )}
        {entry.status === "cancelled" && (
          <XCircle className="h-4 w-4 text-muted-foreground" />
        )}
      </div>

      {/* Category icon + name */}
      <div className="flex flex-1 min-w-0 items-center gap-2">
        <CategoryIcon category={entry.category} />
        <span
          className="truncate text-sm text-muted-foreground"
          title={entry.display_name || entry.filename}
        >
          {entry.display_name || entry.filename}
        </span>
      </div>

      {/* Error text (genuine failures only) */}
      <div className="w-48 shrink-0 text-xs">
        <span
          className="block truncate text-red-600 dark:text-red-400"
          title={isFailed ? (entry.error_msg ?? "Unknown error") : undefined}
        >
          {isFailed ? (entry.error_msg ?? "Unknown error") : ""}
        </span>
      </div>

      {/* Size */}
      <div className="w-24 shrink-0 text-right text-xs text-muted-foreground">
        {entry.total_bytes > 0 ? formatBytes(entry.total_bytes) : "—"}
      </div>

      {/* Retry button (failed only) */}
      <button
        onClick={() => isFailed && onRetry?.(entry)}
        className={[
          "flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground",
          "transition-opacity hover:text-blue-600 dark:text-blue-400",
          isFailed ? "opacity-0 group-hover:opacity-100" : "invisible",
        ].join(" ")}
        aria-label={
          isFailed ? `Retry ${entry.display_name || entry.filename}` : undefined
        }
        title={isFailed ? "Retry" : undefined}
        disabled={!isFailed}
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// ── Empty row ─────────────────────────────────────────────────────────────

function EmptyRow({ message }: { message: string }) {
  return (
    <div className="flex h-10 items-center justify-center px-4 text-xs text-muted-foreground/70">
      {message}
    </div>
  );
}

// ── Log panel ──────────────────────────────────────────────────────────────

function LogPanel() {
  const allLogs = useClientLogSubscriber();
  const [copied, setCopied] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Filter to download-source logs
  const logs = allLogs.filter((l) => l.source === "downloads");

  // Auto-scroll to bottom only if the user hasn't scrolled up
  useEffect(() => {
    const container = bottomRef.current?.parentElement;
    if (!container) return;
    const distFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distFromBottom < 80) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs.length]);

  const handleCopy = useCallback(() => {
    const text = logs
      .map((l) => `[${l.time}] [${l.level.toUpperCase()}] ${l.message}`)
      .join("\n");
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [logs]);

  return (
    <div className="flex flex-col border-t border-border">
      <div className="flex items-center justify-between px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Download Logs
        </span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 rounded px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          title="Copy all log lines to clipboard"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
              <span className="text-emerald-600 dark:text-emerald-400">
                Copied
              </span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              Copy All
            </>
          )}
        </button>
      </div>

      <div className="h-48 overflow-y-auto bg-muted/40 px-4 py-2 font-mono text-[10px] leading-relaxed">
        {logs.length === 0 ? (
          <span className="text-muted-foreground/70">
            No download logs yet.
          </span>
        ) : (
          logs.map((l) => (
            <div
              key={l.id}
              className={[
                "mb-0.5",
                l.level === "error"
                  ? "text-red-600 dark:text-red-400"
                  : l.level === "warn"
                    ? "text-amber-600 dark:text-amber-400"
                    : l.level === "success"
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-muted-foreground",
              ].join(" ")}
            >
              <span className="text-muted-foreground/70">[{l.time}]</span>{" "}
              <span className="text-muted-foreground">
                [{l.level.toUpperCase()}]
              </span>{" "}
              {l.message}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Main modal ────────────────────────────────────────────────────────────

export function DownloadManagerModal() {
  const { downloads, isModalOpen, closeModal, cancel, enqueue } =
    useDownloadManager();

  const [logsExpanded, setLogsExpanded] = useState(false);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Keyboard close
  useEffect(() => {
    if (!isModalOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeModal();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isModalOpen, closeModal]);

  const active = downloads.filter((d) => d.status === "active");
  const queued = downloads.filter((d) => d.status === "queued");
  // Failures the user can fix are prompts, not history — they get their own
  // section at the top of the panel instead of a red row at the bottom.
  const actionNeeded = downloads.filter(
    (d) => d.status === "failed" && d.resolution != null,
  );
  const history = downloads.filter(
    (d) =>
      d.status === "completed" ||
      (d.status === "failed" && d.resolution == null) ||
      d.status === "cancelled",
  );

  const handleCancelAll = useCallback(async () => {
    const toCancel = [...active, ...queued];
    await Promise.allSettled(toCancel.map((d) => cancel(d.id)));
  }, [active, queued, cancel]);

  const handleRetry = useCallback(
    (entry: DownloadEntry) => {
      // Carry the metadata forward but NOT the previous failure's resolution —
      // the engine stores it in metadata, and copying it into a fresh download
      // would make a brand-new attempt start life already asking for help.
      let metadata = entry.metadata ?? undefined;
      if (metadata && "resolution" in metadata) {
        const { resolution: _dropped, ...rest } = metadata;
        metadata = rest;
      }
      void enqueue({
        id: `${entry.id}-retry-${Date.now()}`,
        category: entry.category,
        filename: entry.filename,
        display_name: entry.display_name,
        urls: entry.urls,
        priority: entry.priority,
        ...(metadata !== undefined ? { metadata } : {}),
      });
    },
    [enqueue],
  );

  // Render the modal contents always (to avoid layout shifts when toggling),
  // but hide via pointer-events/opacity when closed so the DOM is stable.
  return (
    <div
      role={isModalOpen ? "dialog" : undefined}
      aria-modal={isModalOpen ? "true" : undefined}
      aria-label={isModalOpen ? "Download Manager" : undefined}
      aria-hidden={!isModalOpen}
      className={[
        "fixed inset-0 z-50 flex items-center justify-center p-4",
        "transition-opacity duration-200",
        isModalOpen
          ? "pointer-events-auto opacity-100"
          : "pointer-events-none opacity-0",
      ].join(" ")}
    >
      {/* Backdrop */}
      <div
        ref={overlayRef}
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={closeModal}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="relative z-10 flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
          <div className="flex items-center gap-3">
            <Download className="h-5 w-5 text-muted-foreground" />
            <h2 className="text-base font-semibold text-foreground">
              Downloads
            </h2>
            {(active.length > 0 || queued.length > 0) && (
              <span className="rounded-full bg-blue-500/20 px-2 py-0.5 text-xs font-medium text-blue-600 dark:text-blue-400">
                {active.length + queued.length} pending
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {(active.length > 0 || queued.length > 0) && (
              <button
                onClick={handleCancelAll}
                className="rounded px-3 py-1 text-xs text-muted-foreground transition-colors hover:bg-red-500/10 hover:text-red-600 dark:hover:text-red-600 dark:text-red-400"
              >
                Cancel All
              </button>
            )}
            <button
              onClick={closeModal}
              className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
              aria-label="Close downloads panel"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto">
          {/* ── Needs your action ──────────────────────────────────────── */}
          {actionNeeded.length > 0 && (
            <>
              <SectionHeader
                label="Needs your action"
                count={actionNeeded.length}
              />
              {actionNeeded.map((entry) => (
                <ActionNeededCard
                  key={entry.id}
                  entry={entry}
                  onRetry={handleRetry}
                />
              ))}
            </>
          )}

          {/* ── In Progress ────────────────────────────────────────────── */}
          <SectionHeader label="In Progress" count={active.length} />
          {active.length > 0 && (
            <TableHeader
              columns={[
                { label: "Name", className: "w-52" },
                { label: "Progress", className: "flex-1" },
                { label: "", className: "w-10" },
                { label: "Size", className: "w-32 text-right" },
                { label: "Speed", className: "w-20 text-right" },
                { label: "ETA", className: "w-16 text-right" },
                { label: "Part", className: "w-12 text-right" },
                { label: "", className: "w-6" },
              ]}
            />
          )}
          {active.length === 0 && <EmptyRow message="No active downloads" />}
          {active.map((entry) => (
            <ActiveRow key={entry.id} entry={entry} onCancel={cancel} />
          ))}

          {/* ── Waiting ────────────────────────────────────────────────── */}
          <SectionHeader label="Waiting" count={queued.length} />
          {queued.length > 0 && (
            <TableHeader
              columns={[
                { label: "#", className: "w-6" },
                { label: "Name", className: "flex-1" },
                { label: "Size", className: "w-24 text-right" },
                { label: "Priority", className: "w-16 text-right" },
                { label: "", className: "w-6" },
              ]}
            />
          )}
          {queued.length === 0 && <EmptyRow message="Queue is empty" />}
          {queued.map((entry, idx) => (
            <WaitingRow
              key={entry.id}
              entry={entry}
              position={idx + 1}
              onCancel={cancel}
            />
          ))}

          {/* ── Completed & Failed ─────────────────────────────────────── */}
          <SectionHeader label="Completed & Failed" count={history.length} />
          {history.length > 0 && (
            <TableHeader
              columns={[
                { label: "", className: "w-5" },
                { label: "Name", className: "flex-1" },
                { label: "Error", className: "w-48" },
                { label: "Size", className: "w-24 text-right" },
                { label: "", className: "w-6" },
              ]}
            />
          )}
          {history.length === 0 && <EmptyRow message="No history" />}
          {history.map((entry) => (
            <HistoryRow key={entry.id} entry={entry} onRetry={handleRetry} />
          ))}
        </div>

        {/* ── Log panel (collapsible) ─────────────────────────────────── */}
        <div className="shrink-0 border-t border-border">
          <button
            onClick={() => setLogsExpanded((v) => !v)}
            className="flex w-full items-center justify-between px-4 py-2 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
            aria-expanded={logsExpanded}
            aria-controls="download-log-panel"
          >
            <span>Logs</span>
            {logsExpanded ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </button>
          <div
            id="download-log-panel"
            className={[
              "overflow-hidden transition-[max-height] duration-300",
              logsExpanded ? "max-h-64" : "max-h-0",
            ].join(" ")}
          >
            <LogPanel />
          </div>
        </div>
      </div>
    </div>
  );
}
