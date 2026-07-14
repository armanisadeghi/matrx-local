/**
 * LibraryPanel — the visible shelf of saved pools / variables.
 *
 * Stored on disk at ~/.matrx/prompt-matrix/library.json (via the engine).
 * Lives IN the matrix panel so nobody has to hunt for a buried presets menu.
 */

import { useState } from "react";
import {
  AlertCircle,
  BookMarked,
  FolderOpen,
  Plus,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { LibraryEntry } from "@/lib/prompt-matrix";
import { cn } from "@/lib/utils";

export function LibraryPanel({
  entries,
  diskPath,
  error,
  ready,
  onInsert,
  onRemove,
  onRefresh,
}: {
  entries: readonly LibraryEntry[];
  diskPath: string | null;
  error: string | null;
  ready: boolean;
  onInsert: (id: string) => void;
  onRemove: (id: string) => void;
  onRefresh: () => void;
}) {
  const [confirmId, setConfirmId] = useState<string | null>(null);

  return (
    <div className="space-y-2 rounded-lg border border-violet-500/30 bg-violet-500/5 p-3">
      <div className="flex items-start gap-2">
        <BookMarked className="mt-0.5 h-4 w-4 shrink-0 text-violet-600 dark:text-violet-300" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-medium">Library</p>
            <Badge variant="secondary" className="h-4 px-1 text-[10px]">
              {entries.length}
            </Badge>
            {!ready && (
              <span className="text-[10px] text-muted-foreground">
                connecting…
              </span>
            )}
          </div>
          <p className="text-[11px] leading-snug text-muted-foreground">
            Saved option lists you can drop into any matrix. Use{" "}
            <span className="font-medium text-foreground">Save to library</span>{" "}
            on a pool or variable below.
          </p>
          {diskPath !== null && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="mt-1 flex max-w-full items-center gap-1 truncate text-[10px] text-muted-foreground hover:text-foreground"
                  onClick={() => void navigator.clipboard.writeText(diskPath)}
                >
                  <FolderOpen className="h-3 w-3 shrink-0" />
                  <span className="truncate">{diskPath}</span>
                </button>
              </TooltipTrigger>
              <TooltipContent>On-disk JSON — click to copy path</TooltipContent>
            </Tooltip>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 shrink-0 text-[11px]"
          onClick={onRefresh}
        >
          Refresh
        </Button>
      </div>

      {error !== null && (
        <div className="flex gap-1.5 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[11px] text-destructive">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {entries.length === 0 ? (
        <div className="rounded-md border border-dashed px-3 py-4 text-center text-[11px] text-muted-foreground">
          Library is empty. Add options to a pool or variable, then click{" "}
          <span className="font-medium text-foreground">Save to library</span>.
        </div>
      ) : (
        <ul className="space-y-1">
          {entries.map((entry) => {
            const enabled = entry.options.filter((o) => o.enabled).length;
            const preview = entry.options
              .filter((o) => o.enabled && o.value.trim().length > 0)
              .slice(0, 4)
              .map((o) => o.value.trim())
              .join(", ");
            return (
              <li
                key={entry.id}
                className="flex items-center gap-1.5 rounded-md border bg-card px-2 py-1.5"
              >
                <Badge
                  variant="outline"
                  className={cn(
                    "h-5 shrink-0 px-1.5 text-[10px]",
                    entry.kind === "pool" &&
                      "border-violet-500/40 text-violet-700 dark:text-violet-300",
                  )}
                >
                  {entry.kind}
                </Badge>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-medium">{entry.name}</p>
                  <p className="truncate text-[10px] text-muted-foreground">
                    {enabled} option{enabled === 1 ? "" : "s"}
                    {preview.length > 0 ? ` — ${preview}` : ""}
                    {enabled > 4 ? "…" : ""}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 gap-1 shrink-0 px-2 text-[11px]"
                  onClick={() => onInsert(entry.id)}
                >
                  <Plus className="h-3 w-3" />
                  Insert
                </Button>
                {confirmId === entry.id ? (
                  <Button
                    variant="destructive"
                    size="sm"
                    className="h-7 shrink-0 px-2 text-[11px]"
                    onClick={() => {
                      onRemove(entry.id);
                      setConfirmId(null);
                    }}
                  >
                    Delete
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                    onClick={() => setConfirmId(entry.id)}
                    aria-label={`Delete ${entry.name}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
