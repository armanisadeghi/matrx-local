/**
 * Persistent but non-disruptive update notification banner.
 *
 * Appears when an update is available or an older install is already waiting
 * for restart. New updates never mutate the bundle in the background: Install
 * downloads, stops the frozen engine, applies the update, and relaunches.
 *
 * Two different things are on screen here, and only one of them is a
 * notification. "Update available" / "Downloading" are notifications: the user
 * may dismiss them. "A newer build is installed and you are still running the
 * old one" is a STATE — derived from the bundle on disk, not from an in-memory
 * updater status — so it cannot be dismissed, it comes back in every window and
 * after every renderer reload, and it carries the one-click restart. That state
 * silently evaporating is what left About saying "No updates available" on
 * 2026-09-17 while 1.4.147 sat on disk and 1.4.145 kept running.
 */

import { useEffect, useRef, useState } from "react";
import { Button, Progress } from "@ai-matrx/design-system";
import { ArrowUpCircle, Download, RefreshCw, X, Loader2 } from "lucide-react";
import type { AutoUpdateState, AutoUpdateActions } from "@/hooks/use-auto-update";
import { APP_VERSION } from "@/lib/app-version";
import { useVersionStateOrNull } from "@/contexts/VersionStateContext";

// THE package byte-size formatter (`@ai-matrx/kit/format`, duplication
// census H1 2026-09-07). This repo alone carried THIRTEEN `formatBytes`
// bodies with twelve different roundings and five different words for
// "unknown" — the clearest case in the fleet for one owner.
import { formatFileSize } from "@ai-matrx/kit/format";
interface UpdateBannerProps {
  state: AutoUpdateState;
  actions: AutoUpdateActions;
}

export function UpdateBanner({ state, actions }: UpdateBannerProps) {
  const { status, busy, showDownloadProgress, progress, restarting } = state;
  const [visible, setVisible] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const dismissedVersionRef = useRef<string | null>(null);
  const versions = useVersionStateOrNull();

  // The derived truth wins over the ephemeral status: the bundle on disk is
  // ahead of this process whether or not this renderer watched it happen.
  // EITHER signal is enough, and neither may mask the other: the derived state
  // is the durable one, the updater status is the instant one, and outside the
  // provider there is no derived state at all.
  const isInstalled =
    (versions?.restartRequired ?? false) || status?.status === "installed";
  const pendingVersion = versions?.pendingVersion ?? status?.version ?? null;
  const isDownloadingUi = showDownloadProgress && status?.status === "downloading";
  // BYTES. The Tauri updater's `content_length` IS the HTTP Content-Length of
  // the artifact; the byte-size formatter is right here and the local name
  // says so, because `*_length` almost everywhere else in this fleet is a
  // CHARACTER count (see the note on UpdateStatus in lib/sidecar.ts).
  const totalBytes = status?.content_length;
  const showAsAvailable =
    status?.status === "available" ||
    (status?.status === "downloading" && !showDownloadProgress);

  useEffect(() => {
    // A pending restart outranks everything, including a dismissal and an
    // "up to date" verdict that is only true of the disk.
    if (isInstalled) {
      setVisible(true);
      setDismissed(false);
      return;
    }

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

  // The restart state renders on the FIRST paint, with no effect in between:
  // it is derived from the bundle on disk, and a state that has to wait for an
  // effect is a state that a fast reload can skip.
  if (!isInstalled) {
    if (!visible || dismissed) return null;
    if (!showAsAvailable && !isDownloadingUi) return null;
  }

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
              ? (versions?.restartHeadline ??
                "Update installed — restart AI Matrx to finish")
              : isDownloadingUi
                ? "Downloading update…"
                : "Update available"}
          </p>
          {(pendingVersion ?? status?.version) && (
            <p className="text-xs text-muted-foreground mt-0.5">
              {isInstalled
                ? `v${(pendingVersion ?? "").replace(/^v/i, "")} is installed on disk — this window still runs ${APP_VERSION}`
                : isDownloadingUi
                  ? `v${status?.version}`
                  : `${APP_VERSION} → v${status?.version}`}
            </p>
          )}
          {showAsAvailable && !isDownloadingUi && !isInstalled && (
            <p className="text-xs text-muted-foreground mt-1">
              Choose Install when you&apos;re ready. AI Matrx will download the update and restart safely.
            </p>
          )}
        </div>

        {!isDownloadingUi && !isInstalled && (
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
                {formatFileSize(status.downloaded)} /{" "}
                {formatFileSize(totalBytes)}
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
