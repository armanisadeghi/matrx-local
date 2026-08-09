/**
 * BrowserRuntimeNotice — plain-language state + the one-click fix for a
 * missing built-in browser.
 *
 * Renders nothing when the browser is available, so a healthy install sees no
 * change at all. State and install live in BrowserRuntimeContext.
 */

import { Chrome, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useOptionalBrowserRuntimeContext } from "@/contexts/BrowserRuntimeContext";

export function BrowserRuntimeNotice() {
  const runtime = useOptionalBrowserRuntimeContext();
  if (!runtime || !runtime.loaded || runtime.available) return null;

  const { status, installing, percent, message, error, actions } = runtime;
  const sizeHint = status?.download_size_hint ?? "~90 MB";

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
            {installing
              ? "Downloading the built-in browser"
              : "The built-in browser isn't installed yet"}
          </p>
          <p className="mt-0.5 text-xs text-amber-900/80 dark:text-amber-100/75">
            {installing
              ? "Pages that need a real browser will work as soon as this finishes. Everything else keeps working meanwhile."
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
            `Install browser (${sizeHint})`
          )}
        </Button>
      </div>
    </div>
  );
}
