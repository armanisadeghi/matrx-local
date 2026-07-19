/**
 * ScrapeUrlList — table-style list of URLs in the bulk scrape queue.
 * Columns: status icon | URL | HTTP status | elapsed | title | remove action.
 * Used only by the Bulk tab.
 */

import {
  CheckCircle2,
  XCircle,
  Loader2,
  Trash2,
  Clock,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ScrapeEntry } from "@/hooks/use-scrape";

interface ScrapeUrlListProps {
  entries: ScrapeEntry[];
  selectedUrl: string | null;
  onSelect: (url: string) => void;
  onRemove: (url: string) => void;
}

function statusCodeColor(code: number): string {
  if (code >= 200 && code < 300) return "text-emerald-500";
  if (code >= 300 && code < 400) return "text-blue-700 dark:text-blue-400";
  if (code >= 400 && code < 500) return "text-amber-700 dark:text-amber-400";
  if (code >= 500) return "text-red-700 dark:text-red-400";
  return "text-muted-foreground";
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function ScrapeUrlList({
  entries,
  selectedUrl,
  onSelect,
  onRemove,
}: ScrapeUrlListProps) {
  if (entries.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
        <div className="rounded-full border border-dashed border-muted-foreground/30 p-4">
          <Clock className="h-6 w-6 opacity-30" />
        </div>
        <p className="text-xs">No URLs in queue</p>
      </div>
    );
  }

  return (
    <ScrollArea className="flex-1">
      {/* Table header */}
      <div className="sticky top-0 z-10 flex items-center gap-0 border-b bg-background px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        <span className="w-6 shrink-0" />
        <span className="flex-1 min-w-0 pl-1">URL</span>
        <span className="w-14 shrink-0 text-right pr-1">Status</span>
        <span className="w-16 shrink-0 text-right pr-1">Time</span>
        <span className="w-36 shrink-0 pl-2 hidden xl:block">Title</span>
        <span className="w-6 shrink-0" />
      </div>

      <div className="divide-y divide-border/40">
        {entries.map((entry) => (
          <button
            key={entry.id}
            onClick={() => onSelect(entry.url)}
            className={cn(
              "group flex w-full items-center gap-0 px-2 py-1.5 text-left transition-colors hover:bg-accent/50",
              selectedUrl === entry.url && "bg-accent",
            )}
          >
            {/* Status icon */}
            <span className="w-6 shrink-0 flex items-center justify-center">
              {entry.status === "running" && (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
              )}
              {entry.status === "success" && (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
              )}
              {entry.status === "error" && (
                <XCircle className="h-3.5 w-3.5 text-red-700 dark:text-red-400" />
              )}
              {entry.status === "pending" && (
                <span className="inline-block h-3 w-3 rounded-full border border-muted-foreground/40" />
              )}
            </span>

            {/* URL */}
            <span className="flex-1 min-w-0 pl-1 font-mono text-xs truncate" title={entry.url}>
              {entry.url.replace(/^https?:\/\//, "").replace(/\/$/, "")}
            </span>

            {/* HTTP status code */}
            <span className="w-14 shrink-0 text-right pr-1">
              {entry.result?.status_code && entry.result.status_code > 0 ? (
                <Badge
                  variant="secondary"
                  className={cn(
                    "text-[10px] font-mono tabular-nums h-4 px-1",
                    statusCodeColor(entry.result.status_code),
                  )}
                >
                  {entry.result.status_code}
                </Badge>
              ) : null}
            </span>

            {/* Elapsed */}
            <span className="w-16 shrink-0 text-right pr-1 text-[10px] text-muted-foreground tabular-nums">
              {entry.result?.elapsed_ms
                ? formatElapsed(entry.result.elapsed_ms)
                : entry.status === "pending"
                  ? "—"
                  : null}
            </span>

            {/* Title (wide screens only) */}
            <span
              className="w-36 shrink-0 pl-2 text-[10px] text-muted-foreground truncate hidden xl:block"
              title={entry.result?.title}
            >
              {entry.result?.title || ""}
            </span>

            {/* Remove */}
            <span className="w-6 shrink-0 flex items-center justify-center">
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(entry.url);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.stopPropagation();
                    onRemove(entry.url);
                  }
                }}
                className="opacity-0 group-hover:opacity-100 transition-opacity hover:text-destructive cursor-pointer"
              >
                <Trash2 className="h-3 w-3" />
              </span>
            </span>
          </button>
        ))}
      </div>
    </ScrollArea>
  );
}
