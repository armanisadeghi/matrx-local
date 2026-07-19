import { useCallback, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  File,
  Folder,
  FolderOpen,
  HardDrive,
  Link2,
  Loader2,
  MoreHorizontal,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { engine } from "@/lib/api";
import { cn } from "@/lib/utils";
import { invoke } from "@tauri-apps/api/core";
import type {
  FilesystemDirectoryPage,
  FilesystemEntry,
  FilesystemSearchPage,
  FilesystemContentSearch,
  FilesystemSemanticSearch,
  FilesystemPlace,
  FilesystemResult,
} from "./types";
import { useFilesystemPlaces } from "./use-filesystem-places";

export interface FilesystemResultViewProps {
  result: FilesystemResult;
  layout?: "embedded" | "page";
  onReference?: (paths: string[]) => void;
  onNavigate?: (path: string) => void;
  onLoadChildren?: (entry: FilesystemEntry, cursor?: string) => Promise<FilesystemDirectoryPage>;
  onLoadMore?: (cursor: string) => void;
  loadingMore?: boolean;
  pagingError?: string | null;
}

async function openPath(path: string, reveal = false): Promise<void> {
  let target = path;
  if (!reveal) {
    const prepared = await engine.prepareFilesystemOpen(path);
    if (!prepared.ready) throw new Error(prepared.error ?? "This file is not ready to open.");
    target = prepared.path;
  }
  await invoke("open_filesystem_path", { path: target, reveal });
}

function PathActions({ path, onReference }: { path: string; onReference: () => void }) {
  const [opening, setOpening] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const runOpen = useCallback(async (reveal: boolean) => {
    setOpening(true);
    setActionError(null);
    try {
      await openPath(path, reveal);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setActionError(message);
      console.error("Unable to open filesystem path", { path, reveal, reason });
    } finally {
      setOpening(false);
    }
  }, [path]);
  return (
    <div className="flex shrink-0 items-center gap-1">
      {actionError && (
        <span className="max-w-44 truncate text-[10px] text-destructive" role="alert" title={actionError}>
          {actionError}
        </span>
      )}
      <button
        type="button"
        className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        title="Reference path"
        aria-label={`Reference ${path}`}
        onClick={(event) => {
          event.stopPropagation();
          onReference();
        }}
      >
        <Link2 className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        title="Open"
        aria-label={`Open ${path}`}
        disabled={opening}
        onClick={(event) => {
          event.stopPropagation();
          void runOpen(false);
        }}
      >
        <FolderOpen className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        title="Show in containing folder"
        aria-label={`Show ${path} in containing folder`}
        disabled={opening}
        onClick={(event) => {
          event.stopPropagation();
          void runOpen(true);
        }}
      >
        <MoreHorizontal className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function formatSize(bytes?: number | null): string | null {
  if (bytes == null || bytes < 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

export function mergeFilesystemEntries(
  current: readonly FilesystemEntry[],
  next: readonly FilesystemEntry[],
): FilesystemEntry[] {
  const byPath = new Map(current.map((entry) => [entry.path, entry]));
  for (const entry of next) byPath.set(entry.path, entry);
  return [...byPath.values()];
}

function EntryRow({
  entry,
  depth,
  selectedPaths,
  onToggleSelected,
  onReference,
  onLoadChildren,
  onNavigate,
}: {
  entry: FilesystemEntry;
  depth: number;
  selectedPaths: ReadonlySet<string>;
  onToggleSelected: (path: string) => void;
  onReference: (paths: string[]) => void;
  onLoadChildren?: (entry: FilesystemEntry, cursor?: string) => Promise<FilesystemDirectoryPage>;
  onNavigate?: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [childError, setChildError] = useState<string | null>(null);
  const [loadedChildren, setLoadedChildren] = useState<FilesystemEntry[] | null>(null);
  const [childCursor, setChildCursor] = useState<string | null>(null);
  const [rowActionError, setRowActionError] = useState<string | null>(null);
  const directory = entry.kind === "directory";
  const selected = selectedPaths.has(entry.path);
  const children = loadedChildren ?? entry.children ?? [];
  const expandable = directory && (entry.hasChildren || children.length > 0 || !!onLoadChildren);

  const toggleExpanded = useCallback(async () => {
    if (!expandable) return;
    const next = !expanded;
    setExpanded(next);
    if (next && !entry.children && loadedChildren === null && onLoadChildren) {
      setLoading(true);
      setChildError(null);
      try {
        const page = await onLoadChildren(entry);
        setLoadedChildren(page.entries);
        setChildCursor(page.nextCursor ?? null);
      } catch (reason) {
        setChildError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setLoading(false);
      }
    }
  }, [entry, expandable, expanded, loadedChildren, onLoadChildren]);

  const loadMoreChildren = useCallback(async () => {
    if (!onLoadChildren || !childCursor || loading) return;
    setLoading(true);
    setChildError(null);
    try {
      const page = await onLoadChildren(entry, childCursor);
      setLoadedChildren((current) => {
        return mergeFilesystemEntries(current ?? [], page.entries);
      });
      setChildCursor(page.nextCursor ?? null);
    } catch (reason) {
      setChildError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [childCursor, entry, loading, onLoadChildren]);

  const openEntry = useCallback(async () => {
    setRowActionError(null);
    try {
      await openPath(entry.path);
    } catch (reason) {
      setRowActionError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [entry.path]);

  const icon = entry.kind === "directory"
    ? <Folder className="h-4 w-4 text-amber-500" />
    : entry.kind === "symlink"
      ? <Link2 className="h-4 w-4 text-sky-500" />
      : <File className="h-4 w-4 text-muted-foreground" />;

  return (
    <>
      <div
        className={cn(
          "group flex min-h-9 items-center gap-2 rounded px-2 text-xs hover:bg-muted/70",
          selected && "bg-primary/10",
        )}
        style={{ paddingLeft: `${8 + depth * 18}px` }}
      >
        <button
          type="button"
          className="flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground"
          disabled={!expandable}
          aria-label={expanded ? `Collapse ${entry.name}` : `Expand ${entry.name}`}
          onClick={() => void toggleExpanded()}
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : expandable ? (
            expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />
          ) : null}
        </button>
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelected(entry.path)}
          aria-label={`Select ${entry.name}`}
          className="h-3.5 w-3.5 rounded border-border"
        />
        {icon}
        <button
          type="button"
          className="min-w-0 flex-1 truncate text-left"
          title={entry.path}
          onDoubleClick={() => {
            if (!directory) void openEntry();
          }}
          onClick={() => {
            if (!directory) return;
            if (onNavigate) onNavigate(entry.path);
            else void toggleExpanded();
          }}
          onKeyDown={(event) => {
            if (!directory && event.key === "Enter") {
              event.preventDefault();
              void openEntry();
            }
          }}
        >
          {entry.name}
        </button>
        {formatSize(entry.size) && (
          <span className="hidden shrink-0 text-[10px] text-muted-foreground sm:inline">
            {formatSize(entry.size)}
          </span>
        )}
        <div className="opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
          <PathActions path={entry.path} onReference={() => onReference([entry.path])} />
        </div>
      </div>
      {expanded && children.map((child) => (
        <EntryRow
          key={child.path}
          entry={child}
          depth={depth + 1}
          selectedPaths={selectedPaths}
          onToggleSelected={onToggleSelected}
          onReference={onReference}
          {...(onLoadChildren ? { onLoadChildren } : {})}
          {...(onNavigate ? { onNavigate } : {})}
        />
      ))}
      {expanded && loadedChildren?.length === 0 && !childCursor && !loading && !childError && (
        <div className="py-1 pr-2 text-[11px] text-muted-foreground" style={{ paddingLeft: `${44 + depth * 18}px` }}>
          Folder is empty.
        </div>
      )}
      {expanded && childCursor && (
        <div className="py-1 pr-2" style={{ paddingLeft: `${44 + depth * 18}px` }}>
          <Button type="button" variant="ghost" size="sm" className="h-7 text-[11px]" disabled={loading} onClick={() => void loadMoreChildren()}>
            {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {loadedChildren?.length === 0 ? "Continue scanning" : "Load more children"}
          </Button>
        </div>
      )}
      {expanded && childError && (
        <div className="py-1 pr-2 text-[11px] text-destructive" style={{ paddingLeft: `${44 + depth * 18}px` }}>
          {childError}
        </div>
      )}
      {rowActionError && (
        <div className="py-1 pr-2 text-[11px] text-destructive" role="alert" style={{ paddingLeft: `${44 + depth * 18}px` }}>
          {rowActionError}
        </div>
      )}
    </>
  );
}

function Breadcrumbs({ path }: { path: string }) {
  if (!path) return null;
  const separator = path.includes("\\") && !path.includes("/") ? "\\" : "/";
  const parts = path.split(/[\\/]/).filter(Boolean);
  return (
    <div className="flex min-w-0 items-center gap-1 overflow-x-auto px-2 py-1.5 text-[11px] text-muted-foreground">
      <HardDrive className="h-3.5 w-3.5 shrink-0" />
      {parts.map((part, index) => (
        <span key={`${part}-${index}`} className="flex shrink-0 items-center gap-1">
          {index > 0 && <ChevronRight className="h-3 w-3" />}
          <span>{index === 0 && separator === "\\" ? `${part}\\` : part}</span>
        </span>
      ))}
    </div>
  );
}

function Places({ places, onReference, onNavigate }: { places: FilesystemPlace[]; onReference: (paths: string[]) => void; onNavigate?: (path: string) => void }) {
  return (
    <div className="grid gap-1 p-2 sm:grid-cols-2">
      {places.map((place) => (
        <div key={place.id} className={cn("group flex min-w-0 items-center gap-2 rounded-md border bg-background px-2 py-1.5", place.available === false && "opacity-60")}>
          <Folder className="h-4 w-4 shrink-0 text-amber-500" />
          <button
            type="button"
            className="min-w-0 flex-1 text-left"
            title={place.path}
            disabled={place.available === false}
            onClick={() => {
              if (onNavigate) onNavigate(place.path);
            }}
            onDoubleClick={() => {
              if (!onNavigate) void openPath(place.path).catch((reason: unknown) => {
                console.error("Unable to open filesystem place", { path: place.path, reason });
              });
            }}
          >
            <span className="block truncate text-xs font-medium">{place.label}</span>
            <span className="block truncate text-[10px] text-muted-foreground">{place.path}</span>
            {place.available === false && <span className="block text-[10px] text-destructive">Unavailable</span>}
          </button>
          {place.available !== false && <PathActions path={place.path} onReference={() => onReference([place.path])} />}
        </div>
      ))}
    </div>
  );
}

export function EnginePlaces({ onReference }: { onReference?: (paths: string[]) => void }) {
  const [state, actions] = useFilesystemPlaces();
  const reference = useCallback((paths: string[]) => {
    if (onReference) onReference(paths);
    else void navigator.clipboard.writeText(paths.join("\n"));
  }, [onReference]);
  if (state.loading) {
    return <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading places…</div>;
  }
  if (state.error) {
    return (
      <div className="flex items-center justify-between gap-2 p-3 text-xs text-destructive">
        <span>{state.error}</span>
        <Button type="button" variant="ghost" size="sm" onClick={() => void actions.refresh()}><RefreshCw className="mr-1 h-3.5 w-3.5" />Retry</Button>
      </div>
    );
  }
  return <Places places={state.places} onReference={reference} />;
}

function DirectoryPage({
  result,
  layout,
  onReference,
  onNavigate,
  onLoadChildren,
  onLoadMore,
  loadingMore,
  pagingError,
}: {
  result: FilesystemDirectoryPage | FilesystemSearchPage;
  layout: "embedded" | "page";
  onReference: (paths: string[]) => void;
  onNavigate?: (path: string) => void;
  onLoadChildren?: (entry: FilesystemEntry, cursor?: string) => Promise<FilesystemDirectoryPage>;
  onLoadMore?: (cursor: string) => void;
  loadingMore?: boolean;
  pagingError?: string | null;
}) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const toggleSelected = useCallback((path: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);
  const selectedPaths = useMemo(() => [...selected], [selected]);
  const directoryPath = result.kind === "filesystem.directory-page" ? result.path : "";
  return (
    <div className={cn(layout === "page" && "flex h-full min-h-[24rem] flex-col")}>
      <div className="flex items-center justify-between gap-2 border-b bg-muted/20 pr-2">
        {result.kind === "filesystem.directory-page" ? (
          <Breadcrumbs path={directoryPath} />
        ) : (
          <div className="min-w-0 truncate px-2 py-1.5 text-[11px] text-muted-foreground">
            Results for <span className="font-medium text-foreground">{result.query}</span>
            {result.source && (
              <span> · {result.source === "index" ? "indexed" : result.source === "hybrid" ? "index + disk" : "disk"}</span>
            )}
            {result.indexComplete === false && <span> · index still improving</span>}
            {result.truncated && <span> · bounded search stopped before every folder was examined; narrow the scope</span>}
          </div>
        )}
        {selectedPaths.length > 0 && (
          <Button type="button" variant="ghost" size="sm" className="h-7 text-xs" onClick={() => onReference(selectedPaths)}>
            <Copy className="mr-1 h-3.5 w-3.5" />Reference {selectedPaths.length}
          </Button>
        )}
      </div>
      <div className={cn("overflow-y-auto p-1", layout === "page" ? "min-h-0 flex-1" : "max-h-80")}>
        {result.entries.length === 0 ? (
          <div className="p-4 text-center text-xs text-muted-foreground">
            {result.kind === "filesystem.search-page"
              ? "No matching files or folders."
              : result.nextCursor
                ? "Scanning this large directory…"
                : "This directory is empty."}
          </div>
        ) : result.entries.map((entry) => (
          <EntryRow
            key={entry.path}
            entry={entry}
            depth={0}
            selectedPaths={selected}
            onToggleSelected={toggleSelected}
            onReference={onReference}
            {...(onLoadChildren ? { onLoadChildren } : {})}
            {...(onNavigate ? { onNavigate } : {})}
          />
        ))}
      </div>
      {result.nextCursor && onLoadMore && (
        <div className="border-t p-2 text-center">
          <Button type="button" variant="ghost" size="sm" disabled={loadingMore} onClick={() => onLoadMore(result.nextCursor!)}>
            {loadingMore && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {result.entries.length === 0 ? "Continue scanning" : "Load more"}
          </Button>
        </div>
      )}
      {pagingError && <div className="border-t px-3 py-2 text-[11px] text-destructive">{pagingError}</div>}
    </div>
  );
}

function ContentSearchView({
  result,
  onReference,
}: {
  result: FilesystemContentSearch;
  onReference: (paths: string[]) => void;
}) {
  return (
    <div>
      <div className="border-b bg-muted/20 px-2 py-1.5 text-[11px] text-muted-foreground">
        Content matches for <span className="font-medium text-foreground">{result.query}</span>
      </div>
      <div className="max-h-80 overflow-y-auto p-1">
        {result.results.length === 0 ? (
          <div className="p-4 text-center text-xs text-muted-foreground">No content matches.</div>
        ) : result.results.map((match) => (
          <div key={match.path} className="group flex items-start gap-2 rounded px-2 py-2 hover:bg-muted/70">
            <File className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium" title={match.path}>{match.path}</div>
              <div className="mt-0.5 line-clamp-3 text-[11px] text-muted-foreground">{match.snippet}</div>
            </div>
            <div className="opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
              <PathActions path={match.path} onReference={() => onReference([match.path])} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SemanticSearchView({
  result,
  onReference,
}: {
  result: FilesystemSemanticSearch;
  onReference: (paths: string[]) => void;
}) {
  return (
    <div>
      <div className="border-b bg-muted/20 px-2 py-1.5 text-[11px] text-muted-foreground">
        Semantic matches for <span className="font-medium text-foreground">{result.query}</span>
        <span> · {result.model}</span>
      </div>
      <div className="max-h-80 overflow-y-auto p-1">
        {result.results.length === 0 ? (
          <div className="p-4 text-center text-xs text-muted-foreground">No semantic matches.</div>
        ) : result.results.map(({ entry, score }) => (
          <div key={entry.path} className="group flex items-center gap-2 rounded px-2 py-2 hover:bg-muted/70">
            {entry.kind === "directory" ? (
              <Folder className="h-4 w-4 shrink-0 text-amber-500" />
            ) : (
              <File className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">{entry.name}</div>
              <div className="truncate text-[10px] text-muted-foreground" title={entry.path}>{entry.path}</div>
            </div>
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">{score.toFixed(3)}</span>
            <div className="opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
              <PathActions path={entry.path} onReference={() => onReference([entry.path])} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function resultIdentity(result: FilesystemDirectoryPage | FilesystemSearchPage): string {
  return result.kind === "filesystem.directory-page"
    ? `${result.kind}:${result.namespace}:${result.path}`
    : `${result.kind}:${result.namespace}:${result.root ?? ""}:${result.query}`;
}

export function FilesystemResultView({ result, layout = "embedded", onReference, onNavigate, onLoadChildren, onLoadMore, loadingMore, pagingError }: FilesystemResultViewProps) {
  const reference = useCallback((paths: string[]) => {
    if (onReference) onReference(paths);
    else void navigator.clipboard.writeText(paths.join("\n"));
  }, [onReference]);
  switch (result.kind) {
    case "filesystem.places":
      return <Places places={result.places} onReference={reference} {...(onNavigate ? { onNavigate } : {})} />;
    case "filesystem.content-search":
      return <ContentSearchView result={result} onReference={reference} />;
    case "filesystem.semantic-search":
      return <SemanticSearchView result={result} onReference={reference} />;
    case "filesystem.directory-page":
    case "filesystem.search-page":
      return (
        <DirectoryPage
          key={resultIdentity(result)}
          result={result}
          layout={layout}
          onReference={reference}
          {...(onLoadChildren ? { onLoadChildren } : {})}
          {...(onNavigate ? { onNavigate } : {})}
          {...(onLoadMore ? { onLoadMore } : {})}
          {...(loadingMore != null ? { loadingMore } : {})}
          {...(pagingError != null ? { pagingError } : {})}
        />
      );
    default: {
      const exhaustive: never = result;
      return exhaustive;
    }
  }
}
