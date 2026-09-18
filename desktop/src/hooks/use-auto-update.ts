/**
 * Auto-update hook for the Tauri desktop app.
 *
 * Flow:
 *   1. After startup delay, silently check for updates (no dialog).
 *   2. If an update is available, begin downloading in the background immediately.
 *      Progress is tracked internally but NOT shown until the user opens the
 *      install flow (dialog / banner install / About "Install Update").
 *   3. When a background download completes, status becomes "prepared" —
 *      the banner can offer "Restart" without the user ever seeing a progress bar.
 *   4. The Rust backend holds the verified staged artifact. Renderer storage
 *      never claims an update is ready.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { isTauri, checkForUpdates, restartForUpdate, type UpdateStatus } from "@/lib/sidecar";
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
  /**
   * Open the install/restart dialog, surfacing download progress if a download is active.
   */
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
  const preDownloadInProgressRef = useRef(false);

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
          } else if (payload.status === "prepared") {
            setProgress(100);
            setShowDownloadProgress(false);
            preDownloadInProgressRef.current = false;
          }
        });
        if (!cancelled) {
          unlistenRef.current = unlisten;
        } else {
          unlisten();
        }
      } catch (error) {
        console.error("[auto-update] Progress listener unavailable:", error);
      }
    })();

    return () => {
      cancelled = true;
      unlistenRef.current?.();
      unlistenRef.current = null;
    };
  }, []);

  const startSilentPreDownload = useCallback(async (forVersion: string) => {
    if (!isTauri() || preDownloadInProgressRef.current) return;

    preDownloadInProgressRef.current = true;
    setProgress(0);
    try {
      const result = await checkForUpdates(true);
      setStatus(result);
      if (result.status === "prepared") {
        setProgress(100);
        setShowDownloadProgress(false);
      }
    } catch (err) {
      console.error("[auto-update] Background pre-download failed:", err);
      setStatus({ status: "error", version: forVersion, body: "The update could not be prepared. Retry the download." });
    } finally {
      preDownloadInProgressRef.current = false;
    }
  }, []);

  const check = useCallback(
    async (opts?: { showResult?: boolean }) => {
      if (!isTauri() || busy) return;
      setBusy(true);
      setShowDownloadProgress(false);
      try {
        const result = await checkForUpdates(false);

        if (result.status === "up_to_date" || result.status === "prepared") {
          setStatus(result);
          if (result.status === "prepared") setProgress(100);
        } else if (result.status === "available" && result.version) {
          setStatus(result);
          if (opts?.showResult) {
            setDialogOpen(true);
            setDismissed(false);
          }
          void startSilentPreDownload(result.version);
        } else {
          setStatus(result);
        }
      } catch (error) {
        console.error("[auto-update] Update check failed:", error);
        setStatus({ status: "error", body: "Update check failed. Retry later." });
        if (opts?.showResult) setDialogOpen(true);
      } finally {
        setBusy(false);
      }
    },
    [busy, startSilentPreDownload],
  );

  const install = useCallback(async () => {
    if (!isTauri()) return;

    if (status?.status === "prepared") {
      setShowDownloadProgress(false);
      setDialogOpen(true);
      return;
    }

    if (preDownloadInProgressRef.current || status?.status === "downloading") {
      setShowDownloadProgress(true);
      setDialogOpen(true);
      return;
    }

    setDialogOpen(true);
    setShowDownloadProgress(true);
    preDownloadInProgressRef.current = true;
    setProgress(0);
    try {
      const result = await checkForUpdates(true);
      setStatus(result);
      if (result.status === "prepared") {
        setProgress(100);
        setShowDownloadProgress(false);
      }
    } catch (err) {
      console.error("[auto-update] Install failed:", err);
      setStatus({
        status: "error",
        ...(status?.version ? { version: status.version } : {}),
        body: "The update could not be prepared. Retry the download.",
      });
    } finally {
      preDownloadInProgressRef.current = false;
    }
  }, [status?.status]);

  const restart = useCallback(async () => {
    setRestarting(true);
    // Brief delay so the UI can render the restarting state before the process exits
    await new Promise((r) => setTimeout(r, 500));
    try {
      await restartForUpdate();
    } catch (error) {
      console.error("[auto-update] Applying prepared update failed:", error);
      setStatus({
        status: "error",
        ...(status?.version ? { version: status.version } : {}),
        body: "The update could not be installed. The current app was restored; retry the download.",
      });
      setRestarting(false);
      return;
    }
    // If restartApp doesn't terminate (e.g. dev/browser env), reset after a few seconds
    setTimeout(() => setRestarting(false), 5000);
  }, [status?.version]);

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
    if (status?.status === "downloading" || preDownloadInProgressRef.current) {
      setShowDownloadProgress(true);
    }
  }, [status?.status]);

  // Startup check + periodic polling — LEADER window only. Two windows
  // silently pre-downloading the same update is a corruption risk; manual
  // checks (user-clicked) remain available from any window. Gating on
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
