import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// THE package byte-size formatter (`@ai-matrx/kit/format`, duplication
// census H1 2026-09-07). This repo alone carried THIRTEEN `formatBytes`
// bodies with twelve different roundings and five different words for
// "unknown" — the clearest case in the fleet for one owner.
import { formatDurationMs } from "@ai-matrx/kit/format";
// Re-exported so the historical `formatBytes` specifier keeps working for
// this module's callers; NEW code should import from kit directly.
export { formatFileSize, formatFileSize as formatBytes } from "@ai-matrx/kit/format";
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}


/** "250ms" / "5.2s" / "5m 30s" / "1h 02m" — the elapsed-work voice. */
export function formatDuration(ms: number): string {
  return formatDurationMs(ms, { style: "compact" });
}

export function truncateUrl(url: string, maxLength = 60): string {
  if (url.length <= maxLength) return url;
  const parsed = new URL(url);
  const path = parsed.pathname + parsed.search;
  const available = maxLength - parsed.origin.length - 3;
  if (available < 10) return url.slice(0, maxLength - 3) + "...";
  return parsed.origin + path.slice(0, available) + "...";
}
