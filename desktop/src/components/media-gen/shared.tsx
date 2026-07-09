/**
 * Shared building blocks for the media-gen UI (Images + Video sections).
 */

import { AlertCircle, X } from "lucide-react";
import type { DownloadEntry } from "@/lib/downloads/types";

// ── Formatting helpers ───────────────────────────────────────────────────────

export function formatGb(gb: number): string {
  if (gb <= 0) return "—";
  if (gb < 1) return `${Math.round(gb * 1000)} MB`;
  return `${gb.toFixed(1)} GB`;
}

// ── Download matching ────────────────────────────────────────────────────────

/**
 * Find the live DownloadManager entry for a model's weights download.
 *
 * Contract: the engine enqueues weight downloads in category
 * "image_gen" / "video_gen" with the download `filename` set to the SANITIZED
 * model id (`/` → `--`, done by the engine's `sanitize_model_id`). The SSE
 * progress payload (ProgressEvent) does NOT carry a metadata field, so the
 * filename is the real join key. We match the sanitized id against the entry's
 * filename (and, as a courtesy, the display_name), case-insensitively and
 * tolerating both `--` and `_` sanitizing variants.
 */
export function findModelDownload(
  downloads: DownloadEntry[],
  category: "image_gen" | "video_gen",
  modelId: string,
): DownloadEntry | null {
  const idLower = modelId.toLowerCase();
  const sanitized = idLower.replace(/\//g, "--");
  const sanitizedUnderscore = idLower.replace(/[/]/g, "_");
  const candidates = downloads.filter((d) => {
    if (d.category !== category) return false;
    const fn = (d.filename ?? "").toLowerCase();
    const dn = (d.display_name ?? "").toLowerCase();
    return (
      fn.includes(idLower) ||
      fn.includes(sanitized) ||
      fn.includes(sanitizedUnderscore) ||
      dn.includes(idLower)
    );
  });
  if (candidates.length === 0) return null;
  // Prefer the most recently updated active/queued entry, else the newest.
  const activeFirst = candidates.sort((a, b) => {
    const rank = (s: string) => (s === "active" ? 0 : s === "queued" ? 1 : 2);
    return (
      rank(a.status) - rank(b.status) ||
      (b.updated_at ?? "").localeCompare(a.updated_at ?? "")
    );
  });
  return activeFirst[0];
}

// ── Small presentational pieces ──────────────────────────────────────────────

export function StarRating({ value, max = 5 }: { value: number; max?: number }) {
  return (
    <span className="flex gap-0.5">
      {Array.from({ length: max }).map((_, i) => (
        <span
          key={i}
          className={`h-2 w-2 rounded-full ${i < value ? "bg-violet-500" : "bg-muted-foreground/20"}`}
        />
      ))}
    </span>
  );
}

export function ErrorNote({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive flex items-center gap-2">
      <AlertCircle className="h-3.5 w-3.5 shrink-0" />
      <span className="break-words min-w-0 flex-1">{message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="shrink-0 text-destructive/70 hover:text-destructive"
          aria-label="Dismiss error"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export function InlineProgressBar({
  percent,
  indeterminate = false,
}: {
  percent: number;
  indeterminate?: boolean;
}) {
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted/60">
      {indeterminate ? (
        <div className="absolute inset-y-0 left-0 w-1/3 animate-pulse rounded-full bg-violet-500" />
      ) : (
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-violet-500 transition-[width] duration-300"
          style={{ width: `${clamped}%` }}
        />
      )}
    </div>
  );
}

/** Open an external URL via the Tauri shell when available, else a new tab. */
export async function openExternalUrl(url: string) {
  if (
    typeof window !== "undefined" &&
    (window as unknown as Record<string, unknown>).__TAURI__
  ) {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
