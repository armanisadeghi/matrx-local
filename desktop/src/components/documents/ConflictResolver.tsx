/**
 * Conflict resolution dialog — shows a side-by-side diff/merge view for
 * conflicting notes, with options to keep local, keep remote, merge,
 * split into two notes, or exclude from sync.
 */

import { useState, useCallback } from "react";
import {
  X,
  Monitor,
  Cloud,
  GitMerge,
  Copy,
  Ban,
  Check,
  ChevronLeft,
  ChevronRight,
  Plus,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ConflictDetail } from "@/lib/api";

interface ConflictResolverProps {
  conflicts: ConflictDetail[];
  onResolve: (
    noteId: string,
    resolution: "keep_local" | "keep_remote" | "merge" | "append" | "split" | "exclude",
    mergedContent?: string,
  ) => void;
  onClose: () => void;
}

function computeLineDiffs(local: string, remote: string): DiffLine[] {
  const localLines = local.split("\n");
  const remoteLines = remote.split("\n");
  const result: DiffLine[] = [];

  const maxLen = Math.max(localLines.length, remoteLines.length);
  for (let i = 0; i < maxLen; i++) {
    const l = i < localLines.length ? localLines[i] : undefined;
    const r = i < remoteLines.length ? remoteLines[i] : undefined;

    if (l === r) {
      result.push({ type: "same", content: l ?? "", lineNumber: i + 1 });
    } else if (l !== undefined && r !== undefined) {
      result.push({ type: "changed", localContent: l, remoteContent: r, lineNumber: i + 1 });
    } else if (l !== undefined) {
      result.push({ type: "local_only", content: l, lineNumber: i + 1 });
    } else if (r !== undefined) {
      result.push({ type: "remote_only", content: r, lineNumber: i + 1 });
    }
  }
  return result;
}

interface DiffLine {
  type: "same" | "changed" | "local_only" | "remote_only";
  content?: string;
  localContent?: string;
  remoteContent?: string;
  lineNumber: number;
}

function DiffView({
  local,
  remote,
  view,
}: {
  local: string;
  remote: string;
  view: "side-by-side" | "unified";
}) {
  const diffs = computeLineDiffs(local, remote);

  if (view === "side-by-side") {
    return (
      <div className="flex gap-0 overflow-auto rounded-lg border text-xs font-mono">
        <div className="flex-1 min-w-0 border-r">
          <div className="sticky top-0 z-10 bg-blue-500/10 px-3 py-1.5 text-xs font-sans font-medium flex items-center gap-1.5">
            <Monitor className="h-3 w-3" />
            Local Version
          </div>
          <div className="p-2">
            {diffs.map((d, i) => (
              <div
                key={i}
                className={cn(
                  "px-2 py-0.5 whitespace-pre-wrap break-all",
                  d.type === "changed" && "bg-red-500/10 text-red-700 dark:text-red-400",
                  d.type === "local_only" && "bg-green-500/10 text-green-700 dark:text-green-400",
                  d.type === "remote_only" && "opacity-30",
                  d.type === "same" && "text-muted-foreground",
                )}
              >
                <span className="inline-block w-6 text-right opacity-40 select-none mr-2">
                  {d.lineNumber}
                </span>
                {d.type === "changed"
                  ? d.localContent
                  : d.type === "remote_only"
                    ? ""
                    : d.content}
              </div>
            ))}
          </div>
        </div>
        <div className="flex-1 min-w-0">
          <div className="sticky top-0 z-10 bg-purple-500/10 px-3 py-1.5 text-xs font-sans font-medium flex items-center gap-1.5">
            <Cloud className="h-3 w-3" />
            Cloud Version
          </div>
          <div className="p-2">
            {diffs.map((d, i) => (
              <div
                key={i}
                className={cn(
                  "px-2 py-0.5 whitespace-pre-wrap break-all",
                  d.type === "changed" && "bg-purple-500/10 text-purple-700 dark:text-purple-400",
                  d.type === "remote_only" && "bg-green-500/10 text-green-700 dark:text-green-400",
                  d.type === "local_only" && "opacity-30",
                  d.type === "same" && "text-muted-foreground",
                )}
              >
                <span className="inline-block w-6 text-right opacity-40 select-none mr-2">
                  {d.lineNumber}
                </span>
                {d.type === "changed"
                  ? d.remoteContent
                  : d.type === "local_only"
                    ? ""
                    : d.content}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-auto rounded-lg border text-xs font-mono p-2">
      {diffs.map((d, i) => {
        if (d.type === "same") {
          return (
            <div key={i} className="px-2 py-0.5 text-muted-foreground whitespace-pre-wrap">
              <span className="inline-block w-6 text-right opacity-40 select-none mr-2">{d.lineNumber}</span>
              {d.content}
            </div>
          );
        }
        if (d.type === "changed") {
          return (
            <div key={i}>
              <div className="px-2 py-0.5 bg-red-500/10 text-red-700 dark:text-red-400 whitespace-pre-wrap">
                <span className="inline-block w-6 text-right opacity-40 select-none mr-2">{d.lineNumber}</span>
                <span className="select-none mr-1">-</span>{d.localContent}
              </div>
              <div className="px-2 py-0.5 bg-green-500/10 text-green-700 dark:text-green-400 whitespace-pre-wrap">
                <span className="inline-block w-6 text-right opacity-40 select-none mr-2">{d.lineNumber}</span>
                <span className="select-none mr-1">+</span>{d.remoteContent}
              </div>
            </div>
          );
        }
        if (d.type === "local_only") {
          return (
            <div key={i} className="px-2 py-0.5 bg-red-500/10 text-red-700 dark:text-red-400 whitespace-pre-wrap">
              <span className="inline-block w-6 text-right opacity-40 select-none mr-2">{d.lineNumber}</span>
              <span className="select-none mr-1">-</span>{d.content}
            </div>
          );
        }
        return (
          <div key={i} className="px-2 py-0.5 bg-green-500/10 text-green-700 dark:text-green-400 whitespace-pre-wrap">
            <span className="inline-block w-6 text-right opacity-40 select-none mr-2">{d.lineNumber}</span>
            <span className="select-none mr-1">+</span>{d.content}
          </div>
        );
      })}
    </div>
  );
}

export function ConflictResolver({ conflicts, onResolve, onClose }: ConflictResolverProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [view, setView] = useState<"side-by-side" | "unified">("side-by-side");
  const [mergeMode, setMergeMode] = useState(false);
  const [mergedContent, setMergedContent] = useState("");
  const [resolving, setResolving] = useState(false);

  const conflict = conflicts[currentIndex];
  if (!conflict) return null;

  const handleResolve = async (
    resolution: "keep_local" | "keep_remote" | "merge" | "append" | "split" | "exclude",
  ) => {
    setResolving(true);
    try {
      await onResolve(
        conflict.note_id,
        resolution,
        resolution === "merge" ? mergedContent : undefined,
      );
      if (currentIndex >= conflicts.length - 1) {
        if (conflicts.length <= 1) {
          onClose();
        } else {
          setCurrentIndex(Math.max(0, currentIndex - 1));
        }
      }
    } finally {
      setResolving(false);
      setMergeMode(false);
    }
  };

  const startMerge = useCallback(() => {
    const localContent = conflict.local_content ?? "";
    const remoteContent = conflict.remote_content ?? "";
    const localLines = localContent.split("\n");
    const remoteLines = remoteContent.split("\n");
    const maxLen = Math.max(localLines.length, remoteLines.length);
    const merged: string[] = [];

    for (let i = 0; i < maxLen; i++) {
      const l = i < localLines.length ? localLines[i] : undefined;
      const r = i < remoteLines.length ? remoteLines[i] : undefined;
      if (l === r) {
        merged.push(l ?? "");
      } else if (l !== undefined && r !== undefined) {
        merged.push(`<<<< LOCAL`);
        merged.push(l);
        merged.push(`====`);
        merged.push(r);
        merged.push(`>>>> CLOUD`);
      } else if (l !== undefined) {
        merged.push(l);
      } else if (r !== undefined) {
        merged.push(r);
      }
    }
    setMergedContent(merged.join("\n"));
    setMergeMode(true);
  }, [conflict]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="mx-4 flex h-[85vh] w-full max-w-5xl flex-col rounded-xl border bg-background shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b px-6 py-3">
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-semibold">Resolve Sync Conflicts</h2>
            {conflicts.length > 1 && (
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <button
                  onClick={() => setCurrentIndex(Math.max(0, currentIndex - 1))}
                  disabled={currentIndex === 0}
                  className="rounded p-0.5 hover:bg-accent disabled:opacity-30"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                <span>{currentIndex + 1} / {conflicts.length}</span>
                <button
                  onClick={() => setCurrentIndex(Math.min(conflicts.length - 1, currentIndex + 1))}
                  disabled={currentIndex === conflicts.length - 1}
                  className="rounded p-0.5 hover:bg-accent disabled:opacity-30"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            {!mergeMode && (
              <div className="flex rounded-md border text-xs">
                <button
                  onClick={() => setView("side-by-side")}
                  className={cn(
                    "rounded-l-md px-2.5 py-1 transition-colors",
                    view === "side-by-side" ? "bg-primary/15 text-primary" : "hover:bg-accent",
                  )}
                >
                  Side by Side
                </button>
                <button
                  onClick={() => setView("unified")}
                  className={cn(
                    "rounded-r-md px-2.5 py-1 transition-colors",
                    view === "unified" ? "bg-primary/15 text-primary" : "hover:bg-accent",
                  )}
                >
                  Unified
                </button>
              </div>
            )}
            <button onClick={onClose} className="rounded-md p-1.5 hover:bg-accent">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Note info */}
        <div className="border-b px-6 py-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{conflict.label ?? "Untitled Note"}</span>
          {conflict.folder_name && (
            <span className="ml-2">in {conflict.folder_name}</span>
          )}
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-auto p-4">
          {mergeMode ? (
            <div className="flex h-full flex-col">
              <div className="mb-2 text-xs text-muted-foreground">
                Edit the merged content below. Resolve conflict markers ({"<<<< LOCAL / ==== / >>>> CLOUD"}) manually.
              </div>
              <textarea
                value={mergedContent}
                onChange={(e) => setMergedContent(e.target.value)}
                className="flex-1 w-full rounded-lg border bg-background p-3 font-mono text-xs resize-none focus:outline-none focus:ring-1 focus:ring-primary"
                spellCheck={false}
              />
            </div>
          ) : (
            <DiffView
              local={conflict.local_content ?? ""}
              remote={conflict.remote_content ?? ""}
              view={view}
            />
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between border-t px-6 py-3">
          <div className="flex items-center gap-2">
            {mergeMode ? (
              <>
                <button
                  onClick={() => setMergeMode(false)}
                  className="rounded-md border px-3 py-1.5 text-xs hover:bg-accent"
                >
                  Back to Diff
                </button>
                <button
                  onClick={() => handleResolve("merge")}
                  disabled={resolving || !mergedContent.trim()}
                  className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
                >
                  <Check className="h-3.5 w-3.5" />
                  Save Merged
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => handleResolve("keep_local")}
                  disabled={resolving}
                  className="flex items-center gap-1.5 rounded-md border border-blue-500/30 bg-blue-500/10 px-3 py-1.5 text-xs text-blue-500 hover:bg-blue-500/20 disabled:opacity-60"
                >
                  <Monitor className="h-3.5 w-3.5" />
                  Keep Local
                </button>
                <button
                  onClick={() => handleResolve("keep_remote")}
                  disabled={resolving}
                  className="flex items-center gap-1.5 rounded-md border border-purple-500/30 bg-purple-500/10 px-3 py-1.5 text-xs text-purple-500 hover:bg-purple-500/20 disabled:opacity-60"
                >
                  <Cloud className="h-3.5 w-3.5" />
                  Keep Cloud
                </button>
                <button
                  onClick={startMerge}
                  disabled={resolving}
                  className="flex items-center gap-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-500 hover:bg-emerald-500/20 disabled:opacity-60"
                >
                  <GitMerge className="h-3.5 w-3.5" />
                  Merge
                </button>
                <button
                  onClick={() => handleResolve("append")}
                  disabled={resolving}
                  className="flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-500 hover:bg-amber-500/20 disabled:opacity-60"
                  title="Combine both versions into one note (local first, then cloud)"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Append
                </button>
                <button
                  onClick={() => handleResolve("split")}
                  disabled={resolving}
                  className="flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs hover:bg-accent disabled:opacity-60"
                >
                  <Copy className="h-3.5 w-3.5" />
                  Split (Keep Both)
                </button>
              </>
            )}
          </div>

          <button
            onClick={() => handleResolve("exclude")}
            disabled={resolving}
            className="flex items-center gap-1.5 rounded-md border border-red-500/30 px-3 py-1.5 text-xs text-red-700 dark:text-red-400 hover:bg-red-500/10 disabled:opacity-60"
            title="Exclude this note from all future syncs"
          >
            <Ban className="h-3.5 w-3.5" />
            Exclude from Sync
          </button>
        </div>
      </div>
    </div>
  );
}
