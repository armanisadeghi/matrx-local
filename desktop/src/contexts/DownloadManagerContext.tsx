/**
 * Universal Download Manager Context.
 *
 * Single source of truth for all downloads across the app.
 * Listens to:
 *  1. Tauri events: dm-progress, dm-queued, dm-completed, dm-failed, dm-cancelled
 *     (for Rust-side downloads: LLM models, Whisper models, and all generic downloads)
 *     NOTE: legacy llm-download-progress / whisper-download-progress bridge handlers
 *     are intentionally REMOVED — the Rust manager now emits proper dm-* events for
 *     every category including "llm" and "whisper". The bridges created duplicate entries.
 *  2. SSE stream: GET /downloads/stream
 *     (for Python-side downloads: image-gen, TTS, file-sync)
 *
 * Exposes:
 *  - downloads: DownloadEntry[]  sorted active→queued→failed→cancelled→completed
 *  - activeCount: number  (active + queued)
 *  - enqueue(opts): trigger a download via Tauri (Rust) or Python (POST /downloads)
 *  - cancel(id): cancel by ID
 *  - openModal() / closeModal() / isModalOpen
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { isTauri } from "@/lib/sidecar";
import { engine } from "@/lib/api";
import { emitClientLog } from "@/hooks/use-unified-log";
import type { DownloadEntry, EnqueueOptions } from "@/lib/downloads/types";
import { DOWNLOAD_LOG_SOURCE } from "@/lib/downloads/types";

// Re-export for convenience
export type { DownloadEntry, EnqueueOptions };

interface DownloadManagerContextValue {
  downloads: DownloadEntry[];
  activeCount: number;
  isModalOpen: boolean;
  openModal: () => void;
  closeModal: () => void;
  enqueue: (opts: EnqueueOptions) => Promise<DownloadEntry | null>;
  cancel: (id: string) => Promise<void>;
}

const DownloadManagerContext =
  createContext<DownloadManagerContextValue | null>(null);

// How long to keep completed/failed/cancelled entries in memory before pruning
const HISTORY_TTL_MS = 30 * 60 * 1000; // 30 minutes

// How often to emit a full-state snapshot to the log
const LOG_INTERVAL_MS = 15_000;

async function tauriInvoke<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

async function tauriListen(
  event: string,
  handler: (payload: unknown) => void,
): Promise<() => void> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen(event, (e) => handler(e.payload));
}

function mergeEntry(
  prev: Map<string, DownloadEntry>,
  incoming: Partial<DownloadEntry> & { id: string },
): Map<string, DownloadEntry> {
  const next = new Map(prev);
  const existing = next.get(incoming.id);

  // Always ensure updated_at is set so the prune logic can compare it safely
  const updated_at =
    incoming.updated_at ?? existing?.updated_at ?? new Date().toISOString();

  next.set(incoming.id, {
    ...existing,
    ...incoming,
    // Preserve created_at from the existing entry — progress events may not include it
    created_at:
      incoming.created_at ?? existing?.created_at ?? new Date().toISOString(),
    updated_at,
    // Keep richer speed/eta from the last progress event
    speed_bps: incoming.speed_bps ?? existing?.speed_bps ?? 0,
    eta_seconds:
      incoming.eta_seconds !== undefined
        ? incoming.eta_seconds
        : existing?.eta_seconds,
    // Propagate bandwidth_bps when provided
    bandwidth_bps:
      (incoming as DownloadEntry & { bandwidth_bps?: number }).bandwidth_bps ??
      existing?.bandwidth_bps ??
      0,
  } as DownloadEntry);
  return next;
}

function sortedEntries(map: Map<string, DownloadEntry>): DownloadEntry[] {
  const rank = (s: string) => {
    switch (s) {
      case "active":
        return 0;
      case "queued":
        return 1;
      case "failed":
        return 2;
      case "cancelled":
        return 3;
      case "completed":
        return 4;
      default:
        return 5;
    }
  };
  return Array.from(map.values()).sort(
    (a, b) =>
      rank(a.status) - rank(b.status) ||
      (a.created_at ?? "").localeCompare(b.created_at ?? ""),
  );
}

export function DownloadManagerProvider({ children }: { children: ReactNode }) {
  const [entriesMap, setEntriesMap] = useState<Map<string, DownloadEntry>>(
    new Map(),
  );
  const [isModalOpen, setIsModalOpen] = useState(false);
  const sseRef = useRef<EventSource | null>(null);

  // Speed tracker scoped inside the provider (not module-level) to avoid
  // stale data surviving remounts.
  const speedTrackerRef = useRef<Map<string, { bytes: number; time: number }>>(
    new Map(),
  );

  // Mirror of entriesMap for reading inside intervals without stale closures.
  const entriesMapRef = useRef<Map<string, DownloadEntry>>(new Map());

  // Keep the ref in sync so the periodic log can read state without a stale closure.
  useEffect(() => {
    entriesMapRef.current = entriesMap;
  }, [entriesMap]);

  // Memoize sorted downloads — only recomputes when entriesMap reference changes
  const downloads = useMemo(() => sortedEntries(entriesMap), [entriesMap]);

  // Memoize activeCount
  const activeCount = useMemo(
    () =>
      downloads.filter(
        (d) => d.status === "active" || d.status === "queued",
      ).length,
    [downloads],
  );

  // ── Event handler ────────────────────────────────────────────────────────

  const handleEvent = useCallback((raw: unknown) => {
    if (!raw || typeof raw !== "object") return;
    const payload = raw as Partial<DownloadEntry> & { id?: string };
    if (!payload.id) return;

    const speedTracker = speedTrackerRef.current;

    // Compute speed from byte delta when the emitter doesn't provide it
    let speed_bps = (payload as { speed_bps?: number }).speed_bps ?? 0;
    const bytes = (payload as { bytes_done?: number }).bytes_done ?? 0;
    if (speed_bps === 0 && bytes > 0) {
      const now = Date.now();
      const prev = speedTracker.get(payload.id);
      if (prev && bytes > prev.bytes) {
        const dt = (now - prev.time) / 1000;
        if (dt > 0.5) {
          speed_bps = (bytes - prev.bytes) / dt;
          speedTracker.set(payload.id, { bytes, time: now });
        }
      } else {
        speedTracker.set(payload.id, { bytes, time: now });
      }
    }

    // Compute ETA from speed + remaining bytes when not provided
    let eta_seconds = (payload as { eta_seconds?: number }).eta_seconds;
    if (eta_seconds === undefined && speed_bps > 0) {
      const total = (payload as { total_bytes?: number }).total_bytes ?? 0;
      const remaining = total > bytes ? total - bytes : 0;
      if (remaining > 0) eta_seconds = remaining / speed_bps;
    }

    const merged = {
      ...(payload as DownloadEntry & { id: string }),
      speed_bps,
      ...(eta_seconds !== undefined ? { eta_seconds } : {}),
      // Ensure updated_at always present
      updated_at:
        (payload as DownloadEntry).updated_at ?? new Date().toISOString(),
    };

    // Emit a log line for failures and cancellations. A failure carrying a
    // `resolution` is a user-actionable STATE (accept a license, add a key) —
    // it logs as info with the [action-needed] marker, never as a red error.
    const status = (payload as DownloadEntry).status;
    const resolution = (payload as DownloadEntry).resolution;
    if (status === "failed" && resolution != null) {
      emitClientLog(
        "info",
        `[downloads] [action-needed] id=${payload.id} file=${(payload as DownloadEntry).filename ?? "?"} ` +
          `code=${resolution.code} — ${resolution.title}`,
        DOWNLOAD_LOG_SOURCE,
      );
    } else if (status === "failed") {
      emitClientLog(
        "error",
        `[downloads] FAILED: id=${payload.id} file=${(payload as DownloadEntry).filename ?? "?"} ` +
          `error=${(payload as DownloadEntry).error_msg ?? "unknown"} ` +
          `bytes_done=${bytes} total=${(payload as DownloadEntry).total_bytes ?? 0}`,
        DOWNLOAD_LOG_SOURCE,
      );
    } else if (status === "cancelled") {
      emitClientLog(
        "warn",
        `[downloads] CANCELLED: id=${payload.id} file=${(payload as DownloadEntry).filename ?? "?"}`,
        DOWNLOAD_LOG_SOURCE,
      );
    }

    setEntriesMap((prev) => mergeEntry(prev, merged));
  }, []);

  // ── Tauri event listeners ────────────────────────────────────────────────
  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;
    const cleanup: Array<() => void> = [];

    (async () => {
      const coreEvents = [
        "dm-progress",
        "dm-queued",
        "dm-completed",
        "dm-failed",
        "dm-cancelled",
      ];
      for (const evt of coreEvents) {
        if (cancelled) break;
        const unlisten = await tauriListen(evt, handleEvent);
        if (cancelled) {
          unlisten();
          break;
        }
        cleanup.push(unlisten);
      }
    })();

    return () => {
      cancelled = true;
      cleanup.forEach((fn) => fn());
    };
  }, [handleEvent]);

  // ── SSE stream from Python engine ──────────────────────────────────────
  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;
    let retryDelay = 2000;
    let cancelled = false;

    const connect = async () => {
      const engineUrl = engine.engineUrl;
      if (!engineUrl) {
        retryTimeout = setTimeout(connect, 3000);
        return;
      }

      const token = await engine.getAccessToken();
      const url = token
        ? `${engineUrl}/downloads/stream?token=${encodeURIComponent(token)}`
        : `${engineUrl}/downloads/stream`;

      es = new EventSource(url);
      sseRef.current = es;

      es.onmessage = (evt) => {
        try {
          const payload = JSON.parse(evt.data);
          handleEvent(payload);
          retryDelay = 2000;
        } catch {
          // Ignore malformed events
        }
      };

      es.onerror = () => {
        es?.close();
        es = null;
        sseRef.current = null;
        if (!cancelled) {
          retryTimeout = setTimeout(connect, Math.min(retryDelay, 30000));
          retryDelay = Math.min(retryDelay * 2, 30000);
        }
      };
    };

    retryTimeout = setTimeout(connect, 1000);

    return () => {
      cancelled = true;
      if (retryTimeout) clearTimeout(retryTimeout);
      es?.close();
      sseRef.current = null;
    };
  }, [handleEvent]);

  // ── Prune old history entries ────────────────────────────────────────────
  useEffect(() => {
    const interval = setInterval(
      () => {
        const cutoff = Date.now() - HISTORY_TTL_MS;
        setEntriesMap((prev) => {
          const next = new Map(prev);
          for (const [id, entry] of next) {
            // Prune completed, cancelled, AND failed entries that are old enough
            if (
              (entry.status === "completed" ||
                entry.status === "cancelled" ||
                entry.status === "failed") &&
              new Date(entry.updated_at).getTime() < cutoff
            ) {
              next.delete(id);
            }
          }
          return next;
        });
      },
      5 * 60 * 1000,
    );
    return () => clearInterval(interval);
  }, []);

  // ── Periodic state snapshot log ──────────────────────────────────────────
  useEffect(() => {
    const interval = setInterval(() => {
      // Read from the ref to avoid stale closure; no setState needed for read-only.
      const current = entriesMapRef.current;
      const entries = Array.from(current.values());
      const active = entries.filter((e) => e.status === "active");
      const queued = entries.filter((e) => e.status === "queued");
      const failed = entries.filter((e) => e.status === "failed");
      const completed = entries.filter((e) => e.status === "completed");
      const cancelled = entries.filter((e) => e.status === "cancelled");
      const totalBandwidth = active.reduce(
        (sum, e) => sum + (e.speed_bps ?? 0),
        0,
      );

      const snapshot = {
        timestamp: new Date().toISOString(),
        active: active.map((e) => ({
          id: e.id,
          filename: e.filename,
          percent: Math.round(e.percent * 10) / 10,
          speed_bps: Math.round(e.speed_bps ?? 0),
          eta_seconds: e.eta_seconds ?? null,
          bytes_done: e.bytes_done,
          total_bytes: e.total_bytes,
        })),
        queued: queued.map((e) => ({
          id: e.id,
          filename: e.filename,
          priority: e.priority,
        })),
        failed_count: failed.length,
        completed_count: completed.length,
        cancelled_count: cancelled.length,
        total: entries.length,
        bandwidth_bps: Math.round(totalBandwidth),
      };

      emitClientLog(
        "data",
        `[downloads] STATE ${JSON.stringify(snapshot)}`,
        DOWNLOAD_LOG_SOURCE,
      );
    }, LOG_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  // ── API ──────────────────────────────────────────────────────────────────

  const enqueue = useCallback(
    async (opts: EnqueueOptions): Promise<DownloadEntry | null> => {
      const id = opts.id ?? `${opts.category}-${opts.filename}-${Date.now()}`;
      try {
        let entry: DownloadEntry;
        if (isTauri()) {
          entry = await tauriInvoke<DownloadEntry>("dm_enqueue", {
            id,
            category: opts.category,
            filename: opts.filename,
            displayName: opts.display_name,
            urls: opts.urls,
            priority: opts.priority ?? 0,
            metadata: opts.metadata ? JSON.stringify(opts.metadata) : null,
          });
        } else {
          const engineUrl = engine.engineUrl;
          if (!engineUrl) return null;
          const resp = await fetch(`${engineUrl}/downloads`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              download_id: id,
              category: opts.category,
              filename: opts.filename,
              display_name: opts.display_name,
              urls: opts.urls,
              priority: opts.priority ?? 0,
              metadata: opts.metadata,
            }),
          });
          if (!resp.ok) {
            emitClientLog(
              "error",
              `[downloads] enqueue HTTP ${resp.status} for ${opts.filename}`,
              DOWNLOAD_LOG_SOURCE,
            );
            return null;
          }
          entry = (await resp.json()) as DownloadEntry;
        }
        setEntriesMap((prev) => mergeEntry(prev, entry));
        emitClientLog(
          "info",
          `[downloads] Enqueued: ${opts.filename} (id=${id} category=${opts.category} priority=${opts.priority ?? 0})`,
          DOWNLOAD_LOG_SOURCE,
        );
        return entry;
      } catch (e) {
        emitClientLog(
          "error",
          `[downloads] enqueue FAILED for ${opts.filename}: ${String(e)}`,
          DOWNLOAD_LOG_SOURCE,
        );
        return null;
      }
    },
    [],
  );

  const cancel = useCallback(async (id: string): Promise<void> => {
    try {
      if (isTauri()) {
        await tauriInvoke("dm_cancel", { id });
      } else {
        const engineUrl = engine.engineUrl;
        if (!engineUrl) {
          emitClientLog(
            "error",
            `[downloads] cancel FAILED for id=${id}: engine not connected`,
            DOWNLOAD_LOG_SOURCE,
          );
          throw new Error("Engine not connected — could not cancel download.");
        }
        const resp = await fetch(`${engineUrl}/downloads/${id}`, {
          method: "DELETE",
        });
        if (!resp.ok) {
          const detail = await resp.text().catch(() => `HTTP ${resp.status}`);
          throw new Error(
            `Cancel request failed (HTTP ${resp.status}): ${detail}`,
          );
        }
      }
      // Optimistically update UI — only reached when the cancel actually
      // succeeded (Tauri invoke resolved or the DELETE returned ok).
      setEntriesMap((prev) => {
        const next = new Map(prev);
        const entry = next.get(id);
        if (entry && (entry.status === "active" || entry.status === "queued")) {
          next.set(id, {
            ...entry,
            status: "cancelled",
            updated_at: new Date().toISOString(),
          });
        }
        return next;
      });
      emitClientLog(
        "info",
        `[downloads] Cancel requested: id=${id}`,
        DOWNLOAD_LOG_SOURCE,
      );
    } catch (e) {
      emitClientLog(
        "error",
        `[downloads] cancel FAILED for id=${id}: ${String(e)}`,
        DOWNLOAD_LOG_SOURCE,
      );
    }
  }, []);

  const openModal = useCallback(() => setIsModalOpen(true), []);
  const closeModal = useCallback(() => setIsModalOpen(false), []);

  return (
    <DownloadManagerContext.Provider
      value={{
        downloads,
        activeCount,
        isModalOpen,
        openModal,
        closeModal,
        enqueue,
        cancel,
      }}
    >
      {children}
    </DownloadManagerContext.Provider>
  );
}

export function useDownloadManager(): DownloadManagerContextValue {
  const ctx = useContext(DownloadManagerContext);
  if (!ctx) {
    throw new Error(
      "useDownloadManager must be used inside <DownloadManagerProvider>",
    );
  }
  return ctx;
}
