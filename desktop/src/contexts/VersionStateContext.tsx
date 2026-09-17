/**
 * VersionStateContext — ONE owner for "which build is which".
 *
 * Holds the only poll of the on-disk bundle version and hands every surface the
 * derived state from `lib/version-facts.ts`. Mounted above the whole app —
 * above the startup screen, the first-run screen and the login screen — so an
 * ambient version chip is honest before the engine is even up. That is why the
 * engine and updater facts arrive through `useVersionFactReporter` rather than
 * as props: the surfaces that know them render *inside* this provider.
 *
 * `restartRequired` here is a DERIVED TRUTH, not a notification: it comes from
 * comparing the running build to the bundle on disk, so it is true again after
 * a dismissal, a renderer reload, or in a second window. The updater's own
 * in-memory "installed" status is folded in as a second signal, never the only
 * one — that status is what vanished on 2026-09-17 and left About claiming
 * "No updates available" while a newer build sat on disk.
 *
 * Read-only observation of the bundle: one `installed_app_version` call, no
 * writes, nothing that can disturb a running app.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { APP_VERSION } from "@/lib/app-version-constant";
import { getInstalledAppVersion, isTauri, type UpdateStatus } from "@/lib/sidecar";
import { deriveVersionState, type VersionState } from "@/lib/version-facts";

export interface VersionStateContextValue extends VersionState {
  /** Re-read the bundle on disk now (after an install, or on window focus). */
  refresh: () => Promise<void>;
}

/** What a surface that holds engine/updater facts reports upward. */
export interface VersionFactReport {
  engineVersion?: string | null;
  updateStatus?: UpdateStatus | null;
}

const VersionStateContext = createContext<VersionStateContextValue | null>(null);
const VersionFactReporterContext = createContext<
  ((report: VersionFactReport) => void) | null
>(null);

export function VersionStateProvider({ children }: { children: ReactNode }) {
  const [installedOnDisk, setInstalledOnDisk] = useState<string | null>(null);
  const [engineVersion, setEngineVersion] = useState<string | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null);

  const refresh = useCallback(async () => {
    if (!isTauri()) return;
    setInstalledOnDisk(await getInstalledAppVersion());
  }, []);

  // Initial read + re-read whenever the window regains focus. An update lands
  // while the app is in the background far more often than in the foreground
  // (measured: 03:30), and focus is the moment the user is about to read a
  // version off the screen. Externally-changed data is exactly what a focus
  // handler is for (React patterns, rule 14).
  useEffect(() => {
    if (!isTauri()) return;
    let cancelled = false;

    const read = async () => {
      const disk = await getInstalledAppVersion();
      if (!cancelled) setInstalledOnDisk(disk);
    };

    void read();
    const onFocus = () => void read();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  // The instant the updater finishes writing the new bundle the disk fact has
  // changed — read it back rather than waiting for the next focus, so the loud
  // restart state appears at the moment it becomes true.
  useEffect(() => {
    if (updateStatus?.status === "installed") void refresh();
  }, [updateStatus?.status, refresh]);

  const report = useCallback((next: VersionFactReport) => {
    if ("engineVersion" in next) setEngineVersion(next.engineVersion ?? null);
    if ("updateStatus" in next) setUpdateStatus(next.updateStatus ?? null);
  }, []);

  const value = useMemo<VersionStateContextValue>(
    () => ({
      ...deriveVersionState({
        runningDesktop: APP_VERSION,
        runningEngine: engineVersion,
        installedOnDisk,
        latestAvailable: updateStatus?.version ?? null,
        updaterStatus: updateStatus?.status ?? null,
      }),
      refresh,
    }),
    [engineVersion, installedOnDisk, updateStatus, refresh],
  );

  return (
    <VersionFactReporterContext.Provider value={report}>
      <VersionStateContext.Provider value={value}>
        {children}
      </VersionStateContext.Provider>
    </VersionFactReporterContext.Provider>
  );
}

/**
 * Report the engine `/health` version and the updater's verdict into the one
 * version owner. Called by the app shell, which is where both already live.
 */
export function useVersionFactReporter(report: VersionFactReport): void {
  const push = useContext(VersionFactReporterContext);
  const engineVersion = report.engineVersion ?? null;
  const updateStatus = report.updateStatus ?? null;
  useEffect(() => {
    push?.({ engineVersion, updateStatus });
  }, [push, engineVersion, updateStatus]);
}

/**
 * The version state, or `null` outside the provider.
 *
 * Nullable on purpose: `AppVersion` renders in panel windows and error
 * boundaries that sit outside the provider, and there it must show the running
 * build with no claim about anything else — an absent fact is never dressed up
 * as agreement.
 */
export function useVersionStateOrNull(): VersionStateContextValue | null {
  return useContext(VersionStateContext);
}

export function useVersionState(): VersionStateContextValue {
  const ctx = useContext(VersionStateContext);
  if (!ctx) {
    throw new Error("useVersionState must be used inside <VersionStateProvider>");
  }
  return ctx;
}
