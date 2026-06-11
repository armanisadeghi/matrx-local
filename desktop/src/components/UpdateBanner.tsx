/**
 * Persistent but non-disruptive update notification banner.
 *
 * Appears when an update is available or ready to restart. Background downloads
 * do not show a progress bar here until the user taps Install / View progress.
 */

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ArrowUpCircle, Download, RefreshCw, X, Loader2 } from "lucide-react";
import type { AutoUpdateState, AutoUpdateActions } from "@/hooks/use-auto-update";

declare const __APP_VERSION__: string;

interface UpdateBannerProps {
  state: AutoUpdateState;
  actions: AutoUpdateActions;
}

export function UpdateBanner({ state, actions }: UpdateBannerProps) {
  const { status, busy, showDownloadProgress, progress, restarting } = state;
  const [visible, setVisible] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const dismissedVersionRef = useRef<string | null>(null);

  const isInstalled = status?.status === "installed";
  const isDownloadingUi = showDownloadProgress && status?.status === "downloading";
  const showAsAvailable =
    status?.status === "available" ||
    (status?.status === "downloading" && !showDownloadProgress);

  useEffect(() => {
    if (status?.status === "up_to_date") {
      setVisible(false);
      return;
    }

    if (!status) return;

    if (isInstalled || isDownloadingUi || showAsAvailable) {
      if (
        (showAsAvailable || isDownloadingUi) &&
        status.version &&
        dismissedVersionRef.current === status.version
      ) {
        return;
      }
      setVisible(true);
      setDismissed(false);
    }
  }, [status, isInstalled, isDownloadingUi, showAsAvailable]);

  const handleDismiss = () => {
    setVisible(false);
    setDismissed(true);
    if (status?.version) {
      dismissedVersionRef.current = status.version;
    }
    actions.dismiss();
  };

  const handleInstall = () => {
    void actions.install();
  };

  const handleViewDetails = () => {
    actions.openDialog();
  };

  if (!visible || dismissed) return null;
  if (!showAsAvailable && !isDownloadingUi && !isInstalled) return null;

  return (
    <div
      className={[
        "fixed bottom-4 right-4 z-50 w-80",
        "rounded-xl border bg-card/95 backdrop-blur-sm shadow-xl",
        "animate-in slide-in-from-bottom-4 fade-in duration-300",
      ].join(" ")}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-3 p-4 pb-3">
        <div className="mt-0.5 shrink-0">
          {isInstalled ? (
            <RefreshCw className="h-4 w-4 text-green-500" />
          ) : isDownloadingUi ? (
            <Loader2 className="h-4 w-4 text-primary animate-spin" />
          ) : (
            <ArrowUpCircle className="h-4 w-4 text-primary" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold leading-tight">
            {isInstalled
              ? "Update ready to install"
              : isDownloadingUi
                ? "Downloading update…"
                : "Update available"}
          </p>
          {status?.version && (
            <p className="text-xs text-muted-foreground mt-0.5">
              {isInstalled
                ? `v${status.version} — restart to apply`
                : isDownloadingUi
                  ? `v${status.version}`
                  : `${__APP_VERSION__} → v${status.version}`}
            </p>
          )}
          {showAsAvailable && !isDownloadingUi && !isInstalled && (
            <p className="text-xs text-muted-foreground mt-1">
              Download runs in the background — choose Install when you&apos;re ready.
            </p>
          )}
        </div>

        {!isDownloadingUi && (
          <button
            onClick={handleDismiss}
            className="shrink-0 rounded-sm p-0.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            aria-label="Dismiss update notification"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {isDownloadingUi && (
        <div className="px-4 pb-3 space-y-1">
          <Progress value={progress} className="h-1.5" />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>{progress}%</span>
            {status?.content_length && status?.downloaded != null && (
              <span>
                {formatBytes(status.downloaded)} / {formatBytes(status.content_length)}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 px-4 pb-4">
        {isInstalled ? (
          <Button
            size="sm"
            onClick={() => void actions.restart()}
            disabled={restarting}
            className="flex-1 gap-1.5 h-8 text-xs"
          >
            {restarting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {restarting ? "Restarting…" : "Restart Now"}
          </Button>
        ) : isDownloadingUi ? (
          <Button
            size="sm"
            variant="outline"
            onClick={handleViewDetails}
            className="flex-1 h-8 text-xs"
          >
            View Progress
          </Button>
        ) : (
          <>
            <Button
              size="sm"
              onClick={handleInstall}
              disabled={busy}
              className="flex-1 gap-1.5 h-8 text-xs"
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              Install
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={handleViewDetails}
              className="h-8 text-xs"
            >
              Details
            </Button>
            <button
              onClick={() => void actions.check({ showResult: true })}
              disabled={busy}
              className="shrink-0 rounded-sm p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
              aria-label="Check for newer version"
              title="Check for newer version"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
