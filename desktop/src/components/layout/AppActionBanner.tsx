import { useMemo } from "react";
import {
  AlertTriangle,
  Download,
  FolderLock,
  FolderPlus,
  RefreshCw,
  Settings,
} from "lucide-react";

import { useAccessHealthContext } from "@/contexts/AccessHealthContext";
import { useDownloadManager } from "@/contexts/DownloadManagerContext";
import { usePermissionsContext } from "@/contexts/PermissionsContext";
import { useResolutionAction } from "@/components/downloads/useResolutionAction";
import { Button } from "@/components/ui/button";
import { deriveAccessPresentation } from "@/hooks/use-access-health";
import type { EngineStatus } from "@/hooks/use-engine";

interface AppActionBannerProps {
  engineStatus: EngineStatus;
}

/**
 * Global action banner. Pure RENDERER of shared stores:
 *   - Access health comes from AccessHealthContext (the app's single poll
 *     owner, generation-fenced). This component holds no access state and
 *     runs no poll of its own — the historical dual-poller stale-response
 *     clobber cannot recur here.
 *   - Copy is evidence-based via deriveAccessPresentation: the definitive
 *     "Full Disk Access" claim renders only on a positive engine diagnosis.
 */
export function AppActionBanner(_props: AppActionBannerProps) {
  const { downloads, openModal } = useDownloadManager();
  const { openSettings } = usePermissionsContext();
  const dispatchDownloadAction = useResolutionAction();
  const access = useAccessHealthContext();

  const actionNeededDownloads = useMemo(
    () => downloads.filter((d) => d.status === "failed" && d.resolution != null),
    [downloads],
  );
  const firstDownloadResolution = actionNeededDownloads[0]?.resolution ?? null;

  // Show the most relevant degraded resource: canonical notes first, then
  // any mapped dir / replica — each with copy naming ITS actual path.
  const worst =
    access.degradedResources.find((r) => r.resource_id === "notes-canonical") ??
    access.degradedResources[0] ??
    null;
  const presentation =
    worst && access.health
      ? deriveAccessPresentation(worst, access.health, access.parentFdaProbe)
      : null;

  const hasDownloadActions = actionNeededDownloads.length > 0;
  if (!presentation && !hasDownloadActions) return null;

  const checking = access.checking;

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
              {presentation && (
                <p className="flex min-w-0 items-center gap-1.5">
                  {presentation.primaryAction === "create_folder" ? (
                    <FolderPlus className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <FolderLock className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="truncate">{presentation.body}</span>
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
          {presentation?.showFdaAction && (
            <Button
              size="sm"
              className="h-7 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
              onClick={() => void openSettings("full_disk_access")}
            >
              <Settings className="mr-1.5 h-3.5 w-3.5" />
              Open System Settings
            </Button>
          )}
          {presentation?.primaryAction === "create_folder" && worst && (
            <Button
              size="sm"
              className="h-7 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
              disabled={checking}
              onClick={() =>
                void access.actions.recheck({
                  resourceIds: [worst.resource_id],
                  createMissing: true,
                })
              }
            >
              <FolderPlus className="mr-1.5 h-3.5 w-3.5" />
              Create folder
            </Button>
          )}
          {presentation && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 border-amber-300 bg-background/70 px-2.5 text-xs dark:border-amber-800"
              disabled={checking}
              onClick={() => void access.actions.recheck()}
            >
              <RefreshCw
                className={`mr-1.5 h-3.5 w-3.5 ${checking ? "animate-spin" : ""}`}
              />
              Check again
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
