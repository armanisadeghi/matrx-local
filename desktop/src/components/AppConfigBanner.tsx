/**
 * AppConfigBanner — the remote app-config surface: version gate + operator
 * notice.
 *
 * Two independent strips driven by GET /health `app_config` (see
 * app/services/app_config/FEATURE.md and the cross-repo spec at
 * common-docs/app-config/FEATURE.md, matrx-local item 6):
 *
 * 1. Update required — persistent (not dismissable) while the installed
 *    version is below `min_supported_app_version`. A STATE with a prompt,
 *    not an error: the app keeps working; the action routes into the
 *    existing auto-update flow (use-auto-update).
 * 2. Operator notice — shown once per notice content (deduped by a hash of
 *    level|title|body persisted in localStorage), styled by level, with an
 *    optional "Learn more" link.
 *
 * Rendered by AppLayout next to EngineDownBanner / AppActionBanner.
 */

import { useState } from "react";
import {
  AlertTriangle,
  ArrowUpCircle,
  Download,
  ExternalLink,
  Info,
  Loader2,
  OctagonAlert,
  RefreshCw,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { openExternalUrl } from "@/components/media-gen/shared";
import type { AppConfigNotice, AppConfigStatus } from "@/lib/api";
import type { AutoUpdateState, AutoUpdateActions } from "@/hooks/use-auto-update";

const NOTICE_DISMISSED_KEY = "matrx-app-config-notice-dismissed";

/** Stable content hash (djb2) so a re-published notice with new text shows again. */
export function noticeHash(notice: AppConfigNotice): string {
  const content = `${notice.level}|${notice.title}|${notice.body}`;
  let hash = 5381;
  for (let i = 0; i < content.length; i++) {
    hash = ((hash << 5) + hash + content.charCodeAt(i)) | 0;
  }
  return hash.toString(36);
}

function readDismissedHash(): string | null {
  try {
    return localStorage.getItem(NOTICE_DISMISSED_KEY);
  } catch {
    return null;
  }
}

function writeDismissedHash(hash: string): void {
  try {
    localStorage.setItem(NOTICE_DISMISSED_KEY, hash);
  } catch {
    // Storage unavailable — the notice will show again next launch. Fine.
  }
}

const NOTICE_STYLES: Record<
  AppConfigNotice["level"],
  { container: string; icon: string; Icon: typeof Info }
> = {
  info: {
    container:
      "border-blue-300/60 bg-blue-50/95 text-blue-950 dark:border-blue-800/50 dark:bg-blue-950/35 dark:text-blue-100",
    icon: "text-blue-600 dark:text-blue-400",
    Icon: Info,
  },
  warning: {
    container:
      "border-amber-300/60 bg-amber-50/95 text-amber-950 dark:border-amber-800/50 dark:bg-amber-950/35 dark:text-amber-100",
    icon: "text-amber-600 dark:text-amber-400",
    Icon: AlertTriangle,
  },
  critical: {
    container: "border-destructive/30 bg-destructive/10 text-foreground",
    icon: "text-destructive",
    Icon: OctagonAlert,
  },
};

interface AppConfigBannerProps {
  appConfig: AppConfigStatus | null;
  updateState: AutoUpdateState;
  updateActions: AutoUpdateActions;
}

export function AppConfigBanner({
  appConfig,
  updateState,
  updateActions,
}: AppConfigBannerProps) {
  // Local echo of the persisted dismissal so dismissing re-renders immediately.
  const [dismissedHash, setDismissedHash] = useState<string | null>(readDismissedHash);

  if (!appConfig) return null;

  const notice = appConfig.notice;
  const hash = notice ? noticeHash(notice) : null;
  const showNotice = notice != null && hash !== dismissedHash;
  const showUpdateRequired = appConfig.update_required;

  if (!showNotice && !showUpdateRequired) return null;

  const dismissNotice = () => {
    if (!hash) return;
    writeDismissedHash(hash);
    setDismissedHash(hash);
  };

  return (
    <>
      {showUpdateRequired && (
        <UpdateRequiredStrip state={updateState} actions={updateActions} />
      )}
      {showNotice && notice && (
        <NoticeStrip notice={notice} onDismiss={dismissNotice} />
      )}
    </>
  );
}

function UpdateRequiredStrip({
  state,
  actions,
}: {
  state: AutoUpdateState;
  actions: AutoUpdateActions;
}) {
  const status = state.status?.status;
  const installed = status === "installed";
  const available = status === "available";
  const downloading = status === "downloading";

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex shrink-0 items-center gap-3 border-b border-amber-300/60 bg-amber-50/95 px-4 py-2 text-sm text-amber-950 dark:border-amber-800/50 dark:bg-amber-950/35 dark:text-amber-100"
    >
      <ArrowUpCircle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
      <div className="min-w-0 flex-1">
        <span className="font-medium">Update required.</span>{" "}
        <span className="text-amber-900/80 dark:text-amber-100/75">
          This version of Matrx Local is below the minimum supported version —
          please update to keep everything working with the platform.
        </span>
      </div>
      {installed ? (
        <Button
          size="sm"
          className="h-7 shrink-0 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
          disabled={state.restarting}
          onClick={() => void actions.restart()}
        >
          {state.restarting ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          {state.restarting ? "Restarting…" : "Restart to apply"}
        </Button>
      ) : downloading ? (
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 border-amber-300 bg-background/70 px-2.5 text-xs dark:border-amber-800"
          onClick={actions.openDialog}
        >
          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          Downloading — view progress
        </Button>
      ) : available ? (
        <Button
          size="sm"
          className="h-7 shrink-0 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
          disabled={state.busy}
          onClick={() => void actions.install()}
        >
          {state.busy ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="mr-1.5 h-3.5 w-3.5" />
          )}
          Install update
        </Button>
      ) : (
        <Button
          size="sm"
          className="h-7 shrink-0 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
          disabled={state.busy}
          onClick={() => void actions.check({ showResult: true })}
        >
          {state.busy ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          Check for update
        </Button>
      )}
    </div>
  );
}

function NoticeStrip({
  notice,
  onDismiss,
}: {
  notice: AppConfigNotice;
  onDismiss: () => void;
}) {
  const style = NOTICE_STYLES[notice.level];
  const { Icon } = style;
  const url = notice.url;

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex shrink-0 items-start gap-3 border-b px-4 py-2 text-sm ${style.container}`}
    >
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${style.icon}`} />
      <div className="min-w-0 flex-1">
        <span className="font-medium">{notice.title}</span>{" "}
        <span className="opacity-80">{notice.body}</span>
      </div>
      {url && (
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 bg-background/70 px-2.5 text-xs"
          onClick={() => void openExternalUrl(url)}
        >
          <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
          Learn more
        </Button>
      )}
      <button
        onClick={onDismiss}
        className="shrink-0 rounded-sm p-0.5 opacity-70 transition-opacity hover:opacity-100"
        aria-label="Dismiss notice"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
