/**
 * Update notification dialog.
 *
 * Download progress appears only after the user chooses Install (or opens the
 * dialog while a user-visible download is already in progress).
 */

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Download, RefreshCw, Loader2, ArrowUpCircle, X, CheckCircle2 } from "lucide-react";
import type { AutoUpdateState, AutoUpdateActions } from "@/hooks/use-auto-update";
import { APP_VERSION } from "@/lib/app-version";

interface UpdateDialogProps {
  state: AutoUpdateState;
  actions: AutoUpdateActions;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UpdateDialog({ state, actions }: UpdateDialogProps) {
  const { status, busy, showDownloadProgress, progress, dialogOpen, restarting } = state;
  const isDownloadingUi = showDownloadProgress && status?.status === "downloading";
  const isInstalled = status?.status === "installed";
  const showAsAvailable =
    status?.status === "available" ||
    (status?.status === "downloading" && !showDownloadProgress);

  const titleText = isInstalled
    ? "Ready to Install"
    : isDownloadingUi
      ? "Downloading Update"
      : "Update Available";

  const descriptionText = isInstalled
    ? "The update has been downloaded. Restart to apply the new version."
    : isDownloadingUi
      ? "Downloading the update…"
      : "A new version of AI Matrx is available. If you already checked for updates, the download may be running in the background — tap Install to see progress or finish setup.";

  return (
    <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open && !restarting) actions.dismiss(); }}>
      <DialogContent className="flex max-h-[85vh] max-w-md flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 px-6 pt-6 pb-4">
          <DialogTitle className="flex items-center gap-2">
            {isInstalled ? (
              <CheckCircle2 className="h-5 w-5 text-green-500" />
            ) : (
              <ArrowUpCircle className="h-5 w-5 text-primary" />
            )}
            {titleText}
          </DialogTitle>
          <DialogDescription>{descriptionText}</DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-2">
          <div className="space-y-4 py-2">
            {status?.version && (
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">Current</span>
                  <Badge variant="secondary">{APP_VERSION}</Badge>
                </div>
                <span className="text-muted-foreground">→</span>
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground">New</span>
                  <Badge variant="default">v{status.version}</Badge>
                </div>
              </div>
            )}

            {status?.body && (showAsAvailable || isDownloadingUi) && (
              <div className="rounded-lg bg-muted/50 p-3 max-h-40 overflow-y-auto">
                <p className="text-xs font-medium text-muted-foreground mb-1">Release Notes</p>
                <p className="text-sm whitespace-pre-wrap">{status.body}</p>
              </div>
            )}

            {isDownloadingUi && (
              <div className="space-y-2">
                <Progress value={progress} />
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>{progress}%</span>
                  {status?.content_length && (
                    <span>
                      {formatBytes(status.downloaded ?? 0)} / {formatBytes(status.content_length)}
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="shrink-0 px-6 py-4 border-t">
          {isInstalled ? (
            <Button onClick={() => void actions.restart()} disabled={restarting} className="gap-2">
              {restarting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              {restarting ? "Restarting…" : "Restart Now"}
            </Button>
          ) : isDownloadingUi ? (
            <>
              <Button variant="ghost" onClick={actions.dismiss} className="gap-2">
                <X className="h-4 w-4" />
                Later
              </Button>
              <Button disabled className="gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Downloading…
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={actions.dismiss} className="gap-2">
                <X className="h-4 w-4" />
                Later
              </Button>
              <Button onClick={() => void actions.install()} disabled={busy} className="gap-2">
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                Download & Install
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
