/**
 * useFileSync — state for the Files sync surface (desktop replica of the
 * user's matrx-files cloud tree).
 *
 * Follows the repo's hook contract (see CLAUDE.md → React Patterns):
 * - `actions` wrapped in useMemo (stable reference)
 * - init fetch inside the hook (useEffect with [] semantics)
 * - polling narrowly gated: only while a sync cycle is actually in flight
 *   (manual Sync Now, or the engine reports a running cycle) — no
 *   free-running intervals.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { engine } from "@/lib/api";
import type {
  FileSyncStatus,
  FileSyncConflict,
  FileSyncCycleSummary,
  FileSyncMode,
} from "@/lib/api";
import { saveSetting, broadcastSettingsChanged } from "@/lib/settings";

export interface FileSyncState {
  status: FileSyncStatus | null;
  conflicts: FileSyncConflict[];
  /** True during the initial load only. */
  loading: boolean;
  /** True while a manual sync cycle is in flight. */
  syncing: boolean;
  /** Result of the last manual sync cycle, if any. */
  lastCycle: FileSyncCycleSummary | null;
  /** Gentle, human-readable notice (never a raw stack trace). */
  notice: string | null;
}

export interface FileSyncActions {
  /** Re-fetch status + conflicts from the engine. */
  refresh: () => Promise<void>;
  /** Run one sync cycle now. */
  syncNow: () => Promise<void>;
  /** Change the sync mode (persists the setting and applies it live). */
  setMode: (mode: FileSyncMode) => Promise<void>;
  /** Resolve a conflict by keeping one side. */
  resolveConflict: (
    fileId: string,
    resolution: "keep_local" | "keep_remote",
  ) => Promise<void>;
}

export function useFileSync(): [FileSyncState, FileSyncActions] {
  const [status, setStatus] = useState<FileSyncStatus | null>(null);
  const [conflicts, setConflicts] = useState<FileSyncConflict[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [lastCycle, setLastCycle] = useState<FileSyncCycleSummary | null>(
    null,
  );
  const [notice, setNotice] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    if (!engine.engineUrl) return;
    try {
      const [s, c] = await Promise.all([
        engine.fileSyncStatus(),
        engine.fileSyncConflicts(),
      ]);
      if (!mountedRef.current) return;
      setStatus(s);
      setConflicts(c.conflicts ?? []);
    } catch (err) {
      // Engine may still be starting — stay quiet, the next refresh will land.
      console.warn("[file-sync] refresh failed:", err);
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  const syncNow = useCallback(async () => {
    if (!engine.engineUrl) return;
    setSyncing(true);
    setNotice(null);
    try {
      const summary = await engine.fileSyncNow();
      if (!mountedRef.current) return;
      setLastCycle(summary);
    } catch (err) {
      if (!mountedRef.current) return;
      const msg = err instanceof Error ? err.message : String(err);
      // Gentle prompts, no jargon: the two 409s from the engine are
      // "not signed in" and "mode is off".
      if (msg.includes("409")) {
        setNotice(
          "Sync isn't available yet — make sure you're signed in and sync isn't turned off.",
        );
      } else {
        setNotice("Sync didn't complete this time. It will try again.");
      }
    } finally {
      if (mountedRef.current) setSyncing(false);
      await refresh();
    }
  }, [refresh]);

  const setMode = useCallback(
    async (mode: FileSyncMode) => {
      setNotice(null);
      // Optimistic — the selector should feel instant.
      setStatus((prev) => (prev ? { ...prev, mode } : prev));
      // saveSetting persists to localStorage AND applies live to the engine
      // (POST /file-sync/mode via the syncSetting pipeline in lib/settings).
      await saveSetting("fileSyncMode", mode);
      broadcastSettingsChanged();
      await refresh();
    },
    [refresh],
  );

  const resolveConflict = useCallback(
    async (fileId: string, resolution: "keep_local" | "keep_remote") => {
      if (!engine.engineUrl) return;
      setNotice(null);
      try {
        await engine.fileSyncResolveConflict(fileId, resolution);
      } catch (err) {
        if (mountedRef.current) {
          setNotice("Couldn't resolve that file just now — try again.");
        }
        console.warn("[file-sync] resolve failed:", err);
      }
      await refresh();
    },
    [refresh],
  );

  // ── Init fetch — inside the hook, once ───────────────────────────────────
  useEffect(() => {
    mountedRef.current = true;
    void refresh();
    return () => {
      mountedRef.current = false;
    };
    // refresh is stable (useCallback with []) — run once on mount.
  }, [refresh]);

  // ── Gently-paced polling — ONLY while a cycle is in flight ───────────────
  // A manual Sync Now (syncing) or an engine-side auto cycle reported as
  // running. No interval otherwise.
  const engineCycleRunning = status?.last_sync_status === "running";
  useEffect(() => {
    if (!syncing && !engineCycleRunning) return;
    const id = setInterval(() => void refresh(), 4000);
    return () => clearInterval(id);
  }, [syncing, engineCycleRunning, refresh]);

  const state: FileSyncState = {
    status,
    conflicts,
    loading,
    syncing,
    lastCycle,
    notice,
  };

  // Stable actions object — see React Patterns in CLAUDE.md.
  const actions: FileSyncActions = useMemo(
    () => ({ refresh, syncNow, setMode, resolveConflict }),
    [refresh, syncNow, setMode, resolveConflict],
  );

  return [state, actions];
}
