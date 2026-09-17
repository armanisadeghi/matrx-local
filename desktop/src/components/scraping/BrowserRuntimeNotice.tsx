/**
 * BrowserRuntimeNotice — plain-language state + the one-click fix for a
 * missing built-in browser.
 *
 * Renders nothing when the browser is available, so a healthy install sees no
 * change at all. State and install live in BrowserRuntimeContext.
 */

import { AlertCircle, Chrome, Loader2 } from "lucide-react";

import { Button, Progress } from "@ai-matrx/design-system";
import { useOptionalBrowserRuntimeContext } from "@/contexts/BrowserRuntimeContext";

export function BrowserRuntimeNotice() {
  const runtime = useOptionalBrowserRuntimeContext();
  if (!runtime || !runtime.loaded) return null;

  const { status, installing, percent, message, error, statusFetchFailed, actions } = runtime;
  const sizeHint = status?.download_size_hint ?? "~90 MB";
  // The engine failed one pool launch and has already scheduled the retry. The
  // browser IS installed, so "isn't installed yet" would be a lie and an
  // Install button would be a dead control — say what is actually happening and
  // ask for nothing (the engine clears this on its own, either way).
  const starting = status?.code === "browser_starting";
  const launchFailed = status?.code === "browser_launch_failed";
  const buildMismatch = status?.code === "browser_build_mismatch";
  const installFailed = status?.code === "browser_install_failed";
  const actionLabel = status?.action_needed?.action.label;

  if (statusFetchFailed) {
    return (
      <div
        className="rounded-lg border border-amber-300/70 bg-amber-50/90 p-3 text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/35 dark:text-amber-100"
        role="alert"
        data-testid="browser-runtime-status-unavailable"
      >
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">Couldn’t refresh the built-in browser status</p>
            <p className="mt-0.5 text-xs text-amber-900/80 dark:text-amber-100/75">
              {status ? "The previous browser status may be out of date." : "Browser availability is unknown."}
            </p>
          </div>
          <Button
            size="sm"
            onClick={() => void actions.refresh(true)}
            className="h-7 shrink-0 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
          >
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (runtime.available) return null;

  if (starting) {
    return (
      <div
        className="rounded-lg border border-amber-300/70 bg-amber-50/90 p-3 text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/35 dark:text-amber-100"
        role="status"
        data-testid="browser-runtime-notice"
      >
        <div className="flex items-start gap-3">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">The built-in browser is still starting</p>
            <p className="mt-0.5 text-xs text-amber-900/80 dark:text-amber-100/75">
              Pages that need a real browser will work as soon as it comes up.
              Everything else keeps working meanwhile.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg border border-amber-300/70 bg-amber-50/90 p-3 text-amber-950 dark:border-amber-800/60 dark:bg-amber-950/35 dark:text-amber-100"
      role="status"
      data-testid="browser-runtime-notice"
    >
      <div className="flex items-start gap-3">
        <Chrome className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {installing ? "Downloading the built-in browser" : buildMismatch
              ? "The built-in browser needs an update" : launchFailed
                ? "The built-in browser needs repair" : installFailed
                  ? "The built-in browser download did not finish"
                  : "The built-in browser isn't installed yet"}
          </p>
          <p className="mt-0.5 text-xs text-amber-900/80 dark:text-amber-100/75">
            {installing
              ? "Pages that need a real browser will work as soon as this finishes. Everything else keeps working meanwhile."
              : buildMismatch
                ? "Update the built-in browser to the version this app needs. Everything else keeps working meanwhile."
                : launchFailed
                  ? "Repair the built-in browser, then restart the app if it still cannot start. Everything else keeps working meanwhile."
                  : installFailed
                    ? "Try the browser download again. Everything else keeps working meanwhile."
                    : `The "Browser" method reads pages that only show their content after running JavaScript. It needs a one-time ${sizeHint} download. Regular page fetching works without it.`}
          </p>
          {installing && (
            <div className="mt-2 space-y-1">
              <Progress value={percent} className="h-1.5" />
              <p className="truncate text-[11px] text-amber-900/70 dark:text-amber-100/60">
                {message ?? "Working…"}
              </p>
            </div>
          )}
          {error && (
            <p className="mt-1 text-xs font-medium text-red-700 dark:text-red-300">
              {error}
            </p>
          )}
          {status?.reason && !installing && (
            <p className="mt-1 text-[11px] text-amber-900/60 dark:text-amber-100/50">
              {status.reason}
            </p>
          )}
        </div>
        <Button
          size="sm"
          disabled={installing}
          onClick={() => void actions.install()}
          className="h-7 shrink-0 bg-amber-600 px-2.5 text-xs text-white hover:bg-amber-700 dark:bg-amber-500 dark:text-amber-950 dark:hover:bg-amber-400"
        >
          {installing ? (
            <>
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              Installing
            </>
          ) : (
            actionLabel ?? (buildMismatch ? "Update browser" : launchFailed ? "Repair browser" : installFailed ? "Try again" : `Install browser (${sizeHint})`)
          )}
        </Button>
      </div>
    </div>
  );
}
