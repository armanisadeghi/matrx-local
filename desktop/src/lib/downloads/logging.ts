import type { DownloadEntry } from "./types";

export type DownloadLogLevel = "info" | "warn" | "error";
export type DownloadEventOrigin = "live" | "snapshot";

interface DownloadStatusLog {
  level: DownloadLogLevel;
  message: string;
}

type DownloadLogEntry = Partial<DownloadEntry> & { id: string };

/**
 * Classify download status logs without confusing a persisted-state replay for
 * a new transfer. Snapshot failures are startup/reconnect history, while live
 * `dm-failed`/SSE events are genuine failures from work attempted this run.
 */
export function getDownloadStatusLog(
  entry: DownloadLogEntry,
  origin: DownloadEventOrigin,
): DownloadStatusLog | null {
  const filename = entry.filename ?? "?";

  if (entry.status === "failed" && entry.resolution != null) {
    return {
      level: "info",
      message:
        `[downloads] [action-needed] id=${entry.id} file=${filename} ` +
        `code=${entry.resolution.code} — ${entry.resolution.title}`,
    };
  }

  if (entry.status === "failed" && origin === "snapshot") {
    return {
      level: "warn",
      message:
        `[downloads] Previous failure restored: id=${entry.id} file=${filename} ` +
        `error=${entry.error_msg ?? "unknown"} bytes_done=${entry.bytes_done ?? 0} ` +
        `total=${entry.total_bytes ?? 0} updated_at=${entry.updated_at ?? "unknown"}`,
    };
  }

  if (entry.status === "failed") {
    return {
      level: "error",
      message:
        `[downloads] FAILED: id=${entry.id} file=${filename} ` +
        `error=${entry.error_msg ?? "unknown"} bytes_done=${entry.bytes_done ?? 0} ` +
        `total=${entry.total_bytes ?? 0}`,
    };
  }

  if (entry.status === "cancelled") {
    if (origin === "snapshot") {
      return {
        level: "info",
        message: `[downloads] Previous cancellation restored: id=${entry.id} file=${filename}`,
      };
    }
    return {
      level: "warn",
      message: `[downloads] CANCELLED: id=${entry.id} file=${filename}`,
    };
  }

  return null;
}
