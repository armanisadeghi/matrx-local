/**
 * Auto-update hook for the Tauri desktop app.
 *
 * Flow:
 *   1. After startup delay, silently check for updates (no dialog).
 *   2. If an update is available, show it without mutating the running bundle.
 *   3. Install is explicit. Native Rust downloads first, stops the frozen
 *      engine, installs, and relaunches as one transaction; a PyInstaller
 *      process must never import from an executable being replaced in place.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { isTauri, checkForUpdates, restartApp, type UpdateStatus } from "@/lib/sidecar";
import { loadSettings } from "@/lib/settings";
import { useWindowLeader } from "@/hooks/use-window-leader";

export interface AutoUpdateState {
  /** Current status of the update system */
  status: UpdateStatus | null;
  /** Whether a check (network metadata only) is in progress */
  busy: boolean;
  /** When true, show download progress in banner, dialog, and About */
  showDownloadProgress: boolean;
  /** Download progress (0-100), valid when status is "downloading" */
  progress: number;
  /** Whether the update dialog should be shown */
  dialogOpen: boolean;
  /** User dismissed the dialog — don't auto-show again for this version */
  dismissed: boolean;
  /** True while the app restart sequence is in progress */
  restarting: boolean;
}

export interface AutoUpdateActions {
  /**
   * Trigger an update check.
   * Pass `showResult: true` for manual checks so the dialog opens when an
   * update is found (without showing download progress until Install is used).
   */
  check: (opts?: { showResult?: boolean }) => Promise<void>;
  /** Download, safely install, and relaunch the complete app. */
  install: () => Promise<void>;
  /** Restart the app after update is installed */
  restart: () => Promise<void>;
  /** Dismiss the update dialog */
  dismiss: () => void;
  /** Re-open the update dialog */
  openDialog: () => void;
}

const DISMISSED_VERSION_KEY = "matrx-update-dismissed-version";
const STARTUP_DELAY_MS = 15_000;

export function useAutoUpdate(): [AutoUpdateState, AutoUpdateActions] {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDownloadProgress, setShowDownloadProgress] = useState(false);
  const [progress, setProgress] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const unlistenRef = useRef<(() => void) | null>(null);

  // Listen for real-time download progress events from Rust
  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;

    (async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const unlisten = await listen<UpdateStatus>("update-progress", (event) => {
          if (cancelled) return;
          const payload = event.payload;
          setStatus(payload);

          if (payload.status === "downloading" && payload.content_length) {
            const pct = Math.min(
              100,
              Math.round(((payload.downloaded ?? 0) / payload.content_length) * 100),
            );
            setProgress(pct);
          } else if (payload.status === "installed") {
            setProgress(100);
            setShowDownloadProgress(false);
          }
        });
        if (!cancelled) {
          unlistenRef.current = unlisten;
        } else {
          unlisten();
        }
      } catch {
        // event API not available
      }
    })();

    return () => {
      cancelled = true;
      unlistenRef.current?.();
      unlistenRef.current = null;
    };
  }, []);

  const check = useCallback(
    async (opts?: { showResult?: boolean }) => {
      if (!isTauri() || busy) return;
      setBusy(true);
      setShowDownloadProgress(false);
      try {
        const result = await checkForUpdates(false);

        if (result.status === "up_to_date") {
          setStatus(result);
        } else if (result.status === "available" && result.version) {
          setStatus(result);
          if (opts?.showResult) {
            setDialogOpen(true);
            setDismissed(false);
          }
        } else {
          setStatus(result);
        }
      } catch {
        // Network error or updater not available — fail silently
      } finally {
        setBusy(false);
      }
    },
    [busy],
  );

  const install = useCallback(async () => {
    if (!isTauri() || busy) return;

    if (status?.status === "installed") {
      setShowDownloadProgress(false);
      setDialogOpen(true);
      return;
    }

    if (status?.status === "downloading") {
      setShowDownloadProgress(true);
      setDialogOpen(true);
      return;
    }

    setDialogOpen(true);
    setShowDownloadProgress(true);
    setProgress(0);
    setBusy(true);
    try {
      const result = await checkForUpdates(true);
      setStatus(result);
      if (result.status === "installed") {
        setProgress(100);
        setShowDownloadProgress(false);
      }
    } catch (err) {
      console.error("[auto-update] Install failed:", err);
      setShowDownloadProgress(false);
    } finally {
      setBusy(false);
    }
  }, [busy, status?.status]);

  const restart = useCallback(async () => {
    setRestarting(true);
    // Brief delay so the UI can render the restarting state before the process exits
    await new Promise((r) => setTimeout(r, 500));
    await restartApp();
    // If restartApp doesn't terminate (e.g. dev/browser env), reset after a few seconds
    setTimeout(() => setRestarting(false), 5000);
  }, []);

  const dismiss = useCallback(() => {
    setDialogOpen(false);
    setDismissed(true);
    setShowDownloadProgress(false);
    if (status?.version) {
      localStorage.setItem(DISMISSED_VERSION_KEY, status.version);
    }
  }, [status?.version]);

  const openDialog = useCallback(() => {
    setDialogOpen(true);
    setDismissed(false);
    if (status?.status === "downloading") {
      setShowDownloadProgress(true);
    }
  }, [status?.status]);

  // Startup check + periodic polling — LEADER window only. Two windows
  // issuing the same update check is needless traffic; manual checks
  // (user-clicked) remain available from any window. Gating on
  // isLeader also handles promotion: the effect re-runs and starts polling
  // when a surviving window inherits leadership.
  const isLeader = useWindowLeader();
  useEffect(() => {
    if (!isTauri() || !isLeader) return;

    let startupTimer: ReturnType<typeof setTimeout> | null = null;

    const setupPolling = async () => {
      const settings = await loadSettings();
      if (!settings.autoCheckUpdates) return;

      startupTimer = setTimeout(() => {
        void check();

        const intervalMs = Math.max(60, settings.updateCheckInterval) * 60 * 1000;
        intervalRef.current = setInterval(() => void check(), intervalMs);
      }, STARTUP_DELAY_MS);
    };

    void setupPolling();

    return () => {
      if (startupTimer) clearTimeout(startupTimer);
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = null;
    };
  }, [isLeader]); // eslint-disable-line react-hooks/exhaustive-deps

  const state: AutoUpdateState = {
    status,
    busy,
    showDownloadProgress,
    progress,
    dialogOpen,
    dismissed,
    restarting,
  };

  // Stable actions object — see React Patterns in CLAUDE.md.
  const actions: AutoUpdateActions = useMemo(
    () => ({
      check,
      install,
      restart,
      dismiss,
      openDialog,
    }),
    [check, install, restart, dismiss, openDialog],
  );

  return [state, actions];
}
