import { useCallback, useEffect, useRef, useState } from "react";
import { engine } from "@/lib/api";
import { FilesystemResultView, mergeFilesystemEntries } from "./FilesystemResultView";
import { normalizeFilesystemPayload } from "./tool-results";
import type {
  FilesystemDirectoryPage,
  FilesystemEntry,
  FilesystemResult,
  FilesystemSearchPage,
} from "./types";

export interface FilesystemResultControllerProps {
  result: FilesystemResult;
  layout?: "embedded" | "page";
  onReference?: (paths: string[]) => void;
  onNavigate?: (path: string) => void;
}

type PageableResult = FilesystemDirectoryPage | FilesystemSearchPage;

export function pageIdentity(page: PageableResult): string {
  return page.kind === "filesystem.directory-page"
    ? `${page.kind}:${page.namespace}:${page.path}`
    : `${page.kind}:${page.namespace}:${page.root ?? ""}:${page.query}`;
}

export function appendFilesystemPage(current: PageableResult, next: PageableResult): PageableResult {
  if (pageIdentity(current) !== pageIdentity(next)) return current;
  return {
    ...current,
    ...next,
    entries: mergeFilesystemEntries(current.entries, next.entries),
  };
}

function requireDirectoryPage(payload: unknown): FilesystemDirectoryPage {
  const result = normalizeFilesystemPayload(payload);
  if (result?.kind !== "filesystem.directory-page") {
    throw new Error("The engine returned an invalid directory page.");
  }
  return result;
}

function requirePageableResult(payload: unknown): PageableResult {
  const result = normalizeFilesystemPayload(payload);
  if (result?.kind !== "filesystem.directory-page" && result?.kind !== "filesystem.search-page") {
    throw new Error("The engine returned an invalid filesystem page.");
  }
  return result;
}

/** Connect the canonical renderer to direct engine paging and lazy child loading. */
export function FilesystemResultController({ result, layout = "embedded", onReference, onNavigate }: FilesystemResultControllerProps) {
  const [current, setCurrent] = useState(result);
  const [loadingMore, setLoadingMore] = useState(false);
  const [pagingError, setPagingError] = useState<string | null>(null);
  const requestId = useRef(0);
  const loadingMoreRef = useRef(false);
  const resultIdentityRef = useRef(
    result.kind === "filesystem.directory-page" || result.kind === "filesystem.search-page"
      ? pageIdentity(result)
      : result.kind,
  );

  resultIdentityRef.current =
    result.kind === "filesystem.directory-page" || result.kind === "filesystem.search-page"
      ? pageIdentity(result)
      : result.kind;

  useEffect(() => {
    requestId.current += 1;
    loadingMoreRef.current = false;
    setCurrent(result);
    setLoadingMore(false);
    setPagingError(null);
  }, [result]);

  const loadChildren = useCallback(async (entry: FilesystemEntry, cursor?: string): Promise<FilesystemDirectoryPage> => {
    return requireDirectoryPage(await engine.listFilesystem(entry.path, {
      ...(cursor ? { cursor } : {}),
      limit: 100,
    }));
  }, []);

  const loadMore = useCallback(async (cursor: string): Promise<void> => {
    if (current.kind !== "filesystem.directory-page" && current.kind !== "filesystem.search-page") return;
    if (loadingMoreRef.current) return;
    loadingMoreRef.current = true;
    const id = ++requestId.current;
    const identity = pageIdentity(current);
    setLoadingMore(true);
    setPagingError(null);
    try {
      const payload = current.kind === "filesystem.directory-page"
        ? await engine.listFilesystem(current.path, { cursor, limit: 100 })
        : await engine.findFilesystem(current.query, {
            ...(current.root ? { root: current.root } : {}),
            cursor,
            limit: 100,
          });
      const next = requirePageableResult(payload);
      if (id !== requestId.current || identity !== resultIdentityRef.current) return;
      setCurrent((value) => {
        if (value.kind !== "filesystem.directory-page" && value.kind !== "filesystem.search-page") {
          return value;
        }
        return appendFilesystemPage(value, next);
      });
    } catch (reason) {
      if (id === requestId.current && identity === resultIdentityRef.current) {
        setPagingError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (id === requestId.current) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  }, [current]);

  const currentIdentity = current.kind === "filesystem.directory-page" || current.kind === "filesystem.search-page"
    ? pageIdentity(current)
    : current.kind;
  const visibleResult = currentIdentity === resultIdentityRef.current ? current : result;

  return (
    <FilesystemResultView
      result={visibleResult}
      layout={layout}
      {...(onReference ? { onReference } : {})}
      {...(onNavigate ? { onNavigate } : {})}
      onLoadChildren={loadChildren}
      onLoadMore={(cursor) => void loadMore(cursor)}
      loadingMore={loadingMore}
      pagingError={pagingError}
    />
  );
}
