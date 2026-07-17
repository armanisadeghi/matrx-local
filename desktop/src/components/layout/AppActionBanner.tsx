import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Download,
  FolderLock,
  FolderPlus,
  RefreshCw,
  Settings,
} from "lucide-react";

import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { usePermissionsContext } from "@/contexts/PermissionsContext";
import { useResolutionAction } from "@/components/downloads/useResolutionAction";
import { Button } from "@/components/ui/button";
import { engine, type NotesAccessStatus } from "@/lib/api";
import type { EngineStatus } from "@/hooks/use-engine";

interface AppActionBannerProps {
  engineStatus: EngineStatus;
}

const NOTES_ACCESS_POLL_MS = 15_000;

type NotesAccessProbe = Pick<typeof engine, "recheckNotesAccess">;

/**
 * Return a filesystem-verified notes access state.
 *
 * The global banner must never use getNotesAccess() here: that endpoint only
 * returns the engine's cached guard. Using it made both the poll and the
 * "Check again" button repeat a stale denial forever even after the engine
 * could read the folder. The Documents page already uses this active probe;
 * keep the app-wide prompt on the same contract.
 */
export function getFreshNotesAccess(
  notesApi: NotesAccessProbe,
  opts?: { createDir?: boolean },
): Promise<NotesAccessStatus> {
  return notesApi.recheckNotesAccess(
    opts?.createDir ? { createDir: true } : undefined,
  );
}

export function AppActionBanner({ engineStatus }: AppActionBannerProps) {
  const { downloads, openModal } = useDownloadManager();
  const { openSettings } = usePermissionsContext();
  const dispatchDownloadAction = useResolutionAction();
  const [notesAccess, setNotesAccess] = useState<NotesAccessStatus | null>(null);
  const [checkingNotes, setCheckingNotes] = useState(false);

  const actionNeededDownloads = useMemo(
    () => downloads.filter((d) => d.status === "failed" && d.resolution != null),
    [downloads],
  );
  const firstDownloadResolution = actionNeededDownloads[0]?.resolution ?? null;

  const refreshNotesAccess = useCallback(
    async (opts?: { createDir?: boolean }) => {
      if (engineStatus !== "connected") {
        setNotesAccess(null);
        return;
      }
      setCheckingNotes(true);
      try {
        const next = await getFreshNotesAccess(engine, opts);
        setNotesAccess(next);
      } catch {
        // Older engines or transient outages should not replace the real
        // problem with banner noise. The feature page still handles its own
        // degraded state when available.
      } finally {
        setCheckingNotes(false);
      }
    },
    [engineStatus],
  );

  useEffect(() => {
    if (engineStatus !== "connected") {
      setNotesAccess(null);
      return;
    }
    void refreshNotesAccess();
    const id = window.setInterval(() => void refreshNotesAccess(), NOTES_ACCESS_POLL_MS);
    return () => window.clearInterval(id);
  }, [engineStatus, refreshNotesAccess]);

  const notesNeedsAction = notesAccess?.degraded === true;
  const hasDownloadActions = actionNeededDownloads.length > 0;

  if (!notesNeedsAction && !hasDownloadActions) return null;

  const isMacNotesPermission =
    notesAccess?.platform === "darwin" && notesAccess.kind === "permission";
  const isMissingNotesDir = notesAccess?.kind === "missing_dir";

  return (
    <div className="border-b border-amber-300/60 bg-amber-50/95 px-4 py-2 text-amber-950 shadow-sm dark:border-amber-800/50 dark:bg-amber-950/35 dark:text-amber-100">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 text-sm">
            <p className="font-medium">
              Matrx needs your help to finish setup.
            </p>
            <div className="mt-0.5 flex flex-col gap-1 text-xs text-amber-900/80 dark:text-amber-100/75">
              {hasDownloadActions && (
                <p className="flex min-w-0 items-center gap-1.5">
                  <Download className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">
                    {actionNeededDownloads.length === 1
                      ? firstDownloadResolution?.title
                      : `${actionNeededDownloads.length} downloads need approval, access, or setup.`}
                  </span>
                </p>
              )}
              {notesNeedsAction && (
                <p className="flex min-w-0 items-center gap-1.5">
                  {isMissingNotesDir ? (
                    <FolderPlus className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <FolderLock className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="truncate">
                    {isMacNotesPermission
                      ? "macOS is blocking the notes folder. Grant Full Disk Access so notes can sync."
                      : notesAccess?.reason ?? "The notes folder needs attention before notes can sync."}
                  </span>
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2 lg:justify-end">
          {hasDownloadActions && firstDownloadResolution && (
            <Button
              size="sm"
              className="h-7 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
              onClick={() => void dispatchDownloadAction(firstDownloadResolution)}
            >
              {firstDownloadResolution.action_label}
            </Button>
          )}
          {hasDownloadActions && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 border-amber-300 bg-background/70 px-2.5 text-xs dark:border-amber-800"
              onClick={openModal}
            >
              Open downloads
            </Button>
          )}
          {isMacNotesPermission && (
            <Button
              size="sm"
              className="h-7 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
              onClick={() => void openSettings("full_disk_access")}
            >
              <Settings className="mr-1.5 h-3.5 w-3.5" />
              Open System Settings
            </Button>
          )}
          {isMissingNotesDir && (
            <Button
              size="sm"
              className="h-7 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
              disabled={checkingNotes}
              onClick={() => void refreshNotesAccess({ createDir: true })}
            >
              <FolderPlus className="mr-1.5 h-3.5 w-3.5" />
              Create notes folder
            </Button>
          )}
          {notesNeedsAction && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 border-amber-300 bg-background/70 px-2.5 text-xs dark:border-amber-800"
              disabled={checkingNotes}
              onClick={() => void refreshNotesAccess()}
            >
              <RefreshCw
                className={`mr-1.5 h-3.5 w-3.5 ${checkingNotes ? "animate-spin" : ""}`}
              />
              Check again
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
