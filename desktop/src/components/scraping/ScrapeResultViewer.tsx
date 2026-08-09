/**
 * ScrapeResultViewer — shared result display used by Single tab, Bulk tab,
 * and QuickScrapeModal.  Shows URL, status, HTTP code, elapsed time, and
 * the raw content or error details.
 */

import { ExternalLink, Clock, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ScrapeResultData } from "@/lib/scrape-result";

interface ScrapeResultViewerProps {
  url?: string;
  result?: ScrapeResultData | null;
  loading?: boolean;
  className?: string;
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function statusCodeColor(code: number): string {
  if (code >= 200 && code < 300) return "text-emerald-500";
  if (code >= 300 && code < 400) return "text-blue-700 dark:text-blue-400";
  if (code >= 400 && code < 500) return "text-amber-700 dark:text-amber-400";
  if (code >= 500) return "text-red-700 dark:text-red-400";
  return "text-muted-foreground";
}

export function ScrapeResultViewer({
  url,
  result,
  loading,
  className,
}: ScrapeResultViewerProps) {
  if (loading) {
    return (
      <div className={cn("flex flex-1 items-center justify-center gap-3 text-muted-foreground", className)}>
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
        <span className="text-sm">Scraping {url ?? "…"}…</span>
      </div>
    );
  }

  if (!result) {
    return (
      <div className={cn("flex flex-1 flex-col items-center justify-center gap-3 text-muted-foreground", className)}>
        <div className="rounded-full border border-dashed border-muted-foreground/30 p-6">
          <ExternalLink className="h-8 w-8 opacity-20" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium">No result yet</p>
          <p className="mt-1 text-xs opacity-60">Enter a URL and scrape to see content here</p>
        </div>
      </div>
    );
  }

  const displayUrl = result.response_url || result.url || url || "";

  return (
    <div className={cn("flex flex-col overflow-hidden", className)}>
      {/* Header bar */}
      <div className="flex shrink-0 items-center gap-2 border-b bg-muted/30 px-4 py-2">
        {result.success ? (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
        ) : (
          <XCircle className="h-4 w-4 shrink-0 text-red-700 dark:text-red-400" />
        )}

        <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground/80" title={displayUrl}>
          {displayUrl}
        </span>

        <div className="flex shrink-0 items-center gap-2">
          {result.status_code !== null && result.status_code > 0 && (
            <Badge variant="secondary" className={cn("text-[10px] font-mono tabular-nums", statusCodeColor(result.status_code))}>
              {result.status_code}
            </Badge>
          )}
          {result.content_type && (
            <Badge variant="outline" className="text-[10px] font-mono hidden sm:inline-flex">
              {result.content_type.split(";")[0]}
            </Badge>
          )}
          {result.elapsed_ms > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Clock className="h-3 w-3" />
              {formatElapsed(result.elapsed_ms)}
            </span>
          )}
          {result.title && (
            <span
              className="max-w-[160px] truncate text-[10px] text-muted-foreground hidden md:block"
              title={result.title}
            >
              {result.title}
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={() => window.open(result.url, "_blank")}
            title="Open in browser"
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Content area */}
      <div className="min-h-0 flex-1 overflow-auto">
        {result.failure_reason && !result.success ? (
          <div className="p-4">
            <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-4">
              <p className="mb-2 text-sm font-semibold text-red-700 dark:text-red-400">Scrape failed</p>
              <pre className="whitespace-pre-wrap font-mono text-xs text-red-700 dark:text-red-400 dark:text-red-300 leading-relaxed">
                {result.failure_reason}
              </pre>
            </div>

            {/* Still show any partial content */}
            {result.text_data && (
              <div className="mt-4">
                <p className="mb-2 text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                  Partial content
                </p>
                <pre className="whitespace-pre-wrap font-mono text-xs text-foreground leading-relaxed">
                  {result.text_data}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <pre className="p-4 font-mono text-xs text-foreground whitespace-pre-wrap break-words leading-relaxed">
            {result.text_data || "(no content returned)"}
          </pre>
        )}
      </div>
    </div>
  );
}
