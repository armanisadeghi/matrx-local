/**
 * Shared types for the universal download manager.
 * Used by DownloadManagerContext, DownloadManagerModal, DownloadBadge, and DownloadIndicator.
 */

export type DownloadStatus = "queued" | "active" | "completed" | "failed" | "cancelled";

export type DownloadCategory =
  | "llm"
  | "whisper"
  | "image_gen"
  | "video_gen"
  | "tts"
  | "file_sync";

/** Log source tag used when emitting download-specific log lines to use-unified-log */
export const DOWNLOAD_LOG_SOURCE = "downloads" as const;

/** What the app must ask the user to do so a failed download can succeed.
 * Mirrors app/services/downloads/failures.py — the engine decides the ask (only
 * it knows WHY the download failed); the UI renders it and runs the action. */
export type DownloadActionKind =
  | "settings_api_keys"
  | "open_url"
  | "install_ai_packages";

export interface DownloadResolution {
  code: string;
  title: string;
  message: string;
  action_kind: DownloadActionKind;
  action_label: string;
  action_url: string | null;
  provider: string | null;
}

export interface DownloadEntry {
  id: string;
  category: string;
  filename: string;
  display_name: string;
  urls: string[];
  total_bytes: number;
  bytes_done: number;
  percent: number;
  status: DownloadStatus;
  error_msg: string | null;
  /** Present ONLY when the failure is one the user can fix (missing API key,
   * unaccepted model license, AI packages not installed). When set, the UI owes
   * the user an explanation and a button — never a raw error string. */
  resolution?: DownloadResolution | null;
  priority: number;
  part_current: number;
  part_total: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  /** Arbitrary per-download metadata passed at enqueue time */
  metadata?: Record<string, unknown> | null;
  /** Computed client-side from recent events; not persisted */
  speed_bps?: number;
  /** Computed client-side; not persisted */
  eta_seconds?: number | null;
  /** Aggregate available bandwidth estimate (bytes/sec) shared from the manager */
  bandwidth_bps?: number;
}

export interface EnqueueOptions {
  id?: string;
  category: string;
  filename: string;
  display_name: string;
  urls: string[];
  priority?: number;
  metadata?: Record<string, unknown> | null;
}

/** Snapshot emitted to the log every 15 seconds */
export interface DownloadQueueSnapshot {
  timestamp: string;
  active: Array<{
    id: string;
    filename: string;
    percent: number;
    speed_bps: number;
    eta_seconds: number | null;
    bytes_done: number;
    total_bytes: number;
  }>;
  queued: Array<{ id: string; filename: string; priority: number }>;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
  total: number;
  bandwidth_bps: number;
}
